"""Mantém o Windows acordado e sem bloquear durante o processamento (renderizações levam horas).

Nada do que o Windows guarda é alterado: o programa só pede ao sistema, enquanto roda, para
não suspender (`SetThreadExecutionState`) e, se pedido, para não apagar a tela nem bloquear.
Bloqueio e apagar a tela são disparados pelo tempo sem uso do teclado e do mouse; por isso,
nesse modo, um evento de mouse sem movimento é enviado a cada poucos segundos. Quando o
programa termina, tudo volta ao normal sozinho.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
_MOUSEEVENTF_MOVE = 0x0001
INTERVALO_PADRAO_S = 45.0


def _definir(flags: int) -> int:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    return kernel32.SetThreadExecutionState(flags)


def _registrar_atividade() -> None:
    """Zera o contador de ociosidade do Windows (sem mover o ponteiro do mouse)."""
    ctypes.windll.user32.mouse_event(_MOUSEEVENTF_MOVE, 0, 0, 0, 0)


@contextmanager
def impedir_suspensao(
    *, sem_bloqueio: bool = False, intervalo_s: float = INTERVALO_PADRAO_S
) -> Iterator[None]:
    """Mantém o sistema acordado enquanto o bloco roda.

    Com `sem_bloqueio=True` a tela também não apaga e o PC não bloqueia por inatividade.
    O pedido de energia vale para a thread que chama e termina com ela: use na thread que
    trabalha.
    """
    if sys.platform != "win32":
        yield
        return
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if sem_bloqueio:
        flags |= ES_DISPLAY_REQUIRED
    _definir(flags)

    parar = threading.Event()
    atividade: threading.Thread | None = None
    if sem_bloqueio:

        def manter_ativo() -> None:
            while not parar.wait(intervalo_s):
                _registrar_atividade()

        _registrar_atividade()
        atividade = threading.Thread(target=manter_ativo, daemon=True, name="manter-acordado")
        atividade.start()
    try:
        yield
    finally:
        parar.set()
        if atividade is not None:
            atividade.join(timeout=2)
        _definir(ES_CONTINUOUS)
