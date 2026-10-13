"""Fase 5: filtros, planejamento das partes, legendas ASS e as etapas de render (FFmpeg real)."""

import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.config import OpcoesProcessamento, raiz_repositorio
from spurgeon.core.estado import EntradaVideo
from spurgeon.core.ffmpeg import InfoMidia, localizar_binario, sondar
from spurgeon.core.tempo import Trecho, calcular_trechos_mantidos
from spurgeon.render import filtros as f
from spurgeon.render import video_longo as vl
from spurgeon.render.finalizar import Finalizar, fim_da_ultima_legenda
from spurgeon.render.legenda import (
    EstiloLegenda,
    PalavraDaLegenda,
    agrupar_em_frases,
    montar_ass,
    montar_eventos,
    palavras_do_short,
    posicao_da_quebra,
)
from spurgeon.render.shorts import (
    RenderizarShorts,
    filtro_de_audio,
    filtro_de_video,
    planejar_shorts,
    texto_de_publicacao,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import gravar_cortes
from spurgeon.transcricao.formato import Palavra
from tests.conftest import PAUSAS_DO_AUDIO

FPS = Fraction(30000, 1001)


def info(**campos) -> InfoMidia:
    base = {
        "caminho": Path("x.mov"),
        "duracao_s": 60.0,
        "tem_video": True,
        "tem_audio": True,
        "largura": 1920,
        "altura": 1080,
        "fps": 29.97,
        "pix_fmt": "yuv420p10le",
        "transferencia": "arib-std-b67",
        "primarias": "bt2020",
        "matriz": "bt2020nc",
    }
    return InfoMidia(**{**base, **campos})


# --- filtros -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (29.97, Fraction(30000, 1001)),
        (30.0, Fraction(30)),
        (25.0, Fraction(25)),
        (23.976, Fraction(24000, 1001)),
        (59.94, Fraction(60000, 1001)),
        (10.0, Fraction(10)),
        (None, Fraction(30000, 1001)),
        (0, Fraction(30000, 1001)),
    ],
)
def test_taxa_de_quadros_constante(entrada, esperado):
    assert f.fps_constante(entrada) == esperado


def test_texto_da_taxa_de_quadros():
    assert f.texto_fps(FPS) == "30000/1001"


def test_origem_hdr_hlg_passa_por_luz_linear_e_tone_mapping():
    cadeia = f.cadeia_para_sdr(info())
    assert "tin=arib-std-b67" in cadeia and "tonemap=tonemap=hable" in cadeia
    assert cadeia.endswith("format=yuv420p")
    assert "t=bt709:m=bt709" in cadeia


def test_origem_hdr10_usa_a_curva_pq():
    assert "tin=smpte2084" in f.cadeia_para_sdr(info(transferencia="smpte2084"))


def test_origem_sdr_so_garante_o_formato_de_pixel():
    cadeia = f.cadeia_para_sdr(info(transferencia="bt709", pix_fmt="yuv420p"))
    assert cadeia.startswith("format=yuv420p,setparams=") and "zscale" not in cadeia
    assert not info(transferencia=None).hdr and info().hdr


def test_recorte_vertical_central_de_um_video_1080p():
    assert f.recorte_vertical(info()) == "crop=608:1080:656:0"


def test_recorte_vertical_com_deslocamento_nunca_sai_da_imagem():
    assert f.recorte_vertical(info(), 10.0) == "crop=608:1080:848:0"
    assert f.recorte_vertical(info(), -10.0) == "crop=608:1080:464:0"
    assert f.recorte_vertical(info(), 30.0) == "crop=608:1080:1232:0"  # 656 + 576
    assert f.recorte_vertical(info(largura=700), 30.0).endswith(":92:0")  # colado na borda


def test_recorte_de_video_ja_vertical_nao_corta_nada_importante():
    assert f.recorte_vertical(info(largura=1080, altura=1920)) == "crop=1080:1920:0:0"


