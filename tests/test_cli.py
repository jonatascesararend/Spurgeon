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
def test_processar_roda_tudo_pela_linha_de_comando(tmp_path, video_com_pausas, capsys):
    codigo = main(
        [
            "processar", str(video_com_pausas), "--normalizar", "--qualidade", "rapido",
            "--nome", "Teste CLI", "--data", "12/10/2026", "--projetos", str(tmp_path / "P"),
        ]
    )  # fmt: skip
    saida = capsys.readouterr().out
    assert codigo == 0, saida
    pasta = tmp_path / "P" / "2026-10-12_Teste_CLI"
    assert "Projeto criado" in saida
    assert "Resultado: concluido" in saida
    assert (pasta / "video_longo_otimizado.mp4").exists()
    assert (pasta / "legendas_video_longo.srt").exists()
    work = pasta / "_work"  # há arquivo final: os intermediários pesados são apagados
    assert not [p for p in work.rglob("*") if p.is_file() and p.suffix != ".json"]
    log = (pasta / "processamento.log").read_text(encoding="utf-8")
    assert "Volume final medido" in log and "Vídeo longo pronto" in log
    assert "Resultado em" in log and "Arquivos intermediários apagados" in log


def opcoes_capturadas(monkeypatch, argv):
    """Roda `processar` só até a pré-verificação e devolve as opções que ela recebeu."""
    from spurgeon import __main__ as principal
    from spurgeon.core.preflight import Nivel, Verificacao

    vistas = []

    def falso(_config, *, video, opcoes):
        vistas.append(opcoes)
        return [Verificacao("parar aqui", Nivel.ERRO, "só para o teste")]

    monkeypatch.setattr(principal, "executar_preflight", falso)
    assert main(argv) == 1
    return vistas[0]


def test_processar_com_marca_dagua(monkeypatch, tmp_path, video_falso):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    opcoes = opcoes_capturadas(
        monkeypatch,
        [
            "processar", str(video_falso), "--data", "12/10/2026", "--projetos", str(tmp_path),
            "--shorts", "--marca", str(logo), "--marca-canto", "sup_esq", "--marca-tamanho", "20",
            "--marca-remover-fundo", "--marca-em", "shorts",
        ],
    )  # fmt: skip
    assert opcoes.marca_dagua and opcoes.marca_imagem == str(logo)
    assert opcoes.marca_canto == "sup_esq" and opcoes.marca_tamanho_pct == 20.0
    assert opcoes.marca_remover_fundo
    assert opcoes.marca_nos_curtos and not opcoes.marca_no_longo


def test_processar_modo_so_shorts_e_so_vinhetas(monkeypatch, tmp_path, video_falso):
    base = ["processar", str(video_falso), "--data", "12/10/2026", "--projetos", str(tmp_path)]
    so_shorts = opcoes_capturadas(monkeypatch, [*base, "--modo", "so_shorts"])
    assert so_shorts.processa_shorts and not so_shorts.processa_video_longo
    vinhetas = opcoes_capturadas(
        monkeypatch, [*base, "--modo", "so_vinhetas", "--introducao", "--vinheta-final"]
    )
    assert vinhetas.so_vinhetas and vinhetas.introducao and vinhetas.vinheta_final


def test_processar_modo_so_vinhetas_sem_nenhuma_vinheta_explica(tmp_path, video_falso, capsys):
    base = ["processar", str(video_falso), "--data", "12/10/2026", "--projetos", str(tmp_path)]
    codigo = main([*base, "--modo", "so_vinhetas"])
    assert codigo == 2
    assert "introdução" in capsys.readouterr().err
