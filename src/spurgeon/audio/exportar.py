"""Etapa do modo "Só o áudio": grava o áudio do sermão num arquivo, na pasta do projeto.

O áudio é o mesmo que o vídeo longo usaria (`audio_final`): limpo e normalizado se a pessoa pediu,
e da trilha de fora se ela escolheu uma. O começo e o fim do vídeo valem aqui também.
"""

from __future__ import annotations

from pathlib import Path

from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.pipeline import ContextoProjeto, Etapa

#: o codificador e a qualidade de cada formato (o MP3 em VBR ~190 kb/s: voz sem perda audível)
ARGUMENTOS_DO_FORMATO = {
    "wav": ["-c:a", "pcm_s16le", "-f", "wav"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3"],
}
CODIFICADOR_DO_FORMATO = {"wav": "pcm_s16le", "mp3": "libmp3lame"}


def filtro_da_janela(inicio_s: float, fim_s: float, duracao_s: float) -> str | None:
    """O `atrim` do começo e do fim escolhidos (None quando o áudio vai inteiro)."""
    if inicio_s <= 0 and fim_s >= duracao_s:
        return None
    return f"atrim=start={inicio_s:.3f}:end={fim_s:.3f},asetpts=PTS-STARTPTS"


class ExportarAudio(Etapa):
    id = "exportar_audio"
    nome = "Gravando o arquivo de áudio"
    depende_de = ("extrair_audio", "limpar_audio", "normalizar_audio")
    opcoes_relevantes = (
        "limpar_audio", "normalizar_volume", "lufs_video_longo", "audio_formato",
        "video_inicio_s", "video_fim_s",
    )  # fmt: skip
    peso = 1.0
    entrega = True
    no_modo_audio = True

    def ativa(self, opcoes) -> bool:
        return opcoes.so_audio

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.audio_exportado(ctx.opcoes.audio_formato)]

    def executar(self, ctx: ContextoProjeto) -> None:
        fonte = ctx.pastas.audio_final(ctx.opcoes)
        formato = ctx.opcoes.audio_formato
        destino = ctx.pastas.audio_exportado(formato)
        duracao = sondar(fonte).duracao_s
        inicio, fim = ctx.opcoes.janela_do_video(duracao)
        argumentos: list = ["-y", "-i", fonte, "-map", "0:a:0", "-vn"]
        if filtro := filtro_da_janela(inicio, fim, duracao):
            argumentos += ["-af", filtro]
            ctx.log.info(
                "Só o trecho de %s a %s entra no arquivo.",
                formatar_duracao(inicio),
                formatar_duracao(fim),
            )
        parcial = destino.with_name(destino.name + ".parcial")
        ctx.ffmpeg(
            [*argumentos, "-ar", 48000, *ARGUMENTOS_DO_FORMATO[formato], parcial],
            duracao_s=fim - inicio,
        )
        parcial.replace(destino)
        ctx.log.info(
            "Áudio gravado: %s (%s, %.0f MB).",
            destino.name,
            formatar_duracao(fim - inicio),
            destino.stat().st_size / 1e6,
        )
