"""O ajuste fino da legenda de um Short: cada palavra com o texto e o tempo que a pessoa acertou.

A transcrição às vezes erra o tempo de uma palavra (a legenda fica adiantada ou atrasada) ou come
uma palavra. No ajuste fino a pessoa ouve o trecho e acerta, palavra por palavra, o texto, o início
e o fim, acrescenta a que faltou e tira a que sobrou.

O resultado fica no próprio corte (`Corte.ajuste_fino`), nunca na transcrição: os outros Shorts e o
vídeo longo não mudam. Ele guarda os trechos do sermão que o Short mostrava quando foi ajustado e
as palavras desses trechos; nesses trechos as palavras ajustadas tomam o lugar das da transcrição.
Se a pessoa depois puxa o início ou o fim do Short, o que entrou de novo vem da transcrição e o que
já foi ajustado continua ajustado. Tudo que monta a legenda (render, prévia, audição do celular,
exportar) passa por `palavras_com_a_legenda`, que usa `base_do_corte` daqui.

Quando o texto muda, `Corte.legenda` passa a ter o texto das palavras ajustadas, para as outras
caixas de edição (que trabalham só com o texto) mostrarem o mesmo que a legenda do vídeo.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from spurgeon.shorts.dados import Corte
from spurgeon.transcricao.formato import Palavra

DURACAO_MINIMA_DA_PALAVRA_S = 0.04
_TOLERANCIA_S = 0.001
_PROB_HUMANA = 1.0


class AjusteInvalido(ValueError):
    """O que a pessoa mandou não dá uma legenda (sem palavras, tempo ilegível…)."""


@dataclass(frozen=True)
class PalavraAjustada:
    texto: str
    inicio: float
    fim: float

    def para_lista(self) -> list[Any]:
        return [self.texto, round(self.inicio, 3), round(self.fim, 3)]


def trechos_visiveis(corte: Corte) -> list[tuple[float, float]]:
    """Os pedaços do sermão que o Short mostra: do início ao fim, menos os saltos."""
    trechos: list[tuple[float, float]] = []
    atual = corte.inicio
    for de, ate in sorted((float(a), float(b)) for a, b in corte.saltos):
        if de > atual:
            trechos.append((atual, min(de, corte.fim)))
        atual = max(atual, ate)
    if atual < corte.fim:
        trechos.append((atual, corte.fim))
    return [(a, b) for a, b in trechos if b - a > _TOLERANCIA_S]


def _no_salto(meio: float, saltos: Sequence[Sequence[float]]) -> bool:
    return any(de <= meio <= ate for de, ate in saltos)


def _encosta(p: Palavra, trechos: Iterable[Sequence[float]]) -> bool:
    return any(p.fim > de + _TOLERANCIA_S and p.inicio < ate - _TOLERANCIA_S for de, ate in trechos)


def tem_ajuste(corte: Corte) -> bool:
    return bool(corte.ajuste_fino and corte.ajuste_fino.get("palavras"))


def palavras_ajustadas(corte: Corte) -> list[Palavra]:
    """As palavras guardadas no ajuste (todas, mesmo as que hoje ficam fora do Short)."""
    if not tem_ajuste(corte):
        return []
    lidas = []
    for item in corte.ajuste_fino.get("palavras", []):
        try:
            texto, inicio, fim = str(item[0]), float(item[1]), float(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if texto.strip():
            lidas.append(Palavra(texto, inicio, fim, _PROB_HUMANA))
    return lidas


def base_do_corte(dentro: Sequence[Palavra], corte: Corte) -> list[Palavra]:
    """As palavras do Short (`dentro`, já sem as dos saltos) com o ajuste fino no lugar.

    Nos trechos que foram ajustados valem as palavras ajustadas que ainda caem no Short; fora deles,
    as da transcrição.
    """
    if not tem_ajuste(corte):
        return list(dentro)
    trechos = corte.ajuste_fino.get("trechos") or []
    # a palavra da transcrição que encosta num trecho ajustado (mesmo a que atravessa a ponta do
    # Short) dá lugar às ajustadas; só as de fora, que entraram depois do ajuste, continuam
    fora = [p for p in dentro if not _encosta(p, trechos)]
    ajustadas = [
        p
        for p in palavras_ajustadas(corte)
        if p.fim > corte.inicio + _TOLERANCIA_S
        and p.inicio < corte.fim - _TOLERANCIA_S
        and not _no_salto((p.inicio + p.fim) / 2, corte.saltos)
    ]
    return sorted([*fora, *ajustadas], key=lambda p: (p.inicio, p.fim))


def ler_palavras(itens: Any) -> list[PalavraAjustada]:
    """O que veio da tela ou do celular: `[{"texto", "inicio", "fim"}, …]` ou `[[t, i, f], …]`."""
    if not isinstance(itens, list):
        raise AjusteInvalido("A lista de palavras não veio.")
    lidas = []
    for item in itens:
        try:
            if isinstance(item, dict):
                texto, inicio, fim = item["texto"], item["inicio"], item["fim"]
            else:
                texto, inicio, fim = item[0], item[1], item[2]
            lidas.append(PalavraAjustada(str(texto), float(inicio), float(fim)))
        except (KeyError, IndexError, TypeError, ValueError) as erro:
            raise AjusteInvalido("Uma das palavras veio sem texto ou sem tempo.") from erro
    return lidas


def arrumar(itens: Sequence[PalavraAjustada], corte: Corte) -> list[PalavraAjustada]:
    """Deixa a lista pronta para virar legenda: sem palavras vazias, uma palavra por item (o texto
    com espaço vira várias, repartindo o tempo), dentro do Short, em ordem e sem uma palavra
    passar por cima da seguinte."""
    separadas: list[PalavraAjustada] = []
    for item in itens:
        if not (item.inicio == item.inicio and item.fim == item.fim):  # NaN
            raise AjusteInvalido("Uma das palavras tem um tempo que não se lê.")
        partes = item.texto.split()
        if not partes:
            continue  # texto apagado = palavra tirada
        inicio = min(max(item.inicio, corte.inicio), corte.fim)
        fim = min(max(item.fim, inicio), corte.fim)
        passo = (fim - inicio) / len(partes)
        for k, parte in enumerate(partes):
            separadas.append(PalavraAjustada(parte, inicio + k * passo, inicio + (k + 1) * passo))
    if not separadas:
        raise AjusteInvalido("A legenda ficaria sem nenhuma palavra.")
    separadas.sort(key=lambda p: (p.inicio, p.fim))
    arrumadas: list[PalavraAjustada] = []
    for k, p in enumerate(separadas):
        fim = p.fim
        if k + 1 < len(separadas):
            fim = min(fim, separadas[k + 1].inicio)  # não invade a seguinte
        fim = max(fim, p.inicio + DURACAO_MINIMA_DA_PALAVRA_S)
        arrumadas.append(PalavraAjustada(p.texto, p.inicio, min(fim, max(corte.fim, p.inicio))))
    return arrumadas


def montar_ajuste(corte: Corte, itens: Sequence[PalavraAjustada]) -> dict[str, Any]:
    """O que vai para `Corte.ajuste_fino`: os trechos que o Short mostra agora e as palavras."""
    return {
        "trechos": [[round(a, 3), round(b, 3)] for a, b in trechos_visiveis(corte)],
        "palavras": [p.para_lista() for p in itens],
    }


def aplicar(corte: Corte, originais: Sequence[Palavra], itens: Sequence[PalavraAjustada]) -> Corte:
    """Guarda o ajuste no corte (mexe nele) e acerta `Corte.legenda` com o texto ajustado.

    `originais` são as palavras da transcrição que estão no Short (`palavras_do_corte`): se o texto
    ajustado é igual ao delas, a legenda fica "sem correção" (só os tempos mudaram).
    """
    arrumadas = arrumar(itens, corte)
    corte.ajuste_fino = montar_ajuste(corte, arrumadas)
    final = base_do_corte(originais, corte)
    texto = " ".join(p.texto for p in final)
    corte.legenda = "" if texto == " ".join(p.texto for p in originais) else texto
    return corte


def desfazer(corte: Corte) -> Corte:
    """Tira o ajuste fino: os tempos voltam aos da transcrição. O texto corrigido continua."""
    corte.ajuste_fino = {}
    return corte
