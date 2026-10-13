"""Registro das etapas do pipeline, na ordem em que rodam.

As Fases 4 e 5 acrescentam aqui as etapas de Shorts e de render.
"""

from __future__ import annotations

import os

from spurgeon.core.pipeline import Etapa

VARIAVEL_DEMO = "SPURGEON_DEMO"


def etapas_registradas() -> list[Etapa]:
    if os.environ.get(VARIAVEL_DEMO) == "1":
        from spurgeon.core.etapas_demo import etapas_demo

        return etapas_demo()
    from spurgeon.audio.etapas import etapas_audio_base
    from spurgeon.audio.pausas import CalcularCortes, DetectarPausas
    from spurgeon.transcricao.etapas import (
        GerarSrt,
        RefinarPausas,
        RevisarTranscricao,
        Transcrever,
    )

    return [
        *etapas_audio_base(),
        Transcrever(),
        RevisarTranscricao(),
        DetectarPausas(),
        RefinarPausas(),
        CalcularCortes(),
        GerarSrt(),
    ]
