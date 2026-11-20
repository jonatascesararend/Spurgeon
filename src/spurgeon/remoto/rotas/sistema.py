"""Estado do servidor, dormir, acordar e encerrar (Remoto.md, S1), e os avisos (R6)."""

from __future__ import annotations

import json

from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from spurgeon.remoto.avisos import Aviso
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.fila import FilaOcupada
from spurgeon.remoto.seguranca import usuario_do_pedido


def rotas(ctx: Contexto) -> list[Route]:
    def estado(request: Request) -> JSONResponse:
        c = ctx.config
        return JSONResponse(
            {
                "versao": ctx.versao,
                "dormindo": c.dormindo,
                "avisos_ligados": c.avisos_ligados,
                "aparelhos_com_aviso": len(ctx.avisos.inscricoes()),
                "usuario": usuario_do_pedido(request.headers),
                "endereco": c.endereco,
                "fila": ctx.fila.estado(),
            }
        )

    def dormir(_request: Request) -> JSONResponse:
        ctx.mudar_config(dormindo=True)
        return JSONResponse({"dormindo": True})

    def acordar(_request: Request) -> JSONResponse:
        ctx.mudar_config(dormindo=False)
        return JSONResponse({"dormindo": False})

    def encerrar(_request: Request) -> JSONResponse:
        # responde antes de sair: o celular precisa saber que o pedido chegou
        return JSONResponse({"encerrando": True}, background=BackgroundTask(ctx.encerrar))

    def chave(_request: Request) -> PlainTextResponse:
        return PlainTextResponse(ctx.avisos.chave_publica())

    async def inscrever(request: Request) -> JSONResponse:
        try:
            corpo = json.loads(await request.body())
            ctx.avisos.inscrever(corpo)
        except ValueError as erro:
            raise HTTPException(400, str(erro)) from erro
        return JSONResponse({"ok": True, "aparelhos": len(ctx.avisos.inscricoes())})

    def ligar_avisos(_request: Request) -> JSONResponse:
        ctx.mudar_config(avisos_ligados=True)
        return JSONResponse({"avisos_ligados": True})

    def desligar_avisos(_request: Request) -> JSONResponse:
        ctx.mudar_config(avisos_ligados=False)
        return JSONResponse({"avisos_ligados": False})

    def testar(_request: Request) -> JSONResponse:
        entregas = ctx.avisos.enviar(
            Aviso("Studio Spurgeon", "Teste: os avisos do PC chegam neste aparelho.", marca="teste")
        )
        return JSONResponse({"entregues": sum(e.ok for e in entregas), "aparelhos": len(entregas)})

    def processamento(_request: Request) -> JSONResponse:
        return JSONResponse(ctx.fila.estado())

    def comando(request: Request) -> JSONResponse:
        acao = request.path_params["acao"]
        if acao not in ("pausar", "retomar", "cancelar"):
            raise HTTPException(404)
        try:
            ctx.fila.enviar(acao)
        except FilaOcupada as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse(ctx.fila.estado())

    return [
        Route("/api/processamento", processamento),
        Route("/api/processamento/{acao}", comando, methods=["POST"]),
        Route("/api/sistema", estado),
        Route("/api/sistema/dormir", dormir, methods=["POST"]),
        Route("/api/sistema/acordar", acordar, methods=["POST"]),
        Route("/api/sistema/encerrar", encerrar, methods=["POST"]),
        Route("/api/avisos/chave", chave),
        Route("/api/avisos/inscrever", inscrever, methods=["POST"]),
        Route("/api/avisos/ligar", ligar_avisos, methods=["POST"]),
        Route("/api/avisos/desligar", desligar_avisos, methods=["POST"]),
        Route("/api/avisos/teste", testar, methods=["POST"]),
    ]
