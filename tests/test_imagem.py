"""Enquadramento (zoom), tratamento de imagem, sugestão automática e as prévias do vídeo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.ffmpeg import localizar_binario, sondar
from spurgeon.render import filtros as f
from spurgeon.render import previa
from spurgeon.render.imagem import (
    filtro_ampliar_zoom,
    filtro_recorte_do_zoom,
    filtro_tratamento,
    sugerir_tratamento,
)

# --- enquadramento --------------------------------------------------------------------------


def test_sem_zoom_a_regiao_e_o_quadro_inteiro():
    e = Enquadramento()
    assert not e.ativo
    assert e.regiao(1920, 1080) == (0, 0, 1920, 1080)
    assert filtro_recorte_do_zoom(1920, 1080, e) is None
    assert filtro_ampliar_zoom(1920, 1080, e) is None


def test_zoom_de_125_centrado_recorta_o_meio():
    e = Enquadramento(zoom_pct=125)
    assert e.regiao(1920, 1080) == (192, 108, 1536, 864)
    assert filtro_recorte_do_zoom(1920, 1080, e) == "crop=1536:864:192:108"
    assert filtro_ampliar_zoom(1920, 1080, e) == "scale=1920:1080:flags=lanczos,setsar=1"


def test_o_recorte_nunca_sai_do_quadro_e_as_medidas_sao_pares():
    for cx, cy in ((0, 0), (100, 100), (54, 50), (3, 97)):
        e = Enquadramento(zoom_pct=137, centro_x_pct=cx, centro_y_pct=cy)
        x, y, w, h = e.regiao(1920, 1080)
        assert x >= 0 and x + w <= 1920 and y >= 0 and y + h <= 1080
        assert all(v % 2 == 0 for v in (x, y, w, h))
    perto_da_borda = Enquadramento(zoom_pct=200, centro_x_pct=0, centro_y_pct=0)
    assert perto_da_borda.regiao(1920, 1080)[:2] == (0, 0)
    assert perto_da_borda.centro_valido(1920, 1080) == pytest.approx((25.0, 25.0))


def test_o_centro_do_zoom_desloca_o_recorte():
    e = Enquadramento(zoom_pct=150, centro_x_pct=54)
    x, _, w, _ = e.regiao(1920, 1080)
    assert (x + w / 2) / 1920 == pytest.approx(0.54, abs=0.002)


def test_recorte_vertical_dos_shorts_sai_de_dentro_do_zoom(video_hdr):
    info = sondar(video_hdr)  # 640x360
    sem_zoom = f.recorte_vertical(info, 0.0)
    assert sem_zoom == "crop=202:360:218:0"
    com_zoom = f.recorte_vertical(info, 0.0, Enquadramento(zoom_pct=200))
    # o recorte do zoom tem 320x180; o 9:16 dentro dele tem 102x180, no meio (x = 160 + 108)
    assert com_zoom == "crop=102:180:268:90"
    assert f.recorte_vertical(info, 0.0, Enquadramento()) == sem_zoom


# --- tratamento: filtros --------------------------------------------------------------------


def test_tratamento_neutro_nao_gera_filtro():
    assert TratamentoImagem().neutro
    assert filtro_tratamento(TratamentoImagem()) is None


def test_cada_ajuste_aparece_no_filtro():
    assert "brightness=0.125" in filtro_tratamento(TratamentoImagem(brilho=50))
    assert "contrast=1.25" in filtro_tratamento(TratamentoImagem(contraste=50))
    assert "saturation=1.3" in filtro_tratamento(TratamentoImagem(saturacao=30))
    assert "lutyuv=y=" in filtro_tratamento(TratamentoImagem(sombras=40))
    assert "lutyuv=y=" in filtro_tratamento(TratamentoImagem(brancos=-40))
    quente = filtro_tratamento(TratamentoImagem(temperatura=50))
    assert "u='clip(val+(-7)" in quente and "v='clip(val+(7)" in quente
    assert "eq=" not in quente  # só o que foi mexido entra


def test_valores_fora_da_faixa_sao_recusados():
    with pytest.raises(ValueError):
        TratamentoImagem(brilho=101)
    with pytest.raises(ValueError):
        Enquadramento(zoom_pct=99)


# --- tratamento: o FFmpeg de verdade --------------------------------------------------------


def _imagem(tmp_path: Path, nome: str, cor: str, tamanho="64x64") -> Path:
    destino = tmp_path / nome
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={cor}:s={tamanho}:d=1", "-frames:v", "1",
            "-vf", "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:"
            "color_trc=bt709:range=tv", "-c:v", "rawvideo", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


def _medias(png: Path) -> tuple[float, float, float]:
    with Image.open(png) as imagem:
        r, g, b = ImageStat.Stat(imagem.convert("RGB")).mean
    return r, g, b


@pytest.mark.ffmpeg
def test_o_ffmpeg_aceita_todos_os_ajustes_nos_extremos(tmp_path):
    base = _imagem(tmp_path, "cinza.nut", "gray")
    for sinal in (-100, 100):
        extremo = TratamentoImagem(**dict.fromkeys(TratamentoImagem.model_fields, sinal))
        saida = previa.quadro_para_png(base, tmp_path / f"extremo_{sinal}.png", tratamento=extremo)
        assert saida.exists() and Image.open(saida).size == (64, 64)


@pytest.mark.ffmpeg
def test_brilho_contraste_e_cor_mexem_na_imagem_como_o_nome_diz(tmp_path):
    base = _imagem(tmp_path, "meio.nut", "0x707070")
    neutro = _medias(
        previa.quadro_para_png(base, tmp_path / "n.png", tratamento=TratamentoImagem())
    )
    mais = _medias(
        previa.quadro_para_png(base, tmp_path / "b.png", tratamento=TratamentoImagem(brilho=60))
    )
    menos = _medias(
        previa.quadro_para_png(base, tmp_path / "m.png", tratamento=TratamentoImagem(brilho=-60))
    )
    assert menos[0] < neutro[0] < mais[0]
    quente = _medias(
        previa.quadro_para_png(
            base, tmp_path / "q.png", tratamento=TratamentoImagem(temperatura=80)
        )
    )
    frio = _medias(
        previa.quadro_para_png(
            base, tmp_path / "c.png", tratamento=TratamentoImagem(temperatura=-80)
        )
    )
    assert quente[0] > quente[2] and frio[2] > frio[0]  # vermelho > azul ao esquentar
    assert abs(neutro[0] - neutro[2]) < 3  # cinza continua cinza
    verde = _medias(
        previa.quadro_para_png(base, tmp_path / "v.png", tratamento=TratamentoImagem(matiz=-80))
    )
    magenta = _medias(
        previa.quadro_para_png(base, tmp_path / "g.png", tratamento=TratamentoImagem(matiz=80))
    )
    assert verde[1] > magenta[1]


@pytest.mark.ffmpeg
def test_sombras_abrem_o_escuro_e_brancos_baixam_o_claro(tmp_path):
    escuro = _imagem(tmp_path, "escuro.nut", "0x202020")
    claro = _imagem(tmp_path, "claro.nut", "0xf0f0f0")
    antes = _medias(previa.quadro_para_png(escuro, tmp_path / "e0.png"))[0]
    abertas = _medias(
        previa.quadro_para_png(escuro, tmp_path / "e1.png", tratamento=TratamentoImagem(sombras=80))
    )[0]
    assert abertas > antes + 8
    antes_claro = _medias(previa.quadro_para_png(claro, tmp_path / "c0.png"))[0]
    recuperado = _medias(
        previa.quadro_para_png(claro, tmp_path / "c1.png", tratamento=TratamentoImagem(brancos=-80))
    )[0]
    assert recuperado < antes_claro - 8
    # as sombras quase não mexem num branco, e os brancos quase não mexem num preto
    intacto = _medias(
        previa.quadro_para_png(claro, tmp_path / "c2.png", tratamento=TratamentoImagem(sombras=80))
    )[0]
    assert abs(intacto - antes_claro) < 4


# --- sugestão automática --------------------------------------------------------------------


def _cheia(cor, tamanho=(80, 60)) -> Image.Image:
    return Image.new("RGB", tamanho, cor)


def _gradiente(minimo: int, maximo: int, cor=(1.0, 1.0, 1.0)) -> Image.Image:
    imagem = Image.new("RGB", (256, 16))
    for x in range(256):
        v = minimo + (maximo - minimo) * x / 255
        imagem.paste(tuple(round(v * c) for c in cor), (x, 0, x + 1, 16))
    return imagem


def test_sugestao_de_imagem_equilibrada_fica_quase_neutra():
    equilibrada = _gradiente(10, 225)
    sugerido = sugerir_tratamento([equilibrada])
    assert sugerido.contraste <= 12 and sugerido.brilho == 0 and sugerido.brancos == 0
    assert sugerido.sombras <= 15
    assert abs(sugerido.temperatura) <= 3 and abs(sugerido.matiz) <= 3


def test_sugestao_para_imagem_lavada_aumenta_o_contraste():
    lavada = _gradiente(90, 170)
    sugerido = sugerir_tratamento([lavada])
    assert sugerido.contraste >= 20


def test_sugestao_para_imagem_escura_clareia():
    escura = _gradiente(0, 90)
    sugerido = sugerir_tratamento([escura])
    assert sugerido.brilho > 10 or sugerido.sombras > 10


def test_sugestao_para_brancos_estourados_recupera_os_brancos():
    estourada = _gradiente(120, 255)
    assert sugerir_tratamento([estourada]).brancos < 0


def test_sugestao_desfaz_dominante_de_cor_e_reforca_cor_apagada():
    amarelada = _gradiente(20, 235, cor=(1.0, 0.95, 0.7))
    sugerido = sugerir_tratamento([amarelada])
    assert sugerido.temperatura < 0  # esfria o que está quente
    sem_cor = sugerir_tratamento([_gradiente(15, 240)])
    assert sem_cor.saturacao > 0  # nada de cor: reforça


def test_sugestao_sem_quadros_e_neutra_e_respeita_os_limites():
    assert sugerir_tratamento([]).neutro
    extremo = sugerir_tratamento([_cheia((0, 0, 0)), _cheia((255, 255, 255))])
    assert all(abs(v) <= 100 for v in extremo.model_dump().values())


# --- quadros e prévias do vídeo -------------------------------------------------------------


@pytest.mark.ffmpeg
def test_extrai_quadro_sdr_de_video_hdr_e_vira_png(tmp_path, video_hdr):
    info = sondar(video_hdr)
    assert info.hdr
    nut = previa.extrair_quadro(video_hdr, info, 1.0, tmp_path / "q.nut", largura=320)
    png = previa.quadro_para_png(nut, tmp_path / "q.png")
    with Image.open(png) as imagem:
        assert imagem.size == (320, 180)
    assert sondar(nut).largura == 320


@pytest.mark.ffmpeg
def test_previa_de_15_segundos_do_comeco_e_do_fim(tmp_path, video_teste):
    info = sondar(video_teste)  # 20 s
    comeco = previa.gerar_previa(video_teste, info, 2.0, tmp_path / "ini.mp4", duracao_s=15)
    assert sondar(comeco).duracao_s == pytest.approx(15.0, abs=0.5)
    fim = previa.gerar_previa(video_teste, info, 5.0, tmp_path / "fim.mp4", duracao_s=15)
    assert sondar(fim).duracao_s == pytest.approx(15.0, abs=0.5)
    assert sondar(fim).tem_audio and sondar(fim).largura == 640


@pytest.mark.ffmpeg
def test_previa_leva_o_zoom_e_o_tratamento(tmp_path, video_teste):
    info = sondar(video_teste)
    saida = previa.gerar_previa(
        video_teste, info, 0.0, tmp_path / "estilo.mp4", duracao_s=2,
        enquadramento=Enquadramento(zoom_pct=150, centro_x_pct=60),
        tratamento=TratamentoImagem(brilho=20, temperatura=30, brancos=-20),
    )  # fmt: skip
    assert sondar(saida).duracao_s == pytest.approx(2.0, abs=0.5)


def test_instantes_de_exemplo_ficam_dentro_do_video():
    instantes = previa.instantes_de_exemplo(100.0, 4)
    assert instantes == [20.0, 40.0, 60.0, 80.0]
    assert previa.instantes_de_exemplo(0) == [0.0]


@pytest.mark.ffmpeg
def test_o_cinza_continua_no_mesmo_nivel_ao_virar_png(tmp_path):
    base = _imagem(tmp_path, "meio.nut", "0x707070")  # 112 de 255
    r, g, b = _medias(previa.quadro_para_png(base, tmp_path / "n.png"))
    assert r == pytest.approx(112, abs=4) and g == pytest.approx(112, abs=4)
    assert b == pytest.approx(112, abs=4)


@pytest.mark.ffmpeg
def test_o_quadro_da_tela_e_o_do_arquivo_final_saem_iguais(tmp_path, video_teste):
    """A prévia do tratamento (quadro .nut + filtro) e o vídeo renderizado dão a mesma imagem."""
    info = sondar(video_teste)
    tratamento = TratamentoImagem(
        brilho=15, contraste=20, saturacao=-30, temperatura=25, matiz=-10, brancos=-25, sombras=30
    )
    nut = previa.extrair_quadro(video_teste, info, 5.0, tmp_path / "q.nut", largura=320)
    da_tela = previa.quadro_para_png(nut, tmp_path / "tela.png", tratamento=tratamento)
    clipe = previa.gerar_previa(
        video_teste, info, 5.0, tmp_path / "p.mp4", duracao_s=1, tratamento=tratamento, largura=320
    )
    final = tmp_path / "final.png"
    subprocess.run(
        [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(clipe), "-frames:v", "1", str(final)],
        check=True,
    )  # fmt: skip
    sem_tratar = _medias(previa.quadro_para_png(nut, tmp_path / "sem.png"))
    tela, arquivo = _medias(da_tela), _medias(final)
    for canal in range(3):
        assert tela[canal] == pytest.approx(arquivo[canal], abs=6)
    assert any(abs(tela[c] - sem_tratar[c]) > 8 for c in range(3))  # o tratamento se nota mesmo
