"""Ajustes que a pessoa faz nos cortes na tela de aprovação (sem depender da interface)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from spurgeon.shorts.dados import Corte
from spurgeon.shorts.selecao import respiro_nas_pontas
from spurgeon.transcricao.formato import Palavra

DURACAO_MAXIMA_S = 60.0
DURACAO_MINIMA_S = 10.0
NOTA_ALTA = 70.0
NOTA_MEDIA = 50.0
_TOLERANCIA_S = 0.001
_PAUSA_SEM_VIZINHA_S = 5.0

Ponta = Literal["inicio", "fim"]


class LimiteInvalido(ValueError):
    """O ajuste deixaria o corte longo ou curto demais, ou fora do sermão."""


def faixa_da_nota(nota: float) -> Literal["alta", "media", "baixa"]:
    if nota >= NOTA_ALTA:
        return "alta"
    return "media" if nota >= NOTA_MEDIA else "baixa"


def indices_das_palavras(palavras: Sequence[Palavra], corte: Corte) -> tuple[int, int]:
    """Posição da primeira e da última palavra que estão dentro do corte."""
    dentro = [
        i
        for i, p in enumerate(palavras)
        if p.fim > corte.inicio + _TOLERANCIA_S and p.inicio < corte.fim - _TOLERANCIA_S
    ]
    if not dentro:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    return dentro[0], dentro[-1]


def _com_limites(palavras: Sequence[Palavra], corte: Corte, primeira: int, ultima: int) -> Corte:
    antes = palavras[primeira].inicio - palavras[primeira - 1].fim if primeira > 0 else None
    depois = (
        palavras[ultima + 1].inicio - palavras[ultima].fim if ultima + 1 < len(palavras) else None
    )
    inicio, fim = respiro_nas_pontas(
        palavras[primeira].inicio,
        palavras[ultima].fim,
        _PAUSA_SEM_VIZINHA_S if antes is None else antes,
        _PAUSA_SEM_VIZINHA_S if depois is None else depois,
    )
    return replace(
        corte,
        inicio=inicio,
        fim=fim,
        texto=" ".join(p.texto for p in palavras[primeira : ultima + 1]),
        editado=True,
    )


def mover_limite(palavras: Sequence[Palavra], corte: Corte, ponta: Ponta, passo: int) -> Corte:
    """Um novo corte com o início (ou o fim) deslocado `passo` palavras (+ avança, − recua).

    Sempre cai em fronteira de palavra, com o respiro de costume. Levanta `LimiteInvalido` se o
    resultado passar de 60 s, ficar com menos de 10 s ou sair da transcrição.
    """
    primeira, ultima = indices_das_palavras(palavras, corte)
    if ponta == "inicio":
        primeira += passo
    else:
        ultima += passo
    if primeira < 0 or ultima >= len(palavras):
        raise LimiteInvalido("Não há mais fala nesse sentido.")
    if primeira > ultima:
        raise LimiteInvalido("O corte ficaria sem nenhuma palavra.")
    novo = _com_limites(palavras, corte, primeira, ultima)
    if novo.duracao > DURACAO_MAXIMA_S + _TOLERANCIA_S:
        raise LimiteInvalido(f"O corte passaria de {DURACAO_MAXIMA_S:g} segundos.")
    if novo.duracao < DURACAO_MINIMA_S:
        raise LimiteInvalido(f"O corte ficaria com menos de {DURACAO_MINIMA_S:g} segundos.")
    return novo


def palavras_das_pontas(
    palavras: Sequence[Palavra], corte: Corte, quantas: int = 7
) -> tuple[str, str]:
    """As primeiras e as últimas palavras do corte, para a pessoa ver onde ele começa e termina."""
    primeira, ultima = indices_das_palavras(palavras, corte)
    comeco = " ".join(p.texto for p in palavras[primeira : primeira + quantas])
    fim = " ".join(p.texto for p in palavras[max(primeira, ultima - quantas + 1) : ultima + 1])
    return comeco, fim


def ler_hashtags(texto: str) -> list[str]:
    """`fé, #Miquéias  shorts` → `['#fé', '#Miquéias', '#shorts']` (sem repetir)."""
    achadas: list[str] = []
    for pedaco in re.split(r"[\s,;]+", texto.strip()):
        nome = pedaco.lstrip("#")
        if nome and f"#{nome}" not in achadas:
            achadas.append(f"#{nome}")
    return achadas


@dataclass(frozen=True)
class ResumoCortes:
    marcados: int
    total: int
    duracao_s: float
    sem_titulo: int


def resumir(cortes: Iterable[Corte]) -> ResumoCortes:
    cortes = list(cortes)
    marcados = [c for c in cortes if c.incluir]
    return ResumoCortes(
        marcados=len(marcados),
        total=len(cortes),
        duracao_s=sum(c.duracao for c in marcados),
        sem_titulo=sum(1 for c in marcados if not c.titulo.strip()),
    )
