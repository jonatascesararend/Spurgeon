"""Obtenção e carregamento do modelo Whisper (faster-whisper) em `modelos/whisper/`."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from spurgeon.core.config import ConfigApp
from spurgeon.core.erros import Cancelado, ErroFerramenta
from spurgeon.core.sinais import SinalControle

# Tamanho aproximado de cada modelo no disco, em MB; só serve para estimar o progresso do download.
TAMANHO_MB = {
    "tiny": 78,
    "base": 145,
    "small": 484,
    "medium": 1530,
    "large-v3-turbo": 1622,
    "large-v3": 3100,
}
_ARQUIVOS_ESSENCIAIS = ("model.bin", "config.json", "tokenizer.json")


def pasta_modelo(config: ConfigApp, nome: str) -> Path:
    return config.pastas.modelos / "whisper" / nome


def modelo_baixado(config: ConfigApp, nome: str) -> bool:
    pasta = pasta_modelo(config, nome)
    return all((pasta / arquivo).is_file() for arquivo in _ARQUIVOS_ESSENCIAIS)


def _tamanho_baixado_mb(pasta: Path) -> float:
    if not pasta.exists():
        return 0.0
    total = sum(
        f.stat().st_size
        for f in pasta.rglob("*")
        if f.is_file() and ".cache" not in f.relative_to(pasta).parts
    )
    return total / 1e6


def baixar_modelo(
    config: ConfigApp,
    nome: str,
    *,
    ao_progresso: Callable[[float], None] | None = None,
    cancelar: SinalControle | None = None,
    baixar: Callable[..., Any] | None = None,
) -> Path:
    """Baixa o modelo do Hugging Face para `modelos/whisper/<nome>` (precisa de internet).

    O download roda numa thread para dar para cancelar e mostrar o progresso; ao cancelar, a
    thread termina sozinha quando o processo acaba. `baixar` existe para testes.
    """
    pasta = pasta_modelo(config, nome)
    if modelo_baixado(config, nome):
        return pasta
    if baixar is None:
        from faster_whisper.utils import download_model as baixar
    pasta.parent.mkdir(parents=True, exist_ok=True)
    falha: list[BaseException] = []

    def trabalho() -> None:
        try:
            baixar(nome, output_dir=str(pasta))
        except BaseException as erro:
            falha.append(erro)

    fio = threading.Thread(target=trabalho, daemon=True)
    fio.start()
    esperado = TAMANHO_MB.get(nome, 1000)
    while fio.is_alive():
        if cancelar is not None and cancelar.is_set():
            raise Cancelado()
        if ao_progresso:
            ao_progresso(min(0.99, _tamanho_baixado_mb(pasta) / esperado))
        time.sleep(0.3)
    if falha:
        raise ErroFerramenta(
            f"Não foi possível baixar o modelo Whisper '{nome}' ({falha[0]}). "
            "Confira a internet e tente de novo."
        ) from falha[0]
    if not modelo_baixado(config, nome):
        raise ErroFerramenta(
            f"O download do modelo '{nome}' terminou, mas faltam arquivos em {pasta}."
        )
    return pasta


def carregar_modelo(
    config: ConfigApp,
    nome: str,
    *,
    dispositivo: str = "cpu",
    compute_type: str = "int8",
    threads: int = 0,
):
    """Carrega o modelo na memória. `threads=0` deixa o CTranslate2 escolher."""
    from faster_whisper import WhisperModel

    return WhisperModel(
        str(pasta_modelo(config, nome)),
        device=dispositivo,
        compute_type=compute_type,
        cpu_threads=threads,
    )
