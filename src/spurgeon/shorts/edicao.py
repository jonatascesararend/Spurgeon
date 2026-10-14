"""Ajustes que a pessoa faz nos cortes na tela de aprovação (sem depender da interface)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from spurgeon.shorts.analise import separar_frases
from spurgeon.shorts.dados import Corte, Frase
from spurgeon.shorts.selecao import respiro_nas_pontas
from spurgeon.transcricao.edicao import aplicar_edicao, separar_pontuacao
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
        legenda="",  # as correções valiam para as palavras de antes: o corte agora é outro
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


def mover_por_frase(
    palavras: Sequence[Palavra],
    corte: Corte,
    ponta: Ponta,
    sentido: int,
    frases: Sequence[Frase] | None = None,
) -> Corte:
    """Leva o início (ou o fim) à fronteira de frase seguinte (`sentido` +1) ou anterior (−1).

    `sentido` é o do tempo: no início, +1 tira a frase de abertura e −1 acrescenta a anterior (ou
    o começo da frase em que o corte abre); no fim, +1 acrescenta a frase seguinte (ou o que falta
    para fechar a frase em que o corte termina) e −1 tira a última. Quem usa vários cortes pode
    passar `frases` já calculadas.
    """
    if sentido not in (-1, 1):
        raise ValueError("sentido deve ser -1 ou +1")
    frases = frases if frases is not None else separar_frases(palavras)
    primeira, ultima = indices_das_palavras(palavras, corte)
    if ponta == "inicio":
        pontos = sorted({f.primeira for f in frases})
        atual = primeira
    else:
        pontos = sorted({f.ultima for f in frases})
        atual = ultima
    alvos = [p for p in pontos if (p > atual if sentido > 0 else p < atual)]
    if not alvos:
        raise LimiteInvalido("Não há mais fala nesse sentido.")
    alvo = alvos[0] if sentido > 0 else alvos[-1]
    return mover_limite(palavras, corte, ponta, alvo - atual)


@dataclass(frozen=True)
class ContextoDasPontas:
    """O que a pessoa precisa ver para decidir onde o corte começa e termina."""

    antes: str  # as palavras que vêm logo antes do corte (ficam de fora)
    comeco: str  # as primeiras palavras do corte
    fim: str  # as últimas palavras do corte
    depois: str  # as palavras que vêm logo depois do corte (ficam de fora)
    avisos: tuple[str, ...] = ()


def contexto_das_pontas(
    palavras: Sequence[Palavra],
    corte: Corte,
    frases: Sequence[Frase] | None = None,
    *,
    fora: int = 14,
    dentro: int = 9,
) -> ContextoDasPontas:
    """Palavras ao redor das duas pontas do corte e avisos de início ou fim mal escolhidos.

    Os avisos são só um palpite (a pontuação do Whisper nem sempre é exata): começar no meio de
    uma frase, começar com “mas”/“então”/“isso” (que dependem do que veio antes), terminar no
    meio de uma frase ou terminar numa pergunta (a resposta pode ter ficado de fora).
    """
    frases = frases if frases is not None else separar_frases(palavras)
    primeira, ultima = indices_das_palavras(palavras, corte)

    def juntar(trecho: Sequence[Palavra]) -> str:
        return " ".join(p.texto for p in trecho)

    avisos: list[str] = []
    frase_inicial = next((f for f in frases if f.primeira <= primeira <= f.ultima), None)
    frase_final = next((f for f in frases if f.primeira <= ultima <= f.ultima), None)
    if frase_inicial is not None:
        if frase_inicial.primeira != primeira:
            avisos.append("Começa no meio de uma frase: use “+ frase” para abrir no começo dela.")
        elif frase_inicial.abre_dependente:
            avisos.append(
                f"Começa com “{separar_pontuacao(palavras[primeira].texto)[1]}”, que depende do "
                "que foi dito antes: confira se faz sentido sozinho."
            )
    if frase_final is not None:
        if frase_final.ultima != ultima:
            avisos.append("Termina no meio de uma frase: use “+ frase” para fechá-la.")
        elif frase_final.pergunta:
            avisos.append(
                "Termina numa pergunta: confira se a resposta não ficou de fora do Short."
            )
    return ContextoDasPontas(
        antes=juntar(palavras[max(0, primeira - fora) : primeira]),
        comeco=juntar(palavras[primeira : primeira + dentro]),
        fim=juntar(palavras[max(primeira, ultima - dentro + 1) : ultima + 1]),
        depois=juntar(palavras[ultima + 1 : ultima + 1 + fora]),
        avisos=tuple(avisos),
    )


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


def definir_limite(
    palavras: Sequence[Palavra], corte: Corte, ponta: Ponta, segundos: float
) -> Corte:
    """Leva o início (ou o fim) ao limite de palavra mais perto do tempo digitado.

    O início cai no começo de uma palavra e o fim, no fim de outra; o respiro é o de sempre.
    """
    primeira, ultima = indices_das_palavras(palavras, corte)
    if ponta == "inicio":
        alvo = min(range(len(palavras)), key=lambda i: abs(palavras[i].inicio - segundos))
        primeira = alvo
    else:
        alvo = min(range(len(palavras)), key=lambda i: abs(palavras[i].fim - segundos))
        ultima = alvo
    if primeira > ultima:
        raise LimiteInvalido("O início ficaria depois do fim.")
    novo = _com_limites(palavras, corte, primeira, ultima)
    if novo.duracao > DURACAO_MAXIMA_S + _TOLERANCIA_S:
        raise LimiteInvalido(f"O corte passaria de {DURACAO_MAXIMA_S:g} segundos.")
    if novo.duracao < DURACAO_MINIMA_S:
        raise LimiteInvalido(f"O corte ficaria com menos de {DURACAO_MINIMA_S:g} segundos.")
    return novo


def palavras_do_corte(palavras: Sequence[Palavra], corte: Corte) -> list[Palavra]:
    """As palavras da transcrição que estão dentro do corte."""
    return [
        p
        for p in palavras
        if p.fim > corte.inicio + _TOLERANCIA_S and p.inicio < corte.fim - _TOLERANCIA_S
    ]


def legenda_do_corte(palavras: Sequence[Palavra], corte: Corte) -> str:
    """O que aparece na legenda: a versão corrigida pela pessoa ou a da transcrição."""
    return corte.legenda or " ".join(p.texto for p in palavras_do_corte(palavras, corte))


def legenda_corrigida(palavras: Sequence[Palavra], corte: Corte, texto: str) -> str:
    """O texto a guardar em `Corte.legenda`: vazio se a pessoa não mudou nada."""
    normal = " ".join(texto.split())
    original = " ".join(p.texto for p in palavras_do_corte(palavras, corte))
    return "" if normal == original else normal


def palavras_com_a_legenda(palavras: Sequence[Palavra], corte: Corte) -> list[Palavra]:
    """As palavras do corte com a legenda corrigida aplicada, mantendo os tempos da fala."""
    dentro = palavras_do_corte(palavras, corte)
    if not corte.legenda or not dentro:
        return dentro
    return aplicar_edicao(dentro, corte.legenda)


_TEMPO = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d{1,2}(?:[.,]\d+)?)\s*$|^\s*(\d+(?:[.,]\d+)?)\s*$")


def ler_tempo(texto: str) -> float:
    """`8:21,3`, `1:02:05.5` ou `501.3` → segundos. Levanta ValueError se não for um tempo."""
    achado = _TEMPO.match(texto)
    if not achado:
        raise ValueError(f"Tempo inválido: {texto!r}. Use minutos:segundos, por exemplo 8:21,3.")
    horas, minutos, segundos, so_segundos = achado.groups()
    if so_segundos is not None:
        return float(so_segundos.replace(",", "."))
    return int(horas or 0) * 3600 + int(minutos) * 60 + float(segundos.replace(",", "."))


def formatar_tempo_preciso(segundos: float) -> str:
    """501,3 → `8:21,3`; 3725,45 → `1:02:05,5` (décimos de segundo, vírgula decimal)."""
    decimos = round(segundos * 10)
    inteiros, decimo = divmod(decimos, 10)
    horas, resto = divmod(inteiros, 3600)
    minutos, seg = divmod(resto, 60)
    base = f"{horas}:{minutos:02d}:{seg:02d}" if horas else f"{minutos}:{seg:02d}"
    return f"{base},{decimo}"
