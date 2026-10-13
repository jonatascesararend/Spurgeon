import pytest

from spurgeon.__main__ import main


def test_processar_com_opcao_invalida_explica_e_nao_roda(tmp_path, video_falso, capsys):
    codigo = main(
        ["processar", str(video_falso), "--limiar-pausa", "abc", "--projetos", str(tmp_path)]
    )
    assert codigo == 2
    assert "Tamanho mínimo da pausa" in capsys.readouterr().err


def test_processar_com_data_invalida(tmp_path, video_falso, capsys):
    codigo = main(["processar", str(video_falso), "--data", "ontem", "--projetos", str(tmp_path)])
    assert codigo == 2
    assert "dd/mm/aaaa" in capsys.readouterr().err


def test_processar_video_inexistente(tmp_path, capsys):
    codigo = main(
        [
            "processar",
            str(tmp_path / "nao_existe.mp4"),
            "--data",
            "12/10/2026",
            "--projetos",
            str(tmp_path),
        ]
    )
    assert codigo == 2
    assert "não existe" in capsys.readouterr().err


@pytest.mark.ffmpeg
def test_processar_roda_o_audio_pela_linha_de_comando(tmp_path, video_com_pausas, capsys):
    codigo = main(
        [
            "processar", str(video_com_pausas), "--normalizar",
            "--nome", "Teste CLI", "--data", "12/10/2026", "--projetos", str(tmp_path / "P"),
        ]
    )  # fmt: skip
    saida = capsys.readouterr().out
    assert codigo == 0, saida
    pasta = tmp_path / "P" / "2026-10-12_Teste_CLI"
    assert "Projeto criado" in saida
    assert "Resultado: concluido" in saida
    assert (pasta / "_work" / "audio_normalizado.wav").exists()
    assert (pasta / "_work" / "cortes.json").exists()
    assert "Volume final medido" in (pasta / "processamento.log").read_text(encoding="utf-8")