def test_argumentos_do_codec_seguem_a_qualidade_escolhida():
    maxima = f.argumentos_video(OpcoesProcessamento(), threads=9)
    assert maxima[maxima.index("-preset") + 1] == "veryslow"
    assert maxima[maxima.index("-crf") + 1] == "16"
    assert maxima[maxima.index("-threads") + 1] == "9"
    assert maxima[maxima.index("-pix_fmt") :][:2] == ["-pix_fmt", "yuv420p"]
    rapido = f.argumentos_video(OpcoesProcessamento(qualidade="rapido"))
    assert rapido[rapido.index("-preset") + 1] == "veryfast" and "-threads" not in rapido
    nvenc = f.argumentos_video(OpcoesProcessamento(), nvenc=True)
    assert "h264_nvenc" in nvenc and "-preset" in nvenc and "libx264" not in nvenc


def test_audio_mono_vira_estereo_sem_perder_volume():
    assert "pan=stereo|c0=c0|c1=c0" in f.filtro_audio_estereo(1)
    assert "pan" not in f.filtro_audio_estereo(2)
    assert f.argumentos_audio()[-4:] == ["-ar", "48000", "-ac", "2"]


def test_numero_para_o_ffmpeg_sem_zeros_nem_notacao_cientifica():
    assert f.numero_ffmpeg(3.5) == "3.5"
    assert f.numero_ffmpeg(2.0) == "2"
    assert f.numero_ffmpeg(0.0000001) == "0"
    assert f.numero_ffmpeg(1234.567891) == "1234.567891"


# --- planejamento do vídeo longo ------------------------------------------------------------


def trechos_de(*pares) -> list[Trecho]:
    return [Trecho(a, b) for a, b in pares]


def test_pedacos_do_intervalo_atravessam_os_cortes():
    trechos = trechos_de((0, 10), (20, 30), (40, 50))
    assert vl.pedacos_do_intervalo(trechos, 0, 30) == ((0, 10), (20, 30), (40, 50))
    assert vl.pedacos_do_intervalo(trechos, 5, 15) == ((5, 10), (20, 25))
    assert vl.pedacos_do_intervalo(trechos, 12, 14) == ((22, 24),)
    assert vl.pedacos_do_intervalo(trechos, 30, 40) == ()


def test_partes_tem_quadros_exatos_e_cobrem_o_video_todo():
    trechos = trechos_de((0, 600), (700, 1500), (1600, 1805.5))
    partes = vl.planejar_partes(trechos, FPS, 600.0)
    total = sum(t.duracao for t in trechos)
    assert sum(p.quadros for p in partes) == round(total * 30000 / 1001)
    assert [p.indice for p in partes] == list(range(len(partes)))
    for p in partes:
        coberto = sum(b - a for a, b in p.pedacos)
        assert coberto == pytest.approx(p.quadros * 1001 / 30000, abs=0.05)
    assert len(partes) == 3  # 1605 s em partes de ~600 s
    assert max(p.quadros for p in partes) - min(p.quadros for p in partes) <= 1


def test_partes_tem_tamanho_parecido_sem_sobra_minuscula():
    partes = vl.planejar_partes(trechos_de((0, 1250)), FPS, 600.0)
    assert len(partes) == 2
    assert vl.planejar_partes(trechos_de((0, 5)), FPS, 600.0)[0].quadros == round(5 * 30000 / 1001)


def test_video_curto_vira_uma_parte_so():
    assert len(vl.planejar_partes(trechos_de((0, 100)), FPS, 600.0)) == 1


def test_roteiro_de_uma_parte_com_um_trecho_so():
    parte = vl.planejar_partes(trechos_de((10, 20)), FPS, 600.0)[0]
    roteiro = vl.script_da_parte(parte, 9.5, info(), FPS)
    assert "split" not in roteiro and "concat" not in roteiro
    assert "[0:v]fps=30000/1001,trim=start=0.5:end=10.5,setpts=PTS-STARTPTS[bruto]" in roteiro
    assert roteiro.rstrip().endswith("tpad=stop_mode=clone:stop_duration=2[vout]")
    assert "tonemap" in roteiro


def test_roteiro_de_varios_trechos_divide_corta_e_junta():
    parte = vl.planejar_partes(trechos_de((0, 10), (20, 30), (40, 50)), FPS, 600.0)[0]
    roteiro = vl.script_da_parte(parte, 0.0, info(transferencia="bt709"), FPS)
    assert "split=3[s0][s1][s2]" in roteiro
    assert roteiro.count("trim=start=") == 3
    assert "[p0][p1][p2]concat=n=3:v=1:a=0[bruto]" in roteiro
    assert "tonemap" not in roteiro


