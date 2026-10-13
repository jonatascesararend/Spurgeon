import subprocess
import sys
import threading
import time

import psutil
import pytest

from spurgeon.core.erros import Cancelado
from spurgeon.core.processos import vigiar_processo

CODIGO_OCUPADO = "import time\nt = time.time()\nwhile time.time() - t < 60:\n    pass"


def iniciar(codigo: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", codigo],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def esperar(condicao, limite_s=5.0):
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if condicao():
            return
        time.sleep(0.02)
    pytest.fail("Tempo esgotado esperando a condição")


def cpu_usada(processo: subprocess.Popen) -> float:
    """CPU do processo e dos filhos: o python do venv é um lançador que cria o python de verdade."""
    raiz = psutil.Process(processo.pid)
    total = 0.0
    for p in [raiz, *raiz.children(recursive=True)]:
        tempos = p.cpu_times()
        total += tempos.user + tempos.system
    return total


def test_devolve_o_codigo_de_saida():
    assert vigiar_processo(iniciar("raise SystemExit(3)")) == 3


def test_ao_tick_roda_enquanto_o_processo_trabalha():
    ticks = []
    processo = iniciar("import time; time.sleep(1)")
    vigiar_processo(processo, ao_tick=lambda: ticks.append(1), intervalo_s=0.1)
    assert len(ticks) >= 3


def test_cancelar_encerra_o_processo():
    processo = iniciar(CODIGO_OCUPADO)
    cancelar = threading.Event()
    threading.Timer(0.5, cancelar.set).start()
    with pytest.raises(Cancelado):
        vigiar_processo(processo, cancelar=cancelar)
    assert processo.poll() is not None


def test_pausar_suspende_de_verdade_e_retomar_continua():
    processo = iniciar(CODIGO_OCUPADO)
    pausar, cancelar = threading.Event(), threading.Event()
    avisos: list[bool] = []
    resultado: dict = {}

    def vigiar():
        try:
            vigiar_processo(
                processo, cancelar=cancelar, pausar=pausar, ao_pausa=avisos.append, intervalo_s=0.05
            )
        except Cancelado:
            resultado["cancelado"] = True

    fio = threading.Thread(target=vigiar)
    fio.start()
    try:
        time.sleep(0.5)
        pausar.set()
        esperar(lambda: avisos == [True])
        antes = cpu_usada(processo)
        time.sleep(0.6)
        assert cpu_usada(processo) == pytest.approx(antes, abs=0.05)  # parou de consumir CPU
        assert psutil.Process(processo.pid).status() == psutil.STATUS_STOPPED

        pausar.clear()
        esperar(lambda: avisos == [True, False])
        time.sleep(0.6)
        assert cpu_usada(processo) > antes + 0.2  # voltou a trabalhar
    finally:
        cancelar.set()
        fio.join(timeout=10)
        if processo.poll() is None:
            processo.kill()
    assert resultado == {"cancelado": True}


def test_cancelar_com_o_processo_pausado_tambem_encerra():
    processo = iniciar(CODIGO_OCUPADO)
    pausar, cancelar = threading.Event(), threading.Event()
    pausar.set()
    threading.Timer(0.6, cancelar.set).start()
    with pytest.raises(Cancelado):
        vigiar_processo(processo, cancelar=cancelar, pausar=pausar, intervalo_s=0.05)
    assert processo.poll() is not None
