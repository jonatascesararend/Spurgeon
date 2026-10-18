"""Zoom só dos Shorts (dentro do recorte 9:16) e a prévia do Short com a legenda de exemplo."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from spurgeon.core.aparencia import Enquadramento
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.ffmpeg import executar_ffmpeg, sondar
from spurgeon.render import filtros as f
from spurgeon.render import previa
from spurgeon.render.shorts import assinatura_do_short, assinatura_visual, filtro_de_video
from spurgeon.ui.formulario import (
    ErroFormulario,
    ValoresFormulario,
    para_opcoes,
)
from tests.test_render import corte, info

FPS = Fraction(25)
FONTES = Path(__file__).resolve().parent.parent / "recursos" / "fontes"


# --- o recorte ------------------------------------------------------------------------------


def numeros_do_crop(texto: str) -> tuple[int, int, int, int]:
    """(w, h, x, y) de um `crop=w:h:x:y`."""
    w, h, x, y = (int(v) for v in texto.removeprefix("crop=").split(":"))
    return w, h, x, y


def test_sem_zoom_do_short_o_recorte_e_o_de_sempre():
    base = f.recorte_vertical(info(), 4.0)
    assert f.recorte_vertical(info(), 4.0, None, Enquadramento()) == base
    assert f.recorte_vertical(info(), 4.0, Enquadramento(), Enquadramento()) == base


def test_zoom_do_short_aproxima_dentro_do_recorte_vertical():
    inteiro = numeros_do_crop(f.recorte_vertical(info(), 0.0))  # 608x1080 no meio do quadro
    w0, h0, x0, y0 = inteiro
    e = Enquadramento(zoom_pct=200)
    dx, dy, w, h = e.regiao(w0, h0)
    assert numeros_do_crop(f.recorte_vertical(info(), 0.0, None, e)) == (w, h, x0 + dx, y0 + dy)
    assert w < w0 and h == h0 // 2
    assert w / h == pytest.approx(9 / 16, abs=0.02)  # continua vertical


def test_o_centro_do_zoom_do_short_anda_dentro_do_recorte_e_nunca_sai_dele():
    w0, h0, x0, y0 = numeros_do_crop(f.recorte_vertical(info(), 0.0))
    for cx, cy in ((0, 0), (100, 100), (30, 80)):
        w, h, x, y = numeros_do_crop(
            f.recorte_vertical(info(), 0.0, None, Enquadramento(zoom_pct=170, centro_x_pct=cx,
                                                                centro_y_pct=cy))
        )  # fmt: skip
        assert x0 <= x and x + w <= x0 + w0 and y0 <= y and y + h <= y0 + h0


def test_zoom_do_short_sai_de_dentro_do_zoom_do_video():
    longo = Enquadramento(zoom_pct=150)
    lx, ly, lw, lh = longo.regiao(1920, 1080)
    w0, h0, x0, y0 = numeros_do_crop(f.recorte_vertical(info(), 0.0, longo))
    assert lx <= x0 and x0 + w0 <= lx + lw and ly <= y0 and y0 + h0 <= ly + lh
    w, h, x, y = numeros_do_crop(
        f.recorte_vertical(info(), 0.0, longo, Enquadramento(zoom_pct=140))
    )
    assert x0 <= x and x + w <= x0 + w0 and y0 <= y and y + h <= y0 + h0 and w < w0


def test_video_ja_vertical_tambem_aceita_o_zoom_do_short():
    vertical = info(largura=1080, altura=1920)
    assert f.recorte_vertical(vertical) == "crop=1080:1920:0:0"
    w, h, _x, _y = numeros_do_crop(
        f.recorte_vertical(vertical, 0.0, None, Enquadramento(zoom_pct=200))
    )
    assert (w, h) == (540, 960)


def test_filtro_de_video_do_short_recorta_so_uma_vez_com_os_dois_zooms():
    cadeia = filtro_de_video(
        info(), FPS, deslocamento_pct=4.0, legenda=None,
        enquadramento=Enquadramento(zoom_pct=125), zoom_do_short=Enquadramento(zoom_pct=130),
    )  # fmt: skip
    assert cadeia.count("crop=") == 1
    sem = filtro_de_video(info(), FPS, deslocamento_pct=4.0, legenda=None,
                          enquadramento=Enquadramento(zoom_pct=125))  # fmt: skip
    assert cadeia != sem


# --- as opções ------------------------------------------------------------------------------


def test_opcoes_sem_zoom_do_short_nao_tem_enquadramento_ativo():
    o = OpcoesProcessamento(shorts_zoom_centro_x_pct=20)  # o centro sozinho não quer dizer nada
    assert not o.enquadramento_do_short.ativo
    assert o.enquadramento_do_short == Enquadramento()
    o = OpcoesProcessamento(shorts_zoom_pct=140, shorts_zoom_centro_x_pct=40)
    assert o.enquadramento_do_short == Enquadramento(zoom_pct=140, centro_x_pct=40, centro_y_pct=50)


def test_o_zoom_do_short_e_independente_do_zoom_do_video():
    o = OpcoesProcessamento(zoom_pct=130, zoom_nos_shorts=False, shorts_zoom_pct=120)
    assert not o.enquadramento_nos_curtos.ativo
    assert o.enquadramento_do_short.zoom_pct == 120


def test_assinatura_antiga_continua_valendo_sem_zoom_do_short():
    campos = (
        "shorts_zoom_pct", "shorts_zoom_centro_x_pct", "shorts_zoom_centro_y_pct", "qualidade"
    )  # fmt: skip
    padrao = OpcoesProcessamento()
    assert padrao.assinatura(campos) == padrao.assinatura(["qualidade"])
    centro_solto = OpcoesProcessamento(shorts_zoom_centro_x_pct=10)
    assert centro_solto.assinatura(campos) == padrao.assinatura(campos)
    assert OpcoesProcessamento(shorts_zoom_pct=130).assinatura(campos) != padrao.assinatura(campos)


def test_assinaturas_dos_shorts_so_mudam_quando_ha_zoom_do_short():
    c = corte(1, 0.0, 2.5)
    base = OpcoesProcessamento()
    antes = assinatura_do_short("h", base, c, "bt709", False, None)
    assert assinatura_do_short("h", OpcoesProcessamento(shorts_zoom_centro_y_pct=10), c,
                               "bt709", False, None) == antes  # fmt: skip
    com = OpcoesProcessamento(shorts_zoom_pct=130)
    assert assinatura_do_short("h", com, c, "bt709", False, None) != antes
    assert assinatura_visual(com) != assinatura_visual(base)
    assert assinatura_visual(OpcoesProcessamento(shorts_zoom_centro_x_pct=10)) == (
        assinatura_visual(base)
    )


def test_formulario_leva_o_zoom_e_a_legenda_dos_shorts_para_as_opcoes():
    v = ValoresFormulario(
        gerar_shorts=True, shorts_zoom_pct=140.0, shorts_zoom_centro_x_pct=35.0,
        shorts_zoom_centro_y_pct=60.0, posicao_legenda="70",
    )  # fmt: skip
    o = para_opcoes(v)
    assert (o.shorts_zoom_pct, o.shorts_zoom_centro_x_pct, o.shorts_zoom_centro_y_pct) == (
        140.0, 35.0, 60.0,
    )  # fmt: skip
    assert o.legenda_posicao_vertical == pytest.approx(0.70)
    assert ValoresFormulario.de_opcoes(o).shorts_zoom_pct == 140.0
    assert ValoresFormulario.de_opcoes(o).posicao_legenda == "70"


def test_formulario_recusa_zoom_do_short_fora_da_faixa():
    with pytest.raises(ErroFormulario, match="Zoom dos Shorts"):
        para_opcoes(ValoresFormulario(gerar_shorts=True, shorts_zoom_pct=400.0))
    with pytest.raises(ErroFormulario, match="Posição da legenda"):
        para_opcoes(ValoresFormulario(gerar_shorts=True, posicao_legenda="95"))


# --- a prévia do Short ----------------------------------------------------------------------


@pytest.fixture
def quadro_nut(video_teste, tmp_path):
    media = sondar(video_teste)
    nut = previa.extrair_quadro(video_teste, media, 1.0, tmp_path / "q.nut", largura=320)
    with Image.open(_png(nut, tmp_path)) as aberta:
        return media, nut, aberta.size


def _png(nut: Path, pasta: Path) -> Path:
    return previa.quadro_para_png(nut, pasta / "q.png")


@pytest.mark.ffmpeg
def test_previa_do_short_sai_em_1080x1920_com_a_legenda_de_exemplo(quadro_nut, tmp_path):
    media, nut, tamanho = quadro_nut
    png = previa.previa_do_short(
        nut, media, tamanho, tmp_path / "p" / "short.png",
        deslocamento_pct=0.0, posicao_legenda=0.62, pasta_fontes=FONTES,
    )  # fmt: skip
    with Image.open(png) as aberta:
        assert aberta.size == (f.LARGURA_SHORT, f.ALTURA_SHORT)
    sobras = [p.name for p in png.parent.iterdir()]
    assert "short.ass" not in sobras and not any(n.endswith(".parcial.png") for n in sobras)


@pytest.mark.ffmpeg
def test_a_legenda_de_exemplo_acompanha_a_altura_escolhida(quadro_nut, tmp_path):
    media, nut, tamanho = quadro_nut

    def prever(posicao: float) -> Image.Image:
        png = previa.previa_do_short(
            nut, media, tamanho, tmp_path / f"p{posicao}.png",
            deslocamento_pct=0.0, posicao_legenda=posicao, pasta_fontes=FONTES,
        )  # fmt: skip
        return Image.open(png).convert("RGB")

    sem_legenda = _sem_legenda(nut, media, tamanho, tmp_path)

    def centro_da_legenda(imagem: Image.Image) -> float:
        caixa = (
            ImageChops.difference(imagem, sem_legenda)
            .convert("L")
            .point(lambda v: 255 if v > 40 else 0)
            .getbbox()
        )
        assert caixa is not None, "a legenda de exemplo não apareceu"
        return (caixa[1] + caixa[3]) / 2 / f.ALTURA_SHORT

    alta, baixa = centro_da_legenda(prever(0.40)), centro_da_legenda(prever(0.80))
    assert alta == pytest.approx(0.40, abs=0.03)
    assert baixa == pytest.approx(0.80, abs=0.03)


def _sem_legenda(nut: Path, media, tamanho, pasta: Path) -> Image.Image:
    """O mesmo Short sem a legenda: o recorte e a ampliação do render, só com o FFmpeg."""
    medidas = replace(media, largura=tamanho[0], altura=tamanho[1])
    cadeia = ",".join(
        [
            f.recorte_vertical(medidas, 0.0),
            f"scale={f.LARGURA_SHORT}:{f.ALTURA_SHORT}:flags=lanczos",
            "setsar=1",
            "format=rgb24",
        ]
    )
    destino = pasta / "sem_legenda.png"
    executar_ffmpeg(["-y", "-i", nut, "-frames:v", "1", "-vf", cadeia, "-f", "image2", destino])
    return Image.open(destino).convert("RGB")


@pytest.mark.ffmpeg
def test_zoom_do_short_muda_a_imagem_da_previa(quadro_nut, tmp_path):
    media, nut, tamanho = quadro_nut
    comum = {"deslocamento_pct": 0.0, "posicao_legenda": 0.62, "pasta_fontes": FONTES}
    sem = previa.previa_do_short(nut, media, tamanho, tmp_path / "sem.png", **comum)
    com = previa.previa_do_short(
        nut, media, tamanho, tmp_path / "com.png", zoom_do_short=Enquadramento(zoom_pct=200),
        **comum,
    )  # fmt: skip
    with Image.open(sem) as a, Image.open(com) as b:
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None
        assert b.size == a.size


@pytest.mark.ffmpeg
def test_previa_sem_a_pasta_de_fontes_ainda_sai(quadro_nut, tmp_path):
    media, nut, tamanho = quadro_nut
    png = previa.previa_do_short(
        nut, media, tamanho, tmp_path / "s.png", deslocamento_pct=0.0, posicao_legenda=0.62
    )
    assert png.exists()
