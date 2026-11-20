"""Projetos, andamento e Shorts já renderizados (Fase R1: só ver).

Ainda por vir aqui (Remoto.md, Fases R2 e R5): iniciar, pausar, cancelar e aprovar os cortes.
"""

from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.remoto import dados
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.fila import AJUSTES_DA_RENDERIZACAO, FilaOcupada


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    def lista(_request: Request) -> JSONResponse:
        return JSONResponse(dados.projetos(ctx.pasta_projetos))

    def detalhe(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        resposta = dados.detalhe_do_projeto(pastas)
        resposta["a_renderizar"] = len(dados.a_renderizar(pastas))
        resposta["tem_video_original"] = dados.video_original(pastas) is not None
        return JSONResponse(resposta)

    def andamento(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        resposta = dados.andamento(pastas)
        fila = ctx.fila.estado()
        resposta["fila"] = fila if fila["projeto"] == pastas.raiz.name else None
        resposta["a_renderizar"] = len(dados.a_renderizar(pastas))
        return JSONResponse(resposta)

    def shorts(request: Request) -> JSONResponse:
        return JSONResponse(dados.shorts(_pastas(request)))

    def video_do_short(request: Request) -> FileResponse:
        arquivo = dados.arquivo_do_short(_pastas(request), request.path_params["corte"])
        if arquivo is None:
            raise HTTPException(404, "Este Short ainda não foi renderizado.")
        # sem `filename`: o celular toca, não baixa; o Range (pular no vídeo) é do Starlette
        return FileResponse(arquivo, media_type="video/mp4")

    async def aprovar(request: Request) -> JSONResponse:
        """Aprova a revisão em que o processamento parou e o faz seguir (como "Aprovar e
        continuar" da janela). As marcações dos cortes já foram gravadas uma a uma."""
        pastas = _pastas(request)
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        estado = carregar_estado(pastas.raiz)
        esperando = [
            e.id for e in (estado.etapas if estado else [])
            if e.status == StatusEtapa.AGUARDANDO_REVISAO
        ]  # fmt: skip
        etapa = corpo.get("etapa") if isinstance(corpo, dict) else None
        if not esperando or etapa not in esperando:
            raise HTTPException(409, "O projeto não está esperando esta revisão.")
        if etapa == "aprovar_cortes" and not any(c.incluir for c in dados.carregar_cortes(pastas)):
            raise HTTPException(422, "Marque pelo menos um corte para seguir.")
        try:
            await run_in_threadpool(ctx.fila.iniciar, pastas, aprovar=etapa)
        except FilaOcupada as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse({"ok": True, "fila": ctx.fila.estado()})

    def renderizar(request: Request) -> JSONResponse:
        """Renderiza os Shorts marcados que são novos ou mudaram (como o "Renderizar" do Studio)."""
        pastas = _pastas(request)
        faltam = dados.a_renderizar(pastas)
        if not faltam:
            raise HTTPException(409, "Nenhum Short marcado precisa ser renderizado.")
        if dados.video_original(pastas) is None:
            raise HTTPException(
                409,
                "O vídeo original deste projeto não está mais no PC: sem ele não dá para "
                "renderizar. Ponha o vídeo de volta no lugar de antes.",
            )
        try:
            ctx.fila.iniciar(pastas, ajustes=AJUSTES_DA_RENDERIZACAO)
        except FilaOcupada as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse({"ok": True, "shorts": len(faltam), "fila": ctx.fila.estado()})

    def processar(request: Request) -> JSONResponse:
        """Começa (ou continua de onde parou) o processamento do projeto, com as opções dele."""
        pastas = _pastas(request)
        andamento = dados.andamento(pastas)
        if andamento["esperando_revisao"]:
            raise HTTPException(409, "O projeto está esperando a sua revisão: aprove para seguir.")
        if dados.video_original(pastas) is None:
            raise HTTPException(409, "O vídeo original deste projeto não está mais no PC.")
        try:
            ctx.fila.iniciar(pastas)
        except FilaOcupada as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse({"ok": True, "fila": ctx.fila.estado()})

    return [
        Route("/api/projetos/{projeto}/processar", processar, methods=["POST"]),
        Route("/api/projetos/{projeto}/aprovar", aprovar, methods=["POST"]),
        Route("/api/projetos/{projeto}/renderizar", renderizar, methods=["POST"]),
        Route("/api/projetos", lista),
        Route("/api/projetos/{projeto}", detalhe),
        Route("/api/projetos/{projeto}/andamento", andamento),
        Route("/api/projetos/{projeto}/shorts", shorts),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/video", video_do_short),
    ]
