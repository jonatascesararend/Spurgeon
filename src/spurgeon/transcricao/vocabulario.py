"""Vocabulário teológico: dicas para o Whisper e correção de termos depois da transcrição.

O prompt do Whisper tem espaço para ~224 tokens, então só as primeiras linhas do arquivo vão
como dica (`hotwords`, que entra em TODA janela de 30 s). A lista inteira serve para corrigir,
depois, as palavras de baixa confiança que se parecem com um termo conhecido.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

from spurgeon.transcricao.edicao import separar_pontuacao
from spurgeon.transcricao.formato import Palavra

LIMITE_DICAS_CARACTERES = 300  # ≈ 120 a 150 tokens, bem dentro do limite do Whisper
PROB_MAXIMA_PARA_CORRIGIR = 0.75  # palavras seguras do Whisper não são mexidas
SEMELHANCA_MINIMA = 85
TAMANHO_MINIMO = 5  # palavras curtas demais dão troca errada
_MAX_PALAVRAS_DO_TERMO = 4


@dataclass(frozen=True)
class Correcao:
    antes: str
    depois: str
    inicio: float


def carregar_vocabulario(caminho: Path) -> list[str]:
    """Termos do arquivo, na ordem; ignora linhas vazias, comentários e repetições."""
    if not Path(caminho).is_file():
        return []
    termos: list[str] = []
    vistos: set[str] = set()
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        termo = linha.strip()
        if not termo or termo.startswith("#") or termo.casefold() in vistos:
            continue
        vistos.add(termo.casefold())
        termos.append(termo)
    return termos


def acrescentar_ao_vocabulario(caminho: Path, termo: str) -> bool:
    """Põe o termo no fim do arquivo (cria o arquivo se faltar). `False` se já estava lá."""
    termo = termo.strip()
    if not termo or termo.casefold() in {t.casefold() for t in carregar_vocabulario(caminho)}:
        return False
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    existente = caminho.read_text(encoding="utf-8") if caminho.is_file() else ""
    separador = "" if not existente or existente.endswith("\n") else "\n"
    caminho.write_text(f"{existente}{separador}{termo}\n", encoding="utf-8")
    return True


def montar_dicas(termos: list[str], limite: int = LIMITE_DICAS_CARACTERES) -> str:
    """Os termos do topo da lista, separados por vírgula, até caber no limite."""
    partes: list[str] = []
    tamanho = 0
    for termo in termos:
        acrescimo = len(termo) + (2 if partes else 0)
        if tamanho + acrescimo > limite:
            break
        partes.append(termo)
        tamanho += acrescimo
    return ", ".join(partes)


def corrigir_termos(palavras: list[Palavra], termos: list[str]) -> list[Correcao]:
    """Troca, no lugar, palavras de baixa confiança parecidas com um termo da lista.

    Só mexe em trechos de até 4 palavras cuja confiança média é baixa, e só troca por um termo
    com a mesma quantidade de palavras. Cada troca guarda o texto antigo em `Palavra.original`.
    """
    por_tamanho: dict[int, list[str]] = {}
    for termo in termos:
        n = len(termo.split())
        if 1 <= n <= _MAX_PALAVRAS_DO_TERMO:
            por_tamanho.setdefault(n, []).append(termo)
    conhecidos = {t.casefold() for t in termos}

    correcoes: list[Correcao] = []
    i = 0
    while i < len(palavras):
        trocou = False
        for n in range(min(_MAX_PALAVRAS_DO_TERMO, len(palavras) - i), 0, -1):
            candidatas = por_tamanho.get(n)
            if not candidatas:
                continue
            trecho = palavras[i : i + n]
            nucleos = [separar_pontuacao(p.texto)[1] for p in trecho]
            texto = " ".join(nucleos)
            if (
                len(texto) < TAMANHO_MINIMO
                or texto.casefold() in conhecidos
                or sum(p.prob for p in trecho) / n >= PROB_MAXIMA_PARA_CORRIGIR
            ):
                continue
            # `casefold`: "Espurgeon" × "Spurgeon" dá só 82 se as maiúsculas contarem.
            achado = process.extractOne(
                texto,
                candidatas,
                scorer=fuzz.ratio,
                processor=str.casefold,
                score_cutoff=SEMELHANCA_MINIMA,
            )
            if achado is None:
                continue
            termo = achado[0]
            _trocar(trecho, termo)
            correcoes.append(
                Correcao(" ".join(p.original or p.texto for p in trecho), termo, trecho[0].inicio)
            )
            i += n
            trocou = True
            break
        if not trocou:
            i += 1
    return correcoes


def _trocar(trecho: list[Palavra], termo: str) -> None:
    novas = termo.split()
    for palavra, nova in zip(trecho, novas, strict=True):
        prefixo, nucleo, sufixo = separar_pontuacao(palavra.texto)
        if nova[:1].islower() and nucleo[:1].isupper():  # início de frase: mantém a maiúscula
            nova = nova[:1].upper() + nova[1:]
        palavra.original = palavra.original or palavra.texto
        palavra.texto = f"{prefixo}{nova}{sufixo}"
