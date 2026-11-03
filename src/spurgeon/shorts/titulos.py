"""Títulos dos Shorts: medir se um título é igual, parecido demais ou genérico demais.

A IA tende a repetir a mesma fórmula ("Por que …?") e a falar do tema geral do sermão, e o simples
"é idêntico a outro?" não pega isso. Este módulo mede, sem IA e sem internet:

* **igual ou parecido**: as palavras de conteúdo (sem artigos, preposições e verbos de ligação, e
  reduzidas ao radical) se repetem em outro título;
* **genérico**: quase só palavras amplas ("Deus", "palavra", "pecado"…) ou o título do sermão
  repetido;
* **abertura saturada**: a mesma abertura ("Por que", "Quem deveria"…) em boa parte dos títulos.

`escolher_titulo` junta as medidas num laço: pede de novo, dizendo à IA por que o título foi
recusado, e no fim fica com o melhor que apareceu (nunca com um título igual, se houve outro).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

#: a partir daqui dois títulos contam como "parecidos demais" (0 a 1; ver `semelhanca`)
LIMITE_DE_SEMELHANCA = 0.55
#: quantas vezes se pede outro título antes de ficar com o melhor que já veio
TENTATIVAS_EXTRAS = 3
#: a temperatura de cada novo pedido: quanto mais recusas, mais solta a IA
TEMPERATURAS_DE_VARIACAO = (0.5, 0.8, 1.0)
#: uma palavra é "radical" pelas primeiras letras: escolher/escolhe/escolheu, pecado/pecados
_TAMANHO_DO_RADICAL = 5
#: a abertura de um título são as suas duas primeiras palavras; a partir de quantos títulos com a
#: mesma abertura, e de que fração dos já criados, a IA deve variar
_ABERTURAS_MINIMAS = 3
_FRACAO_DA_ABERTURA = 0.3
_PALAVRAS_DA_ABERTURA = 2
#: o peso de uma palavra ampla na semelhança (as específicas pesam 1)
_PESO_DA_AMPLA = 0.5
#: o título precisa ter pelo menos tantas palavras que não sejam amplas
_PALAVRAS_ESPECIFICAS_MINIMAS = 2

#: gravidade de cada problema: ao desistir, fica o título de menor gravidade
IGUAL = "igual"
PARECIDO = "parecido"
GENERICO = "generico"
ABERTURA = "abertura"
_GRAVIDADE = {IGUAL: 4, PARECIDO: 3, GENERICO: 2, ABERTURA: 1}


def _sem_acentos(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _lista(texto: str) -> frozenset[str]:
    """As palavras do texto (separadas por espaço), sem acento."""
    return frozenset(_sem_acentos(p) for p in texto.split())


_PALAVRAS_VAZIAS = _lista(
    """
    a o as os um uma uns umas de do da dos das dum duma em no na nos nas num numa por pelo pela
    pelos pelas para pra com sem sob sobre entre ate ao aos ante apos perante contra desde
    ou mas nem que se como quando onde quem qual quais quanto quantos quanta quantas porque
    porem pois logo entao assim tambem ainda so apenas mais menos muito muita muitos muitas pouco
    tao tanto ja nao sim nunca sempre mesmo mesma isso isto aquilo esse essa esses essas este esta
    estes estas aquele aquela aqueles aquelas eu tu ele ela nós vos eles elas me te lhe lhes
    meu minha meus minhas teu tua teus tuas seu sua seus suas nosso nossa nossos nossas
    ser eh é são sao foi foram era eram sera serao seja sendo sido estar está estao estava estamos
    ter tem tinha tenho temos tinham tera haver há havia houve fazer faz fazem fez feito fazendo
    ir vai vão vao vem vir vindo poder pode podem podia podemos dever deve devem deveria deviam
    devemos precisar precisa precisam precisamos precisava ficar fica ficam dar dá dao deu
    cada todo toda todos todas outro outra outros outras algum alguma alguns algumas nenhum
    """
)

#: palavras amplas demais para sustentar um título sozinhas: servem a qualquer parte de um sermão
_PALAVRAS_AMPLAS = _lista(
    """
    deus senhor jesus cristo bíblia palavra escritura evangelho igreja cristão cristãos crente
    fé graça pecado pecados salvação vida amor povo mundo coração verdade santo espírito
    homem homens gente pessoas pessoa coisa coisas tempo dia dias hoje caminho
    """
)

_NAO_E_LETRA = re.compile(r"[^a-z0-9]+")
_NUMERO_NO_COMECO = re.compile(r"^\s*\d+\s*[.\-–:)]\s*")
_PARENTESES = re.compile(r"\([^)]*\)")


def palavras(texto: str) -> list[str]:
    """As palavras do texto em minúsculas e sem acento (números inclusive), na ordem."""
    return [p for p in _NAO_E_LETRA.split(_sem_acentos(texto).casefold()) if p]


def _radical(palavra: str) -> str:
    return palavra[:_TAMANHO_DO_RADICAL]


def _e_vazia(palavra: str) -> bool:
    return palavra in _PALAVRAS_VAZIAS or (len(palavra) < 3 and not palavra.isdigit())


def _e_ampla(palavra: str) -> bool:
    return palavra in _PALAVRAS_AMPLAS or palavra.rstrip("s") in _PALAVRAS_AMPLAS


def termos(texto: str) -> dict[str, float]:
    """Os radicais das palavras que dizem algo (sem as vazias) e o peso de cada um: 1 para as
    específicas, `_PESO_DA_AMPLA` para as amplas ("Deus", "palavra", "pecado"…)."""
    achados: dict[str, float] = {}
    for p in palavras(texto):
        if not _e_vazia(p):
            peso = _PESO_DA_AMPLA if _e_ampla(p) else 1.0
            achados[_radical(p)] = max(achados.get(_radical(p), 0.0), peso)
    return achados


def conteudo(texto: str) -> list[str]:
    """Os radicais das palavras de conteúdo, sem repetir, na ordem."""
    return list(termos(texto))


def especificas(texto: str) -> list[str]:
    """Como `conteudo`, mas só as palavras específicas (sem "Deus", "palavra", "pecado"…)."""
    return [r for r, peso in termos(texto).items() if peso >= 1.0]


def abertura(titulo: str) -> str:
    """As duas primeiras palavras do título ("" se ele é curto demais para ter abertura)."""
    achadas = palavras(titulo)
    return " ".join(achadas[:_PALAVRAS_DA_ABERTURA]) if len(achadas) >= 4 else ""


def iguais(a: str, b: str) -> bool:
    """Só mudam a pontuação, a caixa e os acentos."""
    return palavras(a) == palavras(b)


def semelhanca(a: str, b: str) -> float:
    """O quanto dois títulos se parecem, de 0 a 1, pelas palavras de conteúdo.

    1 = as mesmas palavras de conteúdo. Vale o maior entre a fração do peso das palavras em comum
    no total (Jaccard) e a fração do título menor que está dentro do outro (descontada, e só com
    duas palavras em comum no mínimo: uma palavra só é coincidência). As palavras amplas pesam
    pouco: dois títulos que só dividem "Deus" e "povo" não são parecidos.
    """
    if iguais(a, b):
        return 1.0
    ta, tb = termos(a), termos(b)
    comuns = ta.keys() & tb.keys()
    if not comuns:
        return 0.0
    em_comum = sum(min(ta[r], tb[r]) for r in comuns)
    total_a, total_b = sum(ta.values()), sum(tb.values())
    jaccard = em_comum / (total_a + total_b - em_comum)
    contido = em_comum / min(total_a, total_b) if len(comuns) >= 2 else 0.0
    return max(jaccard, contido * 0.9)


def sao_parecidos(a: str, b: str, limite: float = LIMITE_DE_SEMELHANCA) -> bool:
    return semelhanca(a, b) >= limite


def mais_parecido(titulo: str, outros: Sequence[str]) -> tuple[str, float]:
    """O título de `outros` mais parecido com `titulo` e a semelhança (("", 0.0) se não há)."""
    melhor, nota = "", 0.0
    for outro in outros:
        if not outro.strip():
            continue
        s = semelhanca(titulo, outro)
        if s > nota:
            melhor, nota = outro, s
    return melhor, nota


def nome_do_sermao_sem_numero(sermao: str) -> str:
    """O título do sermão sem o número da frente e sem o texto bíblico entre parênteses."""
    return _PARENTESES.sub(" ", _NUMERO_NO_COMECO.sub("", sermao)).strip()


def repete_o_sermao(titulo: str, sermao: str) -> bool:
    """O título só diz o que o título do sermão já diz: as palavras de um estão todas no outro."""
    do_titulo = set(conteudo(titulo))
    do_sermao = set(conteudo(nome_do_sermao_sem_numero(sermao)))
    if len(do_titulo) < 2 or len(do_sermao) < 2:
        return False
    return do_titulo <= do_sermao or do_sermao <= do_titulo


@dataclass(frozen=True)
class Problema:
    """Por que um título não serve; `motivo` é dito à IA no pedido seguinte."""

    tipo: str
    motivo: str
    semelhanca: float = 0.0

    @property
    def gravidade(self) -> int:
        return _GRAVIDADE[self.tipo]


def problema_do_titulo(
    titulo: str, usados: Sequence[str] = (), *, sermao: str = ""
) -> Problema | None:
    """O que há de errado com o título (o pior problema), ou None se ele serve.

    `usados`: os títulos dos outros Shorts do sermão. `sermao`: o nome do sermão (um título que só
    o repete não diz nada do trecho).
    """
    parecido, nota = mais_parecido(titulo, usados)
    if parecido and iguais(titulo, parecido):
        return Problema(IGUAL, f"é igual ao título “{parecido}” de outro Short", nota)
    if nota >= LIMITE_DE_SEMELHANCA:
        return Problema(
            PARECIDO,
            f"é parecido demais com o título “{parecido}” de outro Short (repete as mesmas ideias)",
            nota,
        )
    if repete_o_sermao(titulo, sermao):
        return Problema(
            GENERICO, "só repete o título do sermão, em vez de dizer o que o trecho tem"
        )
    distintivas = especificas(titulo)
    if len(distintivas) < _PALAVRAS_ESPECIFICAS_MINIMAS:
        return Problema(
            GENERICO,
            "é genérico: fala do tema geral (Deus, a palavra, o pecado…) e serviria para "
            "qualquer parte do sermão; nomeie o que só este trecho diz",
        )
    saturada = abertura_saturada(titulo, usados)
    if saturada:
        return Problema(
            ABERTURA,
            f"começa com “{saturada}”, como muitos títulos já criados; comece de outro jeito",
        )
    return None


def abertura_saturada(titulo: str, usados: Sequence[str]) -> str:
    """A abertura do título se ela já abre muitos dos `usados`; "" se há variedade."""
    nossa = abertura(titulo)
    if not nossa:
        return ""
    titulos = [u for u in usados if u.strip()]
    quantos = sum(1 for u in titulos if abertura(u) == nossa)
    if quantos >= _ABERTURAS_MINIMAS and quantos / len(titulos) >= _FRACAO_DA_ABERTURA:
        return nossa
    return ""


#: o que a IA já ouviu sobre ESTE trecho: (título recusado, motivo)
Recusa = tuple[str, str]


@dataclass(frozen=True)
class Escolha:
    titulo: str
    problema: Problema | None  # o que sobrou de errado (None = serve)
    pedidos: int  # quantas vezes foi preciso pedir outro


def escolher_titulo(
    inicial: str,
    pedir: Callable[[Sequence[Recusa], float], str],
    usados: Sequence[str] = (),
    *,
    sermao: str = "",
    tentativas: int = TENTATIVAS_EXTRAS,
    ao_recusar: Callable[[str, Problema], None] | None = None,
) -> Escolha:
    """Fica com `inicial` se ele serve; senão pede outro (até `tentativas` vezes) e, se nenhum
    servir, com o de menor gravidade (e, empatando, o menos parecido com os `usados`).

    `pedir(recusas, temperatura)` devolve um título novo, sabendo o que já foi recusado.
    `ao_recusar(titulo, problema)` deixa o chamador registrar cada recusa (o log).
    """
    problema = problema_do_titulo(inicial, usados, sermao=sermao)
    candidatos: list[tuple[str, Problema | None]] = [(inicial, problema)]
    recusas: list[Recusa] = []
    pedidos = 0
    while problema is not None and pedidos < tentativas:
        recusas.append((candidatos[-1][0], problema.motivo))
        if ao_recusar is not None:
            ao_recusar(candidatos[-1][0], problema)
        temperatura = TEMPERATURAS_DE_VARIACAO[min(pedidos, len(TEMPERATURAS_DE_VARIACAO) - 1)]
        novo = pedir(tuple(recusas), temperatura).strip()
        pedidos += 1
        problema = problema_do_titulo(novo, usados, sermao=sermao)
        candidatos.append((novo, problema))
    titulo, restante = min(candidatos, key=lambda c: _pior(c[1]))  # empate: o que veio primeiro
    return Escolha(titulo, restante, pedidos)


def _pior(problema: Problema | None) -> tuple[int, float]:
    return (0, 0.0) if problema is None else (problema.gravidade, problema.semelhanca)
