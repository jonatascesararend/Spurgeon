"""O que o instalar.bat usa do aplicativo: o FFmpeg da pasta do projeto e os modelos de IA."""

from __future__ import annotations

import pytest

from spurgeon.audio import deep_filter
from spurgeon.core import ffmpeg, instalacao
from spurgeon.core.config import ConfigApp
from spurgeon.core.erros import ErroConfiguracao, ErroFerramenta
from spurgeon.transcricao import modelo


# --- o FFmpeg que o instalar.bat baixa para dentro do projeto ------------------------------------
@pytest.fixture
def sem_ffmpeg_no_sistema(monkeypatch, tmp_path):
    monkeypatch.delenv("SPURGEON_FFMPEG", raising=False)
    monkeypatch.delenv("SPURGEON_FFPROBE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "vazio"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    ferramentas = tmp_path / "ferramentas"
    monkeypatch.setattr(ffmpeg, "pasta_das_ferramentas", lambda: ferramentas)
    return ferramentas


def test_ffmpeg_da_pasta_do_projeto_e_encontrado(sem_ffmpeg_no_sistema):
    bin_ = sem_ffmpeg_no_sistema / "ffmpeg" / "ffmpeg-9.0.2-full_build" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "ffmpeg.exe").write_bytes(b"")
    (bin_ / "ffprobe.exe").write_bytes(b"")
    assert ffmpeg.localizar_binario.__wrapped__("ffmpeg") == bin_ / "ffmpeg.exe"
    assert ffmpeg.localizar_binario.__wrapped__("ffprobe") == bin_ / "ffprobe.exe"


def test_sem_ffmpeg_em_lugar_nenhum_a_mensagem_manda_rodar_o_instalador(sem_ffmpeg_no_sistema):
    with pytest.raises(ErroConfiguracao, match=r"instalar\.bat"):
        ffmpeg.localizar_binario.__wrapped__("ffmpeg")


def test_o_ffmpeg_da_pasta_do_projeto_vale_mais_que_o_do_path(
    sem_ffmpeg_no_sistema, monkeypatch, tmp_path
):
    do_path = tmp_path / "no_path"
    do_path.mkdir()
    (do_path / "ffmpeg.exe").write_bytes(b"")
    bin_ = sem_ffmpeg_no_sistema / "ffmpeg" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", str(do_path))
    assert ffmpeg.localizar_binario.__wrapped__("ffmpeg") == bin_ / "ffmpeg.exe"


# --- spurgeon baixar-modelos ---------------------------------------------------------------------
@pytest.fixture
def downloads(monkeypatch):
    """Troca os downloads de verdade por registros do que foi pedido."""
    pedidos: list[tuple] = []

    def deep(config, **_):
        pedidos.append(("deep-filter",))
        return config.pastas.modelos / "ferramentas" / "deep-filter.exe"

    def whisper(config, nome, ao_progresso=None, **_):
        pedidos.append(("whisper", nome))
        if ao_progresso:
            ao_progresso(0.5)
            ao_progresso(1.0)
        return config.pastas.modelos / "whisper" / nome

    def ollama(nome, emitir):
        pedidos.append(("ollama", nome))
        return f"{nome} instalado"

    monkeypatch.setattr(deep_filter, "baixar_deep_filter", deep)
    monkeypatch.setattr(modelo, "baixar_modelo", whisper)
    monkeypatch.setattr(instalacao, "_baixar_do_ollama", ollama)
    return pedidos


def test_baixa_os_tres_com_os_modelos_das_opcoes(downloads):
    config = ConfigApp()
    linhas: list[str] = []
    resultados = instalacao.baixar_modelos(config, emitir=linhas.append)
    assert downloads == [
        ("deep-filter",),
        ("whisper", config.padroes.whisper_modelo),
        ("ollama", config.padroes.ollama_modelo),
    ]
    assert all(r.ok for r in resultados) and len(resultados) == 3
    assert any("Whisper: 50%" in linha for linha in linhas)


def test_nome_explicito_e_item_desligado(downloads):
    resultados = instalacao.baixar_modelos(
        ConfigApp(), limpeza_de_audio=False, whisper="tiny", ollama=None, emitir=lambda _l: None
    )
    assert downloads == [("whisper", "tiny")]
    assert [r.ok for r in resultados] == [True]


def test_um_download_que_falha_nao_impede_os_outros(downloads, monkeypatch):
    def quebrado(*_a, **_k):
        raise ErroFerramenta("sem internet")

    monkeypatch.setattr(deep_filter, "baixar_deep_filter", quebrado)
    resultados = instalacao.baixar_modelos(ConfigApp(), emitir=lambda _l: None)
    assert [r.ok for r in resultados] == [False, True, True]
    assert "sem internet" in resultados[0].detalhe


def test_o_progresso_avisa_uma_vez_por_faixa_de_10_por_cento():
    linhas: list[str] = []
    avisar = instalacao._com_progresso("Whisper", linhas.append)
    for fracao in (0.0, 0.02, 0.09, 0.1, 0.15, 0.55, 1.0, 1.0, 1.2):
        avisar(fracao)
    assert linhas == [
        "  Whisper: 0%",
        "  Whisper: 10%",
        "  Whisper: 50%",
        "  Whisper: 100%",
    ]
