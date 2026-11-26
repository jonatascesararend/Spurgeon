"""Postar no YouTube pelo celular (Fase R4; `remoto/postagem.py`), pela fila dos Shorts.

Postar, programar e trocar o horário entram na fila (`shorts/fila.py`): o despachante renderiza
antes o que falta e só abre o YouTube Studio com a renderização parada, um Short por vez.
"""

from __future__ import annotations

import json
from datetime import datetime

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from spurgeon.publicacao.modelo import AGENDADO, ENVIADO
from spurgeon.remoto import dados, postagem, sessao
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.despachante import horario_no_youtube
from spurgeon.shorts import fila


def marca_do_short(pastas, id_do_corte: int) -> dict | None:
    """A marca da fila deste Short (o que o celular mostra na tela dele), ou None."""
    shorts = {s.corte.id: s for s in dados.shorts_no_disco(pastas)}
    marcas = fila.marcas_do_projeto(
        pastas, lambda i: i in shorts and shorts[i].situacao == "pronto", [id_do_corte]
    )
    marca = marcas.get(id_do_corte)
    return marca.para_json() if marca else None


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    async def _corpo(request: Request) -> dict:
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        if not isinstance(corpo, dict):
            raise HTTPException(400, "Pedido inválido.")
        return corpo

    def _acordar() -> None:
        if ctx.despachante is not None:
            ctx.despachante.acordar()

    def preparar(request: Request) -> JSONResponse:
        """O que a tela de postar precisa: o sermão a vincular, o que já foi feito no YouTube e
        como o Short está na fila."""
        pastas = _pastas(request)
        id_do_corte = request.path_params["corte"]
        anterior = postagem.do_youtube(pastas, id_do_corte)
        marcado = horario_no_youtube(pastas, id_do_corte)
        return JSONResponse({
            "relacionado": postagem.sermao_do_projeto(pastas),
            "youtube": anterior.para_dict() if anterior else None,
            "youtube_quando": marcado.strftime(fila.FORMATO) if marcado else "",
            "fila": marca_do_short(pastas, id_do_corte),
            "envio": None,
        })  # fmt: skip

    async def postar(request: Request) -> JSONResponse:
        """Põe o Short na fila do YouTube (renderiza antes, se ainda não está pronto)."""
        pastas = _pastas(request)
        corpo = await _corpo(request)
        if not sessao.ativa(pastas):
            raise HTTPException(409, "sem_trava")
        id_do_corte = request.path_params["corte"]
        short = next((s for s in dados.shorts_no_disco(pastas) if s.corte.id == id_do_corte), None)
        if short is None:
            raise HTTPException(409, "Este corte não existe no projeto.")
        anterior = postagem.do_youtube(pastas, id_do_corte)
        if (
            anterior is not None
            and anterior.estado in (AGENDADO, ENVIADO)
            and not corpo.get("forcar")
        ):
            raise HTTPException(
                409,
                "Este Short já foi "
                + ("programado" if anterior.estado == AGENDADO else "publicado")
                + f" no YouTube ({anterior.url}).",
            )
        try:
            pedido = postagem.ler_pedido(corpo, id_do_corte, datetime.now())
            if not short.corte.incluir:  # programar é aprovar: a fila renderiza os aprovados
                from spurgeon.shorts.etapas import gravar_campos_do_corte

                gravar_campos_do_corte(pastas.cortes_shorts, id_do_corte, incluir=True)
            fila.pedir_postagem(pastas, id_do_corte, pedido.quando, pedido.relacionado)
        except (postagem.PostagemInvalida, fila.PedidoInvalido) as erro:
            raise HTTPException(409, str(erro)) from erro
        _acordar()
        return JSONResponse({"corte": id_do_corte, "fila": marca_do_short(pastas, id_do_corte)})

    async def reagendar(request: Request) -> JSONResponse:
        """Troca o horário de um Short já programado no YouTube (até 5 minutos antes dele sair)."""
        pastas = _pastas(request)
        corpo = await _corpo(request)
        if not sessao.ativa(pastas):
            raise HTTPException(409, "sem_trava")
        id_do_corte = request.path_params["corte"]
        marcado = horario_no_youtube(pastas, id_do_corte)
        if marcado is None:
            raise HTTPException(409, "Este Short não está programado no YouTube pelo Spurgeon.")
        try:
            novo = datetime.strptime(str(corpo.get("quando") or ""), fila.FORMATO)
            fila.pedir_reagendamento(pastas, id_do_corte, marcado, novo)
        except ValueError as erro:  # PedidoInvalido também é um ValueError
            texto = str(erro) if isinstance(erro, fila.PedidoInvalido) else "Data ou hora inválida."
            raise HTTPException(409, texto) from erro
        _acordar()
        return JSONResponse({"corte": id_do_corte, "fila": marca_do_short(pastas, id_do_corte)})

    def tirar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        id_do_corte = request.path_params["corte"]
        try:
            fila.tirar_da_fila(pastas, id_do_corte)
        except fila.PedidoInvalido as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse({"corte": id_do_corte, "fila": marca_do_short(pastas, id_do_corte)})

    def envio(request: Request) -> JSONResponse:
        """O Short do projeto que a fila está programando no YouTube agora (ou None)."""
        pastas = _pastas(request)
        for item in fila.FilaDoProjeto(pastas).itens():
            if item.fase == fila.FASE_YOUTUBE:
                return JSONResponse({"projeto": pastas.raiz.name, "corte": item.corte,
                                     "texto": item.texto, "rodando": True})  # fmt: skip
        return JSONResponse(None)

    return [
        Route("/api/projetos/{projeto}/shorts/{corte:int}/postar", preparar),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/postar", postar, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/reagendar", reagendar, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/fila/tirar", tirar, methods=["POST"]),
        Route("/api/projetos/{projeto}/postagem", envio),
    ]
