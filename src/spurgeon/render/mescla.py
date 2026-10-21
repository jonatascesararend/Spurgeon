"""A mescla entre a introdução (ou a vinheta) e o vídeo: quanto as duas pontas se sobrepõem.

Mesclar é sobrepor: o fim da introdução e o começo do vídeo passam juntos durante o fade (imagem
com `xfade`, som com `acrossfade`), então o vídeo final fica mais curto que a soma das partes
justamente pela duração da sobreposição. Quem calcula tempos (legendas, conferência do vídeo,
duração total) precisa desse número; ele vive aqui, sem depender do render.
"""

from __future__ import annotations

from fractions import Fraction


def mescla_em_s(fade_s: float, duracao_do_clipe_s: float, duracao_do_video_s: float) -> float:
    """A sobreposição, em segundos: o fade pedido, no máximo metade do clipe e do vídeo."""
    if fade_s <= 0:
        return 0.0
    return max(0.0, min(fade_s, duracao_do_clipe_s / 2, duracao_do_video_s / 2))


def mescla_em_quadros(
    fade_s: float, fps: Fraction, quadros_do_clipe: int, quadros_do_video: int
) -> int:
    """A sobreposição em quadros inteiros (o render trabalha com quadros exatos); 0 = sem mescla."""
    if fade_s <= 0 or min(quadros_do_clipe, quadros_do_video) < 2:
        return 0
    pedidos = round(fade_s * float(fps))
    return max(1, min(pedidos, quadros_do_clipe // 2, quadros_do_video // 2))
