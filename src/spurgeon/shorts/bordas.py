"""Início e fim de um Short ajustados pelo nível do áudio, não só pelos tempos do Whisper.

Os tempos de palavra do Whisper erram por dezenas de milissegundos: o fim de uma palavra costuma
vir antes de a voz acabar de verdade e o começo, depois. Um respiro fixo ao redor deles corta o
final de uma sílaba ou o ataque da primeira palavra. Aqui o corte procura o **silêncio de verdade**
(janelas de 50 ms medidas na etapa `medir_nivel`) mais perto da fala: começa um pouco antes de a voz
subir e termina um pouco depois de ela morrer, sem invadir a palavra vizinha.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence

from spurgeon.shorts.dados import Candidato

JANELA_S = 0.05
DURACAO_MAXIMA_S = 60.0
RECUO_MAXIMO_NO_INICIO_S = 0.5  # quanto antes da 1ª palavra se aceita procurar silêncio
AVANCO_MAXIMO_NO_FIM_S = 0.8  # quanto depois da última palavra se espera a voz morrer
SILENCIO_ANTES_DA_VOZ_S = 0.10  # o fade de entrada (100 ms) cabe todo no silêncio
SILENCIO_DEPOIS_DA_VOZ_S = 0.10
_FOLGA_DA_PALAVRA_VIZINHA_S = 0.02

#: (tempo em segundos, nível RMS em dB) de uma janela de 50 ms, em ordem de tempo
Medida = tuple[float, float]


def _janelas(medidas: Sequence[Medida], de: float, ate: float) -> dict[int, float]:
    """As janelas inteiras dentro de [de, ate]: número da janela (tempo / 50 ms) → nível em dB."""
    tempos = [t for t, _ in medidas]
    achadas: dict[int, float] = {}
    for i in range(bisect_left(tempos, de - 1e-9), len(medidas)):
        t, nivel = medidas[i]
        if t + JANELA_S > ate + 1e-9:
            break
        achadas[round(t / JANELA_S)] = nivel
    return achadas


def _corrida_silenciosa(
    janelas: dict[int, float], limiar_db: float, numero: int, passo: int
) -> int:
    """Anda de janela em janela (para trás ou para a frente) enquanto o silêncio continua."""
    while janelas.get(numero + passo, limiar_db) < limiar_db:
        numero += passo
    return numero


def _inicio_do_corte(
    primeira_palavra: float, pausa_antes_s: float, medidas: Sequence[Medida], limiar_db: float
) -> float:
    anterior = max(0.0, primeira_palavra - pausa_antes_s)
    de = max(anterior + _FOLGA_DA_PALAVRA_VIZINHA_S, primeira_palavra - RECUO_MAXIMO_NO_INICIO_S)
    janelas = _janelas(medidas, de, primeira_palavra)
    if not janelas:  # colada na frase anterior: o respiro curto de sempre
        return max(0.0, primeira_palavra - min(0.2, max(0.0, pausa_antes_s) / 2))
    silenciosas = [n for n, nivel in janelas.items() if nivel < limiar_db]
    if not silenciosas:  # sem silêncio: o ponto mais baixo (o mais perto da fala, se empatar)
        numero = min(janelas, key=lambda n: (janelas[n], -n))
        return numero * JANELA_S
    ultima = max(silenciosas)
    comeco_do_silencio = _corrida_silenciosa(janelas, limiar_db, ultima, -1) * JANELA_S
    fim_do_silencio = (ultima + 1) * JANELA_S  # onde a voz volta a subir
    return max(comeco_do_silencio, fim_do_silencio - SILENCIO_ANTES_DA_VOZ_S, 0.0)


def _fim_do_corte(
    ultima_palavra: float, pausa_depois_s: float, medidas: Sequence[Medida], limiar_db: float
) -> float:
    proxima = ultima_palavra + max(0.0, pausa_depois_s)
    ate = min(proxima - _FOLGA_DA_PALAVRA_VIZINHA_S, ultima_palavra + AVANCO_MAXIMO_NO_FIM_S)
    janelas = _janelas(medidas, ultima_palavra - JANELA_S, ate)
    if ate - ultima_palavra < JANELA_S or not janelas:
        return ultima_palavra + min(0.35, max(0.0, pausa_depois_s) / 2)
    silenciosas = [n for n, nivel in janelas.items() if nivel < limiar_db]
    if not silenciosas:  # a voz não morre antes da próxima palavra: o ponto mais baixo
        numero = min(janelas, key=lambda n: (janelas[n], n))
        fim = (numero + 1) * JANELA_S
    else:
        primeira = min(silenciosas)
        fim_do_silencio = (_corrida_silenciosa(janelas, limiar_db, primeira, 1) + 1) * JANELA_S
        fim = min(primeira * JANELA_S + SILENCIO_DEPOIS_DA_VOZ_S, fim_do_silencio)
    return max(fim, ultima_palavra + 0.03)


def refinar_bordas(
    candidato: Candidato,
    medidas: Sequence[Medida],
    limiar_db: float,
    *,
    maximo_s: float = DURACAO_MAXIMA_S,
) -> tuple[float, float]:
    """(início, fim) do Short, em silêncio de verdade e com no máximo `maximo_s` segundos."""
    sinais = candidato.sinais
    inicio = _inicio_do_corte(
        candidato.inicio, float(sinais.get("pausa_antes_s", 0.0)), medidas, limiar_db
    )
    fim = _fim_do_corte(candidato.fim, float(sinais.get("pausa_depois_s", 0.0)), medidas, limiar_db)
    if fim - inicio > maximo_s:  # tira primeiro a folga do começo, depois a do fim
        inicio = max(inicio, min(candidato.inicio, fim - maximo_s))
        fim = min(fim, inicio + maximo_s)
    return inicio, fim
