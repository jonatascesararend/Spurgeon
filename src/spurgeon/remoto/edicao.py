"""O que o celular muda num corte (Fase R2): legenda, início e fim, marcado ou não.

As mesmas regras do Studio Shorts (`shorts/edicao.py`): as pontas caem em fronteira de palavra com o
respiro de costume, menos de 10 s não vale, a legenda corrigida acompanha as palavras que ficam
(`legenda_preservada`). Cada mudança é gravada na hora, só nos campos daquele corte
(`gravar_campos_do_corte`), como o "já postado" do Studio. Quem chama confere antes que o celular
tem a trava do projeto (`remoto/sessao.py`).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from spurgeon.core.projeto import PastasProjeto
from spurgeon.remoto.dados import carregar_cortes
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import (
    DURACAO_MINIMA_S,
    LimiteInvalido,
    corte_com_indices,
    indices_incluidos,
    legenda_corrigida,
    legenda_em_blocos,
    legenda_preservada,
    mover_limite,
    passa_do_limite,
)
from spurgeon.shorts.etapas import gravar_campos_do_corte
from spurgeon.transcricao.formato import Palavra, Transcricao

#: palavras de fora mostradas antes do início e depois do fim, para escolher a ponta nova
PALAVRAS_DE_CONTEXTO = 40
_CAMPOS_DO_CORTE = ("inicio", "fim", "texto", "saltos", "editado", "legenda")


class CorteNaoEncontrado(LookupError):
    pass


def _carregar(pastas: PastasProjeto, id_do_corte: int) -> tuple[Corte, list[Palavra]]:
    corte = next((c for c in carregar_cortes(pastas) if c.id == id_do_corte), None)
    if corte is None:
        raise CorteNaoEncontrado(id_do_corte)
    return corte, Transcricao.carregar(pastas.transcricao).palavras


def _gravar(pastas: PastasProjeto, corte: Corte, *campos: str) -> None:
    if not gravar_campos_do_corte(
        pastas.cortes_shorts, corte.id, **{c: getattr(corte, c) for c in campos}
    ):
        raise CorteNaoEncontrado(corte.id)


def para_editar(pastas: PastasProjeto, id_do_corte: int) -> dict[str, Any]:
    """A legenda como o vídeo a mostra (blocos de até 2 linhas) e as palavras em volta do corte."""
    corte, palavras = _carregar(pastas, id_do_corte)
    dentro = indices_incluidos(palavras, corte)
    contexto: list[dict[str, Any]] = []
    if dentro:
        de = max(0, dentro[0] - PALAVRAS_DE_CONTEXTO)
        ate = min(len(palavras), dentro[-1] + PALAVRAS_DE_CONTEXTO + 1)
        incluidos = set(dentro)
        contexto = [
            {
                "n": i,
                "t": palavras[i].texto,
                "i": round(palavras[i].inicio, 2),
                "dentro": i in incluidos,
            }
            for i in range(de, ate)
        ]
    return {
        "legenda": legenda_em_blocos(palavras, corte) if dentro else corte.legenda or corte.texto,
        "corrigida": bool(corte.legenda),
        "contexto": contexto,
        "primeira": dentro[0] if dentro else None,
        "ultima": dentro[-1] if dentro else None,
        "duracao": round(corte.duracao, 1),
        "minimo_s": DURACAO_MINIMA_S,
        "acima_do_limite": passa_do_limite(corte),
    }


def corrigir_legenda(pastas: PastasProjeto, id_do_corte: int, texto: str) -> Corte:
    """Grava a legenda que a pessoa escreveu (vazia = volta ao texto da transcrição)."""
    corte, palavras = _carregar(pastas, id_do_corte)
    corte.legenda = legenda_corrigida(palavras, corte, texto) if texto.strip() else ""
    _gravar(pastas, corte, "legenda")
    return corte


def corrigir_frase(
    pastas: PastasProjeto, id_do_corte: int, indice: int, antes: str, texto: str
) -> Corte:
    """Troca só a frase `indice` da legenda (as frases como a audição as mostra), sem mexer nas
    outras. `antes` é o texto que o celular via nela: se a legenda mudou desde então, recusa.

    As frases saem do mesmo cálculo da audição (`legenda_do_short` → `agrupar_em_frases`), na
    ordem das palavras da legenda (`palavras_com_a_legenda`, as visíveis do corte); a frase vira o
    trecho dessas palavras, e o texto inteiro passa por `legenda_corrigida` como no Studio.
    """
    from spurgeon.remoto.audicao import legenda_do_short
    from spurgeon.render.legenda import limpar_texto
    from spurgeon.shorts.edicao import palavras_com_a_legenda

    corte, palavras = _carregar(pastas, id_do_corte)
    base = palavras_com_a_legenda(palavras, corte)
    visiveis = [
        k for k, p in enumerate(base)
        if p.fim > corte.inicio and p.inicio < corte.fim and limpar_texto(p.texto)
    ]  # fmt: skip
    frases = legenda_do_short(palavras, corte).frases
    if sum(len(f) for f in frases) != len(visiveis) or not 0 <= indice < len(frases):
        raise LimiteInvalido("A legenda mudou desde que a tela abriu: abra o Short de novo.")
    comeco = sum(len(f) for f in frases[:indice])
    alvo = visiveis[comeco : comeco + len(frases[indice])]
    vista = " ".join(p.texto for p in frases[indice])
    if " ".join(antes.split()) != " ".join(vista.split()):
        raise LimiteInvalido("A legenda mudou desde que a tela abriu: abra o Short de novo.")
    textos = [p.texto for p in base]
    novos = [*textos[: alvo[0]], *texto.split(), *textos[alvo[-1] + 1 :]]
    corte.legenda = legenda_corrigida(palavras, corte, " ".join(novos))
    _gravar(pastas, corte, "legenda")
    return corte


def _aplicar(pastas: PastasProjeto, antes: Corte, palavras: list[Palavra], novo: Corte) -> Corte:
    if novo.duracao < DURACAO_MINIMA_S:
        raise LimiteInvalido(f"O corte ficaria com menos de {DURACAO_MINIMA_S:g} segundos.")
    novo = replace(novo, legenda=legenda_preservada(palavras, antes, novo))
    _gravar(pastas, novo, *_CAMPOS_DO_CORTE)
    return novo


def mudar_pontas(
    pastas: PastasProjeto, id_do_corte: int, *, primeira: int | None = None,
    ultima: int | None = None,
) -> Corte:  # fmt: skip
    """O corte começando na palavra `primeira` e/ou terminando na `ultima` (posições na
    transcrição). Os saltos de dentro continuam; o que entra nas pontas entra inteiro."""
    corte, palavras = _carregar(pastas, id_do_corte)
    dentro = indices_incluidos(palavras, corte)
    if not dentro:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    de = dentro[0] if primeira is None else primeira
    ate = dentro[-1] if ultima is None else ultima
    if not (0 <= de < len(palavras) and 0 <= ate < len(palavras)):
        raise LimiteInvalido("Essa palavra não está na transcrição.")
    if de > ate:
        raise LimiteInvalido("O início ficaria depois do fim.")
    novas = [
        *range(de, min(dentro[0], ate + 1)),
        *(i for i in dentro if de <= i <= ate),
        *range(max(dentro[-1] + 1, de), ate + 1),
    ]
    return _aplicar(pastas, corte, palavras, corte_com_indices(palavras, corte, novas))


def mover_ponta(pastas: PastasProjeto, id_do_corte: int, ponta: str, passo: int) -> Corte:
    """Uma palavra (ou `passo` palavras) a mais ou a menos numa ponta, como as setas do Studio."""
    if ponta not in ("inicio", "fim"):
        raise LimiteInvalido("A ponta é o início ou o fim.")
    corte, palavras = _carregar(pastas, id_do_corte)
    return _aplicar(pastas, corte, palavras, mover_limite(palavras, corte, ponta, passo))


def marcar(pastas: PastasProjeto, id_do_corte: int, incluir: bool) -> Corte:
    corte, _ = _carregar(pastas, id_do_corte)
    corte.incluir = incluir
    _gravar(pastas, corte, "incluir")
    return corte
