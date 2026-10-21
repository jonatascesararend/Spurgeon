"""O que fica do corte de um Short: os saltos (pedaços cortados) e as pausas encurtadas.

O Short é o trecho [`corte.inicio`, `corte.fim`] do original, menos os `corte.saltos` (pedaços
que a pessoa tirou do meio, uma palavra ou uma frase, para continuar logo depois) e, se
`corte.pausa_s` é maior que zero, com as pausas ENTRE palavras (ambas dentro do Short) de
`pausa_s` ou mais encurtadas.
O respiro do começo e o do fim do corte (antes da primeira palavra e depois da última) nunca é
cortado. Tudo vale no vídeo, no som e na legenda.

Usa as mesmas peças do vídeo longo: `calcular_trechos_mantidos` decide quais pausas saem e
`MapaTempo` leva os tempos da legenda para o vídeo novo. Todos os tempos daqui são em segundos
DESDE O COMEÇO DO CORTE (o `-ss` da leitura), não do sermão.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from spurgeon.core.tempo import MapaTempo, Trecho, calcular_trechos_mantidos
from spurgeon.render.legenda import PalavraDaLegenda
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import palavras_do_corte
from spurgeon.transcricao.formato import Palavra

LIMIAR_MINIMO_S = 0.1
LIMIAR_MAXIMO_S = 10.0
LIMIAR_PADRAO_S = 1.0
#: de cada pausa cortada sobra isto (metade em cada ponta), para o corte não soar seco
RESIDUAL_DA_PAUSA_S = 0.4
#: folga para o ruído de ponto flutuante: uma pausa de 1,000 s vale como 1,0 s num limiar de 1,0 s
_FOLGA_DO_LIMIAR_S = 1e-6
_FOLGA_DO_SALTO_S = 0.02


def residual_da_pausa(limiar_s: float) -> float:
    """O que sobra de cada pausa cortada: 0,4 s, ou metade do limiar se ele for menor que 0,8 s."""
    return min(RESIDUAL_DA_PAUSA_S, limiar_s / 2)


def limiar_valido(limiar_s: float) -> float:
    """O limiar dentro dos limites (de 0,1 a 10 s), com uma casa decimal."""
    return round(min(max(limiar_s, LIMIAR_MINIMO_S), LIMIAR_MAXIMO_S), 2)


def saltos_do_corte(corte: Corte) -> list[tuple[float, float]]:
    """Os saltos em segundos desde o começo do corte, limitados a ele e em ordem."""
    saltos = []
    for de, ate in sorted(corte.saltos):
        de, ate = max(0.0, de - corte.inicio), min(corte.janela_s, ate - corte.inicio)
        if ate - de > _FOLGA_DO_SALTO_S / 10:
            saltos.append((de, ate))
    return saltos


def trechos_sem_saltos(corte: Corte) -> list[Trecho]:
    """O que sobra do corte depois dos saltos, cobrindo [0, janela]; um só se não há saltos."""
    trechos: list[Trecho] = []
    cursor = 0.0
    for de, ate in saltos_do_corte(corte):
        if de > cursor:
            trechos.append(Trecho(cursor, de))
        cursor = max(cursor, ate)
    if cursor < corte.janela_s:
        trechos.append(Trecho(cursor, corte.janela_s))
    return trechos or [Trecho(0.0, corte.janela_s)]


def pausas_do_corte(palavras: Sequence[Palavra], corte: Corte) -> list[tuple[float, float]]:
    """Os intervalos (início, fim) entre uma palavra e a seguinte, ambas dentro do Short.

    `palavras` é a transcrição (inteira ou só do corte). Os tempos contam desde `corte.inicio` e
    ficam dentro de [0, janela]; um vão negativo (palavras que se tocam) não é pausa, e o vão que
    um salto atravessa também não (esse já foi cortado inteiro).
    """
    dentro = sorted(
        (p for p in palavras_do_corte(palavras, corte) if p.texto.strip()),
        key=lambda p: (p.inicio, p.fim),
    )
    saltos = [(de + corte.inicio, ate + corte.inicio) for de, ate in saltos_do_corte(corte)]
    pausas = []
    for anterior, seguinte in pairwise(dentro):
        if any(anterior.fim - _FOLGA_DO_SALTO_S <= de and ate <= seguinte.inicio + _FOLGA_DO_SALTO_S
               for de, ate in saltos):  # fmt: skip
            continue
        inicio = max(0.0, anterior.fim - corte.inicio)
        fim = min(corte.janela_s, seguinte.inicio - corte.inicio)
        if fim > inicio:
            pausas.append((round(inicio, 4), round(fim, 4)))
    return pausas


def _cruzar(a: Sequence[Trecho], b: Sequence[Trecho]) -> list[Trecho]:
    """O que está nas duas listas de trechos (ordenadas e sem sobreposição)."""
    comum: list[Trecho] = []
    i = j = 0
    while i < len(a) and j < len(b):
        inicio, fim = max(a[i].inicio, b[j].inicio), min(a[i].fim, b[j].fim)
        if fim - inicio > 1e-6:
            comum.append(Trecho(inicio, fim))
        if a[i].fim < b[j].fim:
            i += 1
        else:
            j += 1
    return comum


def trechos_mantidos(
    palavras: Sequence[Palavra], corte: Corte, limiar_s: float | None = None
) -> list[Trecho]:
    """Os trechos do corte que ficam no Short, cobrindo no máximo [0, janela]; um só se nada sai.

    Os saltos do corte sempre saem. `limiar_s` None usa `corte.pausa_s` (0 = não corta pausa).
    """
    if limiar_s is None:
        limiar_s = corte.pausa_s or None
    sem_saltos = trechos_sem_saltos(corte)
    if limiar_s is None:
        return sem_saltos
    sem_pausas = calcular_trechos_mantidos(
        corte.janela_s,
        pausas_do_corte(palavras, corte),
        limiar_s - _FOLGA_DO_LIMIAR_S,
        residual_da_pausa(limiar_s),
    )
    return _cruzar(sem_saltos, sem_pausas) or sem_saltos


def ha_o_que_cortar(trechos: Sequence[Trecho], janela_s: float | None = None) -> bool:
    """Alguma coisa sai? (Sem corte, os trechos são um só: o corte inteiro.)"""
    if len(trechos) != 1:
        return len(trechos) > 1
    return janela_s is not None and (trechos[0].inicio > 1e-6 or trechos[0].fim < janela_s - 1e-6)


def mapa_do_short(
    palavras: Sequence[Palavra], corte: Corte, limiar_s: float | None = None
) -> MapaTempo:
    """O `MapaTempo` entre o corte (tempo original) e o Short já sem os saltos e as pausas."""
    return MapaTempo(trechos_mantidos(palavras, corte, limiar_s))


def duracao_do_short(
    palavras: Sequence[Palavra], corte: Corte, limiar_s: float | None = None
) -> float:
    """Quanto o Short dura com os saltos tirados e as pausas encurtadas (as de `corte.pausa_s`)."""
    trechos = trechos_mantidos(palavras, corte, limiar_s)
    return sum(t.duracao for t in trechos)


def remapear_palavras(
    palavras: Sequence[PalavraDaLegenda], mapa: MapaTempo
) -> list[PalavraDaLegenda]:
    """As palavras da legenda (tempos desde o começo do Short) na linha do tempo sem os cortes.

    Início e fim de cada palavra passam por `original_para_editado`. Sem corte nenhum, devolve as
    mesmas palavras, sem mexer em nada.
    """
    if len(mapa.trechos) <= 1 and (not mapa.trechos or mapa.trechos[0].inicio <= 1e-6):
        return list(palavras)
    return [
        PalavraDaLegenda(
            p.texto, mapa.original_para_editado(p.inicio), mapa.original_para_editado(p.fim)
        )
        for p in palavras
    ]
