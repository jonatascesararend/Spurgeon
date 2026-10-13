"""Etapas: encontra as pausas longas do áudio e calcula os trechos que ficam no vídeo.

A detecção aqui é por energia, com limiar adaptado ao ruído de fundo da própria gravação.
O nível é medido em janelas de 50 ms (RMS) e o limiar sai dessa mesma medida; usar o
`silencedetect` do FFmpeg não serviria, pois ele compara amostras instantâneas, cujos picos
ficam 10 a 15 dB acima do RMS do ruído. Cortar fala é o erro caro, então o critério é
conservador: só entra como pausa o que fica abaixo do limiar durante todo o intervalo. A Fase 3
refinará isso com os tempos das palavras do Whisper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao, formatar_numero
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.core.tempo import calcular_trechos_mantidos

JANELA_AMOSTRAS = 2400  # 50 ms a 48 kHz (o áudio é sempre reamostrado para 48 kHz na extração)
JANELA_S = 0.05
NIVEL_MUDO_DB = -120.0  # silêncio digital: o astats imprime -inf
# O menor tamanho de pausa que a interface permite (0,5 s). Detectamos tudo a partir daqui;
# o limiar escolhido pelo usuário é aplicado depois, em `CalcularCortes`.
DURACAO_MINIMA_S = 0.5
_FRACAO_ACIMA_DO_PISO = 0.25
_MARGEM_MINIMA_DB = 4.0
_MARGEM_ATE_A_FALA_DB = 12.0
_CHAVE_RMS = "lavfi.astats.Overall.RMS_level="
_TEMPO = re.compile(r"pts_time:\s*(-?[\d.]+)")

#: (tempo em segundos, nível RMS em dB) de uma janela de 50 ms
Medida = tuple[float, float]


@dataclass(frozen=True)
class NivelAudio:
    piso_db: float  # ruído de fundo: percentil 10 do nível em janelas de 50 ms
    fala_db: float  # fala: percentil 90


def ler_niveis(texto: str) -> list[Medida]:
    """Lê o que o `ametadata` imprimiu: uma linha `frame:` com o tempo e a linha do nível.

    Janelas de silêncio digital (`-inf`) valem `NIVEL_MUDO_DB`, para contarem como silêncio.
    """
    medidas: list[Medida] = []
    tempo: float | None = None
    for linha in texto.splitlines():
        if linha.startswith("frame:"):
            achado = _TEMPO.search(linha)
            tempo = float(achado.group(1)) if achado else None
        elif linha.startswith(_CHAVE_RMS) and tempo is not None:
            try:
                valor = float(linha[len(_CHAVE_RMS) :].strip())  # aceita "-inf"
            except ValueError:
                continue
            if valor == valor:  # descarta nan
                medidas.append((tempo, max(valor, NIVEL_MUDO_DB)))
    return medidas


def calcular_nivel(medidas: list[Medida]) -> NivelAudio:
    """Piso e fala a partir dos percentis 10 e 90 das janelas que têm algum sinal."""
    valores = [nivel for _, nivel in medidas if nivel > NIVEL_MUDO_DB]
    if len(valores) < 20:
        raise ErroSpurgeon("O áudio é curto ou silencioso demais para medir o nível de ruído.")
    ordenados = sorted(valores)
    return NivelAudio(
        piso_db=ordenados[int(0.10 * (len(ordenados) - 1))],
        fala_db=ordenados[int(0.90 * (len(ordenados) - 1))],
    )


def limiar_silencio_db(nivel: NivelAudio) -> float:
    """Um pouco acima do ruído de fundo, e sempre bem abaixo do nível da fala."""
    acima_do_piso = max(_MARGEM_MINIMA_DB, _FRACAO_ACIMA_DO_PISO * (nivel.fala_db - nivel.piso_db))
    return min(nivel.piso_db + acima_do_piso, nivel.fala_db - _MARGEM_ATE_A_FALA_DB)


def encontrar_pausas(
    medidas: list[Medida], limiar_db: float, duracao_minima_s: float, duracao_total_s: float
) -> list[tuple[float, float]]:
    """Intervalos em que todas as janelas ficam no limiar ou abaixo, por `duracao_minima_s`."""
    pausas: list[tuple[float, float]] = []
    inicio: float | None = None
    fim = 0.0
    for tempo, nivel in medidas:
        if nivel <= limiar_db:
            if inicio is None:
                inicio = tempo
            fim = tempo + JANELA_S
        elif inicio is not None:
            if fim - inicio >= duracao_minima_s:
                pausas.append((inicio, min(fim, duracao_total_s)))
            inicio = None
    if inicio is not None and fim - inicio >= duracao_minima_s:  # a gravação acaba em silêncio
        pausas.append((inicio, min(fim, duracao_total_s)))
    return pausas


#: Tamanhos mínimos de pausa que o log compara, para o usuário escolher com dados.
LIMIARES_SUGERIDOS_S = (1.0, 2.0, 3.0, 5.0, 10.0)


def resumo_por_limiar(
    pausas: list[tuple[float, float]],
    residual_s: float,
    limiares: tuple[float, ...] = LIMIARES_SUGERIDOS_S,
) -> list[tuple[float, int, float]]:
    """Para cada tamanho mínimo: quantas pausas seriam cortadas e quantos segundos sairiam."""
    resumo = []
    for limiar in limiares:
        cortes = [
            (b - a) - residual_s for a, b in pausas if (b - a) >= limiar and (b - a) > residual_s
        ]
        resumo.append((limiar, len(cortes), sum(cortes)))
    return resumo


class DetectarPausas(Etapa):
    id = "detectar_pausas"
    nome = "Procurando as pausas longas"
    depende_de = ("extrair_audio", "limpar_audio")
    opcoes_relevantes = ("limpar_audio",)
    peso = 2.0

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.pausas]

    def executar(self, ctx: ContextoProjeto) -> None:
        audio = ctx.pastas.audio_base(ctx.opcoes)
        duracao = sondar(audio).duracao_s
        trabalho = ctx.pastas.work

        # O FFmpeg escreve as medições num arquivo, e o caminho é relativo à pasta de
        # trabalho (um "C:" dentro de um filtro precisaria de escape).
        arquivo_niveis = trabalho / "niveis.txt"
        ctx.ffmpeg(
            ["-i", audio, "-af",
             f"asetnsamples=n={JANELA_AMOSTRAS}:p=0,astats=metadata=1:reset=1,"
             f"ametadata=mode=print:key={_CHAVE_RMS[:-1]}:file={arquivo_niveis.name}",
             "-f", "null", "-"],
            duracao_s=duracao, cwd=trabalho,
        )  # fmt: skip
        medidas = ler_niveis(arquivo_niveis.read_text(encoding="utf-8"))
        arquivo_niveis.unlink(missing_ok=True)
        nivel = calcular_nivel(medidas)
        limiar = limiar_silencio_db(nivel)
        ctx.log.info(
            "Ruído de fundo: %s dB; fala: %s dB; limiar de silêncio: %s dB.",
            formatar_numero(nivel.piso_db),
            formatar_numero(nivel.fala_db),
            formatar_numero(limiar),
        )

        pausas = encontrar_pausas(medidas, limiar, DURACAO_MINIMA_S, duracao)

        escrever_json_atomico(
            ctx.pastas.pausas,
            {
                "duracao_s": duracao,
                "piso_db": nivel.piso_db,
                "fala_db": nivel.fala_db,
                "limiar_db": limiar,
                "duracao_minima_s": DURACAO_MINIMA_S,
                "pausas": [[round(a, 3), round(b, 3)] for a, b in pausas],
            },
        )
        longas = [p for p in pausas if p[1] - p[0] >= ctx.opcoes.limiar_pausa_s]
        ctx.log.info(
            "%d pausas de %s s ou mais; %d com %s s ou mais.",
            len(pausas),
            formatar_numero(DURACAO_MINIMA_S),
            len(longas),
            formatar_numero(ctx.opcoes.limiar_pausa_s),
        )
        residual = ctx.opcoes.pausa_residual_s
        ctx.log.info(
            "Quanto cada tamanho mínimo de pausa cortaria (mantendo %s s em cada corte):",
            formatar_numero(residual),
        )
        for limiar, quantas, segundos in resumo_por_limiar(pausas, residual):
            ctx.log.info(
                "   %s s ou mais: %d pausas, %s",
                formatar_numero(limiar).rjust(4),
                quantas,
                formatar_duracao(segundos) if segundos >= 1 else "nada",
            )


class CalcularCortes(Etapa):
    id = "calcular_cortes"
    nome = "Calculando os cortes do vídeo"
    depende_de = ("detectar_pausas",)
    opcoes_relevantes = ("limiar_pausa_s", "pausa_residual_s")
    peso = 0.2

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.cortes]

    def executar(self, ctx: ContextoProjeto) -> None:
        dados = ler_json(ctx.pastas.pausas)
        duracao = float(dados["duracao_s"])
        limiar = ctx.opcoes.limiar_pausa_s
        residual = ctx.opcoes.pausa_residual_s
        pausas = [(a, b) for a, b in dados["pausas"]]
        trechos = calcular_trechos_mantidos(duracao, pausas, limiar, residual)
        editada = sum(t.duracao for t in trechos)
        cortadas = sum(1 for a, b in pausas if b - a >= limiar and (b - a) > residual)

        escrever_json_atomico(
            ctx.pastas.cortes,
            {
                "duracao_original_s": duracao,
                "duracao_editada_s": editada,
                "removido_s": duracao - editada,
                "limiar_pausa_s": limiar,
                "pausa_residual_s": residual,
                "pausas_cortadas": cortadas,
                "trechos": [[round(t.inicio, 3), round(t.fim, 3)] for t in trechos],
            },
        )
        if cortadas:
            ctx.log.info(
                "%d pausas de %s s ou mais serão cortadas (restam %s s em cada): o vídeo de %s "
                "fica com %s.",
                cortadas,
                formatar_numero(limiar),
                formatar_numero(residual),
                formatar_duracao(duracao),
                formatar_duracao(editada),
            )
        else:
            ctx.log.info(
                "Nenhuma pausa de %s s ou mais: o vídeo de %s não terá cortes.",
                formatar_numero(limiar),
                formatar_duracao(duracao),
            )
