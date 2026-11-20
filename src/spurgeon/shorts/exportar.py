"""A legenda no tempo do Short pronto (a audição do celular) e a legenda do sermão inteiro num
arquivo de texto (o "Exportar a legenda do sermão" do celular e do Studio do PC).

A do Short é a mesma que seria queimada no vídeo: as palavras do corte com a correção da pessoa
(`palavras_com_a_legenda`), sem os saltos e com as pausas encurtadas (`trechos_mantidos` +
`remapear_palavras`), agrupadas em frases como a tela mostra (`agrupar_em_frases`). A do sermão é a
transcrição inteira, uma frase por linha, com o tempo no vídeo original.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.render.legenda import PalavraDaLegenda, agrupar_em_frases, palavras_do_short
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import palavras_com_a_legenda
from spurgeon.shorts.pausas import ha_o_que_cortar, remapear_palavras, trechos_mantidos
from spurgeon.transcricao.formato import Palavra


@dataclass(frozen=True)
class LegendaNoTempo:
    duracao: float
    trechos: list[Trecho]  # em segundos desde o início do corte, no tempo original
    frases: list[list[PalavraDaLegenda]]  # tempos desde o começo do Short pronto


def legenda_no_tempo_do_short(palavras: Sequence[Palavra], corte: Corte) -> LegendaNoTempo:
    """A legenda que o Short teria, no tempo do Short pronto, e os trechos que o som leva."""
    faladas = palavras_do_short(palavras_com_a_legenda(palavras, corte), corte.inicio, corte.fim)
    mantidos = trechos_mantidos(palavras, corte)
    duracao = corte.janela_s
    if ha_o_que_cortar(mantidos, corte.janela_s):
        mapa = MapaTempo(mantidos)
        faladas = remapear_palavras(faladas, mapa)
        duracao = mapa.duracao_editada
    return LegendaNoTempo(duracao, mantidos, agrupar_em_frases(faladas))


def tempo_longo(segundos: float) -> str:
    """`12:05` ou `1:02:03` (o lugar no vídeo do sermão)."""
    total = round(max(0.0, segundos))
    horas, resto = divmod(total, 3600)
    return (
        f"{horas}:{resto // 60:02d}:{resto % 60:02d}"
        if horas
        else f"{resto // 60}:{resto % 60:02d}"
    )


def texto_da_legenda_do_sermao(
    palavras: Sequence[Palavra], *, sermao: str = "", duracao_s: float | None = None
) -> str:
    """A legenda do sermão inteiro num arquivo de texto: uma frase por linha, com o tempo em que
    ela começa no vídeo original (o mesmo vídeo do YouTube). É a transcrição com as revisões que a
    pessoa já fez; as frases são as do Studio (`separar_frases`: pontuação e pausas)."""
    from spurgeon.shorts.analise import separar_frases

    frases = separar_frases(list(palavras)) if palavras else []
    cabecalho = [f"Legenda do sermão: {sermao}" if sermao else "Legenda do sermão"]
    if duracao_s:
        cabecalho.append(f"Duração: {tempo_longo(duracao_s)}")
    cabecalho.append("Os tempos são do vídeo original do sermão (o mesmo do YouTube).")
    linhas = [f"{tempo_longo(f.inicio)}  {' '.join(f.texto.split())}" for f in frases]
    return "\n".join([*cabecalho, "", *linhas]) + "\n"


def nome_do_arquivo_do_sermao(sermao: str) -> str:
    base = re.sub(r"[^\w-]+", "_", sermao).strip("_")[:80] or "sermao"
    return f"legenda_{base}.txt"