def test_roteiro_da_introducao_padroniza_o_clipe():
    roteiro = vl.script_de_clipe(info(transferencia="bt709", largura=1280), 1920, 1080, FPS)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in roteiro
    assert "pad=1920:1080" in roteiro and "setsar=1" in roteiro and "fps=30000/1001" in roteiro


def test_lista_do_audio_tem_um_trecho_por_corte_e_escapa_aspas(tmp_path):
    lista = vl.lista_do_audio(tmp_path / "o'audio.wav", trechos_de((1, 2.5), (4, 5)))
    linhas = lista.splitlines()
    assert len(linhas) == 6
    assert linhas[1] == "inpoint 1.000000" and linhas[2] == "outpoint 2.500000"
    assert r"o'\''audio.wav" in linhas[0]


def test_roteiro_do_audio_com_introducao_e_vinheta_sem_som():
    def clipe(tem_audio):
        return vl._Clipe(Path("c.mp4"), info(tem_audio=tem_audio, canais_audio=2), 60, "c.mp4")

    roteiro = vl.script_do_audio(
        intro=clipe(True), vinheta=clipe(False), quadros_principais=300, fps=FPS,
        canais=1, normalizar=True, lufs=-16.0,
    )  # fmt: skip
    assert "[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0" in roteiro
    assert "[1:a]" in roteiro and "loudnorm=I=-16" in roteiro
    assert "anullsrc=r=48000:cl=stereo" in roteiro  # vinheta muda ganha silêncio
    assert "[ai][am][av]concat=n=3:v=0:a=1[aout]" in roteiro
    assert f"atrim=duration={float(Fraction(300) / FPS):.6f}".rstrip("0") in roteiro


def test_roteiro_do_audio_sem_extras_so_tem_o_principal():
    roteiro = vl.script_do_audio(
        intro=None, vinheta=None, quadros_principais=30, fps=Fraction(30), canais=2,
        normalizar=False, lufs=-16.0,
    )  # fmt: skip
    assert roteiro.count("atrim") == 1 and "concat=n=1:v=0:a=1[aout]" in roteiro


def test_estimativa_de_tamanho_cresce_com_a_qualidade():
    assert vl.estimar_tamanho_bytes(3600, 16) > vl.estimar_tamanho_bytes(3600, 22) * 1.9
    assert 3e9 < vl.estimar_tamanho_bytes(3900, 16) < 5e9


# --- legendas ASS ---------------------------------------------------------------------------


def pal(texto, inicio, fim=None) -> PalavraDaLegenda:
    return PalavraDaLegenda(texto, inicio, inicio + 0.3 if fim is None else fim)


def fala(texto: str, inicio=0.0, passo=0.4) -> list[PalavraDaLegenda]:
    return [pal(t, inicio + i * passo) for i, t in enumerate(texto.split())]


def test_palavras_do_short_contam_o_tempo_desde_o_inicio_do_corte():
    palavras = [Palavra("antes", 9, 9.5), Palavra("dentro", 10.2, 10.6), Palavra("fim", 11.8, 12.4)]
    achadas = palavras_do_short(palavras, 10.0, 12.0)
    assert [p.texto for p in achadas] == ["dentro", "fim"]
    assert achadas[0].inicio == pytest.approx(0.2)
    assert achadas[1].fim == pytest.approx(2.0)  # cortada no fim do Short


def test_frase_curta_cabe_numa_linha_e_a_longa_vira_duas_equilibradas():
    assert posicao_da_quebra(["Deus", "é", "bom"], 24) is None
    textos = ["Substituíram", "o", "profeta", "por", "um", "falso"]
    quebra = posicao_da_quebra(textos, 24)
    assert quebra is not None
    assert len(" ".join(textos[:quebra])) <= 24 and len(" ".join(textos[quebra:])) <= 24


def test_grupos_terminam_no_fim_da_frase_e_nas_pausas_longas():
    palavras = fala("Deus é fiel. Ele cumpre") + fala("o que prometeu", inicio=10.0)
    grupos = agrupar_em_frases(palavras)
    assert [" ".join(p.texto for p in g) for g in grupos] == [
        "Deus é fiel.",
        "Ele cumpre",  # a pausa de 6 s separa o grupo seguinte
        "o que prometeu",
    ]


