"""Roda o pipeline de um projeto, fora da interface.

A interface o inicia como subprocesso (`python -m spurgeon.core.trabalhador`), o que
libera a memória ao final de cada execução e permite cancelar de verdade.

Protocolo:
  * stdout: um JSON por linha, com os eventos `log`, `etapas`, `progresso`, `pausa` e `fim`;
  * stdin: uma palavra por linha: `pausar`, `retomar` ou `cancelar`. Se o stdin fechar
    (a interface caiu), o trabalho é cancelado e o projeto continua retomável.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from spurgeon.core.config import ConfigApp
from spurgeon.core.energia import impedir_suspensao
from spurgeon.core.erros import ErroConfiguracao
from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.core.etapas import etapas_registradas
from spurgeon.core.log import criar_log_projeto, encerrar_log
from spurgeon.core.pipeline import (
    ContextoProjeto,
    Etapa,
    Pipeline,
    ResultadoPipeline,
    SinalControle,
)
from spurgeon.core.projeto import PastasProjeto, limpar_intermediarios

Emitir = Callable[[dict[str, Any]], None]


def executar(
    pasta_projeto: Path,
    config: ConfigApp,
    *,
    aprovar: str | None = None,
    emitir: Emitir,
    cancelar: SinalControle,
    pausar: SinalControle,
    etapas: Sequence[Etapa] | None = None,
) -> ResultadoPipeline:
    """Roda (ou retoma) o pipeline do projeto. `aprovar` é a etapa de revisão aprovada."""
    pastas = PastasProjeto(Path(pasta_projeto))
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        raise ErroConfiguracao(f"Não há estado.json em {pastas.raiz}; não é um projeto.")
    pastas.criar()  # `_work/` some quando um processamento anterior terminou; reabrir recria

    log = criar_log_projeto(
        pastas.log,
        nome=f"spurgeon.trabalhador.{pastas.raiz.name}",
        callback=lambda linha, nivel: emitir({"tipo": "log", "texto": linha, "nivel": nivel}),
    )
    pipeline = Pipeline(etapas if etapas is not None else etapas_registradas())
    ctx = ContextoProjeto(
        pastas=pastas,
        config=config,
        opcoes=estado.opcoes,
        estado=estado,
        log=log,
        cancelar=cancelar,
        pausar=pausar,
    )
    ctx.ao_progresso = lambda id_etapa, fracao: emitir(
        {
            "tipo": "progresso",
            "etapa": id_etapa,
            "fracao": fracao,
            "total": pipeline.progresso_total(ctx),
        }
    )
    ctx.ao_pausa = lambda ativa: emitir({"tipo": "pausa", "ativa": ativa})

    try:
        ativas = pipeline.ativas(estado.opcoes)
        emitir(
            {
                "tipo": "etapas",
                "etapas": [
                    {"id": e.id, "nome": e.nome, "peso": e.peso_para(estado.opcoes)} for e in ativas
                ],
            }
        )
        if aprovar:
            pipeline.aprovar_revisao(ctx, aprovar)
        with impedir_suspensao(sem_bloqueio=estado.opcoes.impedir_bloqueio):
            resultado = pipeline.executar(ctx)
        if not ativas:
            log.warning("Nenhuma etapa de processamento está disponível nesta versão.")
        if resultado == ResultadoPipeline.CONCLUIDO:
            _tratar_intermediarios(pastas, ativas, estado.opcoes.manter_intermediarios, log)
        emitir(_evento_fim(pipeline, ctx, resultado))
        return resultado
    except Exception as erro:
        log.exception("Falha inesperada no processamento.")
        emitir({"tipo": "fim", "resultado": ResultadoPipeline.ERRO.value, "erro": str(erro)})
        return ResultadoPipeline.ERRO
    finally:
        encerrar_log(log)


def _tratar_intermediarios(
    pastas: PastasProjeto, ativas: Sequence[Etapa], manter: bool, log: logging.Logger
) -> None:
    """Apaga os intermediários só se algum arquivo final foi gerado: senão eles são o resultado."""
    if manter:
        log.info("Arquivos intermediários mantidos, como pedido.")
    elif not any(e.entrega for e in ativas):
        log.info(
            "Arquivos intermediários mantidos em %s: nenhuma etapa gera o vídeo final nesta "
            "versão, então eles são o único resultado.",
            pastas.work.name,
        )
    else:
        limpar_intermediarios(pastas)
        log.info("Arquivos intermediários apagados.")


def _evento_fim(pipeline: Pipeline, ctx: ContextoProjeto, resultado: ResultadoPipeline) -> dict:
    evento: dict[str, Any] = {"tipo": "fim", "resultado": resultado.value, "erro": None}
    nomes = {e.id: e.nome for e in pipeline.etapas}
    for registro in ctx.estado.etapas:
        if registro.status == StatusEtapa.AGUARDANDO_REVISAO:
            evento["revisao"] = {"id": registro.id, "nome": nomes.get(registro.id, registro.id)}
        if registro.status == StatusEtapa.ERRO:
            evento["erro"] = registro.erro
    return evento


def _ouvir_comandos(cancelar: threading.Event, pausar: threading.Event) -> None:
    for linha in sys.stdin:
        comando = linha.strip()
        if comando == "pausar":
            pausar.set()
        elif comando == "retomar":
            pausar.clear()
        elif comando == "cancelar":
            cancelar.set()
    cancelar.set()  # o stdin fechou: a interface sumiu


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spurgeon.core.trabalhador")
    parser.add_argument("--projeto", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--aprovar")
    args = parser.parse_args(argv)

    # Qualquer print perdido de uma biblioteca vai para o stderr e não quebra o protocolo.
    protocolo = sys.stdout
    sys.stdout = sys.stderr
    protocolo.reconfigure(encoding="utf-8")

    def emitir(evento: dict[str, Any]) -> None:
        protocolo.write(json.dumps(evento, ensure_ascii=False) + "\n")
        protocolo.flush()

    cancelar, pausar = threading.Event(), threading.Event()
    threading.Thread(target=_ouvir_comandos, args=(cancelar, pausar), daemon=True).start()
    config = ConfigApp.model_validate_json(args.config.read_text(encoding="utf-8"))
    resultado = executar(
        args.projeto,
        config,
        aprovar=args.aprovar,
        emitir=emitir,
        cancelar=cancelar,
        pausar=pausar,
    )
    return 1 if resultado == ResultadoPipeline.ERRO else 0


if __name__ == "__main__":
    sys.exit(main())
