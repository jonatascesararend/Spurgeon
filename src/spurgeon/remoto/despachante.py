"""O despachante: anda a fila dos Shorts (`spurgeon.shorts.fila`) de todos os projetos.

Uma coisa por vez, na ordem dos pedidos, sem atrapalhar o que o PC está fazendo:

1. com qualquer processamento rodando no PC (pedido pela janela, pelo celular ou pela própria
   fila) ele só espera;
2. se há Shorts esperando a renderização, começa uma renderização "só estes" do projeto do pedido
   mais antigo (como o "Renderizar só este" do celular), e espera ela acabar;
3. com a renderização toda feita, abre o YouTube Studio e faz um item: programa (ou publica) um
   Short pronto, ou troca o horário de um já programado. Depois volta ao passo 1.

A janela do PC e o servidor do celular têm cada um o seu despachante, mas só um anda a fila: o
que está com a vez (`Vez`, um arquivo com o pid de quem a tem). Se esse processo fecha, o outro a
pega em menos de um minuto.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts import fila as fila_dos_shorts
from spurgeon.shorts.fila import (
    FASE_ESPERANDO,
    FASE_RENDER,
    FASE_YOUTUBE,
    FORMATO,
    REAGENDAR,
    FilaDoProjeto,
    ItemDaFila,
)

log = logging.getLogger(__name__)

INTERVALO_S = 5.0
#: quem tem a vez e não dá sinal por mais que isto a perde (o processo travou)
VALIDADE_DA_VEZ_S = 60.0
#: um horário pedido que ficou perto demais enquanto o Short esperava a fila não serve mais
FOLGA_NA_HORA_DE_PROGRAMAR_S = 120.0

Andamento = Callable[[str], None]


def arquivo_da_vez() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Spurgeon" / "despachante.json"


def _pid_vivo(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        return True


class Vez:
    """Quem anda a fila neste PC: o processo cujo pid está no arquivo (e que deu sinal há pouco)."""

    def __init__(
        self,
        arquivo: Path,
        *,
        pid: int | None = None,
        relogio: Callable[[], float] = time.time,
        vivo: Callable[[int], bool] = _pid_vivo,
    ):
        self.arquivo = arquivo
        self.pid = os.getpid() if pid is None else pid
        self._relogio, self._vivo = relogio, vivo

    def _ler(self) -> tuple[int, float] | None:
        try:
            dados = ler_json(self.arquivo)
            return int(dados["pid"]), float(dados["visto"])
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def tomar(self) -> bool:
        """Fica com a vez (ou renova a que já tem). False: outro processo vivo a tem."""
        lido = self._ler()
        agora = self._relogio()
        if lido is not None:
            pid, visto = lido
            if pid != self.pid and self._vivo(pid) and agora - visto <= VALIDADE_DA_VEZ_S:
                return False
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            escrever_json_atomico(self.arquivo, {"pid": self.pid, "visto": agora})
        except OSError:
            return False
        lido = self._ler()
        return lido is not None and lido[0] == self.pid

    def soltar(self) -> None:
        lido = self._ler()
        if lido is not None and lido[0] == self.pid:
            self.arquivo.unlink(missing_ok=True)


@dataclass(frozen=True)
class EventoDaFila:
    """Um item da fila terminou (ou parou com erro): para avisar o celular e a janela."""

    projeto: str
    corte: int
    titulo: str
    texto: str
    erro: str = ""
    link: str = ""


# --- o que o despachante faz no YouTube ----------------------------------------------------------
def programar_no_youtube(pastas: PastasProjeto, item: ItemDaFila, andamento: Andamento) -> str:
    """Posta o Short pelo YouTube Studio (programado em `item.quando`, ou publicado agora) e grava
    o resultado como o celular sempre fez (`remoto/postagem.py`). Devolve o texto do aviso."""
    from spurgeon.publicacao.modelo import ERRO, RASCUNHO, Resultado
    from spurgeon.remoto import postagem

    pedido = postagem.Pedido(item.corte, item.horario(), item.relacionado)
    short, rascunho = postagem.conferir(pastas, pedido)
    try:
        feito = postagem._postar_de_verdade(pastas, short, pedido, rascunho, andamento)
    except Exception as erro:
        enviado = bool(getattr(erro, "enviado", False))
        postagem.registro(pastas).gravar(item.corte, Resultado(
            postagem.REDE, RASCUNHO if enviado else ERRO, getattr(erro, "video_id", ""),
            detalhe=str(erro) or erro.__class__.__name__,
        ))  # fmt: skip
        raise
    quando = datetime.strptime(feito.programado_para, FORMATO) if feito.programado_para else None
    postagem.registrar_sucesso(pastas, item.corte, feito, quando)
    texto = (
        f"Programado no YouTube para {quando:%d/%m às %H:%M}." if quando
        else "Publicado no YouTube."
    )  # fmt: skip
    if feito.relacionado:
        texto += f" Vinculado a “{feito.relacionado}”."
    return texto + (f"\n{feito.link}" if feito.link else "")


def horario_no_youtube(pastas: PastasProjeto, id_do_corte: int) -> datetime | None:
    """A hora para a qual o Short está programado no YouTube, pelo que o Spurgeon sabe (o
    registro da postagem, que o cronograma acerta com o canal), na hora do computador."""
    from spurgeon.publicacao.modelo import AGENDADO
    from spurgeon.remoto import postagem

    youtube = postagem.do_youtube(pastas, id_do_corte)
    if youtube is None or youtube.estado != AGENDADO or not youtube.id_remoto:
        return None
    try:
        return datetime.fromisoformat(youtube.quando).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def reagendar_no_youtube(
    pastas: PastasProjeto,
    item: ItemDaFila,
    andamento: Andamento,
    *,
    abrir=None,
    reprogramar=None,
    agora: Callable[[], datetime] = datetime.now,
) -> str:
    """Troca no YouTube Studio o horário de um Short já programado (até 5 minutos antes dele
    sair) e acerta o registro e o "agendado para" do Short com a hora que o canal mostra."""
    from spurgeon.canal.navegador import abrir_studio
    from spurgeon.canal.postar import reprogramar as reprogramar_no_studio
    from spurgeon.remoto import postagem
    from spurgeon.shorts.etapas import gravar_campos_do_corte

    novo = item.horario()
    marcado = horario_no_youtube(pastas, item.corte)
    youtube = postagem.do_youtube(pastas, item.corte)
    if novo is None or marcado is None or youtube is None:
        raise fila_dos_shorts.PedidoInvalido(
            "Este Short não está programado no YouTube pelo Spurgeon: não há o que reagendar."
        )
    motivo = fila_dos_shorts.pode_reagendar(marcado, novo, agora())
    if motivo:
        raise fila_dos_shorts.PedidoInvalido(motivo)
    with (abrir or abrir_studio)() as studio:
        marcada = (reprogramar or reprogramar_no_studio)(
            studio, youtube.id_remoto, novo, fotos=pastas.work / "remoto" / "postagem",
            andamento=andamento,
        )  # fmt: skip
    youtube.quando = marcada.astimezone().isoformat()
    postagem.registro(pastas).gravar(item.corte, youtube)
    gravar_campos_do_corte(
        pastas.cortes_shorts, item.corte, agendado_para=marcada.strftime(FORMATO)
    )
    return f"Horário trocado no YouTube: agora sai em {marcada:%d/%m às %H:%M}."


# --- o despachante -------------------------------------------------------------------------------
class Despachante:
    def __init__(
        self,
        pasta_projetos: Callable[[], Path],
        *,
        fila,
        vez: Vez,
        programar: Callable[[PastasProjeto, ItemDaFila, Andamento], str] = programar_no_youtube,
        reagendar: Callable[[PastasProjeto, ItemDaFila, Andamento], str] = reagendar_no_youtube,
        procurar: Callable[[], list[int]] | None = None,
        ao_terminar: Callable[[EventoDaFila], None] = lambda _e: None,
        relogio: Callable[[], datetime] = datetime.now,
        intervalo_s: float = INTERVALO_S,
    ):
        from spurgeon.remoto.fila import trabalhadores_rodando

        self._pasta_projetos = pasta_projetos
        self.fila = fila  # `remoto.fila.Fila`: começa a renderização (um processamento por vez)
        self.vez = vez
        self._programar, self._reagendar = programar, reagendar
        self._procurar = procurar or trabalhadores_rodando
        self._ao_terminar = ao_terminar
        self._relogio = relogio
        self._intervalo_s = intervalo_s
        self._render: tuple[PastasProjeto, list[int]] | None = None  # a renderização da fila
        self._com_a_vez = False
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None
        #: o item que está no YouTube agora (projeto, corte), para a janela saber
        self.no_youtube: tuple[str, int] | None = None

    # --- a thread ------------------------------------------------------------------------------
    def iniciar(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._rodar, name="fila-dos-shorts", daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()
        if self._com_a_vez and self._render is None:
            self.vez.soltar()

    def acordar(self) -> None:
        """Um pedido novo entrou: olha a fila agora, sem esperar o próximo passo."""
        self._acordar.set()

    def _rodar(self) -> None:
        while not self._parar.is_set():
            try:
                self.passo()
            except Exception:  # um projeto estragado não para a fila dos outros
                log.exception("A fila dos Shorts falhou neste passo.")
            self._acordar.wait(self._intervalo_s)
            self._acordar.clear()

    # --- um passo ------------------------------------------------------------------------------
    def passo(self) -> None:
        if not self.vez.tomar():
            self._com_a_vez = False
            return
        if not self._com_a_vez:  # acabou de pegar a vez: o que outro deixou "em andamento" volta
            self._com_a_vez = True
            self._soltar_o_que_ficou_pela_metade()
        if self._render is not None:
            if self.fila.rodando:
                return
            self._fim_da_renderizacao()
        if self.fila.rodando or self._ocupado():
            return
        projetos = fila_dos_shorts.projetos_com_itens(self._pasta_projetos())
        pendentes = [(p, i, s) for p in projetos for i, s in self._itens_com_situacao(p)]
        pendentes = self._esquecer_os_que_ficaram_prontos(pendentes)
        if self._comecar_renderizacao(pendentes):
            return
        self._um_no_youtube(pendentes)

    def _ocupado(self) -> bool:
        try:
            return bool(self._procurar())
        except Exception:  # sem como saber (permissão do Windows): espera
            return True

    def _soltar_o_que_ficou_pela_metade(self) -> None:
        for pastas in fila_dos_shorts.projetos_com_itens(self._pasta_projetos()):
            fila = FilaDoProjeto(pastas)
            for item in fila.itens():
                if item.fase != FASE_ESPERANDO:
                    fila.mudar(item.corte, fase=FASE_ESPERANDO, texto="")

    def _itens_com_situacao(self, pastas: PastasProjeto):
        """Cada item do projeto com o Short dele (`ShortNoDisco`, None se o corte sumiu)."""
        from spurgeon.remoto.dados import shorts_no_disco

        itens = FilaDoProjeto(pastas).itens()
        if not itens:
            return []
        shorts = {s.corte.id: s for s in shorts_no_disco(pastas)}
        return [(i, shorts.get(i.corte)) for i in itens]

    @staticmethod
    def _precisa_renderizar(item: ItemDaFila, short) -> bool:
        if item.erro or short is None:
            return False
        if item.renderizar:
            return True
        return item.vai_ao_youtube and item.acao != REAGENDAR and short.situacao != "pronto"

    def _parar_item(self, pastas: PastasProjeto, item: ItemDaFila, short, erro: str) -> None:
        FilaDoProjeto(pastas).mudar(item.corte, fase=FASE_ESPERANDO, texto="", erro=erro)
        self._avisar(pastas, item.corte, short, "", erro)

    # --- renderizar ----------------------------------------------------------------------------
    @staticmethod
    def _esquecer_os_que_ficaram_prontos(pendentes):
        """Um "renderizar" que ficou pronto por outro caminho (o botão do PC, o celular) não
        precisa de outra renderização: sai da fila (ou só perde o "renderizar antes")."""
        restantes = []
        for pastas, item, short in pendentes:
            if (
                item.renderizar and not item.refazer and not item.erro and short is not None
                and short.situacao == "pronto"
            ):  # fmt: skip
                if not item.vai_ao_youtube:
                    FilaDoProjeto(pastas).remover(item.corte)
                    continue
                item = FilaDoProjeto(pastas).mudar(item.corte, renderizar=False) or item
            restantes.append((pastas, item, short))
        return restantes

    def _comecar_renderizacao(self, pendentes) -> bool:
        """Começa a renderização do projeto com o pedido mais antigo. False se não há o que
        renderizar (ou se não deu para começar: o item fica com o erro e a fila segue)."""
        from spurgeon.remoto import dados
        from spurgeon.remoto.fila import AJUSTES_DA_RENDERIZACAO, FilaOcupada
        from spurgeon.shorts.edicao import passa_do_limite

        precisam = [(p, i, s) for p, i, s in pendentes if self._precisa_renderizar(i, s)]
        for pastas, item, short in [(p, i, s) for p, i, s in pendentes if s is None and not i.erro]:
            self._parar_item(pastas, item, short, "Este corte não existe mais no projeto.")
        if not precisam:
            return False
        precisam.sort(key=lambda t: t[1].pedido_em)
        pastas = precisam[0][0]
        do_projeto = [(i, s) for p, i, s in precisam if p.raiz == pastas.raiz]
        refazer = any(i.refazer for i, _s in do_projeto)
        do_projeto = [(i, s) for i, s in do_projeto if i.refazer == refazer]
        prontos_para_render = []
        for item, short in do_projeto:
            if short.numero is None:
                self._parar_item(pastas, item, short, "Aprove este Short para ele ser renderizado.")
            elif passa_do_limite(short.corte):
                self._parar_item(
                    pastas, item, short, "Este Short passa do limite de duração: encurte-o."
                )
            else:
                prontos_para_render.append(item)
        if not prontos_para_render:
            return True  # os erros foram marcados; o próximo passo olha o resto
        if dados.video_original(pastas) is None:
            for item, short in do_projeto:
                self._parar_item(
                    pastas, item, short,
                    "O vídeo original deste projeto não está mais no PC: sem ele não dá para "
                    "renderizar.",
                )  # fmt: skip
            return True
        ids = [i.corte for i in prontos_para_render]
        fila = FilaDoProjeto(pastas)
        for id_do_corte in ids:
            fila.mudar(id_do_corte, fase=FASE_RENDER, texto="Renderizando…", erro="")
        try:
            self.fila.iniciar(
                pastas, ajustes=AJUSTES_DA_RENDERIZACAO, so_os_shorts=ids, refazer=refazer
            )
        except FilaOcupada:  # outro processamento começou neste meio tempo: tenta no próximo
            for id_do_corte in ids:
                fila.mudar(id_do_corte, fase=FASE_ESPERANDO, texto="")
            return True
        except Exception as erro:
            for item in prontos_para_render:
                self._parar_item(pastas, item, None, f"A renderização não começou: {erro}")
            return True
        self._render = (pastas, ids)
        log.info("Fila dos Shorts: renderizando %s de %s.", ids, pastas.raiz.name)
        return True

    def _fim_da_renderizacao(self) -> None:
        from spurgeon.remoto.dados import shorts_no_disco

        assert self._render is not None
        pastas, ids = self._render
        self._render = None
        shorts = {s.corte.id: s for s in shorts_no_disco(pastas)}
        fila = FilaDoProjeto(pastas)
        motivo = getattr(self.fila, "ultimo_erro", "") or ""
        if getattr(self.fila, "ultimo_resultado", "") == "cancelado":
            motivo = "A renderização foi cancelada."
        for id_do_corte in ids:
            item = fila.do_corte(id_do_corte)
            if item is None:
                continue  # tirado da fila enquanto renderizava
            short = shorts.get(id_do_corte)
            if short is not None and short.situacao == "pronto":
                if item.vai_ao_youtube:
                    fila.mudar(id_do_corte, renderizar=False, refazer=False,
                               fase=FASE_ESPERANDO, texto="")  # fmt: skip
                else:
                    fila.remover(id_do_corte)
                    self._avisar(pastas, id_do_corte, short, "Renderizado.", "")
            else:
                self._parar_item(
                    pastas, item, short,
                    "A renderização não deixou o Short pronto"
                    + (f": {motivo}" if motivo else " (veja o registro do processamento)."),
                )  # fmt: skip

    # --- o YouTube ------------------------------------------------------------------------------
    def _um_no_youtube(self, pendentes) -> None:
        candidatos = [
            (p, i, s) for p, i, s in pendentes
            if i.vai_ao_youtube and not i.erro and s is not None
            and (i.acao == REAGENDAR or s.situacao == "pronto")
        ]  # fmt: skip
        if not candidatos:
            return
        pastas, item, short = min(candidatos, key=lambda t: t[1].pedido_em)
        quando = item.horario()
        if (
            item.acao != REAGENDAR and quando is not None
            and (quando - self._relogio()).total_seconds() < FOLGA_NA_HORA_DE_PROGRAMAR_S
        ):  # fmt: skip
            self._parar_item(
                pastas, item, short,
                "O horário pedido chegou enquanto o Short esperava a fila: escolha outro horário "
                "e peça de novo.",
            )  # fmt: skip
            return
        fila = FilaDoProjeto(pastas)
        fila.mudar(item.corte, fase=FASE_YOUTUBE, texto="Abrindo o YouTube Studio…", erro="")
        self.no_youtube = (pastas.raiz.name, item.corte)

        def andamento(texto: str) -> None:
            fila.mudar(item.corte, texto=texto)

        try:
            fazer = self._reagendar if item.acao == REAGENDAR else self._programar
            texto = fazer(pastas, item, andamento)
        except ImportError:  # sem o extra do controle remoto (o Playwright) neste PC
            self._parar_item(
                pastas, item, short,
                "A automação do YouTube Studio não está instalada neste PC (o extra do controle "
                "remoto, com o Playwright).",
            )  # fmt: skip
        except Exception as erro:
            log.exception(
                "A fila dos Shorts não conseguiu fazer o Short %s no YouTube.", item.corte
            )
            self._parar_item(pastas, item, short, str(erro) or erro.__class__.__name__)
        else:
            fila.remover(item.corte)
            texto, _, link = texto.partition("\n")
            self._avisar(pastas, item.corte, short, texto, "", link)
            log.info("Fila dos Shorts: %s (Short %s de %s)", texto, item.corte, pastas.raiz.name)
        finally:
            self.no_youtube = None

    def _avisar(self, pastas, id_do_corte: int, short, texto: str, erro: str, link: str = ""):
        titulo = short.corte.titulo if short is not None else f"Corte {id_do_corte}"
        try:
            self._ao_terminar(
                EventoDaFila(pastas.raiz.name, id_do_corte, titulo, texto, erro, link)
            )
        except Exception:
            log.exception("Falha ao avisar do fim de um item da fila.")