def test_grupo_nunca_passa_da_capacidade_de_duas_linhas():
    palavras = fala("palavra " * 30)
    for grupo in agrupar_em_frases(palavras):
        assert len(" ".join(p.texto for p in grupo)) <= 48


def test_eventos_destacam_uma_palavra_por_vez_e_nao_se_sobrepoem():
    palavras = fala("A graça de Deus basta")
    eventos = montar_eventos(palavras)
    assert len(eventos) == 5
    for i, (ini, fim, texto) in enumerate(eventos):
        assert texto.count(r"{\1c&H0000FFFF&}") == 1
        assert palavras[i].texto in texto.split(r"{\1c&H0000FFFF&}")[1].split("{")[0]
        assert fim > ini
        if i:
            assert ini >= eventos[i - 1][1] - 1e-9
    assert eventos[-1][1] == pytest.approx(palavras[-1].fim + 0.2)


def test_ultima_palavra_nao_invade_o_grupo_seguinte():
    palavras = fala("um dois três.") + fala("quatro cinco seis.", inicio=1.6)
    eventos = montar_eventos(palavras)
    primeiro_do_segundo = next(i for i, e in enumerate(eventos) if "quatro" in e[2])
    assert eventos[primeiro_do_segundo - 1][1] <= palavras[3].inicio + 1e-9


def test_ass_completo_tem_estilo_posicao_e_formato_de_tempo():
    ass = montar_ass(fala("Graça e paz"), EstiloLegenda(posicao_vertical=0.62))
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Karaoke,Montserrat ExtraBold,74,&H00FFFFFF" in ass
    assert r"{\an5\pos(540,1190)}" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:00.40,Karaoke" in ass
    assert ass.endswith("\n")


def test_texto_perigoso_para_o_ass_e_limpo():
    ass = montar_ass([pal(r"a{\b1}b", 0.0)])
    assert r"\b1" not in ass.split("Dialogue")[1]


def test_posicao_vertical_muda_o_y_da_legenda():
    assert r"\pos(540,960)" in montar_ass(fala("oi"), EstiloLegenda(posicao_vertical=0.5))


# --- planejamento dos Shorts ----------------------------------------------------------------


def corte(id_, inicio, fim=None, **campos) -> Corte:
    base = {
        "id": id_, "inicio": inicio, "fim": inicio + 40 if fim is None else fim,
        "texto": "t", "categoria": "outra", "nota": 80.0, "detalhe": {},
        "titulo": f"Título {id_}", "descricao": "Descrição.", "hashtags": ["#shorts", "#fé"],
        "slug": f"corte_{id_}",
    }  # fmt: skip
    return Corte(**{**base, **campos})


def test_shorts_sao_numerados_pela_ordem_no_sermao_e_so_os_marcados():
    cortes = [corte(1, 900), corte(2, 100), corte(3, 500, incluir=False), corte(4, 300)]
    planejados = planejar_shorts(cortes)
    assert [(s.numero, s.corte.id) for s in planejados] == [(1, 2), (2, 4), (3, 1)]
    assert planejados[0].video == "short_01_corte_2.mp4"
    assert planejados[0].texto == "short_01_corte_2.txt"


def test_texto_de_publicacao_traz_titulo_descricao_e_hashtags():
    texto = texto_de_publicacao(corte(1, 603.0, alerta_contexto="pode soar legalista"))
    assert texto.startswith("Título: Título 1\n")
    assert "Descrição:\nDescrição.\n" in texto
    assert "Hashtags: #shorts #fé" in texto
    assert "10:03 a 10:43 (40 s)" in texto and "Nota de qualidade: 80 de 100" in texto
    assert "Atenção (contexto): pode soar legalista" in texto


def test_filtro_de_video_do_short_segue_a_ordem_recorte_sdr_ampliacao_legenda():
    cadeia = filtro_de_video(info(), FPS, deslocamento_pct=0.0, legenda="legendas/s.ass")
    ordem = ["fps=30000/1001", "crop=608:1080", "zscale", "scale=1080:1920:flags=lanczos"]
    posicoes = [cadeia.index(item) for item in ordem]
    assert posicoes == sorted(posicoes)
    assert cadeia.index("setsar=1") < cadeia.index("ass=legendas/s.ass:fontsdir=fontes")
    assert cadeia.endswith("format=yuv420p")
    assert "ass=" not in filtro_de_video(info(), FPS, deslocamento_pct=0.0, legenda=None)


