"""Ouvir o Short com a legenda (áudio sem vídeo; `remoto/audicao.py`).

Ainda por vir aqui (Remoto.md, S2): o proxy do vídeo para a prévia rápida com imagem.
"""

from __future__ import annotations

import logging

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from spurgeon.core.erros import ErroSpurgeon
from spurgeon.remoto import audicao, dados
from spurgeon.remoto.contexto import Contexto

log = logging.getLogger(__name__)


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    def _short(pastas, id_do_corte: int) -> dados.ShortNoDisco:
        for short in dados.shorts_no_disco(pastas):
            if short.corte.id == id_do_corte:
                return short
        raise HTTPException(404, "Este corte não existe no projeto.")

    def legenda(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        short = _short(pastas, request.path_params["corte"])
        try:
            palavras = audicao.carregar_palavras(pastas)
        except (OSError, ValueError) as erro:
            raise HTTPException(404, "A transcrição deste projeto não foi encontrada.") from erro
        resposta = audicao.legenda_para_json(audicao.legenda_do_short(palavras, short.corte))
        try:
            fonte = audicao.fonte_do_audio(pastas, short)
            resposta["fonte"], resposta["aviso"] = fonte.tipo, fonte.aviso
        except audicao.SemFonteDeAudio as erro:
            resposta["fonte"], resposta["aviso"] = "", str(erro)
        return JSONResponse(resposta)

    def audio(request: Request) -> FileResponse:
        pastas = _pastas(request)
        _short(pastas, request.path_params["corte"])
        try:
            pronto = audicao.audio_do_short(pastas, request.path_params["corte"])
        except audicao.SemFonteDeAudio as erro:
            raise HTTPException(404, str(erro)) from erro
        except (ErroSpurgeon, OSError) as erro:
            log.exception("Falha ao montar o áudio do corte.")
            raise HTTPException(500, f"Não consegui montar o áudio: {erro}") from erro
        return FileResponse(pronto.arquivo, media_type="audio/mp4")

    return [
        Route("/api/projetos/{projeto}/shorts/{corte:int}/legenda", legenda),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/audio", audio),
    ]
