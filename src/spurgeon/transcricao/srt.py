"""Legendas .srt a partir das palavras com tempo (sem depender do Whisper nem do FFmpeg)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations, pairwise

from spurgeon.core.tempo import MapaTempo
from spurgeon.transcricao.formato import Palavra

DURACAO_MAXIMA_S = 7.0
DURACAO_MINIMA_S = 1.0
#: pausa entre duas palavras a partir da qual começa outra legenda
PAUSA_QUEBRA_S = 1.0
#: uma frase só fecha a legenda depois de durar isto, para "Sim." não ficar sozinha
FRASE_MINIMA_S = 1.5
FOLGA_ENTRE_LEGENDAS_S = 0.04
_FIM_DE_FRASE = (".", "?", "!", "…")
_PONTUACAO_DE_QUEBRA = (",", ";", ":", ".", "?", "!", "…")
_BONUS_QUEBRA_EM_PONTUACAO = 6


@dataclass(frozen=True)
class Legenda:
    inicio: float
    fim: float
    linhas: tuple[str, ...]


def dividir_em_linhas(
    palavras: list[str], max_caracteres: int, max_linhas: int
) -> list[str] | None:
    """Reparte as palavras em até `max_linhas` linhas, equilibradas e de até `max_caracteres`.

    Prefere quebrar depois de vírgula ou ponto. Devolve None se não couber. Uma palavra sozinha
    sempre cabe, mesmo maior que o limite (não há como quebrá-la).
    """
    texto = " ".join(palavras)
    if len(texto) <= max_caracteres or len(palavras) == 1:
        return [texto]
    for n_linhas in range(2, max_linhas + 1):
        melhor: tuple[float, list[str]] | None = None
        for cortes in combinations(range(1, len(palavras)), n_linhas - 1):
            limites = [0, *cortes, len(palavras)]
            linhas = [" ".join(palavras[a:b]) for a, b in pairwise(limites)]
            if any(len(linha) > max_caracteres for linha in linhas):
                continue
            nota = max(len(linha) for linha in linhas)
            nota -= sum(
                _BONUS_QUEBRA_EM_PONTUACAO
                for linha in linhas[:-1]
                if linha.endswith(_PONTUACAO_DE_QUEBRA)
            )
            if melhor is None or nota < melhor[0]:
                melhor = (nota, linhas)
        if melhor is not None:
            return melhor[1]
    return None


def montar_legendas(
    palavras: Iterable[Palavra],
    *,
    max_linhas: int = 2,
    max_caracteres: int = 42,
    duracao_maxima_s: float = DURACAO_MAXIMA_S,
    pausa_quebra_s: float = PAUSA_QUEBRA_S,
) -> list[Legenda]:
    legendas: list[Legenda] = []
    atual: list[Palavra] = []

    def fechar() -> None:
        if atual:
            linhas = dividir_em_linhas([p.texto for p in atual], max_caracteres, max_linhas)
            legendas.append(Legenda(atual[0].inicio, atual[-1].fim, tuple(linhas or [])))
            atual.clear()

    for palavra in palavras:
        if atual:
            pausa = palavra.inicio - atual[-1].fim
            cabe = dividir_em_linhas(
                [p.texto for p in (*atual, palavra)], max_caracteres, max_linhas
            )
            if (
                pausa >= pausa_quebra_s
                or cabe is None
                or palavra.fim - atual[0].inicio > duracao_maxima_s
            ):
                fechar()
        atual.append(palavra)
        if (
            palavra.texto.endswith(_FIM_DE_FRASE)
            and palavra.fim - atual[0].inicio >= FRASE_MINIMA_S
        ):
            fechar()
    fechar()
    return _ajustar_tempos(legendas)


def _ajustar_tempos(legendas: list[Legenda]) -> list[Legenda]:
    """Dá a cada legenda ao menos `DURACAO_MINIMA_S`, sem invadir a seguinte."""
    ajustadas = []
    for i, leg in enumerate(legendas):
        limite = (
            legendas[i + 1].inicio - FOLGA_ENTRE_LEGENDAS_S
            if i + 1 < len(legendas)
            else float("inf")
        )
        fim = max(leg.fim, min(leg.inicio + DURACAO_MINIMA_S, limite))
        ajustadas.append(Legenda(leg.inicio, fim, leg.linhas))
    return ajustadas


def remapear_palavras(palavras: Iterable[Palavra], mapa: MapaTempo) -> list[Palavra]:
    """Passa as palavras para o tempo do vídeo editado; descarta as de trechos cortados."""
    saida = []
    for p in palavras:
        if not mapa.mantido((p.inicio + p.fim) / 2):
            continue
        inicio = mapa.original_para_editado(p.inicio)
        fim = mapa.original_para_editado(p.fim)
        saida.append(Palavra(p.texto, inicio, max(fim, inicio), p.prob, p.original))
    return saida


def formatar_tempo(segundos: float) -> str:
    """3725.4 → `01:02:05,400`."""
    milissegundos = round(max(segundos, 0.0) * 1000)
    horas, resto = divmod(milissegundos, 3_600_000)
    minutos, resto = divmod(resto, 60_000)
    seg, ms = divmod(resto, 1000)
    return f"{horas:02d}:{minutos:02d}:{seg:02d},{ms:03d}"


def formatar_srt(legendas: Iterable[Legenda]) -> str:
    blocos = []
    for i, leg in enumerate(legendas, start=1):
        blocos.append(
            f"{i}\n{formatar_tempo(leg.inicio)} --> {formatar_tempo(leg.fim)}\n"
            + "\n".join(leg.linhas)
        )
    return "\n\n".join(blocos) + "\n" if blocos else ""
