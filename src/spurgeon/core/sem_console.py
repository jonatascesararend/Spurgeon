"""O aplicativo aberto pelo atalho (`pythonw.exe`) não tem console: sem `stdout` nem `stderr`.

Sem isto, um erro de importação, uma exceção de um botão ou uma queda do Tk sumiriam sem deixar
rastro (e qualquer biblioteca que escreve direto em `sys.stderr` levantaria `AttributeError`).
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
from datetime import datetime
from pathlib import Path

import platformdirs

from spurgeon.core.config import NOME_APP

NOME_DO_LOG = "interface.log"
LIMITE_DO_LOG_BYTES = 1_000_000  # passou disso, a próxima abertura recomeça o arquivo


def tem_console() -> bool:
    return sys.stdout is not None and sys.stderr is not None


def preparar_sem_console(
    pasta: Path | None = None, *, limite_bytes: int = LIMITE_DO_LOG_BYTES
) -> Path | None:
    """Sem console, manda `stdout` e `stderr` para o `interface.log` e devolve o caminho dele.

    Com console (terminal, `uv run`) não muda nada e devolve `None`. Se o arquivo não puder ser
    aberto, descarta a saída (`os.devnull`): o aplicativo não pode cair por causa do log.
    """
    if tem_console():
        return None
    pasta = pasta or platformdirs.user_log_path(NOME_APP, appauthor=False)
    caminho = pasta / NOME_DO_LOG
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        recomecar = caminho.exists() and caminho.stat().st_size > limite_bytes
        arquivo = caminho.open("w" if recomecar else "a", encoding="utf-8", buffering=1)
        arquivo.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} · abertura do aplicativo ---\n")
    except OSError:
        arquivo = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 (vive até o fim)
    if sys.stdout is None:
        sys.stdout = arquivo
    if sys.stderr is None:
        sys.stderr = arquivo
    with contextlib.suppress(OSError, ValueError, RuntimeError):
        faulthandler.enable(arquivo)  # queda do próprio Python (não é exceção) também fica no log
    return caminho
