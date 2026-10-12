import threading

import pytest

from spurgeon.core.erros import Cancelado, ErroFFmpeg
from spurgeon.core.ffmpeg import (
    codificadores_disponiveis,
    executar_ffmpeg,
    filtros_disponiveis,
    sondar,
)

pytestmark = pytest.mark.ffmpeg


def test_sondar_le_o_video_de_teste(video_teste):
    info = sondar(video_teste)
    assert info.duracao_s == pytest.approx(20.0, abs=0.2)
    assert (info.largura, info.altura) == (640, 360)
    assert info.fps == pytest.approx(25.0)
    assert info.codec_video == "h264"
    assert info.codec_audio == "aac"
    assert info.taxa_audio == 48000
    assert info.tem_video and info.tem_audio


def test_sondar_arquivo_inexistente_da_erro(tmp_path):
    with pytest.raises(ErroFFmpeg):
        sondar(tmp_path / "nao_existe.mp4")


def test_recursos_que_o_projeto_exige_existem():
    assert {"ass", "loudnorm"} <= filtros_disponiveis()
    assert {"libx264", "aac"} <= codificadores_disponiveis()


def test_executar_reporta_progresso_ate_o_fim(video_teste, tmp_path):
    saida = tmp_path / "saida.mp4"
    fracoes: list[float] = []
    executar_ffmpeg(
        ["-y", "-i", video_teste, "-c:v", "libx264", "-preset", "ultrafast", saida],
        duracao_s=20.0,
        ao_progresso=fracoes.append,
    )
    assert saida.exists()
    assert fracoes
    assert fracoes == sorted(fracoes)
    assert fracoes[-1] == pytest.approx(1.0, abs=0.05)


def test_erro_do_ffmpeg_traz_a_mensagem(tmp_path):
    with pytest.raises(ErroFFmpeg) as erro:
        executar_ffmpeg(["-i", tmp_path / "nao_existe.mp4", "-f", "null", "-"])
    assert erro.value.codigo != 0
    assert "nao_existe" in erro.value.saida_erro


def test_cancelar_encerra_o_processo(tmp_path):
    cancelar = threading.Event()

    def ao_progresso(_):
        cancelar.set()

    with pytest.raises(Cancelado):
        executar_ffmpeg(
            ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=600",
             "-c:v", "libx264", "-preset", "slow", "-f", "null", "-"],
            duracao_s=600.0,
            ao_progresso=ao_progresso,
            cancelar=cancelar,
        )  # fmt: skip
