"""Marca d'água: imagem com ou sem fundo, posição, tamanho e a sobreposição no vídeo."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.estado import EntradaVideo
from spurgeon.core.ffmpeg import localizar_binario, sondar
from spurgeon.render import marca as m
from spurgeon.render import video_longo as vl
from spurgeon.render.shorts import RenderizarShorts, filtro_de_video
from spurgeon.shorts.etapas import gravar_cortes
from tests.test_render import (
    FPS,
    corte,
    info,
    preparar_longo,
    rodar,
)

# --- imagens de teste ---------------------------------------------------------------------------


def logotipo(fundo=(255, 255, 255), lado=100) -> Image.Image:
    """Quadrado preto com um buraco branco no meio, sobre um fundo liso."""
    img = Image.new("RGB", (lado, lado), fundo)
    desenho = ImageDraw.Draw(img)
    desenho.rectangle((30, 30, 70, 70), fill=(0, 0, 0))
    desenho.rectangle((45, 45, 55, 55), fill=fundo)  # o "miolo" tem a cor do fundo
    return img


@pytest.fixture
def png_transparente(tmp_path) -> Path:
    img = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((10, 10, 70, 50), fill=(200, 30, 30, 255))
    caminho = tmp_path / "logo_transparente.png"
    img.save(caminho)
    return caminho


@pytest.fixture
def jpg_com_fundo(tmp_path) -> Path:
    caminho = tmp_path / "logo.jpg"
    logotipo().save(caminho, quality=95)
    return caminho


@pytest.fixture
def png_sem_transparencia(tmp_path) -> Path:
    caminho = tmp_path / "logo_opaco.png"
    logotipo(fundo=(200, 220, 255)).save(caminho)
    return caminho


# --- leitura e fundo -------------------------------------------------------------------------


def test_png_com_transparencia_e_reconhecido(png_transparente):
    info_ = m.ler_info(png_transparente)
    assert info_.eh_png and info_.tem_transparencia
    assert (info_.largura, info_.altura) == (80, 60)


def test_jpg_e_png_sem_transparencia_pedem_a_opcao_de_tirar_o_fundo(
    jpg_com_fundo, png_sem_transparencia
):
    jpg = m.ler_info(jpg_com_fundo)
    assert not jpg.eh_png and not jpg.tem_transparencia
    opaco = m.ler_info(png_sem_transparencia)
    assert opaco.eh_png and not opaco.tem_transparencia


def test_remover_fundo_apaga_so_o_que_esta_ligado_a_borda():
    pronta = m.remover_fundo(logotipo())
    assert pronta.mode == "RGBA"
    # cortou as margens vazias: sobra aproximadamente o quadrado preto (41 px)
    assert 38 <= pronta.width <= 46 and 38 <= pronta.height <= 46
    centro = (pronta.width // 2, pronta.height // 2)
    assert pronta.getpixel(centro)[3] == 255  # o miolo branco do logotipo continua opaco
    assert pronta.getpixel((pronta.width // 2, 6))[3] == 255  # e o preto também
    assert pronta.getpixel((0, 0))[3] < 120  # o canto é contorno suave de um fundo que sumiu


def test_remover_fundo_colorido_e_com_ruido_de_compressao(jpg_com_fundo, tmp_path):
    azul = tmp_path / "azul.jpg"
    logotipo(fundo=(120, 170, 230)).save(azul, quality=70)  # o JPEG borra e suja o fundo
    with Image.open(azul) as img:
        pronta = m.remover_fundo(img)
    assert pronta.getpixel((pronta.width // 2, pronta.height // 2))[3] == 255
    transparentes = sum(1 for a in pronta.getchannel("A").tobytes() if a == 0)
    assert transparentes < pronta.width * pronta.height * 0.25  # só restou a beirada do contorno


def test_preparar_marca_usa_a_transparencia_do_png_como_esta(png_transparente, tmp_path):
    destino = m.preparar_marca(png_transparente, tmp_path / "saida.png", remover=True)
    with Image.open(destino) as img:
        assert img.mode == "RGBA"
        assert img.getpixel((img.width // 2, img.height // 2)) == (200, 30, 30, 255)
        assert img.size == (61, 41)  # as margens transparentes foram cortadas


def test_preparar_marca_sem_pedir_para_tirar_o_fundo_mantem_a_imagem(jpg_com_fundo, tmp_path):
    destino = m.preparar_marca(jpg_com_fundo, tmp_path / "s.png", remover=False)
    with Image.open(destino) as img:
        assert img.size == (100, 100) and img.getpixel((0, 0))[3] == 255


def test_preparar_marca_tirando_o_fundo_do_jpg(jpg_com_fundo, tmp_path):
    destino = m.preparar_marca(jpg_com_fundo, tmp_path / "s.png", remover=True)
    with Image.open(destino) as img:
        assert img.size[0] < 60 and img.mode == "RGBA"


def test_imagem_muito_grande_e_reduzida(tmp_path):
    grande = tmp_path / "grande.png"
    Image.new("RGB", (3000, 1500), (255, 255, 255)).save(grande)
    destino = m.preparar_marca(grande, tmp_path / "s.png", remover=False)
    with Image.open(destino) as img:
        assert max(img.size) == m.LADO_MAXIMO_PX


def test_a_marca_pronta_do_projeto_e_reaproveitada_e_refeita_se_a_escolha_muda(
    jpg_com_fundo, tmp_path
):
    work = tmp_path / "_work"
    opcoes = OpcoesProcessamento(marca_dagua=True, marca_imagem=str(jpg_com_fundo))
    primeira = m.marca_para_o_projeto(work, opcoes)
    marca_de_tempo = primeira.stat().st_mtime_ns
    assert m.marca_para_o_projeto(work, opcoes) == primeira
    assert primeira.stat().st_mtime_ns == marca_de_tempo
    opcoes.marca_remover_fundo = True
    assert m.marca_para_o_projeto(work, opcoes) != primeira


# --- posição e tamanho -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("canto", "x", "y"),
    [
        ("sup_esq", "30", "30"),
        ("sup_dir", "W-w-30", "30"),
        ("inf_esq", "30", "H-h-30"),
        ("inf_dir", "W-w-30", "H-h-30"),
    ],
)
def test_posicao_nos_quatro_cantos(canto, x, y):
    assert m.posicao(canto, 30) == (x, y)


def test_tamanho_e_margem_acompanham_a_largura_do_video():
    opcoes = OpcoesProcessamento(marca_dagua=True, marca_imagem="x.png", marca_tamanho_pct=15.0)
    longo = m.montar_marca(Path("m.png"), opcoes, 1920)
    curto = m.montar_marca(Path("m.png"), opcoes, 1080)
    assert (longo.largura_px, curto.largura_px) == (288, 162)
    assert longo.largura_px % 2 == 0 and curto.largura_px % 2 == 0
    assert longo.margem_px == 58 and curto.margem_px == 32  # 3% da largura


def test_o_filtro_redimensiona_a_marca_e_a_poe_no_canto():
    opcoes = OpcoesProcessamento(marca_dagua=True, marca_imagem="x.png", marca_canto="sup_esq")
    marca = m.montar_marca(Path("m.png"), opcoes, 1920)
    filtro = m.trecho_do_filtro(marca, base="base", entrada="1:v", saida="marcado")
    assert filtro.startswith("[1:v]scale=288:-1:flags=lanczos,format=rgba[marca_marcado];")
    assert "[base][marca_marcado]overlay=58:58:format=auto[marcado]" in filtro


def test_assinatura_da_marca_muda_com_o_conteudo_o_canto_e_o_tamanho(png_transparente, tmp_path):
    base = OpcoesProcessamento(marca_dagua=True, marca_imagem="x.png")
    a = m.montar_marca(png_transparente, base, 1920)
    b = m.montar_marca(png_transparente, base.model_copy(update={"marca_canto": "sup_dir"}), 1920)
    c = m.montar_marca(png_transparente, base.model_copy(update={"marca_tamanho_pct": 30}), 1920)
    assinaturas = {str(m.assinatura_da_marca(png_transparente, x)) for x in (a, b, c)}
    assert len(assinaturas) == 3


def test_o_grafo_do_short_poe_a_marca_depois_da_ampliacao_e_antes_da_legenda():
    opcoes = OpcoesProcessamento(marca_dagua=True, marca_imagem="x.png")
    marca = m.montar_marca(Path("m.png"), opcoes, 1080)
    grafo = filtro_de_video(info(), FPS, deslocamento_pct=0.0, legenda="l.ass", marca=marca)
    assert grafo.index("scale=1080:1920") < grafo.index("[2:v]") < grafo.index("ass=l.ass")
    assert grafo.endswith("[v]") and "[marcado]ass=l.ass" in grafo


def test_o_roteiro_do_video_longo_poe_a_marca_depois_do_sdr():
    opcoes = OpcoesProcessamento(marca_dagua=True, marca_imagem="x.png")
    marca = m.montar_marca(Path("m.png"), opcoes, 1920)
    parte = vl.planejar_partes([vl.Trecho(0, 10)], FPS, 600.0)[0]
    roteiro = vl.script_da_parte(parte, 0.0, info(), FPS, marca)
    assert roteiro.index("tonemap") < roteiro.index("overlay") < roteiro.index("tpad")
    assert "[1:v]scale=288" in roteiro and roteiro.rstrip().endswith("[vout]")


# --- com FFmpeg de verdade ---------------------------------------------------------------------


def _cor_no_ponto(arquivo: Path, instante: float, x: int, y: int) -> tuple[int, int, int]:
    """A cor (RGB) de um pixel de um quadro do vídeo."""
    import subprocess

    bruto = subprocess.run(
        [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-ss",
         str(instante), "-i", str(arquivo), "-frames:v", "1", "-vf", f"crop=2:2:{x}:{y}",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return bruto[0], bruto[1], bruto[2]


@pytest.fixture(scope="session")
def video_cinza(tmp_path_factory) -> Path:
    """Vídeo liso (cinza) de 6 s: sobre ele, qualquer vermelho é da marca d'água."""
    import subprocess

    destino = tmp_path_factory.mktemp("cinza") / "cinza.mp4"
    subprocess.run(
        [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=0x808080:s=160x120:r=10:d=6", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(destino)],
        check=True,
    )  # fmt: skip
    return destino


@pytest.fixture
def logo_vermelho(tmp_path) -> Path:
    img = Image.new("RGBA", (200, 100), (255, 0, 0, 255))  # um retângulo vermelho inteiro
    caminho = tmp_path / "vermelho.png"
    img.save(caminho)
    return caminho


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    ("canto", "ponto_com_marca", "ponto_sem_marca"),
    [
        ("sup_esq", (12, 8), (150, 100)),
        ("sup_dir", (148, 8), (12, 100)),
        ("inf_esq", (12, 112), (150, 10)),
        ("inf_dir", (148, 112), (12, 10)),
    ],
)
def test_video_longo_com_marca_nos_quatro_cantos(
    contexto, video_cinza, audio_com_pausas, logo_vermelho, canto, ponto_com_marca,
    ponto_sem_marca,
):  # fmt: skip
    preparar_longo(
        contexto, video_cinza, audio_com_pausas,
        marca_dagua=True, marca_imagem=str(logo_vermelho), marca_canto=canto,
        marca_tamanho_pct=20.0,
    )  # fmt: skip
    contexto.pastas.cortes.write_text('{"trechos": [[0.0, 3.0]]}', encoding="utf-8")
    rodar(vl.RenderizarVideoLongo(), contexto)
    saida = contexto.pastas.video_longo
    assert sondar(saida).duracao_s == pytest.approx(3.0, abs=0.3)
    r, g, b = _cor_no_ponto(saida, 1.0, *ponto_com_marca)
    assert r > 150 and g < 90 and b < 90  # o vermelho da marca (com perda do codec)
    r2, g2, b2 = _cor_no_ponto(saida, 1.0, *ponto_sem_marca)
    assert not (r2 > 150 and g2 < 90 and b2 < 90)  # o resto do quadro não é vermelho


