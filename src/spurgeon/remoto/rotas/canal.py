"""Começar pelo celular (Fase R5; `remoto/novo.py`): os vídeos do canal e o projeto novo."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from spurgeon.core.config import carregar_config
from spurgeon.remoto import novo
from spurgeon.remoto.contexto import Contexto

#: a lista do canal vale por este tempo (abrir o Studio leva uns 15 s)
VALIDADE_DA_LISTA_S = 600.0


def rotas(ctx: Contexto) -> list[Route]:
    guardada: dict[str, Any] = {"quando": 0.0, "videos": []}
    trava = threading.Lock()

    def _videos(atualizar: bool) -> list[dict[str, Any]]:
        with trava:
            if atualizar or time.monotonic() - guardada["quando"] > VALIDADE_DA_LISTA_S:
                guardada["videos"] = [v.para_json() for v in ctx.listar_canal()]
                guardada["quando"] = time.monotonic()
            return list(guardada["videos"])

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
        try:
            tarefa = ctx.novos.iniciar(video)
        except novo.NovoInvalido as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse(tarefa.para_json())

    def tarefa(_request: Request) -> JSONResponse:
        atual = ctx.novos.atual
        return JSONResponse(atual.para_json() if atual else None)

    return [
        Route("/api/canal/videos", videos),
        Route("/api/canal/novo", comecar, methods=["POST"]),
        Route("/api/canal/tarefa", tarefa),
    ]
