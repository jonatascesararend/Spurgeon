"""Abrir o servidor com o Windows (Remoto.md, R7): um atalho na pasta "Inicializar" do usuário.

O atalho chama o `pythonw.exe` do `.venv` (sem janela de console), como o atalho do Studio.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from spurgeon.core.config import raiz_repositorio

NOME_DO_ATALHO = "Studio Spurgeon - controle remoto.lnk"
_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pasta_de_inicializacao() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def caminho_do_atalho() -> Path:
    return pasta_de_inicializacao() / NOME_DO_ATALHO


def python_sem_console() -> Path:
    exe = Path(sys.executable)
    sem = exe.with_name("pythonw.exe")
    return sem if sem.is_file() else exe


def _aspas_ps(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def script_do_atalho(atalho: Path, alvo: Path, argumentos: str, pasta: Path) -> str:
    return "; ".join(
        [
            "$s = New-Object -ComObject WScript.Shell",
            f"$a = $s.CreateShortcut({_aspas_ps(str(atalho))})",
            f"$a.TargetPath = {_aspas_ps(str(alvo))}",
            f"$a.Arguments = {_aspas_ps(argumentos)}",
            f"$a.WorkingDirectory = {_aspas_ps(str(pasta))}",
            "$a.WindowStyle = 7",
            "$a.Description = 'Servidor do controle remoto do Studio Spurgeon'",
            "$a.Save()",
        ]
    )


def ligar() -> Path:
    atalho = caminho_do_atalho()
    atalho.parent.mkdir(parents=True, exist_ok=True)
    script = script_do_atalho(
        atalho, python_sem_console(), "-m spurgeon remoto iniciar", raiz_repositorio()
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, timeout=60, creationflags=_SEM_JANELA,
    )  # fmt: skip
    return atalho


def desligar() -> bool:
    atalho = caminho_do_atalho()
    if atalho.exists():
        atalho.unlink()
        return True
    return False


def ligada() -> bool:
    return caminho_do_atalho().exists()
