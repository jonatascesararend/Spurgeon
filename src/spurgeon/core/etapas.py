"""Registro das etapas do pipeline, na ordem em que rodam.

Os vídeos pesados (vídeo longo e Shorts) vêm por último: toda a análise e as revisões humanas
acontecem antes, e a renderização, que leva horas, roda sem ninguém por perto. O vídeo longo vem
ANTES dos Shorts porque eles são cortados dele (`render/fonte_do_short.py`): saem com a mesma imagem
e o mesmo som do vídeo final, já tratados.
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
    from spurgeon.audio.exportar import ExportarAudio
    from spurgeon.audio.pausas import CalcularCortes, DetectarPausas
    from spurgeon.render.finalizar import Finalizar
    from spurgeon.render.shorts import RenderizarShorts
    from spurgeon.render.video_longo import RenderizarVideoLongo
    from spurgeon.render.vinhetas import AdicionarVinhetas
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
        ExportarAudio(),
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
        RenderizarVideoLongo(),
        RenderizarShorts(),
        AdicionarVinhetas(),
        Finalizar(),
    ]
