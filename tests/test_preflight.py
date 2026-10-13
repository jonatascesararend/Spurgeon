import pytest

from spurgeon.core import preflight
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.hardware import Gpu, PerfilHardware
from spurgeon.core.preflight import Nivel, executar_preflight, tem_erro


@pytest.fixture(autouse=True)
def ambiente_controlado(monkeypatch):
    """Isola o teste do PC real: hardware fixo, sem teste de NVENC e sem Ollama de verdade."""
    monkeypatch.setattr(preflight, "nvenc_funciona", lambda: False)
    monkeypatch.setattr(preflight, "_modelos_ollama", lambda: ["qwen3:8b"])
    monkeypatch.setattr(
        preflight,
        "detectar_hardware",
        lambda *_: PerfilHardware(
            cpu_nome="CPU de teste", nucleos_fisicos=6, threads_logicos=12,
            ram_total_gb=16.0, ram_livre_gb=8.0, disco_livre_gb=500.0,
            gpus=(Gpu("GPU de teste", "nvidia", 2048),), cuda_utilizavel=False,
            motivo_cuda="teste",
        ),
    )  # fmt: skip


def por_nome(resultados):
    return {v.nome: v for v in resultados}


@pytest.mark.ffmpeg
def test_checagem_geral_passa_no_ambiente_de_desenvolvimento(config_tmp):
    resultados = por_nome(executar_preflight(config_tmp))
    assert resultados["Python"].nivel == Nivel.OK
    assert resultados["FFmpeg"].nivel == Nivel.OK
    assert resultados["FFmpeg: recursos"].nivel == Nivel.OK
    assert resultados["Pasta de projetos"].nivel == Nivel.OK
    assert resultados["Ollama"].nivel == Nivel.OK


@pytest.mark.ffmpeg
def test_introducao_marcada_sem_o_arquivo_e_erro(config_tmp, video_teste):
    opcoes = OpcoesProcessamento(introducao=True)
    resultados = executar_preflight(config_tmp, video=video_teste, opcoes=opcoes)
    assert por_nome(resultados)["Introdução"].nivel == Nivel.ERRO
    assert tem_erro(resultados)


@pytest.mark.ffmpeg
def test_introducao_marcada_com_o_arquivo_passa(config_tmp, video_teste):
    config_tmp.pastas.recursos.mkdir(parents=True)
    config_tmp.pastas.introducao.write_bytes(b"x")
    opcoes = OpcoesProcessamento(introducao=True)
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste, opcoes=opcoes))
    assert resultados["Introdução"].nivel == Nivel.OK


@pytest.mark.ffmpeg
def test_falta_de_disco_para_o_video_e_erro(config_tmp, video_teste, monkeypatch):
    monkeypatch.setattr(preflight, "_MARGEM_DISCO_GB", 10**9)
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste))
    assert resultados["Espaço em disco"].nivel == Nivel.ERRO


@pytest.mark.ffmpeg
@pytest.mark.ffmpeg
def test_ollama_parado_so_e_erro_quando_ha_shorts_e_nao_esta_instalado(
    config_tmp, video_teste, monkeypatch
):
    monkeypatch.setattr(preflight, "_modelos_ollama", lambda: None)
    monkeypatch.setattr(preflight, "localizar_ollama", lambda: None)
    sem_shorts = executar_preflight(config_tmp, video=video_teste, opcoes=OpcoesProcessamento())
    assert "Ollama" not in por_nome(sem_shorts)
    com_shorts = executar_preflight(
        config_tmp, video=video_teste, opcoes=OpcoesProcessamento(gerar_shorts=True)
    )
    assert por_nome(com_shorts)["Ollama"].nivel == Nivel.ERRO
    assert "winget install Ollama.Ollama" in por_nome(com_shorts)["Ollama"].detalhe
    geral = por_nome(executar_preflight(config_tmp))
    assert geral["Ollama"].nivel == Nivel.AVISO


@pytest.mark.ffmpeg
def test_ollama_instalado_mas_fechado_so_avisa_porque_o_app_abre(
    config_tmp, video_teste, monkeypatch, tmp_path
):
    monkeypatch.setattr(preflight, "_modelos_ollama", lambda: None)
    monkeypatch.setattr(preflight, "localizar_ollama", lambda: tmp_path / "ollama.exe")
    opcoes = OpcoesProcessamento(gerar_shorts=True)
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste, opcoes=opcoes))
    assert resultados["Ollama"].nivel == Nivel.AVISO
    assert "será aberto" in resultados["Ollama"].detalhe


