"""Divisão do áudio em blocos de ~10 min, cortados em pausas.

Cada bloco concluído é gravado; se o processo cair, a transcrição retoma do próximo. Cortar
em silêncio evita partir uma palavra ao meio na fronteira entre dois blocos.
"""

from __future__ import annotations

from itertools import pairwise

ALVO_S = 600.0
FOLGA_S = 90.0  # quanto se procura antes e depois do ponto ideal
PAUSA_MINIMA_S = 0.3
Medida = tuple[float, float]  # (tempo, nível em dB)


def planejar_blocos(
    duracao_s: float,
    pausas: list[tuple[float, float]],
    alvo_s: float = ALVO_S,
    folga_s: float = FOLGA_S,
) -> list[tuple[float, float]]:
    """Intervalos consecutivos que cobrem [0, duracao_s], cada um com ~`alvo_s`.

    As fronteiras ficam no meio da maior pausa dentro da folga ao redor de cada ponto ideal;
    sem pausa por perto, no próprio ponto ideal. O último bloco nunca fica menor que metade
    do alvo.
    """
    if duracao_s <= alvo_s * 1.5:
        return [(0.0, duracao_s)]
    fronteiras: list[float] = []
    ideal = alvo_s
    while ideal < duracao_s - alvo_s / 2:
        candidatas = [
            (fim - ini, (ini + fim) / 2)
            for ini, fim in pausas
            if fim - ini >= PAUSA_MINIMA_S and abs((ini + fim) / 2 - ideal) <= folga_s
        ]
        # a maior pausa; em empate, a mais perto do ponto ideal
        fronteira = (
            max(candidatas, key=lambda c: (round(c[0], 1), -abs(c[1] - ideal)))[1]
            if candidatas
            else ideal
        )
        if not fronteiras or fronteira - fronteiras[-1] >= alvo_s / 2:
            fronteiras.append(fronteira)
        ideal += alvo_s
    pontos = [0.0, *fronteiras, duracao_s]
    return list(pairwise(pontos))
