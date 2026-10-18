"""Zoom e tratamento de imagem: os filtros do FFmpeg e a sugestão automática de ajustes.

O tratamento roda DEPOIS da conversão para SDR, em yuv420p de faixa limitada (Y de 16 a 235): é a
mesma cadeia no vídeo longo, nos Shorts e na prévia da tela, então o que a pessoa vê é o que sai no
arquivo. Brilho, contraste e saturação usam o filtro `eq`; brancos, sombras e a correção de cor
(temperatura e tom) usam `lutyuv`, que mexe direto no luma e no croma.
"""

from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageStat

from spurgeon.core.aparencia import NEUTRO, Enquadramento, TratamentoImagem

#: o que cada +100 do ajuste vale no filtro
_BRILHO_MAXIMO = 0.25  # fração da faixa de luz (o `eq` aceita de −1 a 1)
_CONTRASTE_MAXIMO = 0.5  # o fator de contraste vai de 0,5 a 1,5
_NIVEIS_BRANCOS_SOMBRAS = 42.0  # níveis de luma (de 219) mexidos no extremo da faixa
_NIVEIS_CROMA_TEMPERATURA = 14.0
_NIVEIS_CROMA_MATIZ = 10.0
_LUMA_MIN, _LUMA_MAX, _CROMA_MIN, _CROMA_MAX = 16, 235, 16, 240


def _n(valor: float) -> str:
    texto = f"{valor:.4f}".rstrip("0").rstrip(".")
    return texto if texto not in ("", "-0") else "0"


def filtro_recorte_do_zoom(largura: int, altura: int, enquadramento: Enquadramento) -> str | None:
    """O `crop` do zoom (None se não há zoom). Roda antes da conversão para SDR."""
    if not enquadramento.ativo:
        return None
    x, y, w, h = enquadramento.regiao(largura, altura)
    return f"crop={w}:{h}:{x}:{y}"


def filtro_ampliar_zoom(largura: int, altura: int, enquadramento: Enquadramento) -> str | None:
    """Devolve o recorte ao tamanho do vídeo (None se não há zoom). Roda depois do SDR."""
    if not enquadramento.ativo:
        return None
    return f"scale={largura // 2 * 2}:{altura // 2 * 2}:flags=lanczos,setsar=1"


def filtro_tratamento(t: TratamentoImagem) -> str | None:
    """A cadeia de filtros do tratamento (None se tudo está em zero)."""
    if t.neutro:
        return None
    partes: list[str] = []

    eq = []
    if abs(t.brilho) >= NEUTRO:
        eq.append(f"brightness={_n(t.brilho / 100 * _BRILHO_MAXIMO)}")
    if abs(t.contraste) >= NEUTRO:
        eq.append(f"contrast={_n(1 + t.contraste / 100 * _CONTRASTE_MAXIMO)}")
    if abs(t.saturacao) >= NEUTRO:
        eq.append(f"saturation={_n(1 + t.saturacao / 100)}")
    if eq:
        partes.append("eq=" + ":".join(eq))

    lut = []
    if abs(t.brancos) >= NEUTRO or abs(t.sombras) >= NEUTRO:
        u = f"clip((val-{_LUMA_MIN})/{_LUMA_MAX - _LUMA_MIN},0,1)"
        sombras = t.sombras / 100 * _NIVEIS_BRANCOS_SOMBRAS
        brancos = t.brancos / 100 * _NIVEIS_BRANCOS_SOMBRAS
        lut.append(
            f"y='clip(val+({_n(sombras)})*pow(1-{u},2)+({_n(brancos)})*pow({u},2),"
            f"{_LUMA_MIN},{_LUMA_MAX})'"
        )
    if abs(t.temperatura) >= NEUTRO or abs(t.matiz) >= NEUTRO:
        # Cb (u) e Cr (v): quente = menos azul e mais vermelho; magenta = mais dos dois
        quente = t.temperatura / 100 * _NIVEIS_CROMA_TEMPERATURA
        magenta = t.matiz / 100 * _NIVEIS_CROMA_MATIZ
        lut.append(f"u='clip(val+({_n(-quente + magenta)}),{_CROMA_MIN},{_CROMA_MAX})'")
        lut.append(f"v='clip(val+({_n(quente + magenta)}),{_CROMA_MIN},{_CROMA_MAX})'")
    if lut:
        partes.append("lutyuv=" + ":".join(lut))
    return ",".join(partes)


# --- sugestão automática ---------------------------------------------------------------------

_LADO_DA_ANALISE = 240
_ALVO_AMPLITUDE = 0.86  # distância desejada entre os escuros e os claros (0 a 1)
_ALVO_MEDIANA = 0.46
_TOLERANCIA_DA_MEDIANA = 0.06
_LIMITE_DA_SUGESTAO = 45.0  # a sugestão é um ponto de partida: nada de extremos


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


