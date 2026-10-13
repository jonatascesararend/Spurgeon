"""Mede a velocidade do Whisper nesta máquina com um trecho curto, para estimar o tempo total."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from spurgeon.core.config import ConfigApp
from spurgeon.core.ffmpeg import executar_ffmpeg, sondar
from spurgeon.core.hardware import detectar_hardware, recomendar, threads_para_uso
from spurgeon.transcricao.modelo import baixar_modelo, carregar_modelo, modelo_baixado
from spurgeon.transcricao.motor import TAXA_HZ, parametros_de_decodificacao


@dataclass(frozen=True)
class ResultadoBenchmark:
    modelo: str
    trecho_s: float
    carga_s: float
    transcricao_s: float
    palavras: int
    threads: int
    dispositivo: str

    @property
    def fator(self) -> float:
        """Quantas vezes o tempo real a transcrição leva (0,7 = 70% da duração do áudio)."""
        return self.transcricao_s / self.trecho_s

    def segundos_para(self, duracao_s: float) -> float:
        """Quanto tempo levaria transcrever `duracao_s` segundos de áudio nesta velocidade."""
        return self.fator * duracao_s


def medir_velocidade(
    config: ConfigApp,
    modelo: str,
    midia: Path,
    *,
    trecho_s: float = 60.0,
    uso_cpu_percentual: int = 75,
) -> ResultadoBenchmark:
    """Transcreve `trecho_s` segundos do meio de `midia` e mede quanto tempo levou."""
    from faster_whisper.audio import decode_audio

    if not modelo_baixado(config, modelo):
        baixar_modelo(config, modelo)
    duracao = sondar(midia).duracao_s
    inicio = max(0.0, duracao / 2 - trecho_s / 2)
    trecho_s = min(trecho_s, duracao)
    with tempfile.TemporaryDirectory() as pasta:
        wav = Path(pasta) / "trecho.wav"
        executar_ffmpeg(
            ["-y", "-ss", inicio, "-t", trecho_s, "-i", midia, "-map", "0:a:0", "-vn",
             "-ac", 1, "-ar", TAXA_HZ, "-c:a", "pcm_s16le", wav],
        )  # fmt: skip
        audio = decode_audio(str(wav), sampling_rate=TAXA_HZ)

    perfil = detectar_hardware(config.pastas.projetos)
    recomendacao = recomendar(perfil)
    threads = threads_para_uso(perfil, uso_cpu_percentual)
    t0 = time.monotonic()
    whisper = carregar_modelo(
        config,
        modelo,
        dispositivo=recomendacao.whisper_dispositivo,
        compute_type=recomendacao.whisper_compute_type,
        threads=threads,
    )
    carga = time.monotonic() - t0

    t0 = time.monotonic()
    segmentos, _ = whisper.transcribe(audio, **parametros_de_decodificacao(""))
    palavras = sum(len(s.words or []) for s in segmentos)  # o gerador só trabalha ao ser lido
    gasto = time.monotonic() - t0
    return ResultadoBenchmark(
        modelo=modelo,
        trecho_s=len(audio) / TAXA_HZ,
        carga_s=carga,
        transcricao_s=gasto,
        palavras=palavras,
        threads=threads,
        dispositivo=recomendacao.whisper_dispositivo,
    )
