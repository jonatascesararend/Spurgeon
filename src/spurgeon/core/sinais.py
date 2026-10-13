"""Tipo dos sinais de controle (cancelar e pausar) que a interface manda ao processamento."""

from __future__ import annotations

from typing import Protocol


class SinalControle(Protocol):
    """O que o processamento usa de um `threading.Event`."""

    def is_set(self) -> bool: ...
    def set(self) -> None: ...
    def clear(self) -> None: ...
