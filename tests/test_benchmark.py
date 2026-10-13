import pytest

from spurgeon.__main__ import main
from spurgeon.core.config import ConfigApp
from spurgeon.transcricao.benchmark import ResultadoBenchmark, medir_velocidade
from tests.test_fase3 import MODELOS_DO_PROJETO, exige_modelo


def test_fator_e_projecao_de_tempo():
    r = ResultadoBenchmark("tiny", trecho_s=60.0, carga_s=1.0, transcricao_s=42.0, palavras=100,
                           threads=9, dispositivo="cpu")  # fmt: skip
    assert r.fator == pytest.approx(0.7)
    assert r.segundos_para(3600) == pytest.approx(2520)  # 1 h de áudio em 42 min


def test_benchmark_de_arquivo_inexistente_explica(tmp_path, capsys):
    assert main(["benchmark", str(tmp_path / "nao_existe.mp4")]) == 2
    assert "não existe" in capsys.readouterr().err


@pytest.mark.ffmpeg
@pytest.mark.modelo
def test_benchmark_de_verdade_com_o_tiny(fala_pt):
    config = exige_modelo("tiny")
    config = ConfigApp()
    config.pastas.modelos = MODELOS_DO_PROJETO
    r = medir_velocidade(config, "tiny", fala_pt, trecho_s=10)
    assert r.trecho_s == pytest.approx(10, abs=1)
    assert r.transcricao_s > 0 and r.palavras > 5
    assert r.dispositivo == "cpu"


@pytest.mark.ffmpeg
@pytest.mark.modelo
def test_comando_benchmark_imprime_a_estimativa(fala_pt, capsys, monkeypatch):
    exige_modelo("tiny")
    monkeypatch.setattr("spurgeon.__main__.carregar_config", lambda: _config_com_modelos())
    assert main(["benchmark", str(fala_pt), "--modelo", "tiny", "--segundos", "10"]) == 0
    saida = capsys.readouterr().out
    assert "velocidade:" in saida and "60 min de áudio" in saida


def _config_com_modelos() -> ConfigApp:
    config = ConfigApp()
    config.pastas.modelos = MODELOS_DO_PROJETO
    return config
