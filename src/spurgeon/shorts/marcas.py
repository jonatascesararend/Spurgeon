"""O que já se sabe do sermão: os trechos que a IA avaliou (com a categoria) e os Shorts já feitos.

O Shorts livre usa isto para deixar o texto "vivo": cada frase ganha a cor da categoria do melhor
trecho detectado que a cobre, e as palavras que já viraram Short aparecem marcadas. Nada aqui
mexe em arquivo nem em tela; só lê o que a análise guardou.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from spurgeon.core.arquivos import ler_json
from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts.dados import Corte, Frase
from spurgeon.shorts.edicao import indices_incluidos
from spurgeon.shorts.etapas import carregar_candidatos, marca_do_candidato
from spurgeon.transcricao.formato import Palavra

_TOLERANCIA_S = 0.05


@dataclass(frozen=True)
class Detectado:
    """Um trecho que a IA avaliou: da palavra `primeira` à `ultima`, com a categoria e a nota."""

    primeira: int
    ultima: int
    categoria: str
    nota: int  # 0 a 10: o potencial de retenção que a IA deu


@dataclass(frozen=True)
class ShortFeito:
    """Um Short que já existe na lista de cortes, com as palavras que ele fala."""

    id: int
    categoria: str
    titulo: str
    indices: frozenset[int]


class _TempoDasPalavras:
    """Os instantes de cada palavra, para achar por tempo (busca binária) quais cabem num trecho."""

    def __init__(self, palavras: Sequence[Palavra]):
        self._inicios = [p.inicio for p in palavras]
        self._fins = [p.fim for p in palavras]

    def intervalo(self, inicio: float, fim: float) -> tuple[int, int] | None:
        primeira = bisect_left(self._inicios, inicio - _TOLERANCIA_S)
        ultima = bisect_right(self._fins, fim + _TOLERANCIA_S) - 1
        return (primeira, ultima) if primeira <= ultima else None


def palavras_do_intervalo(
    palavras: Sequence[Palavra], inicio: float, fim: float
) -> tuple[int, int] | None:
    """A primeira e a última palavra que ficam dentro de `inicio`..`fim` (em segundos)."""
    return _TempoDasPalavras(palavras).intervalo(inicio, fim)


def trechos_detectados(pastas: PastasProjeto, palavras: Sequence[Palavra]) -> list[Detectado]:
    """Os candidatos que a IA avaliou neste projeto (vazio se a análise ainda não rodou).

    Se o vídeo foi aparado, as palavras são só as do trecho usado; o que cai fora dele é ignorado.
    """
    if not pastas.candidatos.exists() or not pastas.avaliacoes.exists() or not palavras:
        return []
    try:
        candidatos = carregar_candidatos(pastas.candidatos)[1]
        dados: dict[str, Any] = ler_json(pastas.avaliacoes)
        guardadas = dados["avaliacoes"]
        por_marca = dados.get("versao", 1) >= 2
        tempo = _TempoDasPalavras(palavras)
        achados: list[Detectado] = []
        for c in candidatos:
            avaliacao = guardadas.get(marca_do_candidato(c) if por_marca else str(c.id))
            limites = tempo.intervalo(c.inicio, c.fim) if avaliacao else None
            if limites is not None:
                achados.append(
                    Detectado(
                        limites[0], limites[1], avaliacao["categoria"], int(avaliacao["nota"])
                    )
                )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return achados


def categoria_de_cada_frase(
    detectados: Iterable[Detectado], frases: Sequence[Frase]
) -> dict[int, Detectado]:
    """Para cada frase (a posição em `frases`), o trecho detectado que a cobre e tem a maior nota.

    Se dois têm a mesma nota, vale o mais curto (o mais específico) e, depois, o que vem antes.
    Frases que nenhum trecho cobre ficam de fora do resultado.
    """
    ordenados = sorted(detectados, key=lambda d: (-d.nota, d.ultima - d.primeira, d.primeira))
    resultado: dict[int, Detectado] = {}
    for d in ordenados:
        for posicao, frase in enumerate(frases):
            if posicao in resultado:
                continue
            if frase.primeira <= d.ultima and frase.ultima >= d.primeira:
                resultado[posicao] = d
    return resultado


def shorts_feitos(palavras: Sequence[Palavra], cortes: Iterable[Corte]) -> list[ShortFeito]:
    """Os Shorts da lista, cada um com as palavras que fala (sem as que os saltos tiram)."""
    return [
        ShortFeito(c.id, c.categoria, c.titulo, frozenset(indices_incluidos(palavras, c)))
        for c in cortes
    ]


def short_de_cada_palavra(feitos: Iterable[ShortFeito]) -> dict[int, ShortFeito]:
    """Para cada palavra que algum Short fala, o primeiro Short (da lista) que a fala."""
    resultado: dict[int, ShortFeito] = {}
    for feito in feitos:
        for i in feito.indices:
            resultado.setdefault(i, feito)
    return resultado
