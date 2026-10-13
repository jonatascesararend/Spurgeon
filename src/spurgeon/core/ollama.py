"""O servidor do Ollama: ver se responde e, se estiver fechado, abri-lo em segundo plano."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

URL_OLLAMA = "http://127.0.0.1:11434"
_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FORA_DO_PROCESSO = getattr(subprocess, "DETACHED_PROCESS", 0)


def servidor_ativo(url: str = URL_OLLAMA) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=1.5):
            return True
    except OSError:
        return False


def localizar_ollama() -> Path | None:
    """O executável do Ollama: no PATH ou na pasta onde o instalador do Windows o coloca."""
    achado = shutil.which("ollama")
    if achado:
        return Path(achado)
    padrao = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return padrao if padrao.exists() else None


def garantir_servidor(
    *,
    esperar_s: float = 40.0,
    ativo: Callable[[], bool] = servidor_ativo,
    abrir: Callable[[Path], None] | None = None,
) -> bool:
    """True se o servidor está respondendo, abrindo-o (`ollama serve`) se for preciso.

    Depois de uma reinicialização, ou se a pessoa fechou o ícone da bandeja, o servidor não está
    de pé; numa execução de madrugada isso não pode parar o processamento.
    """
    if ativo():
        return True
    executavel = localizar_ollama()
    if executavel is None:
        return False
    (abrir or _abrir_servidor)(executavel)
    limite = time.monotonic() + esperar_s
    while time.monotonic() < limite:
        if ativo():
            return True
        time.sleep(0.5)
    return False


def _abrir_servidor(executavel: Path) -> None:
    subprocess.Popen(
        [str(executavel), "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_SEM_JANELA | _FORA_DO_PROCESSO,
    )
