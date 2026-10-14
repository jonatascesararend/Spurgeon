"""Legendas dinâmicas dos Shorts, em ASS: a frase inteira na tela e a palavra falada em amarelo.

Cada frase vira vários eventos, um por palavra: o texto é o mesmo, só muda qual palavra está
colorida. O FFmpeg queima o arquivo com o filtro `ass`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from spurgeon.transcricao.formato import Palavra

NOME_ESTILO = "Karaoke"
_AMARELO = r"&H0000FFFF&"  # ASS usa BGR: amarelo = azul 00, verde FF, vermelho FF
_BRANCO = r"&H00FFFFFF&"
_FIM_DE_FRASE = (".", "?", "!", "…")
_MINIMO_DE_PALAVRAS_PARA_QUEBRAR = 3
_DURACAO_MINIMA_S = 0.04


@dataclass(frozen=True)
class EstiloLegenda:
    fonte: str = "Montserrat ExtraBold"
    tamanho: int = 74
    contorno: int = 5
    espacamento: float = 2.0  # entre as letras; evita que o contorno cole as palavras
    posicao_vertical: float = 0.62  # fração da altura da tela, para o centro da legenda
    margem_lateral: int = 70
    largura: int = 1080
    altura: int = 1920
    max_caracteres_por_linha: int = 24
    max_linhas: int = 2
    pausa_que_separa_frases_s: float = 0.6
    permanencia_apos_a_fala_s: float = 0.2


@dataclass(frozen=True)
class PalavraDaLegenda:
    texto: str
    inicio: float  # segundos desde o começo do Short
    fim: float


def palavras_do_short(
    palavras: Sequence[Palavra], inicio: float, fim: float
) -> list[PalavraDaLegenda]:
    """As palavras que caem dentro do Short, com os tempos contados a partir do começo dele."""
    return [
        PalavraDaLegenda(p.texto, max(0.0, p.inicio - inicio), min(fim, p.fim) - inicio)
        for p in palavras
        if p.fim > inicio and p.inicio < fim and p.texto.strip()
    ]


def limpar_texto(texto: str) -> str:
    """Tira o que o ASS entenderia como comando (chaves e barras invertidas)."""
    return texto.replace("{", "").replace("}", "").replace("\\", "").strip()


def agrupar_em_frases(
    palavras: Sequence[PalavraDaLegenda], estilo: EstiloLegenda | None = None
) -> list[list[PalavraDaLegenda]]:
    """Grupos de palavras que cabem juntos na tela (até `max_linhas` linhas curtas).

    Um grupo termina no fim de uma frase, numa pausa ou quando não cabe mais uma palavra.
    """
    e = estilo or EstiloLegenda()
    capacidade = e.max_caracteres_por_linha * e.max_linhas
    grupos: list[list[PalavraDaLegenda]] = []
    atual: list[PalavraDaLegenda] = []
    tamanho = 0
    for palavra in palavras:
        texto = limpar_texto(palavra.texto)
        if not texto:
            continue
        acrescimo = len(texto) + (1 if atual else 0)
        pausa = palavra.inicio - atual[-1].fim if atual else 0.0
        if atual and (tamanho + acrescimo > capacidade or pausa >= e.pausa_que_separa_frases_s):
            grupos.append(atual)
            atual, tamanho, acrescimo = [], 0, len(texto)
        atual.append(PalavraDaLegenda(texto, palavra.inicio, palavra.fim))
        tamanho += acrescimo
        fecha_frase = texto.endswith(_FIM_DE_FRASE)
        if fecha_frase and len(atual) >= _MINIMO_DE_PALAVRAS_PARA_QUEBRAR:
            grupos.append(atual)
            atual, tamanho = [], 0
    if atual:
        grupos.append(atual)
    return grupos


def posicao_da_quebra(textos: Sequence[str], max_caracteres: int) -> int | None:
    """Onde dividir o grupo em duas linhas parecidas (índice da 1ª palavra da 2ª linha).

    Devolve None se tudo cabe numa linha só.
    """
    if len(" ".join(textos)) <= max_caracteres or len(textos) < 2:
        return None

    def custo(i: int) -> tuple[bool, int, int]:
        primeira, segunda = len(" ".join(textos[:i])), len(" ".join(textos[i:]))
        # primeiro, que as duas linhas caibam; depois, a mais curta possível; por fim, parecidas
        return (
            max(primeira, segunda) > max_caracteres,
            max(primeira, segunda),
            abs(primeira - segunda),
        )

    return min(range(1, len(textos)), key=custo)


def blocos_de_linhas(
    palavras: Sequence[PalavraDaLegenda], estilo: EstiloLegenda | None = None
) -> list[list[str]]:
    """O que aparece na tela, bloco a bloco: cada bloco tem uma ou duas linhas.

    É a mesma divisão de `montar_eventos` (os mesmos grupos e as mesmas quebras), sem o karaokê,
    para a pessoa ver no editor como a legenda vai ficar no vídeo.
    """
    e = estilo or EstiloLegenda()
    blocos = []
    for grupo in agrupar_em_frases(palavras, e):
        textos = [p.texto for p in grupo]
        quebra = posicao_da_quebra(textos, e.max_caracteres_por_linha)
        if quebra is None:
            blocos.append([" ".join(textos)])
        else:
            blocos.append([" ".join(textos[:quebra]), " ".join(textos[quebra:])])
    return blocos


def texto_em_blocos(blocos: Sequence[Sequence[str]]) -> str:
    """As linhas de cada bloco separadas por Enter, e uma linha em branco entre os blocos."""
    return "\n\n".join("\n".join(linhas) for linhas in blocos)


def _tempo_ass(segundos: float) -> str:
    centesimos = round(max(0.0, segundos) * 100)
    horas, resto = divmod(centesimos, 360000)
    minutos, resto = divmod(resto, 6000)
    segs, cent = divmod(resto, 100)
    return f"{horas}:{minutos:02d}:{segs:02d}.{cent:02d}"


def _texto_do_evento(textos: Sequence[str], destacada: int, quebra: int | None) -> str:
    partes = []
    for i, texto in enumerate(textos):
        if i == destacada:
            texto = rf"{{\1c{_AMARELO}}}{texto}{{\1c{_BRANCO}}}"
        if i:
            partes.append(r"\N" if i == quebra else " ")
        partes.append(texto)
    return "".join(partes)


def montar_eventos(
    palavras: Sequence[PalavraDaLegenda], estilo: EstiloLegenda | None = None
) -> list[tuple[float, float, str]]:
    """(início, fim, texto) de cada evento, sem sobreposição no tempo."""
    e = estilo or EstiloLegenda()
    grupos = agrupar_em_frases(palavras, e)
    eventos: list[tuple[float, float, str]] = []
    for g, grupo in enumerate(grupos):
        textos = [p.texto for p in grupo]
        quebra = posicao_da_quebra(textos, e.max_caracteres_por_linha)
        proximo_grupo = grupos[g + 1][0].inicio if g + 1 < len(grupos) else None
        limite = grupo[-1].fim + e.permanencia_apos_a_fala_s
        if proximo_grupo is not None:
            limite = min(limite, proximo_grupo)
        for i, palavra in enumerate(grupo):
            inicio = palavra.inicio
            fim = grupo[i + 1].inicio if i + 1 < len(grupo) else limite
            fim = max(fim, inicio + _DURACAO_MINIMA_S)
            eventos.append((inicio, fim, _texto_do_evento(textos, i, quebra)))
    return eventos


def montar_ass(palavras: Sequence[PalavraDaLegenda], estilo: EstiloLegenda | None = None) -> str:
    """O conteúdo do arquivo .ass das legendas de um Short."""
    e = estilo or EstiloLegenda()
    x, y = e.largura // 2, round(e.altura * e.posicao_vertical)
    linhas = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {e.largura}",
        f"PlayResY: {e.altura}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: {NOME_ESTILO},{e.fonte},{e.tamanho},{_BRANCO[:-1]},{_AMARELO[:-1]},&H00000000,"
        f"&H00000000,0,0,0,0,100,100,{e.espacamento:g},0,1,{e.contorno},0,5,{e.margem_lateral},"
        f"{e.margem_lateral},0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for inicio, fim, texto in montar_eventos(palavras, e):
        linhas.append(
            f"Dialogue: 0,{_tempo_ass(inicio)},{_tempo_ass(fim)},{NOME_ESTILO},,0,0,0,,"
            rf"{{\an5\pos({x},{y})}}{texto}"
        )
    return "\n".join(linhas) + "\n"
