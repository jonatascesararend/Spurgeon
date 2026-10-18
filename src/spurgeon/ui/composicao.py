"""Imagens das prévias (só PIL, sem janela): o antes e depois e o zoom com a área sombreada."""

from __future__ import annotations

from PIL import Image, ImageDraw

from spurgeon.core.aparencia import Enquadramento

_COR_DIVISAO = (255, 255, 255)
_COR_MOLDURA = (255, 213, 74)
_ESCURECER = 0.62  # quanto da luz some na área que o zoom corta


def ajustar_ao_tamanho(imagem: Image.Image, tamanho: tuple[int, int]) -> Image.Image:
    """A imagem reduzida (ou ampliada) para caber em `tamanho`, sem mudar a proporção."""
    largura, altura = tamanho
    escala = min(largura / imagem.width, altura / imagem.height)
    novo = (max(1, round(imagem.width * escala)), max(1, round(imagem.height * escala)))
    return imagem.convert("RGB").resize(novo, Image.Resampling.LANCZOS)


def compor_antes_depois(
    original: Image.Image, tratado: Image.Image, fracao: float, *, com_alca: bool = True
) -> Image.Image:
    """O original à esquerda e o tratado à direita, separados por uma linha em `fracao` (0 a 1).

    As duas imagens precisam ter o mesmo tamanho (o tratado sai do original pelo FFmpeg).
    """
    original = original.convert("RGB")
    tratado = tratado.convert("RGB")
    if tratado.size != original.size:
        tratado = tratado.resize(original.size, Image.Resampling.LANCZOS)
    x = round(min(max(fracao, 0.0), 1.0) * original.width)
    quadro = tratado.copy()
    if x > 0:
        quadro.paste(original.crop((0, 0, x, original.height)), (0, 0))
    desenho = ImageDraw.Draw(quadro)
    desenho.line([(x, 0), (x, original.height)], fill=_COR_DIVISAO, width=2)
    if com_alca:
        meio, raio = original.height // 2, 11
        desenho.ellipse(
            (x - raio, meio - raio, x + raio, meio + raio), fill=_COR_DIVISAO, outline=(60, 60, 60)
        )
        desenho.polygon([(x - 6, meio), (x - 2, meio - 4), (x - 2, meio + 4)], fill=(60, 60, 60))
        desenho.polygon([(x + 6, meio), (x + 2, meio - 4), (x + 2, meio + 4)], fill=(60, 60, 60))
    return quadro


def sombrear_fora_do_zoom(imagem: Image.Image, enquadramento: Enquadramento) -> Image.Image:
    """O quadro com o que o zoom corta escurecido e uma moldura em volta do que fica."""
    imagem = imagem.convert("RGB")
    if not enquadramento.ativo:
        return imagem
    x, y, w, h = enquadramento.regiao(imagem.width, imagem.height)
    escuro = Image.eval(imagem, lambda v: int(v * (1 - _ESCURECER)))
    escuro.paste(imagem.crop((x, y, x + w, y + h)), (x, y))
    ImageDraw.Draw(escuro).rectangle((x, y, x + w - 1, y + h - 1), outline=_COR_MOLDURA, width=2)
    return escuro


def centro_pelo_clique(
    x: float, y: float, largura_exibida: float, altura_exibida: float
) -> tuple[float, float]:
    """Um clique na prévia → o centro do zoom em % do quadro (0 a 100)."""
    return (
        min(max(x / max(largura_exibida, 1), 0.0), 1.0) * 100.0,
        min(max(y / max(altura_exibida, 1), 0.0), 1.0) * 100.0,
    )
