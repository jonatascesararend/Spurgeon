"""Tempo decorrido e tempo restante estimado de um processamento."""

from __future__ import annotations

import time
from collections.abc import Callable

_FRACAO_MINIMA = 0.02  # antes disso a estimativa oscila demais
_DECORRIDO_MINIMO_S = 5.0
_ALISAMENTO = 0.3  # peso da medida mais recente na média móvel


class EstimadorTempo:
    """Estima o que falta a partir da velocidade observada, sem contar o tempo em pausa."""

    def __init__(self, relogio: Callable[[], float] = time.monotonic):
        self._relogio = relogio
        self._inicio: float | None = None
        self._fracao_inicial = 0.0
        self._pausado_desde: float | None = None
        self._tempo_pausado = 0.0
        self._duracao_estimada: float | None = None

    def iniciar(self, fracao_inicial: float = 0.0) -> None:
        """`fracao_inicial` é o que já estava pronto (ao retomar um projeto)."""
        self._inicio = self._relogio()
        self._fracao_inicial = fracao_inicial
        self._pausado_desde = None
        self._tempo_pausado = 0.0
        self._duracao_estimada = None

    def pausar(self) -> None:
        if self._inicio is not None and self._pausado_desde is None:
            self._pausado_desde = self._relogio()

    def retomar(self) -> None:
        if self._pausado_desde is not None:
            self._tempo_pausado += self._relogio() - self._pausado_desde
            self._pausado_desde = None

    def decorrido(self) -> float:
        if self._inicio is None:
            return 0.0
        fim = self._pausado_desde if self._pausado_desde is not None else self._relogio()
        return max(0.0, fim - self._inicio - self._tempo_pausado)

    def atualizar(self, fracao_total: float) -> None:
        feito = fracao_total - self._fracao_inicial
        decorrido = self.decorrido()
        if feito < _FRACAO_MINIMA or decorrido < _DECORRIDO_MINIMO_S:
            return
        medida = decorrido / feito * (1.0 - self._fracao_inicial)
        if self._duracao_estimada is None:
            self._duracao_estimada = medida
        else:
            self._duracao_estimada += _ALISAMENTO * (medida - self._duracao_estimada)

    def restante(self, fracao_total: float) -> float | None:
        """Segundos que faltam, ou None enquanto ainda não dá para estimar."""
        if self._duracao_estimada is None:
            return None
        restante_fracao = max(0.0, 1.0 - fracao_total)
        base = 1.0 - self._fracao_inicial
        return self._duracao_estimada * restante_fracao / base if base > 0 else 0.0


def formatar_duracao(segundos: float) -> str:
    """1 h 05 min, 12 min 05 s, 45 s."""
    total = round(segundos)
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas} h {minutos:02d} min"
    if minutos:
        return f"{minutos} min {seg:02d} s"
    return f"{seg} s"


def formatar_restante(segundos: float | None) -> str:
    if segundos is None:
        return "calculando…"
    if segundos < 60:
        return "menos de 1 min"
    if segundos < 3600:
        return f"cerca de {round(segundos / 60)} min"
    horas, resto = divmod(int(segundos), 3600)
    return f"cerca de {horas} h {resto // 60:02d} min"
