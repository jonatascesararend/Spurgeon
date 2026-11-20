"""A legenda de um Short no tempo do Short pronto, e o arquivo de texto para exportá-la.

É a mesma legenda que seria queimada no vídeo: as palavras do corte com a correção da pessoa
(`palavras_com_a_legenda`), sem os saltos e com as pausas encurtadas (`trechos_mantidos` +
`remapear_palavras`), agrupadas em frases como a tela mostra (`agrupar_em_frases`). Serve à audição
do celular e ao "Exportar a legenda" do celular e do Studio do PC.
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


def tempo_curto(segundos: float) -> str:
    """`1:05,3` (minutos, segundos e décimos)."""
    decimos = round(max(0.0, segundos) * 10)
    minutos, resto = divmod(decimos, 600)
    return f"{minutos}:{resto // 10:02d},{resto % 10}"


def tempo_longo(segundos: float) -> str:
    """`1:02:03` (o lugar no sermão)."""
    total = round(max(0.0, segundos))
    horas, resto = divmod(total, 3600)
    return (
        f"{horas}:{resto // 60:02d}:{resto % 60:02d}"
        if horas
        else f"{resto // 60}:{resto % 60:02d}"
    )


def texto_da_legenda(
    corte: Corte, legenda: LegendaNoTempo, *, sermao: str = "", numero: int | None = None
) -> str:
    """O arquivo de texto: um cabeçalho e uma frase por linha, com início e fim no Short pronto."""
    titulo = corte.titulo or corte.slug or f"corte {corte.id}"
    cabecalho = [f"Legenda do Short{f' {numero}' if numero else ''}: {titulo}"]
    if sermao:
        cabecalho.append(f"Sermão: {sermao}")
    cabecalho.append(
        f"Trecho do sermão: {tempo_longo(corte.inicio)} a {tempo_longo(corte.fim)}"
        f" · duração do Short: {tempo_curto(legenda.duracao)}"
    )
    cabecalho.append("Os tempos de cada frase contam do começo do Short pronto.")
    linhas = [
        f"{tempo_curto(frase[0].inicio)} – {tempo_curto(frase[-1].fim)}   "
        + " ".join(p.texto for p in frase)
        for frase in legenda.frases
        if frase
    ]
    return "\n".join([*cabecalho, "", *linhas]) + "\n"


def nome_do_arquivo(corte: Corte, numero: int | None = None) -> str:
    base = re.sub(r"[^\w-]+", "_", corte.slug or corte.titulo or str(corte.id)).strip("_")[:60]
    return (
        f"legenda_short_{numero:02d}_{base}.txt"
        if numero
        else f"legenda_corte_{corte.id}_{base}.txt"
    )
