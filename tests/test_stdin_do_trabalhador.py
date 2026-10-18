"""O trabalhador lê os comandos sem travar os `import` pesados (o "só anda se eu pausar")."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="o travamento é do Windows")

FILHO = """
import sys, threading, time
from spurgeon.core.trabalhador import linhas_do_stdin

def ouvir():
    for linha in linhas_do_stdin():
        print("cmd", linha.strip(), flush=True)
    print("fechou", flush=True)

threading.Thread(target=ouvir, daemon=True).start()
time.sleep(0.3)
inicio = time.perf_counter()
import spacy
print("importou", round(time.perf_counter() - inicio, 1), flush=True)
time.sleep(30)
"""


def _linhas_do_filho(processo, fila: list[str]) -> None:
    for linha in processo.stdout:
        fila.append(linha.strip())


def _esperar(fila: list[str], prefixo: str, segundos: float) -> str | None:
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        for linha in fila:
            if linha.startswith(prefixo):
                return linha
        time.sleep(0.05)
    return None


def test_um_import_pesado_nao_espera_por_um_comando_e_os_comandos_chegam():
    pytest.importorskip("spacy")
    processo = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", FILHO],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
    )  # fmt: skip
    fila: list[str] = []
    threading.Thread(target=_linhas_do_filho, args=(processo, fila), daemon=True).start()
    try:
        # sem mandar nenhum comando: com a leitura bloqueante o import levava minutos
        importou = _esperar(fila, "importou", 40)
        assert importou is not None, "o import travou esperando um comando no stdin"
        assert float(importou.split()[1]) < 30

        processo.stdin.write("pausar\n")
        processo.stdin.flush()
        assert _esperar(fila, "cmd pausar", 5) is not None
        processo.stdin.write("retomar\ncancelar\n")  # duas linhas de uma vez
        processo.stdin.flush()
        assert _esperar(fila, "cmd retomar", 5) is not None
        assert _esperar(fila, "cmd cancelar", 5) is not None

        processo.stdin.close()  # a interface caiu
        assert _esperar(fila, "fechou", 5) is not None
    finally:
        processo.kill()
        processo.wait()
