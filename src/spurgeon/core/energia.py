"""Impede que o Windows suspenda o PC durante o processamento (renderizações levam horas)."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _definir(flags: int) -> int:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    return kernel32.SetThreadExecutionState(flags)


@contextmanager
def impedir_suspensao() -> Iterator[None]:
    """Mantém o sistema acordado enquanto o bloco roda (a tela pode apagar normalmente).

    O estado vale para a thread que chama e termina com ela, então use na thread que trabalha.
    """
    if sys.platform != "win32":
        yield
        return
    _definir(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _definir(ES_CONTINUOUS)
