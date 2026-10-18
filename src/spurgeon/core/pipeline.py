"""Motor do pipeline: executa as etapas em ordem, grava o estado e sabe retomar.

Cada etapa é uma subclasse de `Etapa`. O motor não conhece o que ela faz; só controla
ordem, estado, cancelamento, pontos de revisão e invalidação quando opções mudam.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, TypeVar

from spurgeon.core.config import ConfigApp, OpcoesProcessamento
from spurgeon.core.erros import Cancelado
from spurgeon.core.estado import Estado, EstadoEtapa, StatusEtapa, agora, salvar_estado
from spurgeon.core.ffmpeg import executar_ffmpeg
from spurgeon.core.formatacao import formatar_duracao, formatar_numero
from spurgeon.core.log import contexto_etapa
from spurgeon.core.projeto import PastasProjeto
from spurgeon.core.sinais import SinalControle

T = TypeVar("T")
_INTERVALO_SALVAR_PROGRESSO_S = 1.0
_INTERVALO_PAUSA_S = 0.2
_INTERVALO_BATIMENTO_S = 15.0
_INTERVALO_AVISO_FFMPEG_S = 30.0
_INTERVALO_DA_PILHA_S = 300.0
MOTIVO_ARQUIVO_SUMIU = "um arquivo gerado por ela não existe mais"


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
    cancelar: SinalControle = field(default_factory=threading.Event)
    pausar: SinalControle = field(default_factory=threading.Event)
    ao_progresso: Callable[[str, float], None] | None = None  # (id da etapa, fração 0..1)
    ao_pausa: Callable[[bool], None] | None = None  # True ao parar, False ao seguir
    etapa_id: str = "-"
    _ultimo_salvamento: float = field(default=0.0, repr=False)

    def ponto_seguro(self) -> None:
        """Lugar onde a etapa aceita ser cancelada ou pausada. Chame entre blocos de trabalho."""
        if self.cancelar.is_set():
            raise Cancelado()
        if not self.pausar.is_set():
            return
        self.log.info("Pausado pelo usuário.")
        if self.ao_pausa is not None:
            self.ao_pausa(True)
        while self.pausar.is_set():
            if self.cancelar.is_set():
                raise Cancelado()
            time.sleep(_INTERVALO_PAUSA_S)
        self.log.info("Retomado.")
        if self.ao_pausa is not None:
            self.ao_pausa(False)

    def executar_vigiado(
        self,
        descricao: str,
        funcao: Callable[[], T],
        *,
        detalhe: Callable[[], str] | None = None,
        intervalo_s: float = _INTERVALO_BATIMENTO_S,
        alerta_apos_s: float | None = None,
        alerta: str = "",
    ) -> T:
        """Roda `funcao` (um passo que não dá para interromper no meio) numa thread vigiada.

        Enquanto ela não termina, o log diz a cada `intervalo_s` que o passo continua, há quanto
        tempo e o que `detalhe()` mede (MB lidos do disco, memória livre…); sem isso um passo
        longo parece travado. O cancelamento vale na hora; a pausa só vale depois do passo, e o
        log avisa isso quando ela é pedida. `alerta` é dito uma vez se passar de `alerta_apos_s`.
        """
        resultado: list[T] = []
        falhas: list[BaseException] = []

        def trabalho() -> None:
            try:
                resultado.append(funcao())
            except BaseException as erro:
                falhas.append(erro)

        fio = threading.Thread(target=trabalho, daemon=True, name=f"vigiado-{self.etapa_id}")
        vigia = _Vigia(self, descricao, detalhe, intervalo_s, alerta_apos_s, alerta)
        fio.start()
        vigia.ident_do_trabalho = fio.ident
        while fio.is_alive():
            fio.join(timeout=_INTERVALO_PAUSA_S)
            if fio.is_alive():
                if self.cancelar.is_set():
                    self.log.warning("Cancelamento pedido durante: %s. Encerrando.", descricao)
                    raise Cancelado()
                vigia.passo(
                    quando_pausa=f"quando “{descricao}” terminar (este passo não para no meio)"
                )
        if falhas:
            raise falhas[0]
        return resultado[0]

    @contextmanager
    def batimento(
        self,
        descricao: str,
        *,
        detalhe: Callable[[], str] | None = None,
        intervalo_s: float = _INTERVALO_BATIMENTO_S,
        quando_pausa: str = "no próximo ponto seguro",
    ) -> Iterator[None]:
        """Enquanto o bloco roda, o log avisa de tempos em tempos que ele continua trabalhando.

        Para trabalho que a etapa mesma executa (e que só chama `ponto_seguro` de vez em quando).
        """
        parar = threading.Event()
        vigia = _Vigia(self, descricao, detalhe, intervalo_s, None, "", pausavel=True)
        etapa = self.etapa_id

        def laco() -> None:
            with contexto_etapa(etapa):
                while not parar.wait(_INTERVALO_PAUSA_S):
                    vigia.passo(quando_pausa=quando_pausa)

        fio = threading.Thread(target=laco, daemon=True, name=f"batimento-{etapa}")
        fio.start()
        try:
            yield
        finally:
            parar.set()
            fio.join(timeout=2)

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

    def ffmpeg(
        self,
        args: Sequence[str | Path],
        *,
        duracao_s: float | None = None,
        faixa: tuple[float, float] = (0.0, 1.0),
        cwd: Path | None = None,
        ao_fracao: Callable[[float], None] | None = None,
    ) -> str:
        """Roda o FFmpeg atendendo cancelamento e pausa.

        O progresso do comando (0..1) ocupa `faixa` do progresso da etapa, para etapas que
        rodam vários comandos em sequência. `ao_fracao` recebe o progresso próprio do comando.
        """
        inicio, fim = faixa
        comeco = ultimo_aviso = time.monotonic()

        def ao_progresso(fracao: float) -> None:
            nonlocal ultimo_aviso
            self.progresso(inicio + fracao * (fim - inicio))
            if ao_fracao is not None:
                ao_fracao(fracao)
            agora_ = time.monotonic()
            if duracao_s and fracao > 0 and agora_ - ultimo_aviso >= _INTERVALO_AVISO_FFMPEG_S:
                ultimo_aviso = agora_
                feito = fracao * duracao_s
                velocidade = feito / max(agora_ - comeco, 0.1)
                self.log.info(
                    "FFmpeg: %d%% (%s de %s de mídia) · %s× o tempo real · falta cerca de %s.",
                    round(fracao * 100),
                    formatar_duracao(feito),
                    formatar_duracao(duracao_s),
                    formatar_numero(velocidade),
                    formatar_duracao((duracao_s - feito) / max(velocidade, 0.01)),
                )

        return executar_ffmpeg(
            args,
            duracao_s=duracao_s,
            ao_progresso=ao_progresso if duracao_s else None,
            cancelar=self.cancelar,
            pausar=self.pausar,
            ao_pausa=self.avisar_pausa_de_processo,
            cwd=cwd,
        )

    def avisar_pausa_de_processo(self, ativa: bool) -> None:
        self.log.info("Pausado pelo usuário." if ativa else "Retomado.")
        if self.ao_pausa is not None:
            self.ao_pausa(ativa)


class _Vigia:
    """O que `executar_vigiado` e `batimento` dizem no log enquanto um passo demora."""

    def __init__(
        self,
        ctx: ContextoProjeto,
        descricao: str,
        detalhe: Callable[[], str] | None,
        intervalo_s: float,
        alerta_apos_s: float | None,
        alerta: str,
        pausavel: bool = False,
    ):
        self._ctx = ctx
        self._pausavel = pausavel  # o trabalho para de verdade na pausa: então não há o que dizer
        self._descricao = descricao
        self._detalhe = detalhe
        self._intervalo_s = intervalo_s
        self._alerta_apos_s = alerta_apos_s
        self._alerta = alerta
        self._inicio = self._ultimo = time.monotonic()
        self._avisou_pausa = False
        self._alertou = False
        self._ultima_pilha = 0.0
        self.ident_do_trabalho: int | None = None  # a thread do passo, para mostrar a pilha

    def passo(self, *, quando_pausa: str) -> None:
        ctx, agora = self._ctx, time.monotonic()
        if ctx.pausar.is_set() and not self._avisou_pausa:
            self._avisou_pausa = True
            ctx.log.info("Pausa pedida: ela vale %s.", quando_pausa)
        elif not ctx.pausar.is_set():
            self._avisou_pausa = False
        if agora - self._ultimo >= self._intervalo_s:
            self._ultimo = agora
            if self._pausavel and ctx.pausar.is_set():
                return  # em pausa, o log não deve dizer que o trabalho "continua"
            medida = self._medir()
            ctx.log.info(
                "%s: ainda em andamento há %s%s.",
                self._descricao,
                formatar_duracao(agora - self._inicio),
                f" ({medida})" if medida else "",
            )
        if (
            self._alerta_apos_s is not None
            and not self._alertou
            and agora - self._inicio >= self._alerta_apos_s
        ):
            self._alertou = True
            ctx.log.warning(
                "%s está demorando mais que o normal. %s", self._descricao, self._alerta
            )
            self._mostrar_onde_esta()
        elif self._alertou and agora - self._ultima_pilha >= _INTERVALO_DA_PILHA_S:
            self._mostrar_onde_esta()

    def _mostrar_onde_esta(self) -> None:
        """Escreve no log onde o passo está parado (a pilha da thread), para achar a causa."""
        self._ultima_pilha = time.monotonic()
        quadro = sys._current_frames().get(self.ident_do_trabalho or -1)
        if quadro is None:
            return
        pilha = "".join(traceback.format_stack(quadro, limit=10)).rstrip()
        self._ctx.log.warning("Onde o passo está parado agora:\n%s", pilha)

    def _medir(self) -> str:
        if self._detalhe is None:
            return ""
        try:
            return self._detalhe()
        except Exception:
            return ""


class Etapa(ABC):
    id: ClassVar[str]
    nome: ClassVar[str]
    #: ids das etapas cujo resultado esta etapa usa; se uma delas for refeita, esta também é.
    depende_de: ClassVar[tuple[str, ...]] = ()
    #: campos de OpcoesProcessamento que mudam o resultado desta etapa.
    opcoes_relevantes: ClassVar[tuple[str, ...]] = ()
    #: peso no progresso total e no tempo restante (a transcrição pesa mais que a extração).
    peso: ClassVar[float] = 1.0
    #: True nas etapas que geram os arquivos finais (vídeo longo, Shorts). Só quando alguma
    #: delas roda é que os arquivos intermediários são apagados ao concluir.
    entrega: ClassVar[bool] = False
    #: True nas etapas do modo "só introdução e vinheta" (todas as outras ficam de fora).
    no_modo_vinhetas: ClassVar[bool] = False

    def ativa(self, opcoes: OpcoesProcessamento) -> bool:
        return True

    def peso_para(self, opcoes: OpcoesProcessamento) -> float:
        """O peso com as opções escolhidas (a renderização depende da qualidade, por exemplo)."""
        return self.peso

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
        todos = set(ids)
        for e in etapas:
            # Dependência de etapa que não está na lista é aceita (etapa opcional que o
            # pipeline em uso não inclui); só é erro apontar para uma que vem depois.
            if (set(e.depende_de) & todos) - vistos:
                raise ValueError(f"A etapa {e.id} depende de etapa que vem depois dela.")
            vistos.add(e.id)
        self.etapas = list(etapas)

    def ativas(self, opcoes: OpcoesProcessamento) -> list[Etapa]:
        ativas = [e for e in self.etapas if e.ativa(opcoes)]
        if opcoes.so_vinhetas:
            return [e for e in ativas if e.no_modo_vinhetas]
        return ativas

    def preparar(self, ctx: ContextoProjeto) -> None:
        """Alinha o estado com as opções atuais: refaz o que ficou desatualizado."""
        ativas = self.ativas(ctx.opcoes)
        existentes = {r.id: r for r in [*ctx.estado.etapas_guardadas, *ctx.estado.etapas]}
        ctx.estado.etapas = [existentes.get(e.id) or EstadoEtapa(id=e.id) for e in ativas]
        ids_ativos = {e.id for e in ativas}
        ctx.estado.etapas_guardadas = [
            registro for id_, registro in existentes.items() if id_ not in ids_ativos
        ]

        # `pendentes` reúne as etapas que vão rodar de novo; quem depende delas também roda.
        # Exceção: uma etapa só refeita porque o arquivo dela sumiu (a limpeza de `_work/` apaga os
        # áudios) produz de novo o mesmo resultado, então não invalida quem veio depois.
        pendentes: set[str] = set()
        for etapa in ativas:
            registro = ctx.estado.etapa(etapa.id)
            motivo = self._motivo_para_refazer(etapa, registro, ctx, pendentes)
            regenerando = motivo == MOTIVO_ARQUIVO_SUMIU
            if motivo:
                ctx.log.info("Etapa '%s' será refeita: %s.", etapa.nome, motivo)
                registro.zerar()
            if registro.status == StatusEtapa.PENDENTE and not regenerando:
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
            return MOTIVO_ARQUIVO_SUMIU
        return None

    def executar(self, ctx: ContextoProjeto) -> ResultadoPipeline:
        self.preparar(ctx)
        ativas = self.ativas(ctx.opcoes)
        pendentes = [
            e.nome for e in ativas if ctx.estado.etapa(e.id).status != StatusEtapa.CONCLUIDA
        ]
        ctx.log.info(
            "Modo %s: %d etapas ativas, %d por fazer%s.",
            ctx.opcoes.modo,
            len(ativas),
            len(pendentes),
            f" ({'; '.join(pendentes)})" if pendentes else "",
        )
        for numero, etapa in enumerate(ativas, start=1):
            registro = ctx.estado.etapa(etapa.id)
            if registro.status == StatusEtapa.CONCLUIDA:
                ctx.log.info("Etapa '%s' já concluída; pulando.", etapa.nome)
                continue
            if registro.status == StatusEtapa.AGUARDANDO_REVISAO:
                ctx.log.info("Aguardando a revisão de '%s'.", etapa.nome)
                return ResultadoPipeline.AGUARDANDO_REVISAO

            ctx.etapa_id = etapa.id
            with contexto_etapa(etapa.id):
                resultado = self._rodar_etapa(
                    etapa, registro, ctx, posicao=f"etapa {numero} de {len(ativas)}"
                )
            if resultado is not None:
                return resultado
        return ResultadoPipeline.CONCLUIDO

    def _rodar_etapa(self, etapa, registro, ctx, posicao: str = "") -> ResultadoPipeline | None:
        registro.status = StatusEtapa.EM_ANDAMENTO
        registro.inicio = agora()
        registro.fim = None
        registro.progresso = 0.0
        registro.erro = None
        registro.assinatura = ctx.opcoes.assinatura(etapa.opcoes_relevantes)
        ctx.salvar()
        comeco = time.monotonic()
        ctx.log.info("Iniciando: %s%s", etapa.nome, f" ({posicao})" if posicao else "")
        if ctx.ao_progresso is not None:
            ctx.ao_progresso(etapa.id, 0.0)
        try:
            ctx.ponto_seguro()
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
        ctx.log.info("Concluído: %s em %s", etapa.nome, formatar_duracao(time.monotonic() - comeco))
        if ctx.ao_progresso is not None:
            ctx.ao_progresso(etapa.id, 1.0)
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

    def progresso_total(self, ctx: ContextoProjeto) -> float:
        """Progresso 0..1 das etapas ativas, ponderado pelo `peso` de cada uma."""
        pesos = {e.id: e.peso_para(ctx.opcoes) for e in self.etapas}
        registros = ctx.estado.etapas
        total = sum(pesos[r.id] for r in registros)
        if total <= 0:
            return 0.0
        return sum(pesos[r.id] * r.progresso for r in registros) / total
