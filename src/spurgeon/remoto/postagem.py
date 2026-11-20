"""Postar no YouTube pelo celular (Fase R4): o PC posta pelo YouTube Studio (`canal/postar.py`).

Uma postagem por vez, numa thread do servidor, com o navegador da automação (Remoto.md, S5).
Enquanto ela roda, o servidor segura a trava do celular no projeto (a janela do PC não regrava a
lista de cortes por cima do "agendado para"). No fim:

* o resultado vai para `_work/postagens.json` (`RegistroDePostagens`, o mesmo das redes da `main`),
  na rede `youtube`: `agendado` (com o horário), `enviado` (publicado agora) ou `erro`; um erro
  depois de o arquivo chegar ao Studio fica como `rascunho`, e a próxima tentativa o retoma;
* o corte ganha `agendado_para` (ou `postado`/`postado_em`) e o `.mp4` vai para `agendados/` (ou
  `postados/`), como o Studio do PC faz (`aplicar_marcas_de_postado`); com um processamento rodando
  o arquivo não se mexe (o trabalhador regrava o registro dos renderizados);
* o celular recebe um aviso.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from spurgeon.core.projeto import PastasProjeto, ler_nome_e_data
from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, RegistroDePostagens
from spurgeon.publicacao.modelo import AGENDADO, ENVIADO, ERRO, RASCUNHO, Resultado
from spurgeon.remoto import sessao
from spurgeon.remoto.dados import carregar_cortes, shorts_no_disco

log = logging.getLogger(__name__)

REDE = "youtube"
#: o YouTube pede uma folga para programar; abaixo disso, melhor publicar agora
ANTECEDENCIA_MINIMA = timedelta(minutes=15)
RENOVAR_A_TRAVA_S = 30.0
FORMATO = "%Y-%m-%dT%H:%M"


class PostagemInvalida(ValueError):
    pass


def registro(pastas: PastasProjeto) -> RegistroDePostagens:
    return RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO)


def do_youtube(pastas: PastasProjeto, id_do_corte: int) -> Resultado | None:
    return registro(pastas).do_corte(id_do_corte).get(REDE)


def sermao_do_projeto(pastas: PastasProjeto) -> str:
    """O título do vídeo completo no canal: o que o projeto guardou ao ser criado pelo celular
    (`origem_youtube.json`), senão o nome do projeto (que costuma ser o mesmo título)."""
    from spurgeon.remoto.novo import ler_origem

    if titulo := ler_origem(pastas).get("titulo"):
        return str(titulo)
    lido = ler_nome_e_data(pastas.raiz)
    return lido[0] if lido else pastas.raiz.name


@dataclass(frozen=True)
class Pedido:
    id_do_corte: int
    quando: datetime | None  # None = publicar agora
    relacionado: str  # "" = sem vídeo relacionado
    forcar: bool = False


def ler_pedido(dados: dict[str, Any], id_do_corte: int, agora: datetime) -> Pedido:
    texto = dados.get("quando")
    quando = None
    if texto:
        try:
            quando = datetime.strptime(str(texto), FORMATO)
        except ValueError as erro:
            raise PostagemInvalida("Data ou hora inválida.") from erro
        if quando < agora + ANTECEDENCIA_MINIMA:
            raise PostagemInvalida(
                "Para programar, escolha um horário pelo menos 15 minutos à frente "
                "(ou publique agora)."
            )
    relacionado = str(dados.get("relacionado") or "").strip()[:200]
    return Pedido(id_do_corte, quando, relacionado, bool(dados.get("forcar")))


def conferir(pastas: PastasProjeto, pedido: Pedido):
    """O Short a postar (renderizado e em dia) e se é para retomar o rascunho já enviado."""
    short = next((s for s in shorts_no_disco(pastas) if s.corte.id == pedido.id_do_corte), None)
    if short is None:
        raise PostagemInvalida("Este corte não existe no projeto.")
    if short.arquivo is None or short.situacao != "pronto":
        raise PostagemInvalida("Renderize o Short antes de postar (ele mudou ou ainda não existe).")
    anterior = do_youtube(pastas, pedido.id_do_corte)
    if anterior is not None and anterior.estado in (AGENDADO, ENVIADO) and not pedido.forcar:
        raise PostagemInvalida(
            "Este Short já foi "
            + ("programado" if anterior.estado == AGENDADO else "publicado")
            + f" no YouTube ({anterior.url})."
        )
    return short, anterior is not None and anterior.estado == RASCUNHO


def registrar_sucesso(
    pastas: PastasProjeto,
    id_do_corte: int,
    feito,
    quando: datetime | None,
    *,
    mover_arquivos: bool = True,
    agora: datetime | None = None,
) -> Resultado:
    """Grava o resultado e marca o corte (agendado ou postado), como o Studio faria."""
    from spurgeon.render.shorts import aplicar_marcas_de_postado
    from spurgeon.shorts.etapas import gravar_campos_do_corte

    detalhe = f"Vídeo relacionado: {feito.relacionado}" if feito.relacionado else ""
    if quando is not None:
        resultado = Resultado(REDE, AGENDADO, feito.video_id, feito.link,
                              quando.astimezone().isoformat(), detalhe)  # fmt: skip
        gravar_campos_do_corte(
            pastas.cortes_shorts, id_do_corte, agendado_para=quando.strftime(FORMATO)
        )
    else:
        momento = agora or datetime.now()
        resultado = Resultado(REDE, ENVIADO, feito.video_id, feito.link,
                              momento.astimezone().isoformat(), detalhe)  # fmt: skip
        gravar_campos_do_corte(pastas.cortes_shorts, id_do_corte, postado=True,
                               postado_em=momento.strftime(FORMATO), agendado_para="")  # fmt: skip
    registro(pastas).gravar(id_do_corte, resultado)
    if mover_arquivos:
        aplicar_marcas_de_postado(pastas, carregar_cortes(pastas))
    return resultado


@dataclass
class Envio:
    projeto: str
    id_do_corte: int
    texto: str = "Preparando…"
    rodando: bool = True
    link: str = ""
    erro: str = ""
    _pronto: threading.Event = field(default_factory=threading.Event, repr=False)

    def para_json(self) -> dict[str, Any]:
        return {"projeto": self.projeto, "corte": self.id_do_corte, "texto": self.texto,
                "rodando": self.rodando, "link": self.link, "erro": self.erro}  # fmt: skip


def _postar_de_verdade(pastas, short, pedido: Pedido, rascunho: bool, andamento):
    from spurgeon.canal.navegador import abrir_studio
    from spurgeon.canal.postar import PedidoDePostagem, postar

    corte = short.corte
    with abrir_studio() as studio:
        return postar(
            studio,
            PedidoDePostagem(
                short.arquivo,
                corte.titulo,
                corte.descricao,
                list(corte.hashtags),
                pedido.relacionado,
                pedido.quando,
            ),
            rascunho=rascunho,
            fotos=pastas.work / "remoto" / "postagem",
            andamento=andamento,
        )


class Postagens:
    """Uma postagem por vez (o navegador da automação é um só)."""

    def __init__(
        self,
        *,
        postar: Callable[..., Any] = _postar_de_verdade,
        ao_terminar: Callable[[Envio], None] = lambda _e: None,
        processando: Callable[[], bool] = lambda: False,
        renovar_s: float = RENOVAR_A_TRAVA_S,
    ):
        self._postar = postar
        self._ao_terminar = ao_terminar
        self._processando = processando
        self._renovar_s = renovar_s
        self._trava = threading.Lock()
        self.atual: Envio | None = None

    def ocupada(self) -> bool:
        return self.atual is not None and self.atual.rodando

    def esperar(self, timeout: float | None = None) -> bool:
        return self.atual is None or self.atual._pronto.wait(timeout)

    def iniciar(self, pastas: PastasProjeto, pedido: Pedido) -> Envio:
        short, rascunho = conferir(pastas, pedido)
        with self._trava:
            if self.ocupada():
                raise PostagemInvalida("O PC já está postando um Short. Espere terminar.")
            envio = Envio(pastas.raiz.name, pedido.id_do_corte)
            self.atual = envio
        threading.Thread(target=self._rodar, args=(envio, pastas, short, pedido, rascunho),
                         daemon=True).start()  # fmt: skip
        return envio

    def _rodar(self, envio: Envio, pastas, short, pedido: Pedido, rascunho: bool) -> None:
        parar = threading.Event()

        def renovar() -> None:
            while not parar.wait(self._renovar_s):
                with contextlib.suppress(OSError):
                    sessao.tomar(pastas)

        threading.Thread(target=renovar, daemon=True).start()
        try:
            feito = self._postar(pastas, short, pedido, rascunho,
                                 lambda t: setattr(envio, "texto", t))  # fmt: skip
            sessao.tomar(pastas)
            registrar_sucesso(pastas, pedido.id_do_corte, feito, pedido.quando,
                              mover_arquivos=not self._processando())  # fmt: skip
            envio.link = feito.link
            envio.texto = (
                f"Programado no YouTube para {pedido.quando:%d/%m às %H:%M}."
                if pedido.quando
                else "Publicado no YouTube."
            ) + (f" Vinculado a “{feito.relacionado}”." if feito.relacionado else "")
            log.info("Short %s postado pelo celular: %s", pedido.id_do_corte, feito.link)
        except Exception as erro:
            log.exception("Falha ao postar o Short pelo YouTube Studio.")
            envio.erro = str(erro) or erro.__class__.__name__
            envio.texto = "Não deu para postar."
            enviado = bool(getattr(erro, "enviado", False))
            registro(pastas).gravar(
                pedido.id_do_corte,
                Resultado(
                    REDE,
                    RASCUNHO if enviado else ERRO,
                    getattr(erro, "video_id", ""),
                    detalhe=envio.erro,
                ),
            )
        finally:
            parar.set()
            envio.rodando = False
            envio._pronto.set()
            try:
                self._ao_terminar(envio)
            except Exception:
                log.exception("Falha ao avisar da postagem.")


def fotos_da_postagem(pastas: PastasProjeto) -> Path:
    return pastas.work / "remoto" / "postagem"
