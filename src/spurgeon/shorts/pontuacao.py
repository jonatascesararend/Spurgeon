"""Nota de qualidade (0 a 100) de um corte, com o detalhe de cada parte.

A nota mistura o que a IA achou (retenção e se o trecho se sustenta sozinho) com o que se
mede no texto e nos tempos (bordas em pausas naturais, duração, gancho e ritmo). Os pesos
somam 100; o alerta de contexto desconta pontos.
"""

from __future__ import annotations

from spurgeon.shorts.analise import (
    pontos_de_abertura,
    pontos_de_bordas,
    pontos_de_duracao,
    pontos_de_gancho,
    pontos_de_ritmo,
)
from spurgeon.shorts.dados import Avaliacao, Candidato

PENALIDADE_ALERTA_DE_CONTEXTO = 10.0

#: nome mostrado na tela de aprovação e o máximo de pontos de cada parte
PARTES: dict[str, tuple[str, float]] = {
    "retencao": ("Potencial de retenção (IA)", 40.0),
    "autossuficiencia": ("Se sustenta sozinho", 20.0),
    "bordas": ("Começa e termina em pausas", 15.0),
    "duracao": ("Duração", 10.0),
    "gancho": ("Gancho e fecho", 10.0),
    "ritmo": ("Ritmo da fala", 5.0),
}
ROTULO_ALERTA = "Alerta de contexto"


def calcular_nota(candidato: Candidato, avaliacao: Avaliacao) -> tuple[float, dict[str, float]]:
    """Devolve a nota final e o detalhe (pontos de cada parte; o alerta entra como negativo)."""
    s = candidato.sinais
    detalhe = {
        "retencao": avaliacao.nota * 4.0,
        "autossuficiencia": (12.0 if avaliacao.autossuficiente else 0.0) + pontos_de_abertura(s),
        "bordas": pontos_de_bordas(s),
        "duracao": pontos_de_duracao(candidato.duracao),
        "gancho": pontos_de_gancho(s),
        "ritmo": pontos_de_ritmo(s),
    }
    if avaliacao.alerta_contexto:
        detalhe["alerta_de_contexto"] = -PENALIDADE_ALERTA_DE_CONTEXTO
    nota = max(0.0, min(100.0, sum(detalhe.values())))
    return round(nota, 1), {k: round(v, 1) for k, v in detalhe.items()}


def descrever_detalhe(detalhe: dict[str, float]) -> list[str]:
    """Uma linha por parte, no formato `Potencial de retenção (IA): 32 de 40`."""
    linhas = [
        f"{rotulo}: {detalhe.get(chave, 0):g} de {maximo:g}"
        for chave, (rotulo, maximo) in PARTES.items()
    ]
    if "alerta_de_contexto" in detalhe:
        linhas.append(f"{ROTULO_ALERTA}: {detalhe['alerta_de_contexto']:g}")
    return linhas
