"""Ajustes que a pessoa faz nos cortes na tela de aprovação (sem depender da interface)."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from spurgeon.core.config import DURACAO_MAXIMA_SHORT_S
from spurgeon.core.formatacao import formatar_tempo_preciso, ler_tempo  # noqa: F401 (reexporta)
from spurgeon.render.legenda import blocos_de_linhas, palavras_do_short, texto_em_blocos
from spurgeon.shorts.analise import separar_frases
from spurgeon.shorts.dados import Corte, Frase
from spurgeon.shorts.selecao import respiro_nas_pontas
from spurgeon.transcricao.edicao import aplicar_edicao, separar_pontuacao
from spurgeon.transcricao.formato import Palavra

DURACAO_MAXIMA_S = DURACAO_MAXIMA_SHORT_S  # acima disso o corte não pode virar Short
DURACAO_MINIMA_S = 10.0
NOTA_ALTA = 70.0
NOTA_MEDIA = 50.0
_TOLERANCIA_S = 0.001
_PAUSA_SEM_VIZINHA_S = 5.0

Ponta = Literal["inicio", "fim"]
PontaDaMudanca = Literal["inicio", "fim", "meio"]  # `meio`: palavras tiradas ou postas no meio
AVISO_ACIMA_DO_LIMITE = f"Com mais de {DURACAO_MAXIMA_S:g} segundos o vídeo não pode ser um Short."


class LimiteInvalido(ValueError):
    """O ajuste deixaria o corte longo ou curto demais, ou fora do sermão."""


def faixa_da_nota(nota: float) -> Literal["alta", "media", "baixa"]:
    if nota >= NOTA_ALTA:
        return "alta"
    return "media" if nota >= NOTA_MEDIA else "baixa"


def _no_salto(p: Palavra, saltos: Sequence[Sequence[float]]) -> bool:
    """A palavra foi tirada do Short por um salto (cai dentro de um pedaço cortado)?"""
    meio = (p.inicio + p.fim) / 2
    return any(de <= meio <= ate for de, ate in saltos)


def indices_incluidos(palavras: Sequence[Palavra], corte: Corte) -> list[int]:
    """Posição, na transcrição, de cada palavra que está no Short: as do corte, menos as que os
    saltos tiram (em ordem)."""
    return [
        i
        for i, p in enumerate(palavras)
        if p.fim > corte.inicio + _TOLERANCIA_S
        and p.inicio < corte.fim - _TOLERANCIA_S
        and not _no_salto(p, corte.saltos)
    ]


def indices_das_palavras(palavras: Sequence[Palavra], corte: Corte) -> tuple[int, int]:
    """Posição da primeira e da última palavra que estão dentro do corte."""
    dentro = indices_incluidos(palavras, corte)
    if not dentro:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    return dentro[0], dentro[-1]


def faixas_de(indices: Iterable[int]) -> list[tuple[int, int]]:
    """`[3, 4, 5, 9, 10]` → `[(3, 5), (9, 10)]`: as sequências de palavras seguidas."""
    faixas: list[tuple[int, int]] = []
    for i in sorted(set(indices)):
        if faixas and faixas[-1][1] == i - 1:
            faixas[-1] = (faixas[-1][0], i)
        else:
            faixas.append((i, i))
    return faixas


def faixas_do_corte(palavras: Sequence[Palavra], corte: Corte) -> list[tuple[int, int]]:
    """As sequências de palavras que o Short junta (uma só, se não há saltos)."""
    return faixas_de(indices_incluidos(palavras, corte))


def _com_faixas(
    palavras: Sequence[Palavra], corte: Corte, faixas: Sequence[tuple[int, int]]
) -> Corte:
    """O corte que junta estas faixas de palavras, nesta ordem, cada ponta com o respiro de
    costume (nunca mais que metade da pausa vizinha). O que fica entre duas faixas vira um salto."""
    limites = []
    for primeira, ultima in faixas:
        antes = palavras[primeira].inicio - palavras[primeira - 1].fim if primeira > 0 else None
        depois = (
            palavras[ultima + 1].inicio - palavras[ultima].fim
            if ultima + 1 < len(palavras)
            else None
        )
        limites.append(
            respiro_nas_pontas(
                palavras[primeira].inicio,
                palavras[ultima].fim,
                _PAUSA_SEM_VIZINHA_S if antes is None else antes,
                _PAUSA_SEM_VIZINHA_S if depois is None else depois,
            )
        )
    saltos = [
        [limites[k][1], limites[k + 1][0]]
        for k in range(len(limites) - 1)
        if limites[k + 1][0] - limites[k][1] > _TOLERANCIA_S
    ]
    textos = [palavras[i].texto for primeira, ultima in faixas for i in range(primeira, ultima + 1)]
    return replace(
        corte,
        inicio=limites[0][0],
        fim=limites[-1][1],
        texto=" ".join(textos),
        saltos=saltos,
        editado=True,
        legenda="",  # as correções valiam para as palavras de antes: o corte agora é outro
    )


def _com_limites(palavras: Sequence[Palavra], corte: Corte, primeira: int, ultima: int) -> Corte:
    return _com_faixas(palavras, corte, [(primeira, ultima)])


def corte_com_indices(palavras: Sequence[Palavra], corte: Corte, indices: Iterable[int]) -> Corte:
    """O corte que tem exatamente estas palavras (posições na transcrição). Se não são seguidas,
    o que fica entre elas vira salto. Levanta `LimiteInvalido` se não sobra palavra nenhuma."""
    faixas = faixas_de(i for i in indices if 0 <= i < len(palavras))
    if not faixas:
        raise LimiteInvalido("O corte ficaria sem nenhuma palavra.")
    return _com_faixas(palavras, corte, faixas)


def incluir_palavras(palavras: Sequence[Palavra], corte: Corte, indices: Iterable[int]) -> Corte:
    """O corte com estas palavras a mais (a ferramenta marca-texto)."""
    return corte_com_indices(palavras, corte, [*indices_incluidos(palavras, corte), *indices])


def tirar_palavras(palavras: Sequence[Palavra], corte: Corte, indices: Iterable[int]) -> Corte:
    """O corte sem estas palavras (a ferramenta borracha): as do meio viram salto, as das pontas
    encurtam o Short. Levanta `LimiteInvalido` se não sobra palavra nenhuma."""
    fora = set(indices)
    return corte_com_indices(
        palavras, corte, [i for i in indices_incluidos(palavras, corte) if i not in fora]
    )


def abaixo_do_minimo(corte: Corte) -> bool:
    """O Short ficou curto demais (menos de 10 s) para ser publicado?"""
    return corte.duracao < DURACAO_MINIMA_S


def passa_do_limite(corte: Corte) -> bool:
    """O corte passa do limite de um Short (180 s) (e por isso não pode ser um Short)?"""
    return corte.duracao > DURACAO_MAXIMA_S + _TOLERANCIA_S


def mover_limite(palavras: Sequence[Palavra], corte: Corte, ponta: Ponta, passo: int) -> Corte:
    """Um novo corte com o início (ou o fim) deslocado `passo` palavras (+ avança, − recua).

    Sempre cai em fronteira de palavra, com o respiro de costume. Passar do limite é permitido (a
    tela avisa, pois acima dele o corte não pode ser Short). Levanta `LimiteInvalido` se o
    resultado ficar com menos de 10 s ou sair da transcrição. Num corte com saltos, avançar tira
    as primeiras (ou últimas) palavras que estão no Short, e recuar acrescenta as que vêm antes.
    """
    incluidas = indices_incluidos(palavras, corte)
    if not incluidas:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    if ponta == "inicio":
        if passo >= 0:
            novas = incluidas[passo:]
        else:
            if incluidas[0] + passo < 0:
                raise LimiteInvalido("Não há mais fala nesse sentido.")
            novas = [*range(incluidas[0] + passo, incluidas[0]), *incluidas]
    elif passo >= 0:
        if incluidas[-1] + passo >= len(palavras):
            raise LimiteInvalido("Não há mais fala nesse sentido.")
        novas = [*incluidas, *range(incluidas[-1] + 1, incluidas[-1] + passo + 1)]
    else:
        novas = incluidas[:passo]
    if not novas:
        raise LimiteInvalido("O corte ficaria sem nenhuma palavra.")
    novo = corte_com_indices(palavras, corte, novas)
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
    incluidas = indices_incluidos(palavras, corte)
    if not incluidas:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    if ponta == "inicio":
        pontos = sorted({f.primeira for f in frases})
        atual = incluidas[0]
    else:
        pontos = sorted({f.ultima for f in frases})
        atual = incluidas[-1]
    alvos = [p for p in pontos if (p > atual if sentido > 0 else p < atual)]
    if not alvos:
        raise LimiteInvalido("Não há mais fala nesse sentido.")
    alvo = alvos[0] if sentido > 0 else alvos[-1]
    if ponta == "inicio":
        novas = (
            [i for i in incluidas if i >= alvo]
            if sentido > 0
            else [*range(alvo, atual), *incluidas]
        )
    else:
        novas = (
            [*incluidas, *range(atual + 1, alvo + 1)]
            if sentido > 0
            else [i for i in incluidas if i <= alvo]
        )
    if not novas:
        raise LimiteInvalido("O corte ficaria sem nenhuma palavra.")
    novo = corte_com_indices(palavras, corte, novas)
    if novo.duracao < DURACAO_MINIMA_S:
        raise LimiteInvalido(f"O corte ficaria com menos de {DURACAO_MINIMA_S:g} segundos.")
    return novo


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


@dataclass(frozen=True)
class PalavraNaJanela:
    indice: int  # posição da palavra na transcrição inteira
    texto: str
    dentro: bool  # a palavra faz parte do corte?


@dataclass(frozen=True)
class JanelaDeContexto:
    """Um trecho da transcrição ao redor de uma ponta do corte, com o que fica dentro e fora."""

    ponta: Ponta
    palavras: tuple[PalavraNaJanela, ...]
    indice_do_limite: int  # a primeira palavra do corte (início) ou a última (fim)


def janela_de_contexto(
    palavras: Sequence[Palavra],
    corte: Corte,
    ponta: Ponta,
    *,
    fora: int = 220,
    dentro: int = 60,
) -> JanelaDeContexto:
    """As palavras ao redor do início (ou do fim) do corte: `fora` além da ponta e `dentro` aquém.

    Dá uma janela grande para a pessoa rolar o texto e ver o que fica de fora do Short (por exemplo
    o começo de uma pergunta ou o argumento anterior), sem perder de vista onde o corte começa.
    """
    primeira, ultima = indices_das_palavras(palavras, corte)
    if ponta == "inicio":
        de, ate, limite = (
            max(0, primeira - fora),
            min(len(palavras) - 1, primeira + dentro),
            primeira,
        )
    else:
        de, ate, limite = max(0, ultima - dentro), min(len(palavras) - 1, ultima + fora), ultima
    itens = tuple(
        PalavraNaJanela(i, palavras[i].texto, primeira <= i <= ultima) for i in range(de, ate + 1)
    )
    return JanelaDeContexto(ponta, itens, limite)


@dataclass(frozen=True)
class JanelaDoCorte:
    """O corte inteiro e a transcrição ao redor dele, numa lista só (para uma caixa só)."""

    palavras: tuple[PalavraNaJanela, ...]
    primeira: int  # a primeira palavra do corte
    ultima: int  # a última


def janela_do_corte(palavras: Sequence[Palavra], corte: Corte, *, fora: int = 220) -> JanelaDoCorte:
    """Todas as palavras do corte, mais `fora` palavras antes do começo e depois do fim."""
    primeira, ultima = indices_das_palavras(palavras, corte)
    de, ate = max(0, primeira - fora), min(len(palavras) - 1, ultima + fora)
    itens = tuple(
        PalavraNaJanela(i, palavras[i].texto, primeira <= i <= ultima) for i in range(de, ate + 1)
    )
    return JanelaDoCorte(itens, primeira, ultima)


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
    incluidas = indices_incluidos(palavras, corte)
    if not incluidas:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    if ponta == "inicio":
        alvo = min(range(len(palavras)), key=lambda i: abs(palavras[i].inicio - segundos))
        if alvo > incluidas[-1]:
            raise LimiteInvalido("O início ficaria depois do fim.")
        novas = (
            [i for i in incluidas if i >= alvo]
            if alvo >= incluidas[0]
            else [*range(alvo, incluidas[0]), *incluidas]
        )
    else:
        alvo = min(range(len(palavras)), key=lambda i: abs(palavras[i].fim - segundos))
        if alvo < incluidas[0]:
            raise LimiteInvalido("O início ficaria depois do fim.")
        novas = (
            [i for i in incluidas if i <= alvo]
            if alvo <= incluidas[-1]
            else [*incluidas, *range(incluidas[-1] + 1, alvo + 1)]
        )
    novo = corte_com_indices(palavras, corte, novas)
    if novo.duracao < DURACAO_MINIMA_S:
        raise LimiteInvalido(f"O corte ficaria com menos de {DURACAO_MINIMA_S:g} segundos.")
    return novo


def palavras_do_corte(palavras: Sequence[Palavra], corte: Corte) -> list[Palavra]:
    """As palavras da transcrição que estão dentro do corte (menos as que os saltos tiram)."""
    return [
        p
        for p in palavras
        if p.fim > corte.inicio + _TOLERANCIA_S
        and p.inicio < corte.fim - _TOLERANCIA_S
        and not _no_salto(p, corte.saltos)
    ]


def legenda_do_corte(palavras: Sequence[Palavra], corte: Corte) -> str:
    """O que aparece na legenda: a versão corrigida pela pessoa ou a da transcrição."""
    return corte.legenda or " ".join(p.texto for p in palavras_do_corte(palavras, corte))


def legenda_em_blocos(palavras: Sequence[Palavra], corte: Corte, texto: str | None = None) -> str:
    """A legenda do corte dividida como vai aparecer no vídeo.

    Cada bloco tem até 2 linhas (separadas por Enter) e os blocos são separados por uma linha em
    branco: a mesma divisão que a legenda queimada usa. `texto` é o que a pessoa digitou (para
    reorganizar depois de uma edição); sem ele vale a legenda do corte.
    """
    dentro = palavras_do_corte(palavras, corte)
    if texto is None:
        base = palavras_com_a_legenda(palavras, corte)
    else:
        base = aplicar_edicao(dentro, texto) if dentro else []
    return texto_em_blocos(blocos_de_linhas(palavras_do_short(base, corte.inicio, corte.fim)))


def legenda_corrigida(palavras: Sequence[Palavra], corte: Corte, texto: str) -> str:
    """O texto a guardar em `Corte.legenda`: vazio se a pessoa não mudou nada."""
    normal = " ".join(texto.split())
    original = " ".join(p.texto for p in palavras_do_corte(palavras, corte))
    return "" if normal == original else normal


def legenda_preservada(palavras: Sequence[Palavra], antes: Corte, depois: Corte) -> str:
    """A legenda corrigida de `antes` adaptada a `depois`, que mudou as palavras do Short.

    As palavras que continuam no corte mantêm a correção; as que entraram vêm como a transcrição as
    tem; as que saíram levam a correção embora. Devolve o texto a guardar em `Corte.legenda`
    (vazio se, no fim, nada difere da transcrição, ou se `antes` não tinha correção).
    """
    if not antes.legenda:
        return ""
    antigas, novas = indices_incluidos(palavras, antes), indices_incluidos(palavras, depois)
    comuns = set(antigas) & set(novas)
    if not comuns:  # nenhuma palavra continua: as correções não valem mais
        return ""
    trechos = [(palavras[de].inicio, palavras[ate].fim) for de, ate in faixas_de(comuns)]
    corrigidas = palavras_com_a_legenda(palavras, antes)

    def continua(p: Palavra) -> bool:
        meio = (p.inicio + p.fim) / 2
        return any(de - _TOLERANCIA_S <= meio <= ate for de, ate in trechos)

    mantidas = [p for p in corrigidas if continua(p)]
    entradas = [palavras[i] for i in sorted(set(novas) - set(antigas))]
    ordenadas = sorted([*mantidas, *entradas], key=lambda p: (p.inicio, p.fim))
    return legenda_corrigida(palavras, depois, " ".join(p.texto for p in ordenadas))


# --- a tabela de frases da janela do corte ---------------------------------------------------


@dataclass(frozen=True)
class LinhaDaTabela:
    """Uma linha da tabela da janela: uma frase, ou o pedaço dela que fica de um lado do corte.

    `fora` é o que o Short não pega; `dentro`, o que ele tem; `cortado`, o que um salto tirou de
    dentro dele. `corta` e `volta` são marcadores (sem palavras): onde um pedaço sai do Short e
    onde ele continua. O começo e o fim do Short não têm marcador: é onde o texto muda de cinza
    para branco.
    """

    tipo: Literal["fora", "dentro", "cortado", "corta", "volta"]
    tempo: float = 0.0  # onde a primeira palavra da linha foi dita, em segundos
    textos: tuple[str, ...] = ()
    indices: tuple[int, ...] = ()  # posição de cada palavra na transcrição (vazio = não se sabe)

    @property
    def texto(self) -> str:
        return " ".join(self.textos)

    @property
    def e_marcador(self) -> bool:
        return self.tipo in ("corta", "volta")


def _fecha_frase(texto: str) -> bool:
    return texto.rstrip().rstrip("\"'”’»)]").endswith((".", "?", "!", "…"))


def linhas_da_tabela(
    palavras: Sequence[Palavra],
    corte: Corte,
    frases: Sequence[Frase],
    *,
    fora: int = 220,
    texto_dentro: str | None = None,
) -> list[LinhaDaTabela]:
    """O corte e o que há ao redor, uma frase por linha: as de fora, as do Short (com os pedaços
    cortados e seus marcadores) e as de fora de novo.

    Mostra todas as palavras do corte mais `fora` palavras antes do começo e depois do fim. Uma
    frase que o corte atravessa aparece em duas linhas, uma de cada lado (a de fora e a do Short).
    `texto_dentro` é a legenda como está sendo editada (`None` = a guardada no corte; vazio = a da
    transcrição): se difere da transcrição, as linhas do Short saem dela, quebradas na pontuação, e
    sem `indices`.
    """
    incluidas = indices_incluidos(palavras, corte)
    if not incluidas:
        raise LimiteInvalido("Não há fala da transcrição neste corte.")
    primeira, ultima = incluidas[0], incluidas[-1]
    de, ate = max(0, primeira - fora), min(len(palavras) - 1, ultima + fora)
    no_short = set(incluidas)
    inicios_de_frase = {f.primeira for f in frases}

    regioes: list[tuple[str, list[int]]] = []
    for i in range(de, ate + 1):
        tipo = "fora" if i < primeira or i > ultima else ("dentro" if i in no_short else "cortado")
        if regioes and regioes[-1][0] == tipo:
            regioes[-1][1].append(i)
        else:
            regioes.append((tipo, [i]))

    def por_frases(indices: list[int], tipo: Literal["fora", "dentro", "cortado"]):
        linhas: list[LinhaDaTabela] = []
        atual: list[int] = []
        for i in indices:
            if atual and i in inicios_de_frase:
                linhas.append(_linha_de_indices(palavras, atual, tipo))
                atual = []
            atual.append(i)
        if atual:
            linhas.append(_linha_de_indices(palavras, atual, tipo))
        return linhas

    dentro = palavras_do_corte(palavras, corte)
    escrita = corte.legenda if texto_dentro is None else texto_dentro
    corrigidas = aplicar_edicao(dentro, escrita) if escrita.strip() and dentro else dentro
    editado = [p.texto for p in corrigidas] != [p.texto for p in dentro]

    # as palavras corrigidas vão para o pedaço do Short em que foram ditas
    por_pedaco: dict[int, list[Palavra]] = {}
    if editado:
        pedacos = [r for r in regioes if r[0] == "dentro"]
        comecos = [palavras[r[1][0]].inicio for r in pedacos]
        for p in corrigidas:
            k = max(0, bisect_right(comecos, (p.inicio + p.fim) / 2 + _TOLERANCIA_S) - 1)
            por_pedaco.setdefault(k, []).append(p)

    linhas: list[LinhaDaTabela] = []
    visto_dentro = False
    numero_do_pedaco = -1
    for tipo, indices in regioes:
        if tipo == "fora":
            linhas += por_frases(indices, "fora")
        elif tipo == "cortado":
            linhas.append(LinhaDaTabela("corta"))
            linhas += por_frases(indices, "cortado")
        else:
            numero_do_pedaco += 1
            if visto_dentro:
                linhas.append(LinhaDaTabela("volta"))
            visto_dentro = True
            if not editado:
                linhas += por_frases(indices, "dentro")
            else:
                atual_p: list[Palavra] = []
                for p in por_pedaco.get(numero_do_pedaco, []):
                    atual_p.append(p)
                    if _fecha_frase(p.texto):
                        linhas.append(_linha_de_palavras(atual_p))
                        atual_p = []
                if atual_p:
                    linhas.append(_linha_de_palavras(atual_p))
    return linhas


def _linha_de_indices(
    palavras: Sequence[Palavra],
    indices: list[int],
    tipo: Literal["fora", "dentro", "cortado"],
) -> LinhaDaTabela:
    return LinhaDaTabela(
        tipo,
        tempo=palavras[indices[0]].inicio,
        textos=tuple(palavras[i].texto for i in indices),
        indices=tuple(indices),
    )


def _linha_de_palavras(palavras: Sequence[Palavra]) -> LinhaDaTabela:
    return LinhaDaTabela(
        "dentro", tempo=palavras[0].inicio, textos=tuple(p.texto for p in palavras)
    )


def palavras_com_a_legenda(palavras: Sequence[Palavra], corte: Corte) -> list[Palavra]:
    """As palavras do corte com a legenda corrigida aplicada, mantendo os tempos da fala."""
    dentro = palavras_do_corte(palavras, corte)
    if not corte.legenda or not dentro:
        return dentro
    return aplicar_edicao(dentro, corte.legenda)


@dataclass(frozen=True)
class MudancaDeLimite:
    """O que um ajuste acrescentou ou tirou do corte, palavra por palavra: nas pontas ou no meio."""

    inicio_acrescentou: tuple[str, ...] = ()
    inicio_tirou: tuple[str, ...] = ()
    fim_acrescentou: tuple[str, ...] = ()
    fim_tirou: tuple[str, ...] = ()
    meio_acrescentou: tuple[str, ...] = ()
    meio_tirou: tuple[str, ...] = ()

    @property
    def houve(self) -> bool:
        return bool(
            self.inicio_acrescentou
            or self.inicio_tirou
            or self.fim_acrescentou
            or self.fim_tirou
            or self.meio_acrescentou
            or self.meio_tirou
        )

    @property
    def ponta(self) -> PontaDaMudanca | None:
        """Onde o corte mudou (o fim, se mudou nas duas pontas; `meio` se só mudou por dentro)."""
        if self.fim_acrescentou or self.fim_tirou:
            return "fim"
        if self.inicio_acrescentou or self.inicio_tirou:
            return "inicio"
        if self.meio_acrescentou or self.meio_tirou:
            return "meio"
        return None


def comparar_cortes(palavras: Sequence[Palavra], antes: Corte, depois: Corte) -> MudancaDeLimite:
    """As palavras que entraram e saíram do corte entre `antes` e `depois`."""
    antigas, novas = indices_incluidos(palavras, antes), indices_incluidos(palavras, depois)
    if not antigas or not novas:
        return MudancaDeLimite()

    def textos(indices) -> tuple[str, ...]:
        return tuple(palavras[i].texto for i in indices)

    def repartir(
        indices: list[int], primeira: int, ultima: int
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """(antes da primeira, depois da última, no meio): o texto de cada grupo."""
        return (
            textos(i for i in indices if i < primeira),
            textos(i for i in indices if i > ultima),
            textos(i for i in indices if primeira <= i <= ultima),
        )

    acrescentadas = sorted(set(novas) - set(antigas))
    tiradas = sorted(set(antigas) - set(novas))
    inicio_mais, fim_mais, meio_mais = repartir(acrescentadas, antigas[0], antigas[-1])
    inicio_menos, fim_menos, meio_menos = repartir(tiradas, novas[0], novas[-1])
    return MudancaDeLimite(
        inicio_acrescentou=inicio_mais,
        inicio_tirou=inicio_menos,
        fim_acrescentou=fim_mais,
        fim_tirou=fim_menos,
        meio_acrescentou=meio_mais,
        meio_tirou=meio_menos,
    )


def contexto_da_legenda(
    palavras: Sequence[Palavra], corte: Corte, quantas: int = 30
) -> tuple[str, str]:
    """(o que vem logo antes do corte, o que vem logo depois): fica de fora da legenda."""
    try:
        primeira, ultima = indices_das_palavras(palavras, corte)
    except LimiteInvalido:
        return "", ""
    antes = " ".join(p.texto for p in palavras[max(0, primeira - quantas) : primeira])
    depois = " ".join(p.texto for p in palavras[ultima + 1 : ultima + 1 + quantas])
    return antes, depois