def _percentis(histograma: Sequence[int], *fracoes: float) -> list[float]:
    """Os percentis (0 a 1) de um histograma de 256 níveis."""
    total = sum(histograma) or 1
    achados, acumulado, alvos = [], 0, list(fracoes)
    for nivel, quantos in enumerate(histograma):
        acumulado += quantos
        while alvos and acumulado / total >= alvos[0]:
            achados.append(nivel / 255)
            alvos.pop(0)
    achados += [1.0] * len(alvos)
    return achados


def sugerir_tratamento(quadros: Sequence[Image.Image]) -> TratamentoImagem:
    """Um ponto de partida para o tratamento, medido em alguns quadros do vídeo (já em SDR).

    Estica o contraste até os escuros e os claros usarem boa parte da faixa, leva a luz média para
    perto do meio, abre sombras muito fechadas, recupera brancos estourados, reforça cores muito
    apagadas e desfaz uma dominante de cor (com a metade da força, porque madeira e pele são
    quentes de verdade). Uma imagem já equilibrada volta quase neutra.
    """
    if not quadros:
        return TratamentoImagem()
    luma = [0] * 256
    saturacao, vermelho_azul, verde_magenta, amostras = 0.0, 0.0, 0.0, 0
    for quadro in quadros:
        pequeno = quadro.convert("RGB")
        pequeno.thumbnail((_LADO_DA_ANALISE, _LADO_DA_ANALISE))
        for nivel, quantos in enumerate(pequeno.convert("L").histogram()):
            luma[nivel] += quantos
        hsv = pequeno.convert("HSV")
        medio = ImageStat.Stat(pequeno).mean  # R, G, B
        saturacao += ImageStat.Stat(hsv).mean[1] / 255
        vermelho_azul += (medio[0] - medio[2]) / 255
        verde_magenta += ((medio[0] + medio[2]) / 2 - medio[1]) / 255
        amostras += 1
    saturacao /= amostras
    vermelho_azul /= amostras
    verde_magenta /= amostras

    p1, p5, mediana, p99 = _percentis(luma, 0.01, 0.05, 0.5, 0.99)
    amplitude = max(p99 - p1, 0.05)
    fator = _limitar(_ALVO_AMPLITUDE / amplitude, 1.0, 1.5)  # só aumenta: nunca achata uma imagem
    contraste = _limitar((fator - 1) / _CONTRASTE_MAXIMO * 100, 0.0, 35.0)
    fator_real = 1 + contraste / 100 * _CONTRASTE_MAXIMO
    mediana_depois = (mediana - 0.5) * fator_real + 0.5
    desvio = _ALVO_MEDIANA - mediana_depois
    if abs(desvio) <= _TOLERANCIA_DA_MEDIANA:  # luz média aceitável: não mexe
        desvio = 0.0
    else:
        desvio -= _TOLERANCIA_DA_MEDIANA if desvio > 0 else -_TOLERANCIA_DA_MEDIANA
    brilho = _limitar(desvio / _BRILHO_MAXIMO * 100, -_LIMITE_DA_SUGESTAO, 40.0)
    p5_depois = _limitar((p5 - 0.5) * fator_real + 0.5 + brilho / 100 * _BRILHO_MAXIMO, 0, 1)
    p99_depois = _limitar((p99 - 0.5) * fator_real + 0.5 + brilho / 100 * _BRILHO_MAXIMO, 0, 1)
    sombras = _limitar((0.09 - p5_depois) * 500, 0.0, 40.0)
    if p99_depois > 0.95:
        brancos = -_limitar((p99_depois - 0.93) * 900, 0.0, 40.0)
    else:
        brancos = _limitar((0.80 - p99_depois) * 300, 0.0, 25.0)
    if saturacao < 0.2:
        cor = _limitar((0.26 - saturacao) * 300, 0.0, 30.0)
    elif saturacao > 0.5:
        cor = -_limitar((saturacao - 0.45) * 200, 0.0, 25.0)
    else:
        cor = 0.0
    temperatura = _limitar(-vermelho_azul * 150, -25.0, 25.0)
    matiz = _limitar(-verde_magenta * 150, -20.0, 20.0)

    def pequeno_demais(valor: float) -> float:
        return 0.0 if abs(valor) < 3 else round(valor)

    return TratamentoImagem(
        brilho=pequeno_demais(brilho),
        contraste=pequeno_demais(contraste),
        saturacao=pequeno_demais(cor),
        temperatura=pequeno_demais(temperatura),
        matiz=pequeno_demais(matiz),
        brancos=pequeno_demais(brancos),
        sombras=pequeno_demais(sombras),
    )
