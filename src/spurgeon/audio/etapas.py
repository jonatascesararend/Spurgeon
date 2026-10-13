"""As etapas de áudio da Fase 2, na ordem em que rodam."""

from __future__ import annotations

from spurgeon.audio.deep_filter import LimparAudio
from spurgeon.audio.extrair import ExtrairAudio
from spurgeon.audio.normalizar import NormalizarAudio
from spurgeon.audio.pausas import CalcularCortes, DetectarPausas
from spurgeon.core.pipeline import Etapa


def etapas_audio() -> list[Etapa]:
    return [
        ExtrairAudio(),
        LimparAudio(),
        NormalizarAudio(),
        DetectarPausas(),
        CalcularCortes(),
    ]
