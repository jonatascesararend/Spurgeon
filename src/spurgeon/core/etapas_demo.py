"""Etapas de mentira, só para experimentar a interface e testar o executor de ponta a ponta.

Ativadas com `SPURGEON_DEMO=1`. Cada etapa só espera e avança a barra de progresso.
`SPURGEON_DEMO_PASSO_S` define a espera de cada um dos 40 passos (padrão 0,05 s).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import ClassVar

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.pipeline import ContextoProjeto, Etapa, ResultadoEtapa

_PASSOS = 40


class _EtapaDemo(Etapa):
    passos: ClassVar[int] = _PASSOS

    def executar(self, ctx: ContextoProjeto) -> ResultadoEtapa | None:
        espera = float(os.environ.get("SPURGEON_DEMO_PASSO_S", "0.05"))
        for i in range(self.passos):
            ctx.ponto_seguro()
            time.sleep(espera)
            ctx.progresso((i + 1) / self.passos)
            if i % 10 == 9:
                ctx.log.info("%s: %d%%", self.nome, round(100 * (i + 1) / self.passos))
        (ctx.pastas.work / f"{self.id}.demo").write_text("ok", encoding="utf-8")
        return None

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.work / f"{self.id}.demo"]


class ExtrairAudioDemo(_EtapaDemo):
    id = "demo_extrair"
    nome = "Extraindo o áudio (demonstração)"
    peso = 1.0


class TranscreverDemo(_EtapaDemo):
    id = "demo_transcrever"
    nome = "Transcrevendo (demonstração)"
    depende_de = ("demo_extrair",)
    peso = 4.0


class RevisarTranscricaoDemo(Etapa):
    id = "demo_revisar"
    nome = "Revisão da transcrição (demonstração)"
    depende_de = ("demo_transcrever",)
    peso = 0.0

    def ativa(self, opcoes: OpcoesProcessamento) -> bool:
        return opcoes.pausar_revisar_transcricao

    def executar(self, ctx: ContextoProjeto) -> ResultadoEtapa:
        return ResultadoEtapa.AGUARDANDO_REVISAO


class RenderizarDemo(_EtapaDemo):
    id = "demo_renderizar"
    nome = "Renderizando o vídeo (demonstração)"
    depende_de = ("demo_transcrever",)
    peso = 3.0


def etapas_demo() -> list[Etapa]:
    return [ExtrairAudioDemo(), TranscreverDemo(), RevisarTranscricaoDemo(), RenderizarDemo()]
