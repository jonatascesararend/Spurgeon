import subprocess
from datetime import date

import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.erros import ErroConfiguracao, ProjetoConflito
from spurgeon.core.estado import carregar_estado
from spurgeon.core.ffmpeg import localizar_binario
from spurgeon.core.projeto import (
    PastasProjeto,
    criar_ou_retomar_projeto,
    data_gravacao,
    limpar_intermediarios,
    nome_pasta_projeto,
    sanitizar_nome,
)

OPCOES = OpcoesProcessamento()


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Sermão: A Graça?/Soberana", "Sermão_A_GraçaSoberana"),
        ("  muitos   espaços  ", "muitos_espaços"),
        ("CON", "_CON"),
        ("???", "Sem_Nome"),
        ("termina com ponto...", "termina_com_ponto"),
    ],
)
def test_sanitizar_nome(entrada, esperado):
    assert sanitizar_nome(entrada) == esperado


def test_sanitizar_limita_o_tamanho():
    assert len(sanitizar_nome("a" * 300)) == 80


def test_nome_da_pasta_usa_data_e_nome():
    assert nome_pasta_projeto(date(2026, 10, 12), "Nome Do Sermao") == "2026-10-12_Nome_Do_Sermao"


def test_cria_projeto_novo(tmp_path, video_falso):
    r = criar_ou_retomar_projeto(tmp_path, video_falso, "Sermão", date(2026, 10, 12), OPCOES)
    assert not r.retomado
    assert r.pastas.raiz == tmp_path / "2026-10-12_Sermão"
    assert r.pastas.work.is_dir()
    assert carregar_estado(r.pastas.raiz).entrada.hash_rapido == r.estado.entrada.hash_rapido


def test_mesmo_video_com_mesmo_nome_retoma_o_projeto(tmp_path, video_falso):
    primeiro = criar_ou_retomar_projeto(tmp_path, video_falso, "Sermão", date(2026, 10, 12), OPCOES)
    novas_opcoes = OpcoesProcessamento(limpar_audio=True)
    segundo = criar_ou_retomar_projeto(
        tmp_path, video_falso, "Sermão", date(2026, 10, 12), novas_opcoes
    )
    assert segundo.retomado
    assert segundo.pastas == primeiro.pastas
    assert segundo.estado.opcoes.limpar_audio is True


def test_outro_video_com_o_mesmo_nome_e_conflito(tmp_path, video_falso):
    criar_ou_retomar_projeto(tmp_path, video_falso, "Sermão", date(2026, 10, 12), OPCOES)
    outro = tmp_path / "outro.mp4"
    outro.write_bytes(b"outro conteudo" * 500)
    with pytest.raises(ProjetoConflito):
        criar_ou_retomar_projeto(tmp_path, outro, "Sermão", date(2026, 10, 12), OPCOES)


def test_pasta_existente_que_nao_e_projeto_e_conflito(tmp_path, video_falso):
    alheia = tmp_path / "2026-10-12_Sermão"
    alheia.mkdir()
    (alheia / "arquivo_do_usuario.txt").write_text("não mexa")
    with pytest.raises(ProjetoConflito):
        criar_ou_retomar_projeto(tmp_path, video_falso, "Sermão", date(2026, 10, 12), OPCOES)
    assert (alheia / "arquivo_do_usuario.txt").exists()


def test_caminho_longo_demais_e_recusado(tmp_path, video_falso):
    base = tmp_path / ("p" * 100) / ("q" * 60)
    with pytest.raises(ErroConfiguracao):
        criar_ou_retomar_projeto(base, video_falso, "Sermão", date(2026, 10, 12), OPCOES)


def test_limpar_intermediarios_so_apaga_o_work(tmp_path):
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    (pastas.work / "audio.wav").write_text("x")
    pastas.srt.write_text("legenda")
    limpar_intermediarios(pastas)
    assert not pastas.work.exists()
    assert pastas.srt.exists()


def test_caminhos_do_projeto_sao_relativos_com_barras_normais(tmp_path):
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    assert pastas.relativo(pastas.work / "a.wav") == "_work/a.wav"


@pytest.mark.ffmpeg
def test_data_da_gravacao_vem_do_metadado(tmp_path):
    video = tmp_path / "com_data.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-metadata", "creation_time=2026-03-01T12:00:00Z", str(video),
        ],
        check=True,
    )  # fmt: skip
    assert data_gravacao(video) == date(2026, 3, 1)


@pytest.mark.ffmpeg
def test_sem_metadado_usa_a_data_do_arquivo(video_teste):
    assert data_gravacao(video_teste) == date.fromtimestamp(video_teste.stat().st_mtime)


def test_limpar_intermediarios_guarda_os_json_da_analise(tmp_path):
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    (pastas.work / "audio.wav").write_text("x")
    (pastas.work / "partes").mkdir()
    (pastas.work / "partes" / "p1.mp4").write_text("x")
    (pastas.work / "cortes_shorts.json").write_text("[]")
    limpar_intermediarios(pastas)
    assert sorted(p.name for p in pastas.work.iterdir()) == ["cortes_shorts.json"]


def test_ler_nome_e_data_pela_pasta(tmp_path):
    from datetime import date

    from spurgeon.core.projeto import ler_nome_e_data

    assert ler_nome_e_data(tmp_path / "2026-10-04_Miqueias_2._6-11") == (
        "Miqueias 2. 6-11",
        date(2026, 10, 4),
    )
    assert ler_nome_e_data(tmp_path / "sermao_sem_data") is None
    assert ler_nome_e_data(tmp_path / "2026-13-45_invalida") is None


def test_descrever_projeto_lista_o_que_ja_existe(tmp_path):
    from spurgeon.core.projeto import descrever_projeto

    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    assert "ainda não tem resultados" in descrever_projeto(pastas)
    pastas.transcricao.write_text("{}")
    pastas.video_longo.write_text("x")
    pastas.cortes_shorts.write_text('[{"id": 1}, {"id": 2}, {"id": 3}]')
    pastas.shorts.mkdir()
    (pastas.shorts / "short_01_a.mp4").write_text("x")
    pastas.shorts_renderizados.write_text(
        '{"1": {"arquivo": "short_01_a.mp4", "assinatura": "a"},'
        ' "2": {"arquivo": "short_02_b.mp4", "assinatura": "b"}}'
    )
    texto = descrever_projeto(pastas)
    assert "transcrição" in texto and "vídeo longo" in texto
    assert "3 Shorts sugeridos, 1 renderizados" in texto
