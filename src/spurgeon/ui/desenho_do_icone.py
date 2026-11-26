"""Desenha o ícone do Studio Spurgeon: um púlpito com uma cruz vazada e uma Bíblia aberta em cima.

O mesmo quadrado azul arredondado do ícone anterior (#1F6AA5, borda #144673); o desenho é branco
e o que é "vazado" mostra o azul do fundo. Desenhado em 1024 px e reduzido para cada tamanho do
.ico; nos tamanhos pequenos as linhas do texto da Bíblia saem e a cruz fica mais grossa.

Não é usado pelo programa: refaz o arquivo quando o desenho mudar.
Uso: uv run python -m spurgeon.ui.desenho_do_icone recursos/icone_studio_spurgeon.ico [<prévias>]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

AZUL = (31, 106, 165, 255)
BORDA = (20, 70, 115, 255)
BRANCO = (255, 255, 255, 255)
L = 1024
TAMANHOS = (16, 24, 32, 48, 64, 128, 256)
#: até este tamanho vai o desenho sem as linhas do texto (que viram um borrão cinza)
LIMITE_DO_SIMPLES = 64


def curva(p0, p1, p2, passos=40):
    """Pontos de uma curva de Bézier quadrática."""
    pontos = []
    for i in range(passos + 1):
        t = i / passos
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        pontos.append((x, y))
    return pontos


def desenhar(simples: bool) -> Image.Image:
    im = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # o quadrado arredondado (como o ícone anterior)
    d.rounded_rectangle((36, 36, L - 36, L - 36), radius=210, fill=AZUL, outline=BORDA, width=30)
    # o desenho vai numa camada própria, para ser centralizado no fim (o azul do "vazado" é opaco)
    figura = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    d = ImageDraw.Draw(figura)

    cx = L / 2
    # --- púlpito -------------------------------------------------------------------------------
    # tampo inclinado (onde a Bíblia fica): um trapézio mais largo que o corpo
    topo_y, tampo_base_y = 505, 565
    d.polygon(
        [
            (cx - 300, tampo_base_y),
            (cx + 300, tampo_base_y),
            (cx + 270, topo_y),
            (cx - 270, topo_y),
        ],
        fill=BRANCO,
    )
    # corpo: um trapézio que afina para baixo, separado do tampo por um fio azul
    fio = 22 if simples else 16
    corpo_topo, corpo_base = tampo_base_y + fio, 815
    d.polygon(
        [
            (cx - 215, corpo_topo),
            (cx + 215, corpo_topo),
            (cx + 175, corpo_base),
            (cx - 175, corpo_base),
        ],
        fill=BRANCO,
    )
    # base: um degrau mais largo
    d.rounded_rectangle(
        (cx - 250, corpo_base + fio, cx + 250, corpo_base + fio + 62), radius=14, fill=BRANCO
    )

    # cruz vazada no corpo
    braco = 52 if simples else 40
    meio_y = corpo_topo + (corpo_base - corpo_topo) * 0.36
    d.rectangle((cx - braco / 2, corpo_topo + 42, cx + braco / 2, corpo_base - 38), fill=AZUL)
    d.rectangle((cx - 95, meio_y - braco / 2, cx + 95, meio_y + braco / 2), fill=AZUL)

    # --- Bíblia aberta em cima do tampo --------------------------------------------------------
    # cada lado é uma página: a borda de cima e a de baixo descem até a lombada (o jeito de um
    # livro aberto); a capa é o mesmo desenho um pouco maior e mais baixo. Um contorno azul separa
    # as páginas da capa e o livro do tampo.
    contorno = 18 if simples else 12
    w, alto = 235, 150
    base_y = topo_y + 18  # a base da capa encosta no tampo

    def pagina(lado, afasta=0.0, desce=0.0):
        y_base = base_y - 30 + desce
        baixo = curva(
            (cx, y_base),
            (cx + lado * w * 0.4, y_base - 70),
            (cx + lado * (w + afasta), y_base - 40),
        )
        cima = curva((cx + lado * (w + afasta), y_base - 40 - alto),
                     (cx + lado * w * 0.4, y_base - 70 - alto - 4),
                     (cx, y_base - alto + 22))  # fmt: skip
        return [*baixo, *cima]

    def com_borda(pontos):
        """Uma borda azul por fora (traço arredondado) e o branco por cima."""
        d.line([*pontos, pontos[0], pontos[1]], fill=AZUL, width=2 * contorno, joint="curve")
        for x, y in (pontos[0], pontos[len(pontos) // 2]):
            d.ellipse((x - contorno, y - contorno, x + contorno, y + contorno), fill=AZUL)
        d.polygon(pontos, fill=BRANCO)

    for lado in (-1, 1):  # a capa
        com_borda(pagina(lado, afasta=20, desce=26))
    for lado in (-1, 1):  # as páginas
        com_borda(pagina(lado))

    if not simples:  # linhas de texto e a fita marcadora (só nos tamanhos grandes)
        for lado in (-1, 1):
            for k in range(4):
                desce = 40 + k * 26
                y0 = base_y - 30 - alto + 16
                linha = curva((cx + lado * 38, y0 + desce - 6),
                              (cx + lado * w * 0.4, y0 - 44 + desce - 4),
                              (cx + lado * (w - 30), y0 - 58 + desce + 2))  # fmt: skip
                d.line(linha, fill=AZUL, width=9)
        d.polygon([(cx - 14, base_y - 30), (cx + 14, base_y - 30), (cx + 14, topo_y + 62),
                   (cx, topo_y + 48), (cx - 14, topo_y + 62)], fill=AZUL)  # fmt: skip
    # centraliza na vertical o que foi desenhado (púlpito e Bíblia)
    caixa = figura.getbbox()
    deslocamento = round(L / 2 - (caixa[1] + caixa[3]) / 2)
    im.alpha_composite(figura, (0, deslocamento))
    return im


def gerar(saida: Path, previas: Path | None = None) -> None:
    grande, simples = desenhar(False), desenhar(True)
    imagens = []
    for t in TAMANHOS:
        origem = simples if t <= LIMITE_DO_SIMPLES else grande
        imagens.append(origem.resize((t, t), Image.LANCZOS))
    imagens[-1].save(
        saida, format="ICO", sizes=[(t, t) for t in TAMANHOS], append_images=imagens[:-1]
    )
    if previas is not None:
        previas.mkdir(parents=True, exist_ok=True)
        grande.save(previas / "icone_1024.png")
        # uma folha com todos os tamanhos, em fundo claro e escuro, ampliados 2x sem suavizar
        folha = Image.new("RGBA", (1200, 640), (240, 240, 240, 255))
        x = 20
        for t, im in zip(TAMANHOS, imagens, strict=True):
            for fundo_y, cor in ((20, (240, 240, 240, 255)), (330, (32, 32, 32, 255))):
                ImageDraw.Draw(folha).rectangle(
                    (x - 10, fundo_y - 10, x + 2 * t + 10, fundo_y + 290), fill=cor
                )
                folha.alpha_composite(
                    im.resize((2 * t, 2 * t), Image.NEAREST) if t < 256 else im, (x, fundo_y)
                )
            x += (2 * t if t < 256 else t) + 30
        folha.save(previas / "tamanhos.png")


if __name__ == "__main__":
    gerar(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
