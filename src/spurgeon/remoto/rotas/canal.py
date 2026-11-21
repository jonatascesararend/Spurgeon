"""Começar pelo celular (Fase R5; `remoto/novo.py`): os vídeos do canal e o projeto novo."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import carregar_config
from spurgeon.remoto import novo
from spurgeon.remoto.contexto import Contexto

log = logging.getLogger(__name__)

#: o arquivo (ao lado do `remoto.json`) com a última lista lida do canal
NOME_DA_LISTA = "canal_videos.json"


def _ler_guardada(arquivo: Path | None) -> dict[str, Any]:
    if arquivo is None or not arquivo.is_file():
        return {"lido_em": "", "videos": []}
    try:
        dados = ler_json(arquivo)
        videos = dados.get("videos") if isinstance(dados, dict) else None
        if isinstance(videos, list):
            return {"lido_em": str(dados.get("lido_em") or ""), "videos": videos}
    except (OSError, ValueError):
        log.warning("A lista guardada do canal não pôde ser lida; será lida de novo.")
    return {"lido_em": "", "videos": []}


def rotas(ctx: Contexto) -> list[Route]:
    # a última lista lida do canal fica guardada: abrir a tela não abre o Studio (uns 15 s) toda
    # vez; só o "Atualizar" (ou a primeira vez, sem lista nenhuma) lê de novo
    guardada = _ler_guardada(ctx.arquivo_da_lista)
    trava = threading.Lock()

    def _videos(atualizar: bool) -> list[dict[str, Any]]:
        with trava:
            if atualizar or not guardada["lido_em"]:
                guardada["videos"] = [v.para_json() for v in ctx.listar_canal()]
                guardada["lido_em"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
                if ctx.arquivo_da_lista is not None:
                    try:
                        escrever_json_atomico(ctx.arquivo_da_lista, guardada)
                    except OSError:
                        log.warning("A lista do canal não pôde ser guardada.", exc_info=True)
            return [dict(v) for v in guardada["videos"]]

    async def videos(request: Request) -> JSONResponse:
        atualizar = request.query_params.get("atualizar") == "1"
        try:
            lista = await run_in_threadpool(_videos, atualizar)
        except Exception as erro:  # o Studio fora do ar, a sessão que caiu, o canal errado…
            raise HTTPException(503, f"Não consegui abrir o YouTube Studio: {erro}") from erro
        por_video = novo.projetos_por_video(ctx.pasta_projetos)
        for video in lista:
            video["projeto"] = novo.projeto_do_video(ctx.pasta_projetos, video, por_video)
        tarefa = ctx.novos.atual
        return JSONResponse({
            "videos": lista,
            "lido_em": guardada["lido_em"],
            "opcoes": novo.resumo_das_opcoes(carregar_config()),
            "tarefa": tarefa.para_json() if tarefa else None,
            "ocupado": ctx.fila.ocupada(),
        })  # fmt: skip

    async def comecar(request: Request) -> JSONResponse:
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        video = next((v for v in guardada["videos"] if v["id"] == corpo.get("id")), None)
        if video is None:
            raise HTTPException(404, "Atualize a lista do canal e escolha o vídeo de novo.")
        try:  # só cria o projeto: baixar e processar ficam para o "Começar" da tela dele
            pastas = await run_in_threadpool(ctx.novos.criar, video)
        except novo.NovoInvalido as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse({"projeto": pastas.raiz.name})

    def cronograma(request: Request) -> JSONResponse:
        """A última leitura do cronograma; com `?auto=1`, uma leitura velha já pede outra."""
        if request.query_params.get("auto") == "1" and ctx.cronograma.velho():
            ctx.cronograma.atualizar()
        return JSONResponse(ctx.cronograma.estado())

    def atualizar_cronograma(_request: Request) -> JSONResponse:
        ctx.cronograma.atualizar()
        return JSONResponse(ctx.cronograma.estado())

    def tarefa(_request: Request) -> JSONResponse:
        atual = ctx.novos.atual
        return JSONResponse(atual.para_json() if atual else None)

    return [
        Route("/api/canal/videos", videos),
        Route("/api/canal/novo", comecar, methods=["POST"]),
        Route("/api/canal/tarefa", tarefa),
        Route("/api/canal/cronograma", cronograma),
        Route("/api/canal/cronograma/atualizar", atualizar_cronograma, methods=["POST"]),
    ]
