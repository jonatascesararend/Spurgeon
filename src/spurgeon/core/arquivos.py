"""Escrita atômica de arquivos: grava num temporário e renomeia."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def escrever_texto_atomico(caminho: Path, texto: str, *, encoding: str = "utf-8") -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, nome_tmp = tempfile.mkstemp(dir=caminho.parent, prefix=caminho.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        _substituir(nome_tmp, caminho)
    except BaseException:
        Path(nome_tmp).unlink(missing_ok=True)
        raise


def escrever_json_atomico(caminho: Path, dados: Any, *, indent: int = 2) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=indent)
    escrever_texto_atomico(caminho, texto + "\n")


def ler_json(caminho: Path) -> Any:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _substituir(origem: str, destino: Path, tentativas: int = 8) -> None:
    # No Windows o os.replace falha enquanto outro processo (antivírus, a própria
    # interface lendo o arquivo) mantém o destino aberto; algumas tentativas resolvem.
    for i in range(tentativas):
        try:
            os.replace(origem, destino)
            return
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(0.05 * (i + 1))
