"""Medidas da máquina para o log: o que ajuda a explicar uma etapa que parece parada.

Quando um passo demora (carregar o modelo do Whisper, por exemplo), o log precisa dizer se o
processo está lendo do disco, usando o processador ou esperando memória. Nada aqui levanta
exceção: se a medida falha, o texto simplesmente fica sem ela.
"""

from __future__ import annotations

import psutil

from spurgeon.core.formatacao import formatar_numero


def memoria_livre_gb() -> float:
    return psutil.virtual_memory().available / 1e9


class LeituraDeDisco:
    """Quantos MB este processo já leu do disco desde que a medida começou."""

    def __init__(self) -> None:
        self._processo = psutil.Process()
        self._inicial = self._ler()

    def _ler(self) -> int:
        try:
            return self._processo.io_counters().read_bytes
        except (AttributeError, psutil.Error, OSError):
            return 0

    def lidos_mb(self) -> float:
        return max(0, self._ler() - self._inicial) / 1e6


def situacao_da_maquina() -> str:
    """`RAM livre 8,4 GB · processador 35%` (o consumo medido num instante curto)."""
    try:
        livre = formatar_numero(memoria_livre_gb())
        uso = psutil.cpu_percent(interval=0.2)
        return f"RAM livre {livre} GB · processador {uso:.0f}%"
    except (psutil.Error, OSError):
        return ""