@pytest.mark.ffmpeg
def test_modelo_do_ollama_ausente_so_avisa_pois_o_app_baixa(config_tmp, video_teste, monkeypatch):
    monkeypatch.setattr(preflight, "_modelos_ollama", lambda: ["llama3.1:8b"])
    opcoes = OpcoesProcessamento(gerar_shorts=True, ollama_modelo="qwen3:8b")
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste, opcoes=opcoes))
    assert resultados["Ollama"].nivel == Nivel.AVISO
    assert "ollama pull qwen3:8b" in resultados["Ollama"].detalhe


@pytest.mark.ffmpeg
def test_limpar_audio_sem_o_deep_filter_baixado_avisa_que_precisa_de_internet(
    config_tmp, video_teste
):
    opcoes = OpcoesProcessamento(limpar_audio=True)
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste, opcoes=opcoes))
    assert resultados["deep-filter"].nivel == Nivel.AVISO
    assert "internet" in resultados["deep-filter"].detalhe


@pytest.mark.ffmpeg
def test_modelo_whisper_nao_baixado_avisa_o_tamanho_e_a_internet(config_tmp, video_teste):
    opcoes = OpcoesProcessamento(whisper_modelo="large-v3-turbo")
    r = por_nome(executar_preflight(config_tmp, video=video_teste, opcoes=opcoes))["Modelo Whisper"]
    assert r.nivel == Nivel.AVISO
    assert "1622 MB" in r.detalhe and "internet" in r.detalhe


@pytest.mark.ffmpeg
def test_modelo_whisper_baixado_fica_ok(config_tmp, video_teste):
    pasta = config_tmp.pastas.modelos / "whisper" / "tiny"
    pasta.mkdir(parents=True)
    for arquivo in ("model.bin", "config.json", "tokenizer.json"):
        (pasta / arquivo).write_bytes(b"x")
    opcoes = OpcoesProcessamento(whisper_modelo="tiny")
    r = por_nome(executar_preflight(config_tmp, video=video_teste, opcoes=opcoes))["Modelo Whisper"]
    assert r.nivel == Nivel.OK


@pytest.mark.ffmpeg
def test_sem_o_pacote_faster_whisper_e_erro_com_a_instrucao(config_tmp, video_teste, monkeypatch):
    import importlib.util

    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda nome, *a: None if nome == "faster_whisper" else original(nome, *a),
    )
    r = por_nome(executar_preflight(config_tmp, video=video_teste))["faster-whisper"]
    assert r.nivel == Nivel.ERRO
    assert "uv sync --extra transcricao" in r.detalhe


@pytest.mark.ffmpeg
def test_sem_limpar_audio_o_deep_filter_nao_e_cobrado(config_tmp, video_teste):
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste))
    assert "deep-filter" not in resultados


@pytest.mark.ffmpeg
def test_nvenc_pedido_mas_indisponivel_e_erro(config_tmp):
    opcoes = OpcoesProcessamento(usar_nvenc=True)
    resultados = por_nome(executar_preflight(config_tmp, opcoes=opcoes))
    assert resultados["NVENC"].nivel == Nivel.ERRO


@pytest.mark.ffmpeg
def test_o_espaco_necessario_vem_da_duracao_e_das_opcoes_nao_do_tamanho_do_arquivo(video_teste):
    so_longo = OpcoesProcessamento(gerar_video_longo=True, qualidade="rapido")
    com_shorts = OpcoesProcessamento(gerar_shorts=True, qualidade="rapido")
    pouco = preflight._espaco_necessario_gb(video_teste, so_longo)
    mais = preflight._espaco_necessario_gb(video_teste, com_shorts)
    assert pouco < 1.0  # um vídeo de 20 s ocupa quase nada: só a margem fixa pesa
    assert mais > pouco  # os Shorts acrescentam o que vão gravar


def test_sem_conseguir_ler_a_duracao_usa_o_tamanho_do_arquivo(tmp_path):
    falso = tmp_path / "x.mp4"
    falso.write_bytes(b"0" * 1000)
    necessario = preflight._espaco_necessario_gb(falso, OpcoesProcessamento())
    assert necessario == pytest.approx(3 * 1000 / 1024**3)


@pytest.mark.ffmpeg
def test_ffmpeg_sem_zscale_ou_tonemap_e_erro_porque_o_hdr_do_celular_nao_converte(
    config_tmp, video_teste, monkeypatch
):
    from spurgeon.core import ffmpeg as ffmpeg_mod

    filtros = frozenset(ffmpeg_mod.filtros_disponiveis() - {"zscale"})
    monkeypatch.setattr(preflight, "filtros_disponiveis", lambda: filtros)
    resultados = por_nome(executar_preflight(config_tmp, video=video_teste))
    assert resultados["FFmpeg: recursos"].nivel == Nivel.ERRO
    assert "zscale" in resultados["FFmpeg: recursos"].detalhe
