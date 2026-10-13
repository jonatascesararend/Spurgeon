"""Edição da transcrição preservando os tempos das palavras.

Quem revisa corrige o texto; os tempos das palavras (usados nas legendas e no karaokê dos
Shorts) precisam continuar certos. Palavras que ficam iguais mantêm o tempo; as trocadas em
número igual herdam o tempo das antigas; as que mudam de quantidade dividem o intervalo
das antigas em proporção ao tamanho.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from spurgeon.transcricao.formato import Palavra, Segmento, Transcricao

_PROB_HUMANA = 1.0


def aplicar_edicao(palavras: list[Palavra], texto_novo: str) -> list[Palavra]:
    """Devolve as palavras do segmento depois de a pessoa ter reescrito o texto."""
    novos = texto_novo.split()
    antigos = [p.texto for p in palavras]
    if novos == antigos:
        return palavras
    if not novos:
        return []

    inicio_segmento, fim_segmento = palavras[0].inicio, palavras[-1].fim
    resultado: list[Palavra] = []
    comparacao = SequenceMatcher(a=antigos, b=novos, autojunk=False)
    for tag, i1, i2, j1, j2 in comparacao.get_opcodes():
        if tag == "equal":
            resultado.extend(palavras[i1:i2])
        elif tag == "delete":
            continue
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for antiga, texto in zip(palavras[i1:i2], novos[j1:j2], strict=True):
                resultado.append(
                    Palavra(texto, antiga.inicio, antiga.fim, _PROB_HUMANA, antiga.texto)
                )
        else:  # insert, ou replace com quantidades diferentes
            if i2 > i1:
                inicio, fim = palavras[i1].inicio, palavras[i2 - 1].fim
                original = " ".join(antigos[i1:i2])
            else:  # só inserção: ocupa a folga entre as vizinhas
                inicio = palavras[i1 - 1].fim if i1 > 0 else inicio_segmento
                fim = palavras[i1].inicio if i1 < len(palavras) else fim_segmento
                original = None
            resultado.extend(_distribuir(novos[j1:j2], inicio, max(fim, inicio), original))
    return resultado


def _distribuir(
    textos: list[str], inicio: float, fim: float, original: str | None
) -> list[Palavra]:
    """Reparte [inicio, fim] entre as palavras, em proporção ao número de letras."""
    pesos = [max(len(t), 1) for t in textos]
    total = sum(pesos)
    palavras = []
    cursor = inicio
    for i, (texto, peso) in enumerate(zip(textos, pesos, strict=True)):
        final = fim if i == len(textos) - 1 else cursor + (fim - inicio) * peso / total
        palavras.append(Palavra(texto, cursor, final, _PROB_HUMANA, original if i == 0 else None))
        cursor = final
    return palavras


def aplicar_edicoes(transcricao: Transcricao, textos: list[str]) -> int:
    """Aplica um texto por segmento (na ordem). Devolve quantos segmentos mudaram.

    Um segmento cujo texto fica vazio é removido.
    """
    segmentos = [(b, s) for b in transcricao.blocos for s in b.segmentos]
    if len(textos) != len(segmentos):
        raise ValueError(
            f"A transcrição tem {len(segmentos)} segmentos, mas vieram {len(textos)} textos."
        )
    mudados = 0
    for (bloco, segmento), texto in zip(segmentos, textos, strict=True):
        novas = aplicar_edicao(segmento.palavras, texto)
        if novas is segmento.palavras:
            continue
        mudados += 1
        if novas:
            segmento.palavras = novas
        else:
            bloco.segmentos = [s for s in bloco.segmentos if s is not segmento]
    if mudados:
        transcricao.revisada = True
    return mudados


def trocar_em_tudo(transcricao: Transcricao, procurar: str, trocar_por: str) -> int:
    """Troca `procurar` por `trocar_por` em todas as palavras (útil para nomes recorrentes).

    A busca ignora maiúsculas e pontuação nas pontas; a pontuação e a inicial maiúscula do
    original são preservadas. Devolve quantas palavras mudaram.
    """
    alvo = procurar.strip().casefold()
    if not alvo or " " in alvo or not trocar_por.strip() or " " in trocar_por.strip():
        raise ValueError("Use uma palavra só em cada campo.")
    trocadas = 0
    for palavra in transcricao.palavras:
        prefixo, nucleo, sufixo = separar_pontuacao(palavra.texto)
        if nucleo.casefold() != alvo or nucleo == trocar_por.strip():
            continue
        novo = trocar_por.strip()
        if nucleo[:1].isupper() and novo[:1].islower():
            novo = novo[:1].upper() + novo[1:]
        palavra.original = palavra.original or palavra.texto
        palavra.texto = f"{prefixo}{novo}{sufixo}"
        palavra.prob = _PROB_HUMANA
        trocadas += 1
    if trocadas:
        transcricao.revisada = True
    return trocadas


def separar_pontuacao(token: str) -> tuple[str, str, str]:
    """`"(Deus),"` → (`"("`, `"Deus"`, `"),"`)."""
    inicio = 0
    fim = len(token)
    while inicio < fim and not token[inicio].isalnum():
        inicio += 1
    while fim > inicio and not token[fim - 1].isalnum():
        fim -= 1
    return token[:inicio], token[inicio:fim], token[fim:]


def segmento_de_texto(texto: str, inicio: float, fim: float) -> Segmento:
    """Cria um segmento a partir de texto puro, repartindo o tempo (uso em testes e ferramentas)."""
    return Segmento(_distribuir(texto.split(), inicio, fim, None))
