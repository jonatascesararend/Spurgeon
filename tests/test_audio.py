"""Etapas de áudio da Fase 2: extração, normalização e pausas."""

import shutil
import subprocess

import pytest

from spurgeon.audio.extrair import ExtrairAudio
from spurgeon.audio.nivel import (
    MedirNivel,
    NivelAudio,
    calcular_nivel,
    ler_niveis,
    limiar_silencio_db,
)
from spurgeon.audio.normalizar import (
    NormalizarAudio,
    filtro_aplicar,
    filtro_medir,
    ler_medicao,
)
from spurgeon.audio.pausas import (
    CalcularCortes,
    DetectarPausas,
    encontrar_pausas,
    resumo_por_limiar,
)
from spurgeon.core.arquivos import ler_json
from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.ffmpeg import executar_ffmpeg, localizar_binario, sondar
from tests.conftest import PAUSAS_DO_AUDIO


def usar_audio(contexto, wav):
    shutil.copy(wav, contexto.pastas.audio_original)


def usar_video(contexto, video):
    contexto.estado.entrada.caminho = str(video)


def loudness_integrado(wav) -> float:
    saida = executar_ffmpeg(["-i", wav, "-af", filtro_medir(-16), "-f", "null", "-"])
    return ler_medicao(saida)["input_i"]


def detectar(contexto):
    """Mede o nível e procura as pausas, como o pipeline faz."""
    contexto.etapa_id = "medir_nivel"
    MedirNivel().executar(contexto)
    DetectarPausas().executar(contexto)


# --- partes puras --------------------------------------------------------------------------


def janelas(*niveis: float, inicio: float = 0.0) -> list[tuple[float, float]]:
    """Medidas de janelas de 50 ms consecutivas com os níveis dados."""
    return [(inicio + i * 0.05, nivel) for i, nivel in enumerate(niveis)]


def test_ler_niveis_pareia_o_tempo_com_o_nivel_e_trata_silencio_digital_como_mudo():
    texto = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.astats.Overall.RMS_level=-45.5\n"
        "frame:1    pts:2400    pts_time:0.05\n"
        "lavfi.astats.Overall.RMS_level=-inf\n"
        "frame:2    pts:4800    pts_time:0.1\n"
        "lavfi.astats.Overall.RMS_level=-20.25\n"
        "frame:3    pts:7200    pts_time:0.15\n"
        "lavfi.astats.Overall.RMS_level=nan\n"
    )
    assert ler_niveis(texto) == [(0.0, -45.5), (0.05, -120.0), (0.1, -20.25)]


def test_nivel_usa_percentis_10_e_90():
    nivel = calcular_nivel(janelas(*([-60.0] * 50 + [-20.0] * 50)))
    assert (nivel.piso_db, nivel.fala_db) == (-60.0, -20.0)


def test_nivel_ignora_as_janelas_de_silencio_digital():
    medidas = janelas(*([-120.0] * 100 + [-60.0] * 50 + [-20.0] * 50))
    nivel = calcular_nivel(medidas)
    assert (nivel.piso_db, nivel.fala_db) == (-60.0, -20.0)  # o piso é o ruído real


def test_nivel_com_pouco_audio_da_erro_claro():
    with pytest.raises(ErroSpurgeon):
        calcular_nivel(janelas(*([-40.0] * 5)))


def test_encontrar_pausas_exige_a_duracao_minima():
    # 0,5 s de silêncio (10 janelas), fala, 1 s de silêncio (20 janelas), fala
    medidas = janelas(*([-70.0] * 10 + [-20.0] * 10 + [-70.0] * 20 + [-20.0] * 10))
    pausas = encontrar_pausas(medidas, limiar_db=-55.0, duracao_minima_s=0.8, duracao_total_s=3.0)
    assert len(pausas) == 1
    assert pausas[0] == (pytest.approx(1.0), pytest.approx(2.0))


def test_encontrar_pausas_conta_silencio_digital_e_a_pausa_do_final():
    medidas = janelas(*([-20.0] * 10 + [-120.0] * 40))  # termina em silêncio absoluto
    pausas = encontrar_pausas(medidas, limiar_db=-55.0, duracao_minima_s=0.5, duracao_total_s=2.5)
    assert pausas == [(pytest.approx(0.5), pytest.approx(2.5))]


def test_um_ruido_curto_dentro_da_pausa_a_divide_em_duas():
    medidas = janelas(*([-70.0] * 40 + [-30.0] * 2 + [-70.0] * 40))  # uma tosse no meio
    pausas = encontrar_pausas(medidas, limiar_db=-55.0, duracao_minima_s=0.5, duracao_total_s=5.0)
    assert len(pausas) == 2


def test_o_limiar_vem_do_rms_entao_ruido_com_picos_ainda_conta_como_pausa():
    # Ruído a -66 dB RMS: o limiar fica logo acima dele, não abaixo (como ficaria se
    # comparássemos com picos instantâneos).
    nivel = NivelAudio(piso_db=-66.0, fala_db=-31.5)
    assert -66.0 < limiar_silencio_db(nivel) < -31.5 - 12


