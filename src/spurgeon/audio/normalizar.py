"""Etapa: normaliza o volume do áudio (EBU R128, loudnorm em duas passagens)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_numero
from spurgeon.core.pipeline import ContextoProjeto, Etapa

PICO_VERDADEIRO_DB = -1.5
FAIXA_DINAMICA_LU = 11
_JSON_LOUDNORM = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def ler_medicao(saida_ffmpeg: str) -> dict[str, float]:
    """Extrai o bloco JSON que o loudnorm imprime ao medir. Valores `-inf` viram -120."""
    blocos = _JSON_LOUDNORM.findall(saida_ffmpeg)
    if not blocos:
        raise ErroSpurgeon("Não foi possível ler a medição de volume do FFmpeg.")
    bruto = json.loads(blocos[-1])
    return {chave: _numero(valor) for chave, valor in bruto.items() if _eh_numero(valor)}


def _eh_numero(valor: str) -> bool:
    try:
        _numero(valor)
    except ValueError:
        return False
    return True


def _numero(valor: str) -> float:
    texto = str(valor).strip().lower()
    if texto in ("-inf", "-infinity"):
        return -120.0
    if texto in ("inf", "infinity"):
        return 120.0
    return float(texto)


def filtro_medir(alvo_lufs: float) -> str:
    return (
        f"loudnorm=I={alvo_lufs}:TP={PICO_VERDADEIRO_DB}:LRA={FAIXA_DINAMICA_LU}:print_format=json"
    )


def filtro_aplicar(alvo_lufs: float, medido: dict[str, float]) -> str:
    """Segunda passagem, com os valores medidos: ganho linear, sem comprimir a dinâmica."""
    return (
        f"loudnorm=I={alvo_lufs}:TP={PICO_VERDADEIRO_DB}:LRA={FAIXA_DINAMICA_LU}"
        f":measured_I={medido['input_i']}:measured_TP={medido['input_tp']}"
        f":measured_LRA={medido['input_lra']}:measured_thresh={medido['input_thresh']}"
        f":offset={medido['target_offset']}:linear=true:print_format=json"
    )


class NormalizarAudio(Etapa):
    id = "normalizar_audio"
    nome = "Normalizando o volume"
    depende_de = ("extrair_audio", "limpar_audio")
    opcoes_relevantes = ("limpar_audio", "lufs_video_longo")
    peso = 2.0

    def ativa(self, opcoes) -> bool:
        return opcoes.normalizar_volume

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.audio_normalizado]

    def executar(self, ctx: ContextoProjeto) -> None:
        entrada = ctx.pastas.audio_base(ctx.opcoes)
        destino = ctx.pastas.audio_normalizado
        alvo = ctx.opcoes.lufs_video_longo
        duracao = sondar(entrada).duracao_s

        medido = ler_medicao(
            ctx.ffmpeg(
                ["-i", entrada, "-af", filtro_medir(alvo), "-f", "null", "-"],
                duracao_s=duracao,
                faixa=(0.0, 0.45),
            )
        )
        antes = medido["input_i"]
        if antes <= -70:
            ctx.log.warning("O áudio está praticamente mudo (%.0f LUFS); volume mantido.", antes)
            destino.write_bytes(entrada.read_bytes())
            return
        ctx.log.info(
            "Volume medido: %s LUFS; alvo: %s LUFS (ajuste de %s dB).",
            formatar_numero(antes),
            formatar_numero(alvo),
            formatar_numero(alvo - antes),
        )

        parcial = destino.with_name(destino.name + ".parcial")
        ctx.ffmpeg(
            ["-y", "-i", entrada, "-af", filtro_aplicar(alvo, medido),
             "-ar", 48000, "-c:a", "pcm_s16le", "-f", "wav", parcial],
            duracao_s=duracao,
            faixa=(0.45, 0.9),
        )  # fmt: skip
        parcial.replace(destino)

        depois = ler_medicao(
            ctx.ffmpeg(
                ["-i", destino, "-af", filtro_medir(alvo), "-f", "null", "-"],
                duracao_s=duracao,
                faixa=(0.9, 1.0),
            )
        )
        ctx.log.info(
            "Volume final medido: %s LUFS (pico verdadeiro %s dB).",
            formatar_numero(depois["input_i"]),
            formatar_numero(depois["input_tp"]),
        )
