"""Chamada ao Whisper para um trecho do áudio, devolvendo palavras com tempos absolutos."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from spurgeon.transcricao.edicao import separar_pontuacao
from spurgeon.transcricao.formato import Palavra, Segmento

TAXA_HZ = 16000
#: Mude quando mudar qualquer parâmetro de decodificação: invalida blocos já gravados.
VERSAO_DOS_PARAMETROS = 1


def parametros_de_decodificacao(dicas: str) -> dict[str, Any]:
    """Os parâmetros do `WhisperModel.transcribe`, num lugar só.

    * `condition_on_previous_text=False`: em gravações longas evita repetições em laço; como
      isso faz o `initial_prompt` valer só na primeira janela, o vocabulário vai em `hotwords`,
      que entra em toda janela.
    * `vad_filter=True`: ignora silêncio e ruído, que o Whisper tende a "transcrever".
    * `hallucination_silence_threshold`: descarta frases inventadas sobre trechos mudos.
    """
    return {
        "language": "pt",
        "beam_size": 5,
        "word_timestamps": True,
        "vad_filter": True,
        "condition_on_previous_text": False,
        "hotwords": dicas or None,
        "hallucination_silence_threshold": 2.0,
    }


def transcrever_trecho(
    modelo: Any,
    audio: Any,
    inicio_s: float,
    fim_s: float,
    dicas: str,
    ao_segmento: Callable[[float], None] | None = None,
) -> list[Segmento]:
    """Transcreve `audio[inicio_s:fim_s]`; os tempos das palavras são do áudio inteiro.

    `ao_segmento` recebe o fim (em segundos do áudio inteiro) de cada segmento pronto; é por
    onde a etapa informa o progresso e atende pausa e cancelamento.
    """
    fatia = audio[int(inicio_s * TAXA_HZ) : int(fim_s * TAXA_HZ)]
    segmentos, _ = modelo.transcribe(fatia, **parametros_de_decodificacao(dicas))
    resultado: list[Segmento] = []
    for bruto in segmentos:  # é um gerador: a transcrição acontece aqui
        palavras = _palavras_do_segmento(bruto, inicio_s)
        if palavras:
            resultado.append(Segmento(palavras))
        if ao_segmento:
            ao_segmento(inicio_s + bruto.end)
    return resultado


def _palavras_do_segmento(bruto: Any, deslocamento_s: float) -> list[Palavra]:
    palavras: list[Palavra] = []
    for w in bruto.words or []:
        texto = w.word.strip()
        if not texto:
            continue
        inicio, fim = deslocamento_s + w.start, deslocamento_s + w.end
        _, nucleo, _ = separar_pontuacao(texto)
        if not nucleo and palavras:  # pontuação solta: cola na palavra anterior
            palavras[-1].texto += texto
            palavras[-1].fim = max(palavras[-1].fim, fim)
            continue
        palavras.append(Palavra(texto, inicio, max(fim, inicio), float(w.probability)))
    return palavras