@pytest.mark.parametrize(
    ("piso", "fala", "esperado"),
    [
        (-67.0, -25.0, -56.5),  # 25% do espaço entre o piso e a fala
        (-50.0, -45.0, -57.0),  # fala quase igual ao piso: a margem até a fala limita
        (-40.0, -20.0, -35.0),
    ],
)
def test_limiar_de_silencio(piso, fala, esperado):
    assert limiar_silencio_db(NivelAudio(piso, fala)) == pytest.approx(esperado)


def test_resumo_por_limiar_conta_pausas_e_tempo_removido():
    pausas = [(0.0, 1.5), (10.0, 13.0), (20.0, 31.0)]  # 1,5 s, 3 s e 11 s
    resumo = {limiar: (n, s) for limiar, n, s in resumo_por_limiar(pausas, residual_s=1.0)}
    assert resumo[1.0] == (3, pytest.approx(0.5 + 2.0 + 10.0))
    assert resumo[2.0] == (2, pytest.approx(2.0 + 10.0))
    assert resumo[5.0] == (1, pytest.approx(10.0))
    assert resumo[10.0] == (1, pytest.approx(10.0))


def test_resumo_por_limiar_ignora_pausa_menor_que_o_residual():
    assert resumo_por_limiar([(0.0, 0.8)], residual_s=1.0, limiares=(0.5,)) == [(0.5, 0, 0)]


def test_ler_medicao_do_loudnorm():
    saida = (
        "ruído do ffmpeg\n"
        '{\n\t"input_i" : "-13.08",\n\t"input_tp" : "0.00",\n\t"input_lra" : "7.50",\n'
        '\t"input_thresh" : "-23.79",\n\t"target_offset" : "-0.42"\n}\n'
    )
    medicao = ler_medicao(saida)
    assert medicao["input_i"] == -13.08
    assert medicao["target_offset"] == -0.42


def test_medicao_de_audio_mudo_vira_um_numero_baixo():
    assert ler_medicao('{ "input_i" : "-inf", "input_tp" : "-inf" }')["input_i"] == -120.0


def test_sem_medicao_da_erro():
    with pytest.raises(ErroSpurgeon):
        ler_medicao("nada aqui")


def test_segunda_passagem_do_loudnorm_usa_a_medicao_e_ganho_linear():
    medido = {
        "input_i": -13.1, "input_tp": 0.0, "input_lra": 7.5,
        "input_thresh": -23.8, "target_offset": -0.4,
    }  # fmt: skip
    filtro = filtro_aplicar(-16.0, medido)
    assert "measured_I=-13.1" in filtro
    assert "linear=true" in filtro
    assert "I=-16.0" in filtro


# --- extração ------------------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_extrair_gera_wav_48khz_com_a_duracao_do_video(contexto, video_teste):
    usar_video(contexto, video_teste)
    contexto.etapa_id = "extrair_audio"
    ExtrairAudio().executar(contexto)
    info = sondar(contexto.pastas.audio_original)
    assert info.taxa_audio == 48000
    assert info.codec_audio == "pcm_s16le"
    assert info.duracao_s == pytest.approx(20.0, abs=0.3)
    assert not list(contexto.pastas.work.glob("*.parcial"))


def _video_estereo(destino, esquerda: str, direita: str):
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:size=64x64:rate=5:duration=4",
            "-f", "lavfi", "-i", f"sine=frequency={esquerda}:sample_rate=48000:duration=4",
            "-f", "lavfi", "-i", f"sine=frequency={direita}:sample_rate=48000:duration=4",
            "-filter_complex", "[1:a][2:a]amerge=inputs=2[a]", "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(destino),
        ],
        check=True,
    )  # fmt: skip


@pytest.mark.ffmpeg
def test_extrair_audio_dual_mono_vira_mono(contexto, tmp_path):
    video = tmp_path / "dual_mono.mp4"
    _video_estereo(video, "440", "440")  # os dois canais iguais
    usar_video(contexto, video)
    ExtrairAudio().executar(contexto)
    assert sondar(contexto.pastas.audio_original).canais_audio == 1


@pytest.mark.ffmpeg
def test_extrair_estereo_de_verdade_mantem_os_dois_canais(contexto, tmp_path):
    video = tmp_path / "estereo.mp4"
    _video_estereo(video, "440", "880")  # canais diferentes
    usar_video(contexto, video)
    ExtrairAudio().executar(contexto)
    assert sondar(contexto.pastas.audio_original).canais_audio == 2


@pytest.mark.ffmpeg
def test_extrair_de_video_sem_audio_da_erro_claro(contexto, tmp_path):
    video = tmp_path / "mudo.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:size=64x64:rate=5:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", str(video),
        ],
        check=True,
    )  # fmt: skip
    usar_video(contexto, video)
    with pytest.raises(ErroSpurgeon, match=r"não tem trilha de áudio"):
        ExtrairAudio().executar(contexto)