def test_filtro_de_audio_do_short_tem_fade_nas_pontas_e_volume_medido():
    medido = {"input_i": -20.0, "input_tp": -3.0, "input_lra": 5.0, "input_thresh": -30.0,
              "target_offset": 0.5}  # fmt: skip
    cadeia = filtro_de_audio(1, 40.0, medido, -14.0)
    assert "loudnorm=I=-14.0" in cadeia and "measured_I=-20.0" in cadeia
    assert "afade=t=in:st=0:d=0.1" in cadeia and "afade=t=out:st=39.9:d=0.1" in cadeia
    assert "loudnorm" not in filtro_de_audio(2, 40.0, None, -14.0)


# --- com FFmpeg de verdade ------------------------------------------------------------------


@pytest.fixture(scope="session")
def video_hdr(tmp_path_factory) -> Path:
    """Vídeo de 3 s em HEVC 10 bits marcado como HDR HLG (BT.2020), como o do iPhone."""
    destino = tmp_path_factory.mktemp("hdr") / "hdr.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30000/1001:duration=3",
            "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params",
            "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error",
            str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


@pytest.mark.ffmpeg
def test_o_ffprobe_reconhece_o_hdr(video_hdr, video_teste):
    hdr = sondar(video_hdr)
    assert hdr.hdr and hdr.transferencia == "arib-std-b67" and hdr.primarias == "bt2020"
    assert not sondar(video_teste).hdr


def preparar_longo(contexto, video: Path, audio: Path, **opcoes):
    for chave, valor in opcoes.items():
        setattr(contexto.opcoes, chave, valor)
    contexto.opcoes.qualidade = "rapido"
    contexto.estado.entrada = EntradaVideo.de_arquivo(video)
    contexto.pastas.work.mkdir(parents=True, exist_ok=True)
    contexto.pastas.audio_original.write_bytes(audio.read_bytes())
    trechos = calcular_trechos_mantidos(60.0, PAUSAS_DO_AUDIO, 10.0, 1.0)
    escrever_json_atomico(
        contexto.pastas.cortes,
        {"trechos": [[round(t.inicio, 3), round(t.fim, 3)] for t in trechos]},
    )
    return sum(t.duracao for t in trechos)


def rodar(etapa, ctx):
    ctx.etapa_id = etapa.id
    return etapa.executar(ctx)


@pytest.mark.ffmpeg
def test_video_longo_em_partes_tem_duracao_e_quadros_exatos(
    contexto, video_com_pausas, audio_com_pausas, monkeypatch
):
    esperado = preparar_longo(contexto, video_com_pausas, audio_com_pausas)  # 35 s
    monkeypatch.setattr(vl, "DURACAO_DA_PARTE_S", 12.0)
    rodar(vl.RenderizarVideoLongo(), contexto)

    final = sondar(contexto.pastas.video_longo)
    assert final.tem_audio and (final.largura, final.altura) == (160, 120)
    assert final.duracao_s == pytest.approx(esperado, abs=0.15)
    assert final.canais_audio == 2 and final.taxa_audio == 48000
    assert final.pix_fmt == "yuv420p" and final.transferencia == "bt709"
    assert not (contexto.pastas.work / "render_longo").exists()  # as partes foram apagadas
    assert not list(contexto.pastas.raiz.glob("*.parcial"))


@pytest.mark.ffmpeg
def test_video_longo_hdr_sai_em_sdr_bt709(contexto, video_hdr, audio_com_pausas):
    preparar_longo(contexto, video_hdr, audio_com_pausas)
    escrever_json_atomico(contexto.pastas.cortes, {"trechos": [[0.0, 2.5]]})
    rodar(vl.RenderizarVideoLongo(), contexto)
    final = sondar(contexto.pastas.video_longo)
    assert not final.hdr and final.transferencia == "bt709" and final.primarias == "bt709"
    assert final.pix_fmt == "yuv420p"
    assert final.duracao_s == pytest.approx(2.5, abs=0.15)


