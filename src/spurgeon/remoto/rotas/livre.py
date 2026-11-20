"""Shorts livre pelo celular (Fase R3; `remoto/livre.py`): o texto vivo, a busca, ouvir o trecho e
criar o Short com a IA."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from spurgeon.core.erros import ErroSpurgeon
from spurgeon.remoto import audicao, dados, livre, sessao
from spurgeon.remoto.contexto import Contexto

_textos: dict[Path, tuple[float, livre.Texto]] = {}
_trava = threading.Lock()


def _texto(pastas) -> livre.Texto:
    """O texto do sermão, lido de novo só quando a transcrição ou o estado mudam."""
    try:
        marca = pastas.transcricao.stat().st_mtime + pastas.estado.stat().st_mtime
    except OSError as erro:
        raise HTTPException(404, "Este projeto ainda não tem a transcrição do sermão.") from erro
    with _trava:
        guardado = _textos.get(pastas.raiz)
        if guardado is not None and guardado[0] == marca:
            return guardado[1]
    texto = livre.carregar_texto(pastas)
    with _trava:
        _textos[pastas.raiz] = (marca, texto)
    return texto


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    def _trecho(texto: livre.Texto, origem: dict[str, Any]):
        fora = origem.get("fora") or []
        if isinstance(fora, str):
            fora = [n for n in fora.split(",") if n.strip()]
        try:
            return livre.trecho_do_pedido(texto, origem.get("de"), origem.get("ate"), fora)
        except livre.TrechoInvalido as erro:
            raise HTTPException(422, str(erro)) from erro

    def texto(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        if not pastas.cortes_shorts.exists():
            raise HTTPException(409, "O Shorts livre abre depois que o PC calculou os cortes.")
        resposta = livre.texto_para_json(pastas, _texto(pastas))
        criacao = ctx.criacoes.atual
        resposta["criacao"] = (
            criacao.para_json() if criacao and criacao.projeto == pastas.raiz.name else None
        )
        return JSONResponse(resposta)

    def buscar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        consulta = request.query_params.get("q", "")[:200]
        modo = "tema" if request.query_params.get("modo") == "tema" else "palavras"
        try:
            return JSONResponse(livre.buscar(_texto(pastas), consulta, modo))
        except (ImportError, OSError) as erro:
            raise HTTPException(503, "A busca por tema precisa do modelo de português.") from erro

    def trecho(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        texto_do_sermao = _texto(pastas)
        escolhido = _trecho(texto_do_sermao, dict(request.query_params))
        resposta = livre.resumo_do_trecho(texto_do_sermao, escolhido)
        corte = livre.corte_de_previa(texto_do_sermao, escolhido)
        resposta["legenda"] = audicao.legenda_para_json(
            audicao.legenda_do_short(audicao.carregar_palavras(pastas), corte)
        )
        resposta["tem_som"] = audicao.fonte_do_original(pastas) is not None
        return JSONResponse(resposta)

    def audio(request: Request) -> FileResponse:
        pastas = _pastas(request)
        texto_do_sermao = _texto(pastas)
        escolhido = _trecho(texto_do_sermao, dict(request.query_params))
        fonte = audicao.fonte_do_original(pastas)
        if fonte is None:
            raise HTTPException(
                404, "O vídeo original não está mais no PC: não há de onde tirar o som."
            )
        corte = livre.corte_de_previa(texto_do_sermao, escolhido)
        try:
            arquivo = audicao.audio_do_corte(pastas, corte, "trecho_livre", fonte)
        except (ErroSpurgeon, OSError) as erro:
            raise HTTPException(500, f"Não consegui montar o áudio: {erro}") from erro
        return FileResponse(arquivo, media_type="audio/mp4")

    async def criar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        if not isinstance(corpo, dict):
            raise HTTPException(400, "Pedido inválido.")
        if not sessao.ativa(pastas):
            raise HTTPException(409, "sem_trava")
        texto_do_sermao = await run_in_threadpool(_texto, pastas)
        escolhido = _trecho(texto_do_sermao, corpo)
        problema = livre.problema_do_trecho(texto_do_sermao.palavras, escolhido)
        if problema:
            raise HTTPException(422, problema)
        try:
            criacao = ctx.criacoes.iniciar(pastas, texto_do_sermao, escolhido)
        except livre.TrechoInvalido as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse(criacao.para_json())

    def criacao(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        atual = ctx.criacoes.atual
        if atual is None or atual.projeto != pastas.raiz.name:
            return JSONResponse(None)
        return JSONResponse(atual.para_json())

    return [
        Route("/api/projetos/{projeto}/livre/texto", texto),
        Route("/api/projetos/{projeto}/livre/buscar", buscar),
        Route("/api/projetos/{projeto}/livre/trecho", trecho),
        Route("/api/projetos/{projeto}/livre/audio", audio),
        Route("/api/projetos/{projeto}/livre/criar", criar, methods=["POST"]),
        Route("/api/projetos/{projeto}/livre/criacao", criacao),
    ]
