"""Projetos, andamento e Shorts já renderizados (Fase R1: só ver).

Ainda por vir aqui (Remoto.md, Fases R2 e R5): iniciar, pausar, cancelar e aprovar os cortes.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from spurgeon.remoto import dados
from spurgeon.remoto.contexto import Contexto


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    def lista(_request: Request) -> JSONResponse:
        return JSONResponse(dados.projetos(ctx.pasta_projetos))

    def detalhe(request: Request) -> JSONResponse:
        return JSONResponse(dados.detalhe_do_projeto(_pastas(request)))

    def andamento(request: Request) -> JSONResponse:
        return JSONResponse(dados.andamento(_pastas(request)))

    def shorts(request: Request) -> JSONResponse:
        return JSONResponse(dados.shorts(_pastas(request)))

    def video_do_short(request: Request) -> FileResponse:
        arquivo = dados.arquivo_do_short(_pastas(request), request.path_params["corte"])
        if arquivo is None:
            raise HTTPException(404, "Este Short ainda não foi renderizado.")
        # sem `filename`: o celular toca, não baixa; o Range (pular no vídeo) é do Starlette
        return FileResponse(arquivo, media_type="video/mp4")

    return [
        Route("/api/projetos", lista),
        Route("/api/projetos/{projeto}", detalhe),
        Route("/api/projetos/{projeto}/andamento", andamento),
        Route("/api/projetos/{projeto}/shorts", shorts),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/video", video_do_short),
    ]
