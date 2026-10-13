"""Registro das etapas do pipeline, na ordem em que rodam.

As Fases 3 a 5 acrescentam aqui as etapas de transcrição, Shorts e render.
"""

from __future__ import annotations

import os

from spurgeon.core.pipeline import Etapa

VARIAVEL_DEMO = "SPURGEON_DEMO"


def etapas_registradas() -> list[Etapa]:
    if os.environ.get(VARIAVEL_DEMO) == "1":
        from spurgeon.core.etapas_demo import etapas_demo

        return etapas_demo()
    from spurgeon.audio.etapas import etapas_audio

    return [*etapas_audio()]
