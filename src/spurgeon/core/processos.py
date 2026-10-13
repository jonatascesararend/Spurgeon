"""Vigia de subprocessos longos: atende cancelamento e pausa de verdade.

Extração, limpeza de áudio e renderização são processos únicos que duram de minutos a horas.
Pausar de verdade significa suspender o processo (e os filhos), liberando a CPU na hora.
"""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Callable

import psutil

from spurgeon.core.erros import Cancelado
from spurgeon.core.sinais import SinalControle


def _arvore(pid: int) -> list[psutil.Process]:
    try:
        raiz = psutil.Process(pid)
        return [raiz, *raiz.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def suspender(processo: subprocess.Popen) -> None:
    for p in _arvore(processo.pid):
        with contextlib.suppress(psutil.NoSuchProcess):
            p.suspend()


def retomar(processo: subprocess.Popen) -> None:
    for p in _arvore(processo.pid):
        with contextlib.suppress(psutil.NoSuchProcess):
            p.resume()


def encerrar(processo: subprocess.Popen, espera_s: float = 10.0) -> None:
    if processo.poll() is None:
        processo.terminate()
        try:
            processo.wait(timeout=espera_s)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait()


def vigiar_processo(
    processo: subprocess.Popen,
    *,
    cancelar: SinalControle | None = None,
    pausar: SinalControle | None = None,
    ao_pausa: Callable[[bool], None] | None = None,
    ao_tick: Callable[[], None] | None = None,
    intervalo_s: float = 0.2,
) -> int:
    """Espera o processo terminar. Cancelar o encerra (`Cancelado`); pausar o suspende.

    `ao_tick` roda a cada intervalo (fora da pausa), na thread de quem chamou: é por onde
    o progresso lido por outra thread chega com segurança ao restante do programa.
    """
    suspenso = False
    try:
        while True:
            try:
                return processo.wait(timeout=intervalo_s)
            except subprocess.TimeoutExpired:
                pass
            if cancelar is not None and cancelar.is_set():
                raise Cancelado()
            quer_pausa = pausar is not None and pausar.is_set()
            if quer_pausa and not suspenso:
                suspender(processo)
                suspenso = True
                if ao_pausa is not None:
                    ao_pausa(True)
            elif suspenso and not quer_pausa:
                retomar(processo)
                suspenso = False
                if ao_pausa is not None:
                    ao_pausa(False)
            if ao_tick is not None and not suspenso:
                ao_tick()
    except BaseException:
        encerrar(processo)
        raise
