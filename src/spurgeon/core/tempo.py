"""Mapa de tempo: liga a linha do tempo original à do vídeo sem as pausas longas.

A transcrição é feita uma vez, na linha do tempo ORIGINAL. Depois de decidir quais
trechos ficam, este mapa converte os tempos (legendas .srt, palavras) para o vídeo editado.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Trecho:
    """Intervalo [inicio, fim] em segundos, na linha do tempo original."""

    inicio: float
    fim: float

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


def calcular_trechos_mantidos(
    duracao_total: float,
    pausas: Iterable[tuple[float, float]],
    limiar_s: float,
    residual_s: float,
) -> list[Trecho]:
    """Trechos que ficam depois de cortar as pausas de `limiar_s` ou mais.

    De cada pausa cortada sobra `residual_s` (metade em cada ponta), para o corte não
    soar seco.
    """
    cortes: list[tuple[float, float]] = []
    for ini, fim in sorted(pausas):
        ini, fim = max(0.0, ini), min(duracao_total, fim)
        if fim - ini < limiar_s:
            continue
        corte_ini, corte_fim = ini + residual_s / 2, fim - residual_s / 2
        if corte_fim - corte_ini <= 0:
            continue
        if cortes and corte_ini <= cortes[-1][1]:
            cortes[-1] = (cortes[-1][0], max(cortes[-1][1], corte_fim))
        else:
            cortes.append((corte_ini, corte_fim))

    mantidos: list[Trecho] = []
    cursor = 0.0
    for corte_ini, corte_fim in cortes:
        if corte_ini > cursor:
            mantidos.append(Trecho(cursor, corte_ini))
        cursor = corte_fim
    if cursor < duracao_total:
        mantidos.append(Trecho(cursor, duracao_total))
    return mantidos


class MapaTempo:
    """Converte tempos entre o vídeo original e o editado.

    `deslocamento` soma um tempo fixo ao vídeo editado (duração da introdução, se houver).
    """

    def __init__(self, trechos: Sequence[Trecho], deslocamento: float = 0.0):
        anterior_fim = 0.0
        for t in trechos:
            if t.fim <= t.inicio or t.inicio < anterior_fim:
                raise ValueError("Os trechos devem ser positivos, ordenados e sem sobreposição.")
            anterior_fim = t.fim
        self.trechos = list(trechos)
        self.deslocamento = deslocamento
        self._inicios = [t.inicio for t in self.trechos]
        self._acumulado: list[float] = []
        soma = 0.0
        for t in self.trechos:
            self._acumulado.append(soma)
            soma += t.duracao
        self.duracao_editada = soma

    def original_para_editado(self, t: float) -> float:
        """Tempo original → editado. Tempo dentro de uma pausa cortada cai no ponto do corte."""
        if not self.trechos:
            return self.deslocamento
        i = bisect_right(self._inicios, t) - 1
        if i < 0:
            return self.deslocamento
        trecho = self.trechos[i]
        dentro = min(t, trecho.fim) - trecho.inicio
        return self.deslocamento + self._acumulado[i] + dentro

    def editado_para_original(self, t: float) -> float:
        if not self.trechos:
            return 0.0
        t = min(max(t - self.deslocamento, 0.0), self.duracao_editada)
        i = max(bisect_right(self._acumulado, t) - 1, 0)
        trecho = self.trechos[i]
        return min(trecho.inicio + (t - self._acumulado[i]), trecho.fim)

    def mantido(self, t: float) -> bool:
        i = bisect_right(self._inicios, t) - 1
        return i >= 0 and t <= self.trechos[i].fim

    def remapear_intervalo(self, inicio: float, fim: float) -> list[tuple[float, float]]:
        """Intervalo original → pedaços no vídeo editado (mais de um se atravessa um corte)."""
        pedacos = []
        for trecho in self.trechos:
            ini, fi = max(inicio, trecho.inicio), min(fim, trecho.fim)
            if fi > ini:
                pedacos.append((self.original_para_editado(ini), self.original_para_editado(fi)))
        return pedacos
