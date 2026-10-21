"""Linha de comando da 1.2: começo e fim, zoom, presets, fade e arquivos de introdução e vinheta."""

from __future__ import annotations

import pytest

from spurgeon import __main__ as principal
from spurgeon.__main__ import main
from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.config import ConfigApp
from tests.test_cli import opcoes_capturadas


def base(tmp_path, video):
    return ["processar", str(video), "--data", "12/10/2026", "--projetos", str(tmp_path)]


def test_comeco_e_fim_pela_linha_de_comando(monkeypatch, tmp_path, video_falso):
    opcoes = opcoes_capturadas(
        monkeypatch, [*base(tmp_path, video_falso), "--inicio", "0:07", "--fim", "1:00"]
    )
    assert (opcoes.video_inicio_s, opcoes.video_fim_s) == (7.0, 60.0)


@pytest.mark.ffmpeg
def test_fim_negativo_tira_do_final_do_video(monkeypatch, tmp_path, video_teste):
    opcoes = opcoes_capturadas(monkeypatch, [*base(tmp_path, video_teste), "--fim=-0:05"])
    assert opcoes.video_fim_s == pytest.approx(15.0, abs=0.2)  # o vídeo de teste tem 20 s


def test_fim_negativo_em_video_ilegivel_explica(tmp_path, video_falso, capsys):
    codigo = main([*base(tmp_path, video_falso), "--fim=-0:05"])
    assert codigo == 2 and "--fim" in capsys.readouterr().err


def test_comeco_depois_do_fim_explica(tmp_path, video_falso, capsys):
    codigo = main([*base(tmp_path, video_falso), "--inicio", "2:00", "--fim", "1:00"])
    assert codigo == 2 and "depois do ponto" in capsys.readouterr().err


def test_zoom_pela_linha_de_comando(monkeypatch, tmp_path, video_falso):
    opcoes = opcoes_capturadas(
        monkeypatch,
        [*base(tmp_path, video_falso), "--zoom", "125", "--zoom-x", "54", "--zoom-y", "45"],
    )
    assert opcoes.enquadramento == Enquadramento(zoom_pct=125, centro_x_pct=54, centro_y_pct=45)


def test_zoom_fora_da_faixa_explica(tmp_path, video_falso, capsys):
    assert main([*base(tmp_path, video_falso), "--zoom", "500"]) == 2
    assert "Zoom" in capsys.readouterr().err


def test_presets_salvos_no_aplicativo_valem_na_linha_de_comando(monkeypatch, tmp_path, video_falso):
    config = ConfigApp()
    config.presets_enquadramento["Igreja A"] = Enquadramento(zoom_pct=130, centro_x_pct=54)
    config.presets_tratamento["Quente"] = TratamentoImagem(temperatura=20, sombras=15)
    monkeypatch.setattr(principal, "carregar_config", lambda: config)
    opcoes = opcoes_capturadas(
        monkeypatch,
        [
            *base(tmp_path, video_falso),
            "--preset-zoom",
            "Igreja A",
            "--preset-tratamento",
            "Quente",
        ],
    )
    assert opcoes.zoom_pct == 130 and opcoes.zoom_centro_x_pct == 54
    assert opcoes.tratamento_ativo and opcoes.tratamento == TratamentoImagem(
        temperatura=20, sombras=15
    )


def test_preset_que_nao_existe_explica(monkeypatch, tmp_path, video_falso, capsys):
    monkeypatch.setattr(principal, "carregar_config", lambda: ConfigApp())
    assert main([*base(tmp_path, video_falso), "--preset-zoom", "Nenhum"]) == 2
    assert "Nenhum" in capsys.readouterr().err
    assert main([*base(tmp_path, video_falso), "--preset-tratamento", "Nenhum"]) == 2


def test_arquivos_de_introducao_e_vinheta_e_fade(monkeypatch, tmp_path, video_falso):
    abertura, final = tmp_path / "abertura.mp4", tmp_path / "final.mp4"
    abertura.write_bytes(b"x")
    final.write_bytes(b"x")
    opcoes = opcoes_capturadas(
        monkeypatch,
        [
            *base(tmp_path, video_falso), "--introducao-arquivo", str(abertura),
            "--vinheta-arquivo", str(final), "--fade", "0,8",
        ],
    )  # fmt: skip
    assert opcoes.introducao and opcoes.vinheta_final
    assert opcoes.introducao_arquivo == str(abertura) and opcoes.vinheta_arquivo == str(final)
    assert opcoes.fade_vinhetas and opcoes.fade_vinhetas_s == 0.8


def test_arquivo_de_introducao_que_nao_existe_explica(tmp_path, video_falso, capsys):
    codigo = main([*base(tmp_path, video_falso), "--introducao-arquivo", str(tmp_path / "x.mp4")])
    assert codigo == 2 and "introdução não foi encontrado" in capsys.readouterr().err


def test_fade_estilo_pela_linha_de_comando(monkeypatch, tmp_path, video_falso):
    abertura = tmp_path / "abertura.mp4"
    abertura.write_bytes(b"x")
    base_do_fade = [*base(tmp_path, video_falso), "--introducao-arquivo", str(abertura)]
    padrao = opcoes_capturadas(monkeypatch, [*base_do_fade, "--fade", "0,5"])
    assert padrao.fade_estilo == "mesclar" and padrao.fade_mesclado
    preto = opcoes_capturadas(
        monkeypatch, [*base_do_fade, "--fade", "0,5", "--fade-estilo", "preto"]
    )
    assert preto.fade_estilo == "preto" and not preto.fade_mesclado
