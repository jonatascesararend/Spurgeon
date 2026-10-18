"""Busca no texto do sermão, para achar o trecho de um Short escolhido a dedo.

Duas formas, as duas offline:

* **Palavras parecidas** (`procurar_palavras`): tolera erro de digitação, acento e variação da
  palavra ("gaça" acha "graça"; "perdoar" acha "perdoado"), com o `rapidfuzz`.
* **Tema** (`procurar_tema`): compara o assunto de uma janela de frases com o da busca, pelos
  vetores de palavras do modelo de português do spaCy; acha "perdão" num trecho que fala em
  "misericórdia" e "culpa" sem a palavra aparecer. É uma aproximação: serve para chegar perto, e a
  pessoa confere lendo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spurgeon.shorts.dados import Frase
from spurgeon.transcricao.formato import Palavra

NOTA_MINIMA_PALAVRAS = 72.0  # 0 a 100: abaixo disso o parecido já não é o mesmo
SIMILARIDADE_DA_PALAVRA = 80.0  # duas palavras "são a mesma" a partir daqui
MAXIMO_DE_ACHADOS = 40
FRASES_POR_JANELA_DO_TEMA = 4
PASSO_DA_JANELA_DO_TEMA = 2
SIMILARIDADE_MINIMA_DO_TEMA = 0.25
_PALAVRAS_MINIMAS = 2  # letras de uma palavra da busca (tira "a", "o", "e")


@dataclass(frozen=True)
class Achado:
    """Um lugar do sermão que combina com a busca."""

    primeira: int  # índice da primeira palavra do trecho achado
    ultima: int  # índice da última
    inicio: float
    fim: float
    pontos: float  # 0 a 100
    texto: str
    palavras: tuple[int, ...] = ()  # as palavras que casaram (para destacar), quando se sabe


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.casefold())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def _normalizadas(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", sem_acento(texto))


def palavras_da_busca(consulta: str) -> list[str]:
    return [p for p in _normalizadas(consulta) if len(p) >= _PALAVRAS_MINIMAS]


# --- palavras parecidas ----------------------------------------------------------------------


def procurar_palavras(
    palavras: Sequence[Palavra],
    frases: Sequence[Frase],
    consulta: str,
    *,
    nota_minima: float = NOTA_MINIMA_PALAVRAS,
    maximo: int = MAXIMO_DE_ACHADOS,
) -> list[Achado]:
    """As frases que têm palavras parecidas com as da busca, as melhores primeiro.

    A nota de uma frase é a média de quão bem cada palavra da busca casa com alguma palavra dela
    (0 a 100); se a busca inteira aparece como trecho (mesmo com um erro ou outro), a nota sobe
    para esse valor. Com várias palavras, todas precisam casar para a nota ser alta.
    """
    from rapidfuzz import fuzz

    buscadas = palavras_da_busca(consulta)
    if not buscadas:
        return []
    frase_buscada = " ".join(buscadas)
    achados: list[Achado] = []
    for frase in frases:
        normais = [
            _normalizadas(palavras[k].texto) for k in range(frase.primeira, frase.ultima + 1)
        ]
        # uma "palavra" do Whisper pode ter hífen ou apóstrofo e virar duas: junta de volta
        simples = ["".join(n) for n in normais]
        notas_das_palavras: list[float] = []
        casadas: set[int] = set()
        for buscada in buscadas:
            melhor, onde = 0.0, -1
            for posicao, candidata in enumerate(simples):
                if not candidata:
                    continue
                nota = max(fuzz.ratio(buscada, candidata), _nota_do_radical(buscada, candidata))
                if nota > melhor:
                    melhor, onde = nota, posicao
            notas_das_palavras.append(melhor)
            if melhor >= SIMILARIDADE_DA_PALAVRA and onde >= 0:
                casadas.add(frase.primeira + onde)
        media = sum(notas_das_palavras) / len(notas_das_palavras)
        seguidas = (
            fuzz.partial_ratio(frase_buscada, " ".join(simples)) if len(buscadas) > 1 else 0.0
        )
        pontos = max(media, seguidas)
        if pontos >= nota_minima and casadas:
            achados.append(
                Achado(
                    primeira=frase.primeira,
                    ultima=frase.ultima,
                    inicio=frase.inicio,
                    fim=frase.fim,
                    pontos=round(pontos, 1),
                    texto=frase.texto,
                    palavras=tuple(sorted(casadas)),
                )
            )
    achados.sort(key=lambda a: (-a.pontos, a.inicio))
    return achados[:maximo]


# --- tema ------------------------------------------------------------------------------------


def _vetor(nlp: Any, texto: str):
    """A média dos vetores das palavras de conteúdo do texto (None se nenhuma tem vetor)."""
    doc = nlp.make_doc(texto)
    vetores = [t.vector for t in doc if t.is_alpha and not t.is_stop and t.has_vector]
    if not vetores:
        return None
    import numpy as np

    return np.mean(vetores, axis=0)


def _cosseno(a, b) -> float:
    import numpy as np

    norma = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / norma) if norma else 0.0


def procurar_tema(
    palavras: Sequence[Palavra],
    frases: Sequence[Frase],
    consulta: str,
    nlp: Any,
    *,
    frases_por_janela: int = FRASES_POR_JANELA_DO_TEMA,
    passo: int = PASSO_DA_JANELA_DO_TEMA,
    maximo: int = 12,
) -> list[Achado]:
    """As janelas de frases cujo assunto mais se parece com o da busca (sem se sobrepor).

    Cada janela tem `frases_por_janela` frases seguidas; a nota (0 a 100) mistura a semelhança dos
    vetores de palavras (80%) com a fração das palavras da busca que aparecem na janela (20%), para
    uma palavra exata sempre pesar.
    """
    alvo = _vetor(nlp, consulta)
    buscadas = set(palavras_da_busca(consulta))
    if alvo is None or not frases:
        return []
    pontuadas: list[tuple[float, int, int]] = []
    for comeco in range(0, max(1, len(frases) - frases_por_janela + 1), passo):
        grupo = frases[comeco : comeco + frases_por_janela]
        texto = " ".join(f.texto for f in grupo)
        vetor = _vetor(nlp, texto)
        if vetor is None:
            continue
        similaridade = max(0.0, _cosseno(alvo, vetor))
        presentes = buscadas & set(_normalizadas(texto))
        lexical = len(presentes) / len(buscadas) if buscadas else 0.0
        pontos = 100 * (0.8 * similaridade + 0.2 * lexical)
        if similaridade >= SIMILARIDADE_MINIMA_DO_TEMA or lexical > 0:
            pontuadas.append((pontos, comeco, comeco + len(grupo) - 1))
    pontuadas.sort(key=lambda x: (-x[0], x[1]))
    escolhidas: list[tuple[float, int, int]] = []
    for pontos, de, ate in pontuadas:
        if all(ate < e_de or de > e_ate for _, e_de, e_ate in escolhidas):
            escolhidas.append((pontos, de, ate))
        if len(escolhidas) >= maximo:
            break
    achados = []
    for pontos, de, ate in escolhidas:
        primeira, ultima = frases[de], frases[ate]
        achados.append(
            Achado(
                primeira=primeira.primeira,
                ultima=ultima.ultima,
                inicio=primeira.inicio,
                fim=ultima.fim,
                pontos=round(pontos, 1),
                texto=" ".join(f.texto for f in frases[de : ate + 1]),
            )
        )
    return achados


def carregar_modelo_de_vetores():
    """O modelo de português do spaCy, só com o que os vetores precisam (carrega em ~1 s)."""
    import spacy

    return spacy.load(
        "pt_core_news_md",
        exclude=["ner", "lemmatizer", "parser", "morphologizer", "attribute_ruler"],
    )


def _nota_do_radical(a: str, b: str) -> float:
    """85 se as palavras começam igual por boa parte (perdoar × perdoou × perdoado), senão 0.

    O `ratio` sozinho dá pouco a formas de um mesmo verbo ("perdoar" e "perdoou" só empatam em
    71%); o começo em comum de pelo menos 4 letras e 70% da menor palavra as junta.
    """
    comum = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        comum += 1
    menor = min(len(a), len(b))
    return 85.0 if comum >= 4 and comum >= 0.7 * menor else 0.0
