"""Marca d'água: preparar a imagem (fundo transparente) e montar a sobreposição no FFmpeg.

A imagem pode ser PNG com transparência (usada como está), ou qualquer outra (JPG, WebP…, ou PNG
sem transparência). Para estas, a pessoa pode pedir que o fundo seja removido: a cor do fundo é a
da borda da imagem e só some o que está ligado à borda por pixels dessa cor, então o branco do
interior de um logotipo fica. O resultado vira um PNG com canal alfa em `_work/`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from spurgeon.core.config import CantoMarca, OpcoesProcessamento

EXTENSOES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff")
TOLERANCIA_DO_FUNDO = 40  # diferença máxima, em cada canal, para uma cor ainda ser "o fundo"
LADO_MAXIMO_PX = 1200  # o logotipo nunca passa de uns 600 px no vídeo; mais que isso só pesa
MARGEM_PCT = 3.0  # distância da borda do vídeo, em % da largura
_OPACO = 250  # alfa a partir do qual um pixel conta como opaco
_SEMENTE = 128  # valor da máscara para "fundo ligado à borda"

ROTULOS_DOS_CANTOS: dict[CantoMarca, str] = {
    "sup_esq": "Superior esquerdo",
    "sup_dir": "Superior direito",
    "inf_esq": "Inferior esquerdo",
    "inf_dir": "Inferior direito",
}


@dataclass(frozen=True)
class InfoImagem:
    formato: str  # "PNG", "JPEG"…
    largura: int
    altura: int
    tem_transparencia: bool

    @property
    def eh_png(self) -> bool:
        return self.formato == "PNG"


@dataclass(frozen=True)
class MarcaDagua:
    """A marca pronta para um vídeo: o PNG com transparência e onde e quão grande ela vai."""

    arquivo: Path
    canto: CantoMarca
    largura_px: int
    margem_px: int


def tem_transparencia(imagem: Image.Image) -> bool:
    """Alguma parte da imagem é (pelo menos um pouco) transparente?"""
    if imagem.mode in ("RGBA", "LA"):
        return imagem.getchannel("A").getextrema()[0] < _OPACO
    return "transparency" in imagem.info  # PNG indexado ou em tons de cinza com cor transparente


def ler_info(caminho: Path) -> InfoImagem:
    with Image.open(caminho) as imagem:
        return InfoImagem(
            formato=imagem.format or "",
            largura=imagem.width,
            altura=imagem.height,
            tem_transparencia=tem_transparencia(imagem),
        )


def _reduzir(imagem: Image.Image) -> Image.Image:
    maior = max(imagem.size)
    if maior <= LADO_MAXIMO_PX:
        return imagem
    fator = LADO_MAXIMO_PX / maior
    return imagem.resize(
        (max(1, round(imagem.width * fator)), max(1, round(imagem.height * fator))),
        Image.Resampling.LANCZOS,
    )


def _cor_do_fundo(rgb) -> tuple[int, int, int]:
    """A cor mais comum nas bordas da imagem (mediana de cada canal)."""
    import numpy as np

    borda = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    return tuple(int(v) for v in np.median(borda, axis=0))  # type: ignore[return-value]


def remover_fundo(imagem: Image.Image, tolerancia: int = TOLERANCIA_DO_FUNDO) -> Image.Image:
    """Torna transparente o fundo (a cor da borda) e deixa o que sobrar com a borda suave."""
    import numpy as np

    rgba = _reduzir(imagem.convert("RGBA"))
    cores = np.asarray(rgba)[:, :, :3].astype(np.int16)
    fundo = np.array(_cor_do_fundo(cores), dtype=np.int16)
    parecido = (np.abs(cores - fundo).max(axis=2) <= tolerancia).astype(np.uint8) * 255

    mascara = Image.fromarray(parecido).copy()  # a cópia aceita escrita (a de fromarray, não)
    largura, altura = mascara.size
    bordas = [(x, y) for x in range(largura) for y in (0, altura - 1)]
    bordas += [(x, y) for y in range(altura) for x in (0, largura - 1)]
    for ponto in bordas:  # só o fundo LIGADO à borda: o branco de dentro do logotipo não some
        if mascara.getpixel(ponto) == 255:
            ImageDraw.floodfill(mascara, ponto, _SEMENTE)
    ligado_a_borda = np.asarray(mascara) == _SEMENTE

    alfa = np.where(ligado_a_borda, 0, 255).astype(np.uint8)
    # come 1 px da orla (onde sobra uma franja da cor do fundo) e suaviza o contorno
    suave = Image.fromarray(alfa).filter(ImageFilter.MinFilter(3))
    suave = suave.filter(ImageFilter.GaussianBlur(0.8))
    rgba.putalpha(suave)
    return _recortar_vazio(rgba)


def _recortar_vazio(imagem: Image.Image) -> Image.Image:
    """Corta as margens totalmente transparentes (o logotipo encosta nas bordas do vídeo)."""
    caixa = imagem.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return imagem.crop(caixa) if caixa else imagem


def preparar_imagem(imagem: Image.Image, *, remover: bool) -> Image.Image:
    """A marca como vai para o vídeo: RGBA, com o fundo removido se for o caso."""
    if tem_transparencia(imagem):  # a transparência da própria imagem vale sempre
        return _recortar_vazio(_reduzir(imagem.convert("RGBA")))
    if remover:
        return remover_fundo(imagem)
    return _reduzir(imagem.convert("RGBA"))


def imagem_da_marca(origem: Path, *, remover: bool) -> Image.Image:
    with Image.open(origem) as aberta:
        aberta.load()
        return preparar_imagem(aberta, remover=remover)


def preparar_marca(origem: Path, destino: Path, *, remover: bool) -> Path:
    """Grava em `destino` o PNG com transparência que será sobreposto ao vídeo."""
    pronta = imagem_da_marca(origem, remover=remover)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".parcial")
    pronta.save(temporario, format="PNG")
    temporario.replace(destino)
    return destino


def marca_para_o_projeto(pasta_work: Path, opcoes: OpcoesProcessamento) -> Path:
    """O PNG da marca para estas opções; refeito só quando a imagem ou a escolha muda."""
    origem = Path(opcoes.marca_imagem)
    estatistica = origem.stat()
    chave = hashlib.sha1(
        f"{origem.resolve()}|{estatistica.st_size}|{int(estatistica.st_mtime)}|"
        f"{opcoes.marca_remover_fundo}".encode()
    ).hexdigest()[:12]
    destino = pasta_work / f"marca_{chave}.png"
    if not destino.exists():
        preparar_marca(origem, destino, remover=opcoes.marca_remover_fundo)
    return destino


def montar_marca(arquivo: Path, opcoes: OpcoesProcessamento, largura_do_video: int) -> MarcaDagua:
    """Tamanho e margem em pixels para um vídeo desta largura."""
    largura = max(8, round(largura_do_video * opcoes.marca_tamanho_pct / 100 / 2) * 2)
    margem = max(2, round(largura_do_video * MARGEM_PCT / 100))
    return MarcaDagua(arquivo, opcoes.marca_canto, largura, margem)


def posicao(canto: CantoMarca, margem_px: int) -> tuple[str, str]:
    """Expressões x e y do filtro `overlay` para o canto escolhido."""
    x = f"{margem_px}" if canto.endswith("esq") else f"W-w-{margem_px}"
    y = f"{margem_px}" if canto.startswith("sup") else f"H-h-{margem_px}"
    return x, y


def posicao_em_pixels(
    canto: CantoMarca, largura: int, altura: int, largura_marca: int, altura_marca: int, margem: int
) -> tuple[int, int]:
    """Onde fica o canto superior esquerdo da marca (a mesma conta do filtro `overlay`)."""
    x = margem if canto.endswith("esq") else largura - largura_marca - margem
    y = margem if canto.startswith("sup") else altura - altura_marca - margem
    return x, y


def trecho_do_filtro(marca: MarcaDagua, *, base: str, entrada: str, saida: str) -> str:
    """Filtros que redimensionam a marca (`entrada`, ex.: `1:v`) e a põem sobre `base`.

    `base` e `saida` são rótulos sem colchetes; o FFmpeg repete o único quadro do PNG pelo tempo
    todo do vídeo.
    """
    x, y = posicao(marca.canto, marca.margem_px)
    return (
        f"[{entrada}]scale={marca.largura_px}:-1:flags=lanczos,format=rgba[marca_{saida}];"
        f"[{base}][marca_{saida}]overlay={x}:{y}:format=auto[{saida}]"
    )


def assinatura_da_marca(arquivo: Path, marca: MarcaDagua) -> list:
    """O que muda o resultado do vídeo: o PNG pronto (conteúdo), o canto, o tamanho e a margem."""
    conteudo = hashlib.sha1(arquivo.read_bytes()).hexdigest()[:12]
    return [conteudo, marca.canto, marca.largura_px, marca.margem_px]