@pytest.mark.ffmpeg
def test_video_longo_com_introducao_e_vinheta(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    esperado = preparar_longo(
        contexto, video_com_pausas, audio_com_pausas, introducao=True, vinheta_final=True
    )
    contexto.config.pastas.recursos.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(localizar_binario("ffmpeg"))
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(  # introdução 3 s, 1280x720, com som
        [*base, "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(contexto.config.pastas.introducao)],
        check=True,
    )  # fmt: skip
    subprocess.run(  # vinheta 2 s, 4:3, sem som
        [*base, "-f", "lavfi", "-i", "smptebars=size=640x480:rate=24:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(contexto.config.pastas.vinheta_final)],
        check=True,
    )  # fmt: skip
    rodar(vl.RenderizarVideoLongo(), contexto)
    final = sondar(contexto.pastas.video_longo)
    assert (final.largura, final.altura) == (160, 120)
    assert final.duracao_s == pytest.approx(esperado + 3 + 2, abs=0.3)
    assert final.tem_audio


@pytest.mark.ffmpeg
def test_introducao_marcada_mas_ausente_da_erro_claro(contexto, video_com_pausas, audio_com_pausas):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas, introducao=True)
    with pytest.raises(Exception, match="introdução está marcada"):
        rodar(vl.RenderizarVideoLongo(), contexto)


@pytest.mark.ffmpeg
def test_render_interrompido_reaproveita_as_partes_prontas(
    contexto, video_com_pausas, audio_com_pausas, monkeypatch
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    monkeypatch.setattr(vl, "DURACAO_DA_PARTE_S", 12.0)
    chamadas = []
    original = vl.RenderizarVideoLongo._unir

    def falha_na_uniao(self, *args, **kwargs):
        chamadas.append(1)
        raise RuntimeError("queda de energia")

    monkeypatch.setattr(vl.RenderizarVideoLongo, "_unir", falha_na_uniao)
    with pytest.raises(RuntimeError):
        rodar(vl.RenderizarVideoLongo(), contexto)
    pasta = contexto.pastas.work / "render_longo"
    prontas = {p.name: p.stat().st_mtime_ns for p in pasta.glob("parte_*.mp4")}
    assert len(prontas) == 3 and (pasta / "audio.m4a").exists()

    monkeypatch.setattr(vl.RenderizarVideoLongo, "_unir", original)
    rodar(vl.RenderizarVideoLongo(), contexto)
    assert contexto.pastas.video_longo.exists()
    assert chamadas == [1]


@pytest.mark.ffmpeg
def test_mudar_a_qualidade_recomeca_o_render(
    contexto, video_com_pausas, audio_com_pausas, monkeypatch
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    monkeypatch.setattr(vl, "DURACAO_DA_PARTE_S", 12.0)
    monkeypatch.setattr(vl.RenderizarVideoLongo, "_unir", lambda *_a, **_k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        rodar(vl.RenderizarVideoLongo(), contexto)
    pasta = contexto.pastas.work / "render_longo"
    antes = (pasta / "assinatura.txt").read_text()
    contexto.opcoes.qualidade = "equilibrado"
    with pytest.raises(ZeroDivisionError):
        rodar(vl.RenderizarVideoLongo(), contexto)
    assert (pasta / "assinatura.txt").read_text() != antes


def test_falta_de_espaco_em_disco_avisa_antes_de_renderizar(contexto, monkeypatch):
    import shutil
    from collections import namedtuple

    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Uso(10**12, 10**12 - 10**9, 10**9))
    with pytest.raises(Exception, match="Falta espaço em disco"):
        vl.RenderizarVideoLongo()._conferir_disco(contexto, 3900.0, 16)


# --- Shorts ---------------------------------------------------------------------------------


@pytest.fixture
def ctx_shorts(contexto, video_hdr, audio_com_pausas, tmp_path):
    """Contexto de Shorts sobre o vídeo HDR de 3 s, com fonte e vocabulário do repositório."""
    contexto.opcoes.gerar_shorts = True
    contexto.opcoes.qualidade = "rapido"
    contexto.opcoes.legendas_dinamicas = True
    contexto.opcoes.normalizar_volume = True
    contexto.estado.entrada = EntradaVideo.de_arquivo(video_hdr)
    contexto.config.pastas.recursos = raiz_repositorio() / "recursos"
    contexto.pastas.work.mkdir(parents=True, exist_ok=True)
    contexto.pastas.audio_normalizado.write_bytes(audio_com_pausas.read_bytes())
    palavras = [
        Palavra("Graça", 0.1, 0.5), Palavra("e", 0.6, 0.7), Palavra("paz,", 0.8, 1.2),
        Palavra("irmãos.", 1.4, 1.9),
    ]  # fmt: skip
    from spurgeon.transcricao.formato import Bloco, Segmento, Transcricao

    Transcricao("t", 3.0, blocos=[Bloco(0, 3, [Segmento(palavras)], True)]).salvar(
        contexto.pastas.transcricao
    )
    return contexto


@pytest.mark.ffmpeg
def test_short_sai_vertical_com_legenda_volume_e_texto(ctx_shorts):
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    etapa = RenderizarShorts()
    rodar(etapa, ctx_shorts)

    video = ctx_shorts.pastas.shorts / "short_01_corte_1.mp4"
    info_final = sondar(video)
    assert (info_final.largura, info_final.altura) == (1080, 1920)
    assert info_final.pix_fmt == "yuv420p" and info_final.transferencia == "bt709"
    assert info_final.duracao_s == pytest.approx(2.5, abs=0.2)
    assert info_final.canais_audio == 2 and info_final.taxa_audio == 48000
    texto = (ctx_shorts.pastas.shorts / "short_01_corte_1.txt").read_text(encoding="utf-8")
    assert "Título: Título 1" in texto
    assert etapa.artefatos(ctx_shorts) == [video]
    ass = (ctx_shorts.pastas.work / "legendas" / "short_01_corte_1.ass").read_text(encoding="utf-8")
    assert "Graça" in ass and "irmãos." in ass
    assert (ctx_shorts.pastas.work / "fontes" / "Montserrat-ExtraBold.ttf").exists()


@pytest.mark.ffmpeg
def test_short_sem_legenda_dinamica_nao_queima_texto(ctx_shorts):
    ctx_shorts.opcoes.legendas_dinamicas = False
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    rodar(RenderizarShorts(), ctx_shorts)
    assert (ctx_shorts.pastas.shorts / "short_01_corte_1.mp4").exists()
    assert not (ctx_shorts.pastas.work / "legendas" / "short_01_corte_1.ass").exists()


@pytest.mark.ffmpeg
def test_short_pronto_nao_e_refeito_mas_o_texto_e_atualizado(ctx_shorts):
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    rodar(RenderizarShorts(), ctx_shorts)
    video = ctx_shorts.pastas.shorts / "short_01_corte_1.mp4"
    marca = video.stat().st_mtime_ns

    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5, titulo="Outro título")])
    rodar(RenderizarShorts(), ctx_shorts)
    assert video.stat().st_mtime_ns == marca
    assert "Outro título" in video.with_suffix(".txt").read_text(encoding="utf-8")

    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.0, titulo="Outro título")])
    rodar(RenderizarShorts(), ctx_shorts)
    assert video.stat().st_mtime_ns != marca  # o corte mudou: refaz


