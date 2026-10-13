"""Registro das etapas do pipeline, na ordem em que rodam.

Os vídeos pesados (Shorts e vídeo longo) vêm por último: toda a análise e as revisões humanas
acontecem antes, e a renderização, que leva horas, roda sem ninguém por perto.
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
    from spurgeon.render.finalizar import Finalizar
    from spurgeon.render.shorts import RenderizarShorts
    from spurgeon.render.video_longo import RenderizarVideoLongo
    from spurgeon.shorts.etapas import (
        AnalisarShorts,
        AprovarCortes,
        AvaliarShorts,
        SelecionarShorts,
    )
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
        AnalisarShorts(),
        AvaliarShorts(),
        SelecionarShorts(),
        AprovarCortes(),
        RenderizarShorts(),
        RenderizarVideoLongo(),
        Finalizar(),
    ]
