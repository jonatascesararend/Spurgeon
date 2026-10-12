"""Fixtures compartilhadas pelos testes."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from spurgeon.core.config import ConfigApp, OpcoesProcessamento, Pastas
from spurgeon.core.erros import ErroConfiguracao
from spurgeon.core.estado import EntradaVideo, Estado
from spurgeon.core.ffmpeg import localizar_binario
from spurgeon.core.log import criar_log_projeto, encerrar_log
from spurgeon.core.pipeline import ContextoProjeto
from spurgeon.core.projeto import PastasProjeto


def pytest_collection_modifyitems(items):
    try:
        localizar_binario("ffmpeg")
    except ErroConfiguracao:
        pular = pytest.mark.skip(reason="FFmpeg não instalado")
        for item in items:
            if "ffmpeg" in item.keywords:
                item.add_marker(pular)


@pytest.fixture(scope="session")
def video_teste(tmp_path_factory) -> Path:
    """Vídeo de 20 s (640x360, 25 fps, tom de 440 Hz) gerado pelo FFmpeg; não é versionado."""
    destino = tmp_path_factory.mktemp("fixtures") / "teste.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=20",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


@pytest.fixture
def config_tmp(tmp_path) -> ConfigApp:
    return ConfigApp(
        pastas=Pastas(
            entrada=tmp_path / "Downloads",
            projetos=tmp_path / "Projetos",
            recursos=tmp_path / "recursos",
            modelos=tmp_path / "modelos",
        )
    )


@pytest.fixture
def video_falso(tmp_path) -> Path:
    """Arquivo qualquer, suficiente para os testes que só precisam de hash e tamanho."""
    caminho = tmp_path / "sermao.mp4"
    caminho.write_bytes(b"conteudo-do-video" * 1000)
    return caminho


@pytest.fixture
def contexto(tmp_path, config_tmp, video_falso) -> Iterator[ContextoProjeto]:
    pastas = PastasProjeto(tmp_path / "projeto")
    pastas.criar()
    opcoes = OpcoesProcessamento()
    log = criar_log_projeto(pastas.log, nome=f"spurgeon.teste.{tmp_path.name}")
    yield ContextoProjeto(
        pastas=pastas,
        config=config_tmp,
        opcoes=opcoes,
        estado=Estado(entrada=EntradaVideo.de_arquivo(video_falso), opcoes=opcoes),
        log=log,
    )
    encerrar_log(log)