@pytest.mark.ffmpeg
def test_short_desmarcado_some_da_pasta(ctx_shorts):
    gravar_cortes(
        ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5), corte(2, 0.5, 3.0, slug="outro")]
    )
    rodar(RenderizarShorts(), ctx_shorts)
    nomes = sorted(p.name for p in ctx_shorts.pastas.shorts.iterdir())
    assert nomes == [
        "short_01_corte_1.mp4", "short_01_corte_1.txt",
        "short_02_outro.mp4", "short_02_outro.txt",
    ]  # fmt: skip
    gravar_cortes(
        ctx_shorts.pastas.cortes_shorts,
        [corte(1, 0.0, 2.5), corte(2, 0.5, 3.0, slug="outro", incluir=False)],
    )
    rodar(RenderizarShorts(), ctx_shorts)
    assert sorted(p.name for p in ctx_shorts.pastas.shorts.iterdir()) == [
        "short_01_corte_1.mp4",
        "short_01_corte_1.txt",
    ]


@pytest.mark.ffmpeg
def test_sem_cortes_aprovados_nao_gera_nada(ctx_shorts):
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5, incluir=False)])
    rodar(RenderizarShorts(), ctx_shorts)
    assert not ctx_shorts.pastas.shorts.exists() or not list(ctx_shorts.pastas.shorts.iterdir())


# --- conferência final ----------------------------------------------------------------------


