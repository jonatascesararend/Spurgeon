"""Obtenção e uso do deep-filter. O download é testado com um arquivo local (sem rede)."""

import hashlib
import subprocess

import pytest

from spurgeon.audio import deep_filter
from spurgeon.audio.deep_filter import (
    ErroFerramenta,
    LimparAudio,
    baixar_deep_filter,
    caminho_deep_filter,
    deep_filter_instalado,
    rodar_deep_filter,
    sha256_de,
)
from spurgeon.core.config import ConfigApp
from spurgeon.core.erros import Cancelado
from spurgeon.core.ffmpeg import localizar_binario, sondar

CONTEUDO = b"programa de mentira" * 1000
HASH = hashlib.sha256(CONTEUDO).hexdigest()


@pytest.fixture
def fonte(tmp_path):
    arquivo = tmp_path / "fonte" / "deep-filter.exe"
    arquivo.parent.mkdir()
    arquivo.write_bytes(CONTEUDO)
    return arquivo.as_uri()


def test_baixa_confere_o_hash_e_guarda_em_modelos_ferramentas(config_tmp, fonte):
    progresso: list[float] = []
    destino = baixar_deep_filter(config_tmp, url=fonte, sha256=HASH, ao_progresso=progresso.append)
    assert destino == config_tmp.pastas.modelos / "ferramentas" / "deep-filter.exe"
    assert destino.read_bytes() == CONTEUDO
    assert progresso and progresso[-1] == pytest.approx(1.0)
    assert not list(destino.parent.glob("*.baixando"))
    assert deep_filter_instalado(config_tmp, HASH)


def test_nao_baixa_de_novo_se_ja_esta_instalado(config_tmp, fonte, tmp_path):
    baixar_deep_filter(config_tmp, url=fonte, sha256=HASH)
    # Se tentasse baixar de novo, esta URL quebraria.
    destino = baixar_deep_filter(config_tmp, url=(tmp_path / "nao_existe").as_uri(), sha256=HASH)
    assert destino.read_bytes() == CONTEUDO


def test_hash_diferente_descarta_o_arquivo(config_tmp, fonte):
    with pytest.raises(ErroFerramenta, match="SHA-256"):
        baixar_deep_filter(config_tmp, url=fonte, sha256="0" * 64)
    ferramentas = config_tmp.pastas.modelos / "ferramentas"
    assert list(ferramentas.iterdir()) == []  # nada fica pela metade


def test_arquivo_instalado_adulterado_e_baixado_de_novo(config_tmp, fonte):
    baixar_deep_filter(config_tmp, url=fonte, sha256=HASH)
    caminho_deep_filter(config_tmp).write_bytes(b"adulterado")
    assert not deep_filter_instalado(config_tmp, HASH)
    baixar_deep_filter(config_tmp, url=fonte, sha256=HASH)
    assert sha256_de(caminho_deep_filter(config_tmp)) == HASH


def test_sem_rede_o_erro_diz_o_que_fazer(config_tmp, tmp_path):
    with pytest.raises(ErroFerramenta, match="Baixe manualmente"):
        baixar_deep_filter(config_tmp, url=(tmp_path / "nao_existe.exe").as_uri(), sha256=HASH)


def test_cancelar_o_download_nao_deixa_lixo(config_tmp, fonte):
    import threading

    cancelar = threading.Event()
    cancelar.set()
    with pytest.raises(Cancelado):
        baixar_deep_filter(config_tmp, url=fonte, sha256=HASH, cancelar=cancelar)
    assert list((config_tmp.pastas.modelos / "ferramentas").iterdir()) == []


def test_o_hash_oficial_e_o_que_registramos_no_projeto():
    assert deep_filter.SHA256 == "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915"
    assert deep_filter.URL.startswith("https://github.com/Rikorose/DeepFilterNet/releases/")


def test_limpar_audio_so_roda_se_pedido(contexto):
    contexto.opcoes.limpar_audio = False
    assert not LimparAudio().ativa(contexto.opcoes)
    contexto.opcoes.limpar_audio = True
    assert LimparAudio().ativa(contexto.opcoes)


@pytest.mark.ffmpeg
def test_deep_filter_de_verdade_limpa_ruido_e_mantem_o_alinhamento(tmp_path):
    """Roda o binário oficial se ele já estiver baixado (o primeiro uso real o baixa)."""
    config = ConfigApp()  # procura em modelos/ferramentas do repositório
    if not deep_filter_instalado(config):
        pytest.skip("deep-filter ainda não foi baixado")
    ruidoso = tmp_path / "ruidoso.wav"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=6",
            "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.5:sample_rate=48000:duration=6",
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0",
            "-ac", "1", "-c:a", "pcm_s16le", str(ruidoso),
        ],
        check=True,
    )  # fmt: skip
    progresso: list[float] = []
    saida = rodar_deep_filter(
        caminho_deep_filter(config),
        ruidoso,
        tmp_path / "saida",
        duracao_s=6.0,
        ao_progresso=progresso.append,
    )
    assert sondar(saida).duracao_s == pytest.approx(6.0, abs=0.2)
    assert progresso and max(progresso) <= 0.97
