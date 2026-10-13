"""As etapas de áudio da Fase 2, na ordem em que rodam."""

from __future__ import annotations

from spurgeon.audio.deep_filter import LimparAudio
from spurgeon.audio.extrair import ExtrairAudio
from spurgeon.audio.nivel import MedirNivel
from spurgeon.audio.normalizar import NormalizarAudio
from spurgeon.audio.pausas import CalcularCortes, DetectarPausas
from spurgeon.core.pipeline import Etapa


def etapas_audio_base() -> list[Etapa]:
    """Preparam o áudio; a transcrição vem logo depois delas."""
    return [ExtrairAudio(), LimparAudio(), NormalizarAudio(), MedirNivel()]


def etapas_audio() -> list[Etapa]:
    """O pipeline de áudio completo, sem a transcrição (as pausas só por energia)."""
    return [*etapas_audio_base(), DetectarPausas(), CalcularCortes()]
