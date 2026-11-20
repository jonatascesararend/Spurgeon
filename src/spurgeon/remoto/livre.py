"""Shorts livre pelo celular (Fase R3): escolher um trecho no texto do sermão e criar o Short.

É o mesmo Shorts livre do PC (`ui/shorts_livre.py`), sem a tela: as palavras do trecho do vídeo
que o projeto usa (`palavras_da_janela`), divididas em frases (`separar_frases`), com a cor da
categoria que a IA detectou em cada frase (`shorts/marcas.py`) e as palavras que já estão em algum
Short. A busca é a mesma (`shorts/busca.py`: palavras parecidas ou tema) e o corte sai de
`gerar_corte_livre` (a IA avalia, dá a nota, cria título, descrição e hashtags: cerca de 2 minutos
na CPU).

**Índices:** as posições das palavras aqui são as da lista da janela do vídeo (como no Shorts livre
do PC), não as da transcrição inteira (a Fase R2 usa as da transcrição). O celular só as devolve
para este módulo.

A criação roda numa thread do servidor, uma por vez. Enquanto ela roda, o servidor mantém a trava
do celular no projeto (renova a cada 30 s) para a janela do PC não regravar a lista de cortes por
cima do Short novo; no fim, o corte entra no `cortes_shorts.json` e o celular recebe um aviso.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from spurgeon.core.estado import carregar_estado
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.projeto import PastasProjeto
from spurgeon.remoto import sessao
from spurgeon.remoto.dados import carregar_cortes
from spurgeon.shorts.analise import palavras_da_janela, separar_frases
from spurgeon.shorts.busca import procurar_palavras, procurar_tema
from spurgeon.shorts.dados import Corte, Frase, cor_da_categoria, rotulo_da_categoria
from spurgeon.shorts.edicao import corte_com_indices
from spurgeon.shorts.livre import (
    ContextoDaIA,
    Trecho,
    duracao_do_trecho,
    gerar_corte_livre,
    preparar_cliente,
    problema_do_trecho,
    proximo_id,
    resumo_das_pontas,
)
from spurgeon.shorts.marcas import (
    categoria_de_cada_frase,
    short_de_cada_palavra,
    shorts_feitos,
    trechos_detectados,
)
from spurgeon.transcricao.formato import Palavra, Transcricao

log = logging.getLogger(__name__)

#: de quanto em quanto tempo a criação renova a trava do celular
RENOVAR_A_TRAVA_S = 30.0


class TrechoInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Texto:
    palavras: list[Palavra]
    frases: list[Frase]


def carregar_texto(pastas: PastasProjeto) -> Texto:
    """As palavras do trecho do vídeo que o projeto usa e as frases delas (como o PC)."""
    transcricao = Transcricao.carregar(pastas.transcricao)
    palavras = transcricao.palavras
    estado = carregar_estado(pastas.raiz)
    if estado is not None and estado.opcoes.tem_janela:
        inicio, fim = estado.opcoes.janela_do_video(transcricao.duracao_s)
        palavras = palavras_da_janela(palavras, inicio, fim)
    return Texto(palavras, separar_frases(palavras) if palavras else [])


def texto_para_json(pastas: PastasProjeto, texto: Texto) -> dict[str, Any]:
    """O sermão para o celular: frases (hora, categoria) com as palavras e o Short de cada uma."""
    detectados = trechos_detectados(pastas, texto.palavras)
    categoria = categoria_de_cada_frase(detectados, texto.frases)
    feitos = shorts_feitos(texto.palavras, carregar_cortes(pastas))
    de_cada_palavra = short_de_cada_palavra(feitos)
    frases = []
    for posicao, frase in enumerate(texto.frases):
        detectado = categoria.get(posicao)
        frases.append(
            {
                "de": frase.primeira,
                "i": round(frase.inicio, 2),
                "cat": detectado.categoria if detectado else "",
                "palavras": [
                    [texto.palavras[n].texto, de_cada_palavra[n].id if n in de_cada_palavra else 0]
                    for n in range(frase.primeira, frase.ultima + 1)
                ],
            }
        )
    usadas = {d.categoria for d in detectados} | {f.categoria for f in feitos}
    return {
        "frases": frases,
        "categorias": {
            c: {"rotulo": rotulo_da_categoria(c), "cor": cor_da_categoria(c)}
            for c in sorted(usadas)
            if c
        },
        "shorts": {str(f.id): {"titulo": f.titulo, "cat": f.categoria} for f in feitos},
    }


@lru_cache(maxsize=1)
def _modelo_de_vetores():
    from spurgeon.shorts.busca import carregar_modelo_de_vetores

    return carregar_modelo_de_vetores()


def buscar(texto: Texto, consulta: str, modo: str) -> list[dict[str, Any]]:
    if not consulta.strip():
        return []
    if modo == "tema":
        achados = procurar_tema(texto.palavras, texto.frases, consulta, _modelo_de_vetores())
    else:
        achados = procurar_palavras(texto.palavras, texto.frases, consulta)
    return [
        {
            "de": a.primeira,
            "ate": a.ultima,
            "i": round(a.inicio, 2),
            "pontos": a.pontos,
            "texto": a.texto,
            "casadas": list(a.palavras),
        }
        for a in achados
    ]


def trecho_do_pedido(texto: Texto, primeira: Any, ultima: Any, fora: Any = ()) -> Trecho:
    try:
        de, ate = int(primeira), int(ultima)
        pulados = frozenset(int(n) for n in (fora or ()))
    except (TypeError, ValueError) as erro:
        raise TrechoInvalido("Trecho inválido.") from erro
    if not (0 <= de <= ate < len(texto.palavras)):
        raise TrechoInvalido("O trecho está fora do sermão.")
    trecho = Trecho(de, ate, frozenset(n for n in pulados if de < n < ate))
    if not trecho.indices:
        raise TrechoInvalido("O trecho ficou sem nenhuma palavra.")
    return trecho


def resumo_do_trecho(texto: Texto, trecho: Trecho) -> dict[str, Any]:
    return {
        "duracao": round(duracao_do_trecho(texto.palavras, trecho), 1),
        "problema": problema_do_trecho(texto.palavras, trecho) or "",
        "pontas": resumo_das_pontas(texto.palavras, trecho),
    }


def corte_de_previa(texto: Texto, trecho: Trecho) -> Corte:
    """Um corte provisório do trecho, só para ouvir antes de criar (pontas com o respiro de
    costume e os pedaços de fora como saltos)."""
    base = Corte(id=-1, inicio=0.0, fim=0.0, texto="", categoria="", nota=0.0, detalhe={})
    return corte_com_indices(texto.palavras, base, trecho.indices)


def contexto_da_ia(pastas: PastasProjeto) -> ContextoDaIA:
    """O modelo do Ollama e quanto da máquina usar: as opções do projeto (da última execução)."""
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        raise TrechoInvalido("O projeto não tem estado.json.")
    perfil = detectar_hardware(pastas.raiz.parent)
    return ContextoDaIA(
        modelo=estado.opcoes.ollama_modelo,
        threads=threads_para_uso(perfil, estado.opcoes.uso_cpu_percentual),
        usar_gpu=perfil.cuda_utilizavel,
    )


def gravar_corte_novo(pastas: PastasProjeto, corte: Corte) -> None:
    from spurgeon.shorts.etapas import gravar_cortes

    gravar_cortes(pastas.cortes_shorts, [*carregar_cortes(pastas), corte])


def _criar_de_verdade(pastas: PastasProjeto, texto: Texto, trecho: Trecho, andamento) -> Corte:
    from spurgeon.audio.nivel import carregar_niveis
    from spurgeon.shorts.etapas import nome_do_sermao

    existentes = carregar_cortes(pastas)
    ia = contexto_da_ia(pastas)
    andamento("Abrindo o Ollama…")
    cliente = preparar_cliente(ia.modelo, threads=ia.threads, usar_gpu=ia.usar_gpu)
    niveis = None
    if pastas.niveis.exists():
        medido = carregar_niveis(pastas.niveis)
        niveis = (medido.medidas, medido.limiar_db)
    try:
        return gerar_corte_livre(
            cliente, palavras=texto.palavras, frases=texto.frases, trecho=trecho,
            id_=proximo_id(existentes), sermao=nome_do_sermao(pastas.raiz),
            titulos_usados=[c.titulo for c in existentes if c.titulo],
            nomes_usados=[c.slug for c in existentes if c.slug],
            niveis=niveis, ao_andamento=andamento,
        )  # fmt: skip
    finally:
        cliente.descarregar()  # a renderização precisa da memória que a IA ocupa


@dataclass
class Criacao:
    projeto: str
    texto: str = "Preparando…"
    rodando: bool = True
    corte_id: int | None = None
    titulo: str = ""
    erro: str = ""
    _pronto: threading.Event = field(default_factory=threading.Event, repr=False)

    def para_json(self) -> dict[str, Any]:
        return {"projeto": self.projeto, "texto": self.texto, "rodando": self.rodando,
                "corte_id": self.corte_id, "titulo": self.titulo, "erro": self.erro}  # fmt: skip


class Criacoes:
    """Uma criação de Short livre por vez (a IA usa a CPU inteira)."""

    def __init__(
        self,
        *,
        criar: Callable[[PastasProjeto, Texto, Trecho, Callable[[str], None]], Corte] = (
            _criar_de_verdade
        ),
        ao_terminar: Callable[[Criacao], None] = lambda _c: None,
        renovar_s: float = RENOVAR_A_TRAVA_S,
    ):
        self._criar = criar
        self._ao_terminar = ao_terminar
        self._renovar_s = renovar_s
        self._trava = threading.Lock()
        self.atual: Criacao | None = None

    def ocupada(self) -> bool:
        return self.atual is not None and self.atual.rodando

    def iniciar(self, pastas: PastasProjeto, texto: Texto, trecho: Trecho) -> Criacao:
        with self._trava:
            if self.ocupada():
                raise TrechoInvalido("O PC já está criando um Short livre. Espere terminar.")
            criacao = Criacao(pastas.raiz.name)
            self.atual = criacao
        threading.Thread(
            target=self._rodar, args=(criacao, pastas, texto, trecho), daemon=True
        ).start()
        return criacao

    def esperar(self, timeout: float | None = None) -> bool:
        """Para os testes: espera a criação atual terminar."""
        return self.atual is None or self.atual._pronto.wait(timeout)

    def _rodar(self, criacao: Criacao, pastas: PastasProjeto, texto: Texto, trecho: Trecho):
        parar_de_renovar = threading.Event()

        def renovar() -> None:  # a janela do PC não pode regravar a lista enquanto a IA trabalha
            while not parar_de_renovar.wait(self._renovar_s):
                with contextlib.suppress(OSError):
                    sessao.tomar(pastas)

        threading.Thread(target=renovar, daemon=True).start()
        try:
            corte = self._criar(pastas, texto, trecho, lambda t: setattr(criacao, "texto", t))
            sessao.tomar(pastas)  # a trava valendo na hora de gravar
            gravar_corte_novo(pastas, corte)
            criacao.corte_id, criacao.titulo = corte.id, corte.titulo
            criacao.texto = f"Short criado: “{corte.titulo}” (nota {corte.nota:.0f})."
            log.info("Short livre criado pelo celular: %s (%s).", corte.titulo, pastas.raiz.name)
        except Exception as erro:  # a IA fora do ar, o modelo que falta, o trecho que não serve…
            log.exception("Falha ao criar o Short livre.")
            criacao.erro = str(erro) or erro.__class__.__name__
            criacao.texto = "Não deu para criar o Short."
        finally:
            parar_de_renovar.set()
            criacao.rodando = False
            criacao._pronto.set()
            try:
                self._ao_terminar(criacao)
            except Exception:
                log.exception("Falha ao avisar do Short livre.")
