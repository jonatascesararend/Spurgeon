"""Etapas: encontra as pausas longas do áudio e calcula os trechos que ficam no vídeo.

A detecção é por energia, sobre as janelas RMS de 50 ms medidas por `MedirNivel`, com limiar
adaptado ao ruído de fundo da própria gravação. Não usamos o `silencedetect` do FFmpeg: ele
compara amostras instantâneas, cujos picos ficam 10 a 15 dB acima do RMS do ruído. Cortar
fala é o erro caro, então o critério é conservador: só entra como pausa o que fica abaixo do
limiar durante todo o intervalo. Depois da transcrição, `RefinarPausas` tira das pausas
qualquer trecho em que o Whisper ouviu uma palavra.
"""

from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

from spurgeon.audio.nivel import JANELA_S, Medida, carregar_niveis
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.formatacao import formatar_duracao, formatar_numero
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.core.tempo import calcular_trechos_mantidos

# O menor tamanho de pausa que a interface permite (0,5 s). Detectamos tudo a partir daqui;
# o limiar escolhido pelo usuário é aplicado depois, em `CalcularCortes`.
DURACAO_MINIMA_S = 0.5
#: Tamanhos mínimos de pausa que o log compara, para o usuário escolher com dados.
LIMIARES_SUGERIDOS_S = (1.0, 2.0, 3.0, 5.0, 10.0)


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


def subtrair_intervalos(
    pausas: list[tuple[float, float]],
    fala: list[tuple[float, float]],
    folga_s: float = 0.15,
    duracao_minima_s: float = DURACAO_MINIMA_S,
) -> list[tuple[float, float]]:
    """Tira de cada pausa os trechos de fala (alargados por `folga_s`).

    Sobram só os pedaços de `duracao_minima_s` ou mais. Serve para nunca cortar onde o Whisper
    ouviu uma palavra, mesmo que a energia ali esteja baixa (fala bem baixinha).
    """
    juntos: list[list[float]] = []
    for ini, fim in sorted((max(0.0, a - folga_s), b + folga_s) for a, b in fala):
        if juntos and ini <= juntos[-1][1]:
            juntos[-1][1] = max(juntos[-1][1], fim)
        else:
            juntos.append([ini, fim])
    inicios = [j[0] for j in juntos]

    resultado: list[tuple[float, float]] = []
    for ini, fim in pausas:
        cursor = ini
        i = max(bisect_right(inicios, ini) - 1, 0)  # a fala que já começou antes da pausa
        while i < len(juntos) and juntos[i][0] < fim:
            fala_ini, fala_fim = juntos[i]
            if fala_fim > cursor:
                if fala_ini > cursor and fala_ini - cursor >= duracao_minima_s:
                    resultado.append((cursor, fala_ini))
                cursor = max(cursor, fala_fim)
            i += 1
        if fim - cursor >= duracao_minima_s:
            resultado.append((cursor, fim))
    return resultado


class DetectarPausas(Etapa):
    id = "detectar_pausas"
    nome = "Procurando as pausas longas"
    depende_de = ("medir_nivel",)
    peso = 0.5

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.pausas]

    def executar(self, ctx: ContextoProjeto) -> None:
        niveis = carregar_niveis(ctx.pastas.niveis)
        pausas = encontrar_pausas(
            niveis.medidas, niveis.limiar_db, DURACAO_MINIMA_S, niveis.duracao_s
        )
        escrever_json_atomico(
            ctx.pastas.pausas,
            {
                "duracao_s": niveis.duracao_s,
                "piso_db": niveis.nivel.piso_db,
                "fala_db": niveis.nivel.fala_db,
                "limiar_db": niveis.limiar_db,
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
    # `refinar_pausas` só existe quando a transcrição está no pipeline; o motor ignora a
    # dependência de etapas que não fazem parte da lista.
    depende_de = ("detectar_pausas", "refinar_pausas")
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
        refinadas = "pausas_refinadas" in dados
        pausas = [(a, b) for a, b in dados.get("pausas_refinadas", dados["pausas"])]
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
                "refinadas_pela_transcricao": refinadas,
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
