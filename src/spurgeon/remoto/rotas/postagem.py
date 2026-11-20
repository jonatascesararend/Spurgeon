"""Postar no YouTube pelo celular (Fase R4; `remoto/postagem.py`)."""

from __future__ import annotations

import json
from datetime import datetime

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from spurgeon.remoto import dados, postagem, sessao
from spurgeon.remoto.contexto import Contexto


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    def preparar(request: Request) -> JSONResponse:
        """O que a tela de postar precisa: o sermão a vincular e o que já foi feito no YouTube."""
        pastas = _pastas(request)
        anterior = postagem.do_youtube(pastas, request.path_params["corte"])
        envio = ctx.postagens.atual
        return JSONResponse({
            "relacionado": postagem.sermao_do_projeto(pastas),
            "youtube": anterior.para_dict() if anterior else None,
            "envio": envio.para_json() if envio and envio.projeto == pastas.raiz.name else None,
        })  # fmt: skip

    async def postar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        if not isinstance(corpo, dict):
            raise HTTPException(400, "Pedido inválido.")
        if not sessao.ativa(pastas):
            raise HTTPException(409, "sem_trava")
        try:
            pedido = postagem.ler_pedido(corpo, request.path_params["corte"], datetime.now())
            envio = ctx.postagens.iniciar(pastas, pedido)
        except postagem.PostagemInvalida as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse(envio.para_json())

    def envio(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        atual = ctx.postagens.atual
        if atual is None or atual.projeto != pastas.raiz.name:
            return JSONResponse(None)
        return JSONResponse(atual.para_json())

    return [
        Route("/api/projetos/{projeto}/shorts/{corte:int}/postar", preparar),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/postar", postar, methods=["POST"]),
        Route("/api/projetos/{projeto}/postagem", envio),
    ]
