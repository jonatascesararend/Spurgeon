"""Remove trechos que o Whisper inventa quando o áudio acaba ou recomeça em silêncio.

Em português ele costuma "completar" um silêncio com despedidas de vídeo ("Obrigado por
assistir"), vistas em milhares de legendas de treino. O critério é de propósito estrito: a
frase típica só é removida se vier acompanhada de um sinal físico de erro, para não apagar
um "obrigado" dito de verdade.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from statistics import mean

from spurgeon.transcricao.formato import Segmento, Transcricao

FRASES_SUSPEITAS = (
    "obrigado por assistir",
    "obrigada por assistir",
    "obrigado por ter assistido",
    "legendas pela comunidade",
    "legenda pela comunidade",
    "amara org",
    "inscreva se no canal",
    "se inscreva no canal",
    "curta o video",
    "ate o proximo video",
    "nos vemos no proximo video",
)
#: A fala humana mais rápida passa pouco de 7 palavras por segundo.
PALAVRAS_POR_SEGUNDO_SUSPEITAS = 8.0
#: Acima disto não é fala: é um texto espremido num instante.
PALAVRAS_POR_SEGUNDO_IMPOSSIVEIS = 15.0
CONFIANCA_SUSPEITA = 0.6
_NAO_LETRA = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Removido:
    inicio: float
    texto: str
    motivo: str


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto.casefold())
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return _NAO_LETRA.sub(" ", sem_acento).strip()


def motivo_da_alucinacao(segmento: Segmento) -> str | None:
    """Por que este segmento parece inventado, ou None se parece fala de verdade."""
    palavras = segmento.palavras
    duracao = max(segmento.fim - segmento.inicio, 0.01)
    densidade = len(palavras) / duracao
    if len(palavras) >= 3 and densidade > PALAVRAS_POR_SEGUNDO_IMPOSSIVEIS:
        return f"{len(palavras)} palavras em {duracao:.2f} s (impossível falar tão rápido)"
    texto = _normalizar(segmento.texto)
    frase = next((f for f in FRASES_SUSPEITAS if f in texto), None)
    if frase is None:
        return None
    if len(palavras) >= 3 and densidade > PALAVRAS_POR_SEGUNDO_SUSPEITAS:
        return f"frase típica de alucinação ({frase!r}) dita rápido demais"
    if mean(p.prob for p in palavras) < CONFIANCA_SUSPEITA:
        return f"frase típica de alucinação ({frase!r}) com confiança baixa"
    return None


def remover_alucinacoes(transcricao: Transcricao) -> list[Removido]:
    """Tira da transcrição os segmentos que parecem inventados e devolve o que saiu."""
    removidos: list[Removido] = []
    for bloco in transcricao.blocos:
        mantidos: list[Segmento] = []
        for segmento in bloco.segmentos:
            motivo = motivo_da_alucinacao(segmento)
            if motivo is None:
                mantidos.append(segmento)
            else:
                removidos.append(Removido(segmento.inicio, segmento.texto, motivo))
        bloco.segmentos = mantidos
    transcricao.removidos.extend(
        {"inicio": round(r.inicio, 2), "texto": r.texto, "motivo": r.motivo} for r in removidos
    )
    return removidos
