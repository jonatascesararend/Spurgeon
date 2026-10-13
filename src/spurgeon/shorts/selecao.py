"""Escolha dos cortes: os de maior nota, sem sobreposição, com respiro nas pontas."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

from spurgeon.shorts.analise import FOLGA_FIM_S, FOLGA_INICIO_S, sobreposicao
from spurgeon.shorts.dados import Avaliacao, Candidato, Corte
from spurgeon.shorts.pontuacao import calcular_nota

SOBREPOSICAO_MAXIMA = 0.2
RESERVAS_PADRAO = 8
TAMANHO_MAXIMO_DO_NOME = 40


def respiro_nas_pontas(
    inicio: float, fim: float, pausa_antes_s: float, pausa_depois_s: float
) -> tuple[float, float]:
    """Início e fim do Short: a primeira e a última palavra, mais um respiro dentro da pausa.

    O respiro nunca passa de metade da pausa vizinha, para não invadir a palavra do lado.
    """
    antes = max(0.0, pausa_antes_s) / 2
    depois = max(0.0, pausa_depois_s) / 2
    return max(0.0, inicio - min(FOLGA_INICIO_S, antes)), fim + min(FOLGA_FIM_S, depois)


def com_respiro(candidato: Candidato) -> tuple[float, float]:
    sinais = candidato.sinais
    return respiro_nas_pontas(
        candidato.inicio,
        candidato.fim,
        float(sinais.get("pausa_antes_s", 0.0)),
        float(sinais.get("pausa_depois_s", 0.0)),
    )


def montar_cortes(
    candidatos: Sequence[Candidato],
    avaliacoes: Mapping[int, Avaliacao],
    *,
    limite: int,
    nota_minima: float,
    reservas: int = RESERVAS_PADRAO,
) -> list[Corte]:
    """Os melhores `limite` cortes (e algumas reservas), em ordem de nota.

    Só entram candidatos avaliados pela IA. Um corte que se sobrepõe a outro já escolhido em
    mais de 20% é descartado. Reservas ficam desmarcadas na tela de aprovação.
    """
    pontuados = []
    for c in candidatos:
        av = avaliacoes.get(c.id)
        if av is None:
            continue
        nota, detalhe = calcular_nota(c, av)
        pontuados.append((c, av, nota, detalhe))
    pontuados.sort(key=lambda x: (-x[2], -x[0].pre_nota, x[0].inicio))

    escolhidos: list[tuple[Candidato, Avaliacao, float, dict[str, float]]] = []
    for item in pontuados:
        if any(sobreposicao(item[0], e[0]) > SOBREPOSICAO_MAXIMA for e in escolhidos):
            continue
        escolhidos.append(item)
        if len(escolhidos) >= limite + reservas:
            break

    cortes = []
    for posicao, (c, av, nota, detalhe) in enumerate(escolhidos):
        reserva = posicao >= limite or nota < nota_minima
        inicio, fim = com_respiro(c)
        cortes.append(
            Corte(
                id=c.id,
                inicio=inicio,
                fim=fim,
                texto=c.texto,
                categoria=av.categoria,
                nota=nota,
                detalhe=detalhe,
                alerta_contexto=av.alerta_contexto,
                motivo=av.motivo,
                incluir=not reserva,
                reserva=reserva,
            )
        )
    return cortes


def nome_de_arquivo(
    texto: str, usados: Iterable[str] = (), maximo: int = TAMANHO_MAXIMO_DO_NOME
) -> str:
    """Texto livre → nome seguro (`o_povo_nao_quer_ouvir`), sem repetir os já `usados`."""
    decomposto = unicodedata.normalize("NFD", texto.casefold())
    ascii_ = "".join(c for c in decomposto if unicodedata.category(c) != "Mn")
    base = re.sub(r"[^a-z0-9]+", "_", ascii_).strip("_")[:maximo].strip("_") or "trecho"
    usados = set(usados)
    nome, n = base, 2
    while nome in usados:
        sufixo = f"_{n}"
        nome = base[: maximo - len(sufixo)] + sufixo
        n += 1
    return nome