def test_fim_da_ultima_legenda():
    srt = "1\n00:00:01,000 --> 00:00:03,500\nOi\n\n2\n00:01:02,250 --> 00:01:04,000\nTchau\n"
    assert fim_da_ultima_legenda(srt) == (2, 64.0)
    assert fim_da_ultima_legenda("") == (0, 0.0)


def srt_ate(segundos: float) -> str:
    return f"1\n00:00:00,500 --> 00:00:{segundos:06.3f}\nOi\n".replace(".", ",")


@pytest.mark.ffmpeg
def test_conferencia_final_aprova_o_video_longo_e_registra_o_resumo(
    contexto, video_com_pausas, audio_com_pausas, caplog
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    escrever_json_atomico(
        contexto.pastas.cortes, {"trechos": [[0.0, 6.0]], "duracao_editada_s": 6.0}
    )
    rodar(vl.RenderizarVideoLongo(), contexto)
    contexto.pastas.srt.write_text(srt_ate(5.0), encoding="utf-8")
    with caplog.at_level("INFO"):
        rodar(Finalizar(), contexto)
    assert "video_longo_otimizado.mp4" in caplog.text and "1 legendas" in caplog.text


@pytest.mark.ffmpeg
def test_conferencia_final_recusa_legenda_depois_do_fim_do_video(
    contexto, video_com_pausas, audio_com_pausas
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    escrever_json_atomico(
        contexto.pastas.cortes, {"trechos": [[0.0, 6.0]], "duracao_editada_s": 6.0}
    )
    rodar(vl.RenderizarVideoLongo(), contexto)
    contexto.pastas.srt.write_text(srt_ate(30.0), encoding="utf-8")
    with pytest.raises(Exception, match="depois do fim do vídeo"):
        rodar(Finalizar(), contexto)


def test_conferencia_final_sem_video_longo_da_erro(contexto):
    contexto.pastas.srt.write_text(srt_ate(5.0), encoding="utf-8")
    with pytest.raises(Exception, match="vídeo longo não foi gerado"):
        rodar(Finalizar(), contexto)


@pytest.mark.ffmpeg
def test_conferencia_final_dos_shorts(ctx_shorts, caplog):
    ctx_shorts.opcoes.gerar_video_longo = False
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    rodar(RenderizarShorts(), ctx_shorts)
    with caplog.at_level("INFO"):
        rodar(Finalizar(), ctx_shorts)
    assert "1 Shorts em shorts_gerados/" in caplog.text

    (ctx_shorts.pastas.shorts / "short_01_corte_1.mp4").unlink()
    with pytest.raises(Exception, match=r"short_01_corte_1.mp4 não foi gerado"):
        rodar(Finalizar(), ctx_shorts)


def test_a_conferencia_so_roda_se_algo_foi_gerado():
    assert Finalizar().ativa(OpcoesProcessamento(gerar_video_longo=True))
    assert Finalizar().ativa(OpcoesProcessamento(gerar_video_longo=False, gerar_shorts=True))
    assert not Finalizar().ativa(
        OpcoesProcessamento.model_construct(gerar_video_longo=False, gerar_shorts=False)
    )


def test_o_peso_da_renderizacao_acompanha_a_qualidade():
    maxima = vl.RenderizarVideoLongo().peso_para(OpcoesProcessamento(qualidade="maxima"))
    rapido = vl.RenderizarVideoLongo().peso_para(OpcoesProcessamento(qualidade="rapido"))
    assert maxima > 3 * rapido
    curtos = RenderizarShorts().peso_para(OpcoesProcessamento(qualidade="maxima"))
    assert curtos == pytest.approx(0.3 * maxima)
    personalizado = OpcoesProcessamento(qualidade="personalizado", x264_preset_personalizado="slow")
    assert vl.RenderizarVideoLongo().peso_para(personalizado) == 49.0


def test_as_etapas_de_render_vem_depois_de_toda_a_analise_e_revisao():
    from spurgeon.core.etapas import etapas_registradas

    ids = [e.id for e in etapas_registradas()]
    for anterior in ("revisar_transcricao", "aprovar_cortes", "gerar_srt", "selecionar_shorts"):
        assert ids.index(anterior) < ids.index("renderizar_shorts")
        assert ids.index(anterior) < ids.index("renderizar_video_longo")
    assert ids[-1] == "finalizar"