def test_extrair_com_o_video_removido_da_erro_claro(contexto, tmp_path):
    usar_video(contexto, tmp_path / "sumiu.mp4")
    with pytest.raises(ErroSpurgeon, match="não está mais"):
        ExtrairAudio().executar(contexto)


# --- normalização --------------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_normalizar_leva_o_volume_ao_alvo(contexto, audio_com_pausas):
    usar_audio(contexto, audio_com_pausas)
    contexto.opcoes.normalizar_volume = True
    contexto.opcoes.lufs_video_longo = -20.0
    antes = loudness_integrado(contexto.pastas.audio_original)
    assert abs(antes - (-20.0)) > 2  # o ponto de partida está longe do alvo

    NormalizarAudio().executar(contexto)
    depois = loudness_integrado(contexto.pastas.audio_normalizado)
    assert depois == pytest.approx(-20.0, abs=1.0)
    assert sondar(contexto.pastas.audio_normalizado).duracao_s == pytest.approx(60.0, abs=0.1)


@pytest.mark.ffmpeg
def test_normalizar_audio_mudo_mantem_o_volume_e_avisa(contexto, tmp_path):
    mudo = tmp_path / "mudo.wav"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "5", str(mudo),
        ],
        check=True,
    )  # fmt: skip
    usar_audio(contexto, mudo)
    contexto.opcoes.normalizar_volume = True
    NormalizarAudio().executar(contexto)
    assert contexto.pastas.audio_normalizado.exists()
    assert "praticamente mudo" in contexto.pastas.log.read_text(encoding="utf-8")


def test_normalizar_so_roda_se_pedido(contexto):
    contexto.opcoes.normalizar_volume = False
    assert not NormalizarAudio().ativa(contexto.opcoes)
    contexto.opcoes.normalizar_volume = True
    assert NormalizarAudio().ativa(contexto.opcoes)


# --- pausas e cortes -----------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_detecta_as_pausas_conhecidas(contexto, audio_com_pausas):
    usar_audio(contexto, audio_com_pausas)
    contexto.etapa_id = "detectar_pausas"
    detectar(contexto)

    dados = ler_json(contexto.pastas.pausas)
    assert dados["duracao_s"] == pytest.approx(60.0, abs=0.1)
    assert dados["piso_db"] < -50  # ruído de fundo medido
    assert dados["fala_db"] == pytest.approx(-31.5, abs=2)  # o tom do fixture (sine a 1/8)
    achadas = [tuple(p) for p in dados["pausas"]]
    assert len(achadas) == len(PAUSAS_DO_AUDIO)
    for (ini, fim), (ini_esperado, fim_esperado) in zip(achadas, PAUSAS_DO_AUDIO, strict=True):
        assert ini == pytest.approx(ini_esperado, abs=0.3)
        assert fim == pytest.approx(fim_esperado, abs=0.3)
    assert not (contexto.pastas.work / "niveis.txt").exists()  # temporários apagados


@pytest.mark.ffmpeg
def test_cortes_removem_so_as_pausas_acima_do_limiar_deixando_o_residual(
    contexto, audio_com_pausas
):
    usar_audio(contexto, audio_com_pausas)
    detectar(contexto)
    contexto.opcoes.limiar_pausa_s = 10.0
    contexto.opcoes.pausa_residual_s = 1.0
    CalcularCortes().executar(contexto)

    cortes = ler_json(contexto.pastas.cortes)
    assert cortes["pausas_cortadas"] == 2  # a pausa de 3 s fica
    # 12 s e 15 s de pausa, com 1 s mantido em cada: remove 11 s + 14 s
    assert cortes["removido_s"] == pytest.approx(25.0, abs=0.8)
    assert cortes["duracao_editada_s"] == pytest.approx(35.0, abs=0.8)
    assert len(cortes["trechos"]) == 3


@pytest.mark.ffmpeg
def test_limiar_menor_corta_tambem_a_pausa_curta(contexto, audio_com_pausas):
    usar_audio(contexto, audio_com_pausas)
    detectar(contexto)
    contexto.opcoes.limiar_pausa_s = 2.5
    contexto.opcoes.pausa_residual_s = 0.5
    CalcularCortes().executar(contexto)
    assert ler_json(contexto.pastas.cortes)["pausas_cortadas"] == 3


@pytest.mark.ffmpeg
def test_sem_pausas_longas_nao_ha_cortes(contexto, audio_com_pausas):
    usar_audio(contexto, audio_com_pausas)
    detectar(contexto)
    contexto.opcoes.limiar_pausa_s = 30.0
    CalcularCortes().executar(contexto)
    cortes = ler_json(contexto.pastas.cortes)
    assert cortes["pausas_cortadas"] == 0
    assert cortes["trechos"] == [[0.0, 60.0]]
    assert cortes["removido_s"] == 0


def test_etapas_de_pausas_so_rodam_com_o_video_longo(contexto):
    contexto.opcoes.gerar_video_longo = False
    contexto.opcoes.gerar_shorts = True
    assert not DetectarPausas().ativa(contexto.opcoes)
    assert not CalcularCortes().ativa(contexto.opcoes)
