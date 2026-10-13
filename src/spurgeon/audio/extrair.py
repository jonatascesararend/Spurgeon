"""Etapa: extrai o áudio do vídeo para um WAV sem perdas de 48 kHz."""

from __future__ import annotations

import re
from pathlib import Path

from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.pipeline import ContextoProjeto, Etapa

TAXA_HZ = 48000
# Se o canal "lado" (L−R) está abaixo disto em relação ao centro (L+R), os canais são iguais.
LIMITE_DUAL_MONO_DB = -35.0
_TRECHO_PROVA_S = 60.0
_MEDIA = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf)\s*dB")


class ExtrairAudio(Etapa):
    id = "extrair_audio"
    nome = "Extraindo o áudio do vídeo"
    peso = 1.0

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.audio_original]

    def executar(self, ctx: ContextoProjeto) -> None:
        entrada = Path(ctx.estado.entrada.caminho)
        if not entrada.is_file():
            raise ErroSpurgeon(f"O vídeo não está mais em {entrada}.")
        info = sondar(entrada)
        if not info.tem_audio:
            raise ErroSpurgeon("O vídeo não tem trilha de áudio.")

        mono = _e_dual_mono(ctx, entrada, info.duracao_s, info.canais_audio or 1)
        ctx.log.info(
            "O áudio tem %s; extraindo em %s a %d Hz.",
            "dois canais idênticos (dual-mono)" if mono else f"{info.canais_audio or 1} canal(is)",
            "mono" if mono else "o formato original",
            TAXA_HZ,
        )
        # `aresample=async=1:first_pts=0` garante que o áudio comece no tempo zero do vídeo,
        # que é a referência de todos os tempos (transcrição, pausas e legendas).
        filtros = "aresample=async=1:first_pts=0"
        if mono:
            filtros = "pan=mono|c0=0.5*c0+0.5*c1," + filtros
        destino = ctx.pastas.audio_original
        parcial = destino.with_name(destino.name + ".parcial")
        ctx.ffmpeg(
            ["-y", "-i", entrada, "-map", "0:a:0", "-vn", "-af", filtros,
             "-ar", TAXA_HZ, "-c:a", "pcm_s16le", "-f", "wav", parcial],
            duracao_s=info.duracao_s,
        )  # fmt: skip
        parcial.replace(destino)

        duracao = sondar(destino).duracao_s
        ctx.log.info(
            "Áudio extraído: %s (%.0f MB).", formatar_duracao(duracao), destino.stat().st_size / 1e6
        )
        if abs(duracao - info.duracao_s) > 2.0:
            ctx.log.warning(
                "A duração do áudio (%.1f s) difere da do vídeo (%.1f s); os tempos podem "
                "ficar defasados.",
                duracao,
                info.duracao_s,
            )


def _e_dual_mono(ctx: ContextoProjeto, entrada: Path, duracao_s: float, canais: int) -> bool:
    """Compara o lado (L−R) com o centro (L+R) num trecho do meio do vídeo."""
    if canais < 2:
        return False
    inicio = max(0.0, duracao_s / 2 - _TRECHO_PROVA_S / 2)
    lado = _volume_medio(ctx, entrada, inicio, "pan=mono|c0=0.5*c0-0.5*c1")
    centro = _volume_medio(ctx, entrada, inicio, "pan=mono|c0=0.5*c0+0.5*c1")
    return lado - centro < LIMITE_DUAL_MONO_DB


def _volume_medio(ctx: ContextoProjeto, entrada: Path, inicio: float, filtro: str) -> float:
    saida = ctx.ffmpeg(
        ["-ss", inicio, "-t", _TRECHO_PROVA_S, "-i", entrada, "-map", "0:a:0",
         "-af", f"{filtro},volumedetect", "-f", "null", "-"],
    )  # fmt: skip
    achado = _MEDIA.search(saida)
    if achado is None or achado.group(1) == "-inf":
        return -120.0  # silêncio digital
    return float(achado.group(1))
