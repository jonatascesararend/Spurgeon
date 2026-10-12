"""Log por projeto (processamento.log) e log geral do aplicativo."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

import platformdirs

from spurgeon.core.config import NOME_APP

FORMATO = "%(asctime)s | %(levelname)-7s | %(etapa)-20s | %(message)s"
FORMATO_DATA = "%Y-%m-%d %H:%M:%S"

etapa_atual: ContextVar[str] = ContextVar("etapa_atual", default="-")


class FiltroEtapa(logging.Filter):
    """Coloca em cada registro o id da etapa que está rodando."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.etapa = etapa_atual.get()
        return True


class HandlerCallback(logging.Handler):
    """Entrega cada linha formatada a uma função (painel de logs da interface)."""

    def __init__(self, callback: Callable[[str, int], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record), record.levelno)
        except Exception:
            self.handleError(record)


@contextmanager
def contexto_etapa(id_etapa: str) -> Iterator[None]:
    token = etapa_atual.set(id_etapa)
    try:
        yield
    finally:
        etapa_atual.reset(token)


def criar_log_projeto(
    arquivo: Path,
    *,
    nome: str = "spurgeon.projeto",
    nivel: int = logging.INFO,
    callback: Callable[[str, int], None] | None = None,
) -> logging.Logger:
    """Cria (ou recria) o logger do projeto, gravando em `arquivo` em modo de acréscimo."""
    arquivo = Path(arquivo)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(nome)
    encerrar_log(logger)
    logger.setLevel(nivel)
    logger.propagate = False

    formatador = logging.Formatter(FORMATO, datefmt=FORMATO_DATA)
    destinos: list[logging.Handler] = [logging.FileHandler(arquivo, encoding="utf-8")]
    if callback is not None:
        destinos.append(HandlerCallback(callback))
    for destino in destinos:
        destino.setFormatter(formatador)
        destino.addFilter(FiltroEtapa())
        logger.addHandler(destino)
    return logger


def encerrar_log(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def configurar_log_app(nivel: int = logging.INFO) -> logging.Logger:
    """Log geral (fora de qualquer projeto), com rotação, na pasta de logs do usuário."""
    pasta = platformdirs.user_log_path(NOME_APP, appauthor=False)
    pasta.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("spurgeon")
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(
            pasta / "spurgeon.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(FORMATO, datefmt=FORMATO_DATA))
        handler.addFilter(FiltroEtapa())
        logger.addHandler(handler)
    logger.setLevel(nivel)
    return logger
