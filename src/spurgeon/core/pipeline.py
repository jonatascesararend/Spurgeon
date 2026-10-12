"""Motor do pipeline: executa as etapas em ordem, grava o estado e sabe retomar.

Cada etapa é uma subclasse de `Etapa`. O motor não conhece o que ela faz; só controla
ordem, estado, cancelamento, pontos de revisão e invalidação quando opções mudam.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from spurgeon.core.config import ConfigApp, OpcoesProcessamento
from spurgeon.core.erros import Cancelado
from spurgeon.core.estado import Estado, EstadoEtapa, StatusEtapa, agora, salvar_estado
from spurgeon.core.log import contexto_etapa
from spurgeon.core.projeto import PastasProjeto

_INTERVALO_SALVAR_PROGRESSO_S = 1.0


class ResultadoEtapa(StrEnum):
    CONCLUIDA = "concluida"
    AGUARDANDO_REVISAO = "aguardando_revisao"


class ResultadoPipeline(StrEnum):
    CONCLUIDO = "concluido"
    AGUARDANDO_REVISAO = "aguardando_revisao"
    CANCELADO = "cancelado"
    ERRO = "erro"


@dataclass
class ContextoProjeto:
    """Tudo o que uma etapa precisa para trabalhar."""

    pastas: PastasProjeto
    config: ConfigApp
    opcoes: OpcoesProcessamento
    estado: Estado
    log: logging.Logger
    cancelar: threading.Event = field(default_factory=threading.Event)
    ao_progresso: Callable[[str, float], None] | None = None  # (id da etapa, fração 0..1)
    etapa_id: str = "-"
    _ultimo_salvamento: float = field(default=0.0, repr=False)

    def verificar_cancelamento(self) -> None:
        if self.cancelar.is_set():
            raise Cancelado()

    def progresso(self, fracao: float) -> None:
        fracao = min(1.0, max(0.0, fracao))
        registro = self.estado.etapa(self.etapa_id)
        if registro is not None:
            registro.progresso = fracao
        if self.ao_progresso is not None:
            self.ao_progresso(self.etapa_id, fracao)
        if time.monotonic() - self._ultimo_salvamento >= _INTERVALO_SALVAR_PROGRESSO_S:
            self.salvar()

    def salvar(self) -> None:
        salvar_estado(self.pastas.raiz, self.estado)
        self._ultimo_salvamento = time.monotonic()


class Etapa(ABC):
    id: ClassVar[str]
    nome: ClassVar[str]
    #: ids das etapas cujo resultado esta etapa usa; se uma delas for refeita, esta também é.
    depende_de: ClassVar[tuple[str, ...]] = ()
    #: campos de OpcoesProcessamento que mudam o resultado desta etapa.
    opcoes_relevantes: ClassVar[tuple[str, ...]] = ()

    def ativa(self, opcoes: OpcoesProcessamento) -> bool:
        return True

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        """Arquivos que provam que a etapa terminou; se sumirem, ela é refeita."""
        return []

    @abstractmethod
    def executar(self, ctx: ContextoProjeto) -> ResultadoEtapa | None:
        """Faz o trabalho. Devolve AGUARDANDO_REVISAO para pausar num ponto de revisão."""


class Pipeline:
    def __init__(self, etapas: Sequence[Etapa]):
        ids = [e.id for e in etapas]
        if len(set(ids)) != len(ids):
            raise ValueError("Ids de etapa repetidos.")
        vistos: set[str] = set()
        for e in etapas:
            if not set(e.depende_de) <= vistos:
                raise ValueError(f"A etapa {e.id} depende de etapa que vem depois dela.")
            vistos.add(e.id)
        self.etapas = list(etapas)

    def ativas(self, opcoes: OpcoesProcessamento) -> list[Etapa]:
        return [e for e in self.etapas if e.ativa(opcoes)]

    def preparar(self, ctx: ContextoProjeto) -> None:
        """Alinha o estado com as opções atuais: refaz o que ficou desatualizado."""
        ativas = self.ativas(ctx.opcoes)
        existentes = {r.id: r for r in ctx.estado.etapas}
        ctx.estado.etapas = [existentes.get(e.id) or EstadoEtapa(id=e.id) for e in ativas]

        # `pendentes` reúne as etapas que vão rodar de novo; quem depende delas também roda.
        pendentes: set[str] = set()
        for etapa in ativas:
            registro = ctx.estado.etapa(etapa.id)
            motivo = self._motivo_para_refazer(etapa, registro, ctx, pendentes)
            if motivo:
                ctx.log.info("Etapa '%s' será refeita: %s.", etapa.nome, motivo)
                registro.zerar()
            if registro.status == StatusEtapa.PENDENTE:
                pendentes.add(etapa.id)
        ctx.salvar()

    @staticmethod
    def _motivo_para_refazer(etapa, registro, ctx, refeitas) -> str | None:
        if registro.status == StatusEtapa.PENDENTE:
            return None
        if registro.status == StatusEtapa.ERRO:
            return "falhou na última execução"
        if registro.status in (StatusEtapa.EM_ANDAMENTO, StatusEtapa.CANCELADA):
            return "foi interrompida antes de terminar"
        if registro.assinatura != ctx.opcoes.assinatura(etapa.opcoes_relevantes):
            return "as opções mudaram"
        if any(d in refeitas for d in etapa.depende_de):
            return "uma etapa anterior foi refeita"
        if registro.status == StatusEtapa.CONCLUIDA and not all(
            p.exists() for p in etapa.artefatos(ctx)
        ):
            return "um arquivo gerado por ela não existe mais"
        return None

    def executar(self, ctx: ContextoProjeto) -> ResultadoPipeline:
        self.preparar(ctx)
        for etapa in self.ativas(ctx.opcoes):
            registro = ctx.estado.etapa(etapa.id)
            if registro.status == StatusEtapa.CONCLUIDA:
                ctx.log.info("Etapa '%s' já concluída; pulando.", etapa.nome)
                continue
            if registro.status == StatusEtapa.AGUARDANDO_REVISAO:
                ctx.log.info("Aguardando a revisão de '%s'.", etapa.nome)
                return ResultadoPipeline.AGUARDANDO_REVISAO

            ctx.etapa_id = etapa.id
            with contexto_etapa(etapa.id):
                resultado = self._rodar_etapa(etapa, registro, ctx)
            if resultado is not None:
                return resultado
        return ResultadoPipeline.CONCLUIDO

    def _rodar_etapa(self, etapa, registro, ctx) -> ResultadoPipeline | None:
        registro.status = StatusEtapa.EM_ANDAMENTO
        registro.inicio = agora()
        registro.fim = None
        registro.progresso = 0.0
        registro.erro = None
        registro.assinatura = ctx.opcoes.assinatura(etapa.opcoes_relevantes)
        ctx.salvar()
        ctx.log.info("Iniciando: %s", etapa.nome)
        try:
            ctx.verificar_cancelamento()
            resultado = etapa.executar(ctx)
        except Cancelado:
            registro.status = StatusEtapa.CANCELADA
            ctx.salvar()
            ctx.log.warning(
                "Cancelado pelo usuário em '%s'. O projeto pode ser retomado.", etapa.nome
            )
            return ResultadoPipeline.CANCELADO
        except Exception as erro:
            registro.status = StatusEtapa.ERRO
            registro.erro = f"{type(erro).__name__}: {erro}"
            ctx.salvar()
            ctx.log.exception("Erro em '%s'. O projeto pode ser retomado.", etapa.nome)
            return ResultadoPipeline.ERRO

        registro.fim = agora()
        registro.artefatos = [ctx.pastas.relativo(p) for p in etapa.artefatos(ctx)]
        if resultado == ResultadoEtapa.AGUARDANDO_REVISAO:
            registro.status = StatusEtapa.AGUARDANDO_REVISAO
            ctx.salvar()
            ctx.log.info("Pausado para revisão: %s", etapa.nome)
            return ResultadoPipeline.AGUARDANDO_REVISAO
        registro.status = StatusEtapa.CONCLUIDA
        registro.progresso = 1.0
        ctx.salvar()
        ctx.log.info("Concluído: %s", etapa.nome)
        return None

    @staticmethod
    def aprovar_revisao(ctx: ContextoProjeto, id_etapa: str) -> None:
        """Chamada pela interface quando o usuário aprova; o pipeline segue na próxima execução."""
        registro = ctx.estado.etapa(id_etapa)
        if registro is None or registro.status != StatusEtapa.AGUARDANDO_REVISAO:
            raise ValueError(f"A etapa {id_etapa} não está aguardando revisão.")
        registro.status = StatusEtapa.CONCLUIDA
        registro.progresso = 1.0
        ctx.salvar()
        ctx.log.info("Revisão aprovada: %s", id_etapa)

    @staticmethod
    def progresso_total(ctx: ContextoProjeto) -> float:
        registros = ctx.estado.etapas
        if not registros:
            return 0.0
        return sum(r.progresso for r in registros) / len(registros)
