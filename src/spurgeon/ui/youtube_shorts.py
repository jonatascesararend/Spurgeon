"""Os elementos da tela do YouTube Shorts, desenhados por cima da prévia do Short.

No celular o YouTube cobre parte do vídeo: os botões de curtir e compartilhar à direita, o nome do
canal, o botão de inscrever-se e o título embaixo, os ícones no topo. Pôr esses elementos sobre a
prévia mostra se a legenda (ou o pregador) vai ficar escondido atrás deles. São só uma referência
(o YouTube muda o desenho de tempos em tempos), por isso ficam num módulo à parte, sem depender da
janela, e a tela os liga e desliga sem refazer a prévia.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: a altura que 2 linhas da legenda ocupam, em fração da tela (74 px × 2 linhas × 1,2 de 1920 px)
ALTURA_DA_LEGENDA = 0.094
#: a legenda vai de uma margem (70 px) à outra: a borda direita chega a ~93% da largura
BORDA_DIREITA_DA_LEGENDA = 0.935
_AMPLIACAO = 3  # o desenho é feito maior e reduzido, para as bordas ficarem lisas


@dataclass(frozen=True)
class Regiao:
    """Uma área da tela que o YouTube cobre, em fração da largura e da altura (de cima)."""

    nome: str
    x0: float
    y0: float
    x1: float
    y1: float


BOTOES_DA_DIREITA = Regiao(
    "os botões da direita (curtir, comentar, compartilhar)", 0.865, 0.48, 1.0, 0.975
)
CANAL_E_TITULO = Regiao(
    "o nome do canal, o botão de inscrever-se e o título", 0.0, 0.815, 0.80, 0.975
)
TOPO = Regiao("os ícones do topo (busca e menu)", 0.60, 0.0, 1.0, 0.09)
REGIOES = (BOTOES_DA_DIREITA, CANAL_E_TITULO, TOPO)


def conflitos_da_legenda(posicao: float) -> list[str]:
    """Que partes do YouTube a legenda, centrada em `posicao` (0 a 1, de cima), pode encobrir.

    A legenda ocupa duas linhas ao redor do centro e, quando as linhas são longas, vai quase de
    uma borda à outra; então basta a altura se cruzar para a legenda poder encostar na área.
    """
    de, ate = posicao - ALTURA_DA_LEGENDA / 2, posicao + ALTURA_DA_LEGENDA / 2
    achados = []
    for regiao in REGIOES:
        cruza_na_altura = de < regiao.y1 and ate > regiao.y0
        alcanca_na_largura = regiao.x0 < BORDA_DIREITA_DA_LEGENDA and regiao.x1 > 0.065
        if cruza_na_altura and alcanca_na_largura:
            achados.append(regiao.nome)
    return achados


@lru_cache(maxsize=8)
def _fonte(tamanho: int, negrito: bool = False, simbolos: bool = False) -> ImageFont.ImageFont:
    pasta = Path("C:/Windows/Fonts")
    nomes = ("seguisym.ttf",) if simbolos else (("segoeuib.ttf",) if negrito else ("segoeui.ttf",))
    for nome in (*nomes, "arialbd.ttf" if negrito else "arial.ttf"):
        try:
            return ImageFont.truetype(str(pasta / nome), tamanho)
        except OSError:
            continue
    return ImageFont.load_default()


def _degrade(camada: Image.Image, de_y: int, ate_y: int, alfa_em_de: int, alfa_em_ate: int) -> None:
    """Faixa horizontal preta cuja transparência muda de `de_y` a `ate_y`."""
    pintor = ImageDraw.Draw(camada)
    passos = max(1, abs(ate_y - de_y))
    for i in range(passos):
        y = min(de_y, ate_y) + i
        fracao = i / passos if ate_y > de_y else 1 - i / passos
        alfa = round(alfa_em_de + (alfa_em_ate - alfa_em_de) * fracao)
        pintor.line([(0, y), (camada.width, y)], fill=(0, 0, 0, alfa))


def _centralizado(pintor, centro: tuple[float, float], texto: str, fonte, preenchimento) -> None:
    pintor.text(centro, texto, font=fonte, fill=preenchimento, anchor="mm")


def sobrepor_elementos(imagem: Image.Image) -> Image.Image:
    """A prévia com os elementos do YouTube Shorts por cima (devolve uma cópia em RGB)."""
    largura, altura = imagem.size
    k = _AMPLIACAO
    L, A = largura * k, altura * k
    camada = Image.new("RGBA", (L, A), (0, 0, 0, 0))
    _degrade(camada, round(A * 0.74), A, 0, 175)  # escurece a base, como o aplicativo faz
    _degrade(camada, 0, round(A * 0.10), 95, 0)
    pintor = ImageDraw.Draw(camada)
    branco, cinza = (255, 255, 255, 255), (235, 235, 235, 255)

    # --- coluna da direita: curtir, não gostei, comentar, compartilhar, remixar, disco ---------
    centro_x = L * 0.928
    raio = L * 0.045
    botoes = (
        ("\U0001f44d", "1,2 mil", 0.52),
        ("\U0001f44e", "Não gostei", 0.60),
        ("\U0001f4ac", "48", 0.68),
        ("\u21aa", "Compart.", 0.76),
        ("\u267b", "Remixar", 0.84),
    )
    for glifo, rotulo, y in botoes:
        cy = A * y
        pintor.ellipse(
            [centro_x - raio, cy - raio, centro_x + raio, cy + raio], fill=(40, 40, 40, 150)
        )
        _centralizado(pintor, (centro_x, cy), glifo, _fonte(round(L * 0.05), simbolos=True), branco)
        _centralizado(
            pintor, (centro_x, cy + raio * 1.5), rotulo, _fonte(round(L * 0.025), True), branco
        )
    disco = A * 0.935
    pintor.rounded_rectangle(
        [centro_x - raio * 0.8, disco - raio * 0.8, centro_x + raio * 0.8, disco + raio * 0.8],
        radius=raio * 0.25,
        fill=(230, 230, 230, 255),
        outline=(255, 255, 255, 255),
        width=k,
    )
    _centralizado(
        pintor,
        (centro_x, disco),
        "\u266a",
        _fonte(round(L * 0.05), simbolos=True),
        (30, 30, 30, 255),
    )

    # --- embaixo à esquerda: canal, inscrever-se, título e música ------------------------------
    margem = L * 0.05
    avatar = L * 0.055
    cy = A * 0.855
    pintor.ellipse([margem, cy - avatar, margem + avatar * 2, cy + avatar], fill=(214, 60, 60, 255))
    _centralizado(pintor, (margem + avatar, cy), "C", _fonte(round(L * 0.06), True), branco)
    x = margem + avatar * 2 + L * 0.025
    pintor.text((x, cy), "@seucanal", font=_fonte(round(L * 0.048), True), fill=branco, anchor="lm")
    ponta = x + L * 0.30
    pintor.rounded_rectangle(
        [ponta, cy - A * 0.014, ponta + L * 0.25, cy + A * 0.014],
        radius=A * 0.014,
        fill=(255, 255, 255, 255),
    )
    _centralizado(
        pintor,
        (ponta + L * 0.125, cy),
        "Inscrever-se",
        _fonte(round(L * 0.036), True),
        (20, 20, 20, 255),
    )
    pintor.text(
        (margem, A * 0.905),
        "Título do Short, até duas linhas",
        font=_fonte(round(L * 0.045), True),
        fill=branco,
        anchor="lm",
    )
    pintor.text(
        (margem, A * 0.93),
        "e a descrição curta que vem depois…",
        font=_fonte(round(L * 0.04)),
        fill=cinza,
        anchor="lm",
    )
    pintor.text(
        (margem, A * 0.957),
        "\u266a Som original",
        font=_fonte(round(L * 0.038), simbolos=True),
        fill=cinza,
        anchor="lm",
    )

    # --- no topo: busca e menu -----------------------------------------------------------------
    topo = A * 0.045
    _centralizado(
        pintor, (L * 0.84, topo), "\u2315", _fonte(round(L * 0.075), simbolos=True), branco
    )
    for passo in (-1, 0, 1):  # o menu de três pontos
        ponto, py = L * 0.008, topo + passo * A * 0.014
        pintor.ellipse([L * 0.94 - ponto, py - ponto, L * 0.94 + ponto, py + ponto], fill=branco)

    # --- a barra de progresso fina da base -----------------------------------------------------
    pintor.rectangle([0, A - k * 2, L, A], fill=(255, 255, 255, 90))
    pintor.rectangle([0, A - k * 2, round(L * 0.34), A], fill=(255, 40, 40, 255))

    pronta = camada.resize((largura, altura), Image.LANCZOS)
    return Image.alpha_composite(imagem.convert("RGBA"), pronta).convert("RGB")
