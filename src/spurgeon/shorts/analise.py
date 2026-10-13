"""Análise do texto do sermão: frases, janelas candidatas e a pontuação objetiva de cada uma.

Tudo aqui é determinístico e sem IA generativa: o spaCy separa as frases, o pandas guarda as
palavras com os tempos e as janelas são montadas só com frases inteiras. A IA (`llm.py`) entra
depois, avaliando só os melhores candidatos.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spurgeon.shorts.dados import Candidato, Frase
from spurgeon.transcricao.edicao import separar_pontuacao
from spurgeon.transcricao.formato import Palavra

FIM_DE_FRASE = (".", "?", "!", "…")
#: duas palavras separadas por mais que isto começam frases diferentes mesmo sem pontuação
PAUSA_DE_FRASE_S = 0.35
PAUSA_INTERNA_MAXIMA_S = 3.0
FOLGA_INICIO_S = 0.20
FOLGA_FIM_S = 0.35
#: respiro máximo somado às pontas, para o Short caber nos 60 s mesmo com a folga
RESERVA_DE_FOLGA_S = FOLGA_INICIO_S + FOLGA_FIM_S

# Palavras que ligam a frase ao que veio antes: um corte que começa com elas perde o sentido.
CONECTIVOS = frozenset(
    [
        "e",
        "mas",
        "então",
        "porque",
        "pois",
        "portanto",
        "assim",
        "aí",
        "ou",
        "porém",
        "contudo",
        "logo",
        "senão",
        "também",
        "daí",
        "entretanto",
        "todavia",
        "pois",
        "que",
        "se",
        "como",
        "quando",
        "enquanto",
    ]
)
ANAFORICOS = frozenset(
    [
        "isso",
        "ele",
        "ela",
        "eles",
        "elas",
        "aquilo",
        "esse",
        "essa",
        "esses",
        "essas",
        "aquele",
        "aquela",
        "aqueles",
        "aquelas",
        "este",
        "esta",
        "estes",
        "estas",
        "disso",
        "nisso",
        "dele",
        "dela",
        "deles",
        "delas",
    ]
)
EXPRESSOES_DE_RETOMADA = ("como eu dizia", "como eu disse", "voltando", "como vimos", "como falei")
EXPRESSOES_DE_FECHO = (
    "portanto",
    "por isso",
    "em resumo",
    "resumindo",
    "o que isso significa",
    "a verdade é que",
    "amém",
)

LIVROS = [
    "Gênesis",
    "Êxodo",
    "Levítico",
    "Números",
    "Deuteronômio",
    "Josué",
    "Juízes",
    "Rute",
    "Samuel",
    "Reis",
    "Crônicas",
    "Esdras",
    "Neemias",
    "Ester",
    "Jó",
    "Salmos",
    "Provérbios",
    "Eclesiastes",
    "Cantares",
    "Isaías",
    "Jeremias",
    "Lamentações",
    "Ezequiel",
    "Daniel",
    "Oseias",
    "Joel",
    "Amós",
    "Obadias",
    "Jonas",
    "Miqueias",
    "Miquéias",
    "Naum",
    "Habacuque",
    "Sofonias",
    "Ageu",
    "Zacarias",
    "Malaquias",
    "Mateus",
    "Marcos",
    "Lucas",
    "João",
    "Atos",
    "Romanos",
    "Coríntios",
    "Gálatas",
    "Efésios",
    "Filipenses",
    "Colossenses",
    "Tessalonicenses",
    "Timóteo",
    "Tito",
    "Filemom",
    "Hebreus",
    "Tiago",
    "Pedro",
    "Judas",
    "Apocalipse",
]
_LIVROS_RE = "|".join(sorted((re.escape(x) for x in LIVROS), key=len, reverse=True))
CITACAO = re.compile(
    rf"\b(?:{_LIVROS_RE})\s+\d+|\b(?:capítulo|versículo|verso)\s+\d+|"
    r"\bestá escrito\b|\bdiz a escritura\b|\bdiz o senhor\b|\bdiz a palavra\b",
    re.IGNORECASE,
)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.casefold())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


# --- frases --------------------------------------------------------------------------------


def separar_frases(palavras: Sequence[Palavra], nlp: Any | None = None) -> list[Frase]:
    """Divide as palavras em frases usando a pontuação do Whisper, as pausas e (se dado) o spaCy.

    Uma frase termina onde a palavra termina em `. ? ! …`, onde há uma pausa de pelo menos
    `PAUSA_DE_FRASE_S` ou onde o spaCy (analisando as palavras como já vieram) vê o fim de uma
    oração. `abre_dependente` usa os conectivos e a classe gramatical da primeira palavra.
    """
    if not palavras:
        return []
    inicios_spacy: set[int] = set()
    primeiras_classes: dict[int, str] = {}
    if nlp is not None:
        from spacy.tokens import Doc

        doc = nlp(Doc(nlp.vocab, words=[p.texto for p in palavras], spaces=[True] * len(palavras)))
        for sentenca in doc.sents:
            inicios_spacy.add(sentenca.start)
            primeiras_classes[sentenca.start] = sentenca[0].pos_

    inicios = {0}
    for i, p in enumerate(palavras[:-1]):
        pausa = palavras[i + 1].inicio - p.fim
        if p.texto.endswith(FIM_DE_FRASE) or (
            (pausa >= PAUSA_DE_FRASE_S) and (i + 1) in inicios_spacy
        ):
            inicios.add(i + 1)
    ordenados = sorted(inicios)
    frases = []
    for k, primeira in enumerate(ordenados):
        ultima = (ordenados[k + 1] - 1) if k + 1 < len(ordenados) else len(palavras) - 1
        trecho = palavras[primeira : ultima + 1]
        texto = " ".join(p.texto for p in trecho)
        frases.append(
            Frase(
                primeira=primeira,
                ultima=ultima,
                inicio=trecho[0].inicio,
                fim=trecho[-1].fim,
                texto=texto,
                pergunta=texto.rstrip().endswith("?"),
                abre_dependente=_abre_dependente(trecho[0].texto, primeiras_classes.get(primeira)),
            )
        )
    return frases


def _abre_dependente(primeira_palavra: str, classe: str | None) -> bool:
    nucleo = _sem_acento(separar_pontuacao(primeira_palavra)[1])
    if nucleo in {_sem_acento(c) for c in CONECTIVOS | ANAFORICOS}:
        return True
    return classe in {"CCONJ", "SCONJ"}


# --- janelas candidatas --------------------------------------------------------------------


@dataclass(frozen=True)
class ParametrosJanela:
    minimo_s: float = 30.0
    maximo_s: float = 60.0


def gerar_candidatos(
    palavras: Sequence[Palavra],
    frases: Sequence[Frase],
    parametros: ParametrosJanela | None = None,
) -> list[Candidato]:
    """Todas as janelas de frases inteiras com duração entre o mínimo e o máximo.

    O máximo já desconta a folga de respiro das pontas. Janelas com uma pausa interna maior
    que `PAUSA_INTERNA_MAXIMA_S` são descartadas.
    """
    import pandas as pd

    p = parametros or ParametrosJanela()
    if not palavras:
        return []
    tabela = pd.DataFrame(
        {
            "inicio": [x.inicio for x in palavras],
            "fim": [x.fim for x in palavras],
        }
    )
    tabela["pausa_apos"] = (tabela["inicio"].shift(-1) - tabela["fim"]).fillna(0.0).clip(lower=0.0)
    pausas = tabela["pausa_apos"].to_numpy()
    limite = p.maximo_s - RESERVA_DE_FOLGA_S

    candidatos: list[Candidato] = []
    for i, primeira in enumerate(frases):
        for j in range(i, len(frases)):
            ultima = frases[j]
            duracao = ultima.fim - primeira.inicio
            if duracao > limite:
                break
            if duracao < p.minimo_s:
                continue
            pausa_interna = (
                float(pausas[primeira.primeira : ultima.ultima].max())
                if ultima.ultima > primeira.primeira
                else 0.0
            )
            if pausa_interna > PAUSA_INTERNA_MAXIMA_S:
                continue
            candidatos.append(_montar(len(candidatos), palavras, frases, i, j, pausa_interna))
    return candidatos


def _montar(
    id_: int,
    palavras: Sequence[Palavra],
    frases: Sequence[Frase],
    i: int,
    j: int,
    pausa_interna: float,
) -> Candidato:
    primeira, ultima = frases[i], frases[j]
    trecho = palavras[primeira.primeira : ultima.ultima + 1]
    texto = " ".join(x.texto for x in trecho)
    antes = (primeira.inicio - frases[i - 1].fim) if i > 0 else 5.0
    depois = (frases[j + 1].inicio - ultima.fim) if j + 1 < len(frases) else 5.0
    duracao = ultima.fim - primeira.inicio
    sinais = {
        "abertura_dependente": primeira.abre_dependente,
        "fecho_completo": ultima.texto.rstrip().endswith(FIM_DE_FRASE),
        "pausa_antes_s": round(antes, 2),
        "pausa_depois_s": round(depois, 2),
        "pergunta_no_inicio": any(f.pergunta for f in frases[i : min(i + 2, j + 1)]),
        "tem_pergunta": any(f.pergunta for f in frases[i : j + 1]),
        "tem_citacao": bool(CITACAO.search(texto)),
        "retomada": any(
            e in _sem_acento(texto[:80]) for e in map(_sem_acento, EXPRESSOES_DE_RETOMADA)
        ),
        "fecho_conclusivo": any(
            e in _sem_acento(ultima.texto) for e in map(_sem_acento, EXPRESSOES_DE_FECHO)
        ),
        "palavras_por_segundo": round(len(trecho) / max(duracao, 0.1), 2),
        "pausa_interna_max_s": round(pausa_interna, 2),
    }
    candidato = Candidato(
        id=id_,
        primeira_frase=i,
        ultima_frase=j,
        inicio=primeira.inicio,
        fim=ultima.fim,
        texto=texto,
        pre_nota=0.0,
        sinais=sinais,
    )
    candidato.pre_nota = pre_nota(candidato)
    return candidato


# --- pontuação objetiva --------------------------------------------------------------------


def pontos_de_duracao(duracao_s: float) -> float:
    """0 a 10: o melhor é perto do limite de 60 s; abaixo de 30 s já não serve."""
    if duracao_s >= 50:
        return 10.0
    if duracao_s >= 40:
        return 8.0
    if duracao_s >= 30:
        return 5.0
    return 0.0


def pontos_de_bordas(sinais: dict[str, Any]) -> float:
    """0 a 15: o corte começa e termina onde uma pessoa faria a pausa."""
    pontos = 8.0 if sinais["fecho_completo"] else 0.0
    pontos += 3.5 if sinais["pausa_antes_s"] >= 0.25 else 0.0
    pontos += 3.5 if sinais["pausa_depois_s"] >= 0.25 else 0.0
    return pontos


def pontos_de_abertura(sinais: dict[str, Any]) -> float:
    """0 a 8: não começa dependendo do que foi dito antes."""
    if sinais["abertura_dependente"] or sinais["retomada"]:
        return 0.0
    return 8.0


def pontos_de_gancho(sinais: dict[str, Any]) -> float:
    """0 a 10: tem o que prender a atenção (pergunta, citação) ou fecha com força."""
    pontos = 0.0
    if sinais["pergunta_no_inicio"]:
        pontos += 6.0
    elif sinais["tem_pergunta"]:
        pontos += 3.0
    if sinais["tem_citacao"]:
        pontos += 3.0
    if sinais["fecho_conclusivo"]:
        pontos += 3.0
    return min(pontos, 10.0)


def pontos_de_ritmo(sinais: dict[str, Any]) -> float:
    """0 a 5: fala em ritmo normal, sem buracos."""
    taxa = sinais["palavras_por_segundo"]
    pontos = 3.0 if 1.8 <= taxa <= 3.6 else 1.0 if 1.3 <= taxa <= 4.2 else 0.0
    pontos += 2.0 if sinais["pausa_interna_max_s"] <= 1.5 else 0.0
    return pontos


def pre_nota(c: Candidato) -> float:
    """0 a 100 só com o que se vê no texto e nos tempos; escolhe quem vai para a IA."""
    s = c.sinais
    return (
        pontos_de_duracao(c.duracao) * 2  # 0-20
        + pontos_de_bordas(s) * 2  # 0-30
        + pontos_de_abertura(s) * 2.5  # 0-20
        + pontos_de_gancho(s) * 2  # 0-20
        + pontos_de_ritmo(s) * 2  # 0-10
    )


def escolher_sem_sobreposicao(
    candidatos: Sequence[Candidato], quantos: int, sobreposicao_maxima: float = 0.2
) -> list[Candidato]:
    """Os melhores pela `pre_nota`, sem que dois se sobreponham em mais que a fração dada."""
    escolhidos: list[Candidato] = []
    for c in sorted(candidatos, key=lambda x: (-x.pre_nota, x.inicio)):
        if all(sobreposicao(c, e) <= sobreposicao_maxima for e in escolhidos):
            escolhidos.append(c)
            if len(escolhidos) >= quantos:
                break
    return escolhidos


def sobreposicao(a: Candidato, b: Candidato) -> float:
    """Fração do menor dos dois que está dentro do outro."""
    comum = min(a.fim, b.fim) - max(a.inicio, b.inicio)
    return max(0.0, comum) / max(min(a.duracao, b.duracao), 0.1)