@pytest.mark.ffmpeg
def test_video_longo_sem_a_marca_nos_longos_nao_leva_a_marca(
    contexto, video_cinza, audio_com_pausas, logo_vermelho
):
    preparar_longo(
        contexto, video_cinza, audio_com_pausas,
        marca_dagua=True, marca_imagem=str(logo_vermelho), marca_no_video_longo=False,
        marca_canto="sup_esq", marca_tamanho_pct=20.0,
    )  # fmt: skip
    contexto.pastas.cortes.write_text('{"trechos": [[0.0, 3.0]]}', encoding="utf-8")
    rodar(vl.RenderizarVideoLongo(), contexto)
    r, g, b = _cor_no_ponto(contexto.pastas.video_longo, 1.0, 12, 8)
    assert not (r > 150 and g < 90 and b < 90)


@pytest.mark.ffmpeg
def test_trocar_a_marca_recomeca_o_render_do_video_longo(
    contexto, video_com_pausas, audio_com_pausas, logo_vermelho, monkeypatch
):
    preparar_longo(
        contexto, video_com_pausas, audio_com_pausas,
        marca_dagua=True, marca_imagem=str(logo_vermelho),
    )  # fmt: skip
    contexto.pastas.cortes.write_text('{"trechos": [[0.0, 3.0]]}', encoding="utf-8")
    monkeypatch.setattr(vl.RenderizarVideoLongo, "_unir", lambda *_a, **_k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        rodar(vl.RenderizarVideoLongo(), contexto)
    pasta = contexto.pastas.work / "render_longo"
    antes = (pasta / "assinatura.txt").read_text()
    contexto.opcoes.marca_canto = "sup_esq"
    with pytest.raises(ZeroDivisionError):
        rodar(vl.RenderizarVideoLongo(), contexto)
    assert (pasta / "assinatura.txt").read_text() != antes


@pytest.mark.ffmpeg
def test_imagem_da_marca_que_sumiu_da_erro_claro(contexto, video_com_pausas, audio_com_pausas):
    preparar_longo(
        contexto, video_com_pausas, audio_com_pausas,
        marca_dagua=True, marca_imagem="C:/nao/existe/logo.png",
    )  # fmt: skip
    contexto.pastas.cortes.write_text('{"trechos": [[0.0, 3.0]]}', encoding="utf-8")
    with pytest.raises(Exception, match="marca d'água não foi encontrada"):
        rodar(vl.RenderizarVideoLongo(), contexto)


@pytest.mark.ffmpeg
def test_short_com_marca_no_canto_e_trocar_a_marca_refaz_so_o_short(
    ctx_shorts, logo_vermelho, video_cinza
):
    ctx_shorts.estado.entrada = EntradaVideo.de_arquivo(video_cinza)
    ctx_shorts.opcoes.legendas_dinamicas = False
    ctx_shorts.opcoes.marca_dagua = True
    ctx_shorts.opcoes.marca_imagem = str(logo_vermelho)
    ctx_shorts.opcoes.marca_canto = "sup_dir"
    ctx_shorts.opcoes.marca_tamanho_pct = 20.0
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    rodar(RenderizarShorts(), ctx_shorts)
    video = ctx_shorts.pastas.shorts / "short_01_corte_1.mp4"
    # 1080x1920: a marca de 20% (216 px) fica a 3% (32 px) do canto superior direito
    r, g, b = _cor_no_ponto(video, 1.0, 1080 - 32 - 100, 32 + 40)
    assert r > 150 and g < 90 and b < 90
    antes = video.stat().st_mtime_ns

    rodar(RenderizarShorts(), ctx_shorts)  # nada mudou: não renderiza de novo
    assert video.stat().st_mtime_ns == antes
    ctx_shorts.opcoes.marca_canto = "inf_esq"
    rodar(RenderizarShorts(), ctx_shorts)
    assert video.stat().st_mtime_ns != antes
    r, g, b = _cor_no_ponto(video, 1.0, 32 + 100, 1920 - 32 - 40)
    assert r > 150 and g < 90 and b < 90


# --- peças da interface (sem janela) ------------------------------------------------------------


def test_posicao_em_pixels_segue_a_mesma_conta_do_overlay():
    assert m.posicao_em_pixels("sup_esq", 1920, 1080, 288, 100, 58) == (58, 58)
    assert m.posicao_em_pixels("sup_dir", 1920, 1080, 288, 100, 58) == (1920 - 288 - 58, 58)
    assert m.posicao_em_pixels("inf_esq", 1920, 1080, 288, 100, 58) == (58, 1080 - 100 - 58)
    assert m.posicao_em_pixels("inf_dir", 1920, 1080, 288, 100, 58) == (
        1920 - 288 - 58,
        1080 - 100 - 58,
    )


def test_imagem_da_marca_tira_o_fundo_so_quando_pedido(tmp_path):
    arquivo = tmp_path / "logo.jpg"
    logotipo().save(arquivo)
    com_fundo = m.imagem_da_marca(arquivo, remover=False)
    sem_fundo = m.imagem_da_marca(arquivo, remover=True)
    assert com_fundo.getpixel((0, 0))[3] == 255
    assert sem_fundo.getpixel((0, 0))[3] < 128  # o fundo da borda ficou transparente
    assert sem_fundo.getpixel((sem_fundo.width // 2, sem_fundo.height // 2))[3] == 255
    assert sem_fundo.width < com_fundo.width  # cortou as margens vazias


def test_previa_da_posicao_coloca_a_marca_no_canto_escolhido():
    from spurgeon.ui.painel_marca import previa_da_posicao

    marca = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    vazio = (30, 30, 30)  # o fundo do quadro
    for canto, ponto_com_marca in (
        ("sup_esq", (12, 12)),
        ("sup_dir", (180, 12)),
        ("inf_esq", (12, 96)),
        ("inf_dir", (180, 96)),
    ):
        quadro = previa_da_posicao(marca, canto, 15.0)
        assert quadro.size == (192, 108)
        assert quadro.getpixel(ponto_com_marca) == (255, 255, 255), canto
        assert quadro.getpixel((96, 54)) == vazio  # o centro continua vazio


def test_previa_sem_imagem_mostra_um_marcador():
    from spurgeon.ui.painel_marca import previa_da_posicao

    quadro = previa_da_posicao(None, "inf_dir", 20.0)
    assert quadro.getpixel((170, 90)) != (30, 30, 30)


def test_tamanho_da_previa_acompanha_a_porcentagem():
    from spurgeon.ui.painel_marca import previa_da_posicao

    marca = Image.new("RGBA", (10, 10), (255, 255, 255, 255))

    def largura_branca(pct):
        quadro = previa_da_posicao(marca, "sup_esq", pct)
        return sum(1 for x in range(192) if quadro.getpixel((x, 10)) == (255, 255, 255))

    assert largura_branca(30.0) > largura_branca(10.0) * 2


def test_miniatura_sobre_xadrez_mostra_a_transparencia():
    from spurgeon.ui.painel_marca import miniatura_sobre_xadrez

    transparente = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    miniatura = miniatura_sobre_xadrez(transparente, lado=64)
    cores = {miniatura.getpixel((x, y)) for x in range(0, 64, 4) for y in range(0, 64, 4)}
    assert len(cores) == 2  # só o xadrez aparece


def test_texto_do_formato_diz_se_o_fundo_pode_ser_removido():
    from spurgeon.ui.painel_marca import texto_do_formato

    png = m.InfoImagem("PNG", 10, 10, True)
    texto, pode = texto_do_formato(png)
    assert "como está" in texto and pode is False
    jpg = m.InfoImagem("JPEG", 10, 10, False)
    texto, pode = texto_do_formato(jpg)
    assert "Remover o fundo" in texto and pode is True
    png_liso = m.InfoImagem("PNG", 10, 10, False)
    assert "Este PNG não tem fundo transparente" in texto_do_formato(png_liso)[0]
