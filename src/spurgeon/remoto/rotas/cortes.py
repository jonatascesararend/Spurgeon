"""Editar pelo celular (Fase R2): a trava do projeto, a legenda, as pontas e marcar/desmarcar.

Toda mudança exige a trava do celular valendo (`remoto/sessao.py`): sem ela a janela do PC pode
estar com o mesmo corte aberto. O app renova a trava sozinho; se ela venceu, a resposta é 409 com
`"erro": "sem_trava"` e o app a toma de novo. Também não se edita enquanto o PC ainda está
decidindo os cortes (uma etapa de análise rodando reescreve o `cortes_shorts.json`).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.remoto import dados, edicao, sessao
from spurgeon.remoto.contexto import Contexto
from spurgeon.shorts.edicao import LimiteInvalido

#: as etapas que podem estar rodando enquanto o celular edita (como o Studio do PC)
ETAPAS_QUE_DEIXAM_EDITAR = ("renderizar_video_longo", "renderizar_shorts", "adicionar_vinhetas",
                            "finalizar")  # fmt: skip


def _em_analise(pastas) -> str:
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        return ""
    for etapa in estado.etapas:
        if etapa.status == StatusEtapa.EM_ANDAMENTO and etapa.id not in ETAPAS_QUE_DEIXAM_EDITAR:
            return etapa.id
    return ""


def rotas(ctx: Contexto) -> list[Route]:
    def _pastas(request: Request):
        try:
            return dados.achar_projeto(ctx.pasta_projetos, request.path_params["projeto"])
        except dados.ProjetoNaoEncontrado as erro:
            raise HTTPException(404, "Projeto não encontrado.") from erro

    async def _corpo(request: Request) -> dict[str, Any]:
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        if not isinstance(corpo, dict):
            raise HTTPException(400, "Pedido inválido.")
        return corpo

    def _pode_editar(pastas) -> None:
        if not sessao.ativa(pastas):
            raise HTTPException(409, "sem_trava")
        # "em andamento" num estado.json também pode ser de um processamento que caiu: só vale se
        # há mesmo um trabalhador rodando (o do celular ou o da janela)
        if _em_analise(pastas) and ctx.fila.ocupada():
            raise HTTPException(409, "O PC ainda está escolhendo os cortes. Espere essa etapa.")

    def _resposta_do_short(pastas, id_do_corte: int) -> JSONResponse:
        for short in dados.shorts_no_disco(pastas):
            if short.corte.id == id_do_corte:
                return JSONResponse(dados.resumo_do_short(short))
        raise HTTPException(404, "Este corte não existe no projeto.")

    # --- a trava --------------------------------------------------------------------------------
    async def tomar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        id_da_sessao = sessao.tomar(pastas)
        if sessao.recibo(pastas) == id_da_sessao:
            pc = "salvou"
        else:  # pode esperar uns segundos pela janela do PC: fora do laço do servidor
            pc = await run_in_threadpool(sessao.esperar_o_pc, pastas, id_da_sessao)
        return JSONResponse({"sessao": id_da_sessao, "pc": pc, "validade_s": sessao.VALIDADE_S})

    def soltar(request: Request) -> JSONResponse:
        sessao.soltar(_pastas(request))
        return JSONResponse({"ok": True})

    # --- o corte -----------------------------------------------------------------------------
    def para_editar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        try:
            tamanhos = {}
            for lado in ("antes", "depois"):
                valor = request.query_params.get(lado, "")
                if valor.isdigit():
                    tamanhos[lado] = int(valor)
            return JSONResponse(
                edicao.para_editar(pastas, request.path_params["corte"], **tamanhos)
            )
        except edicao.CorteNaoEncontrado as erro:
            raise HTTPException(404, "Este corte não existe no projeto.") from erro
        except (OSError, ValueError) as erro:
            raise HTTPException(404, "A transcrição deste projeto não foi encontrada.") from erro

    def _mudar(request: Request, corpo: dict[str, Any], fazer) -> JSONResponse:
        pastas = _pastas(request)
        _pode_editar(pastas)
        id_do_corte = request.path_params["corte"]
        try:
            fazer(pastas, id_do_corte, corpo)
        except edicao.CorteNaoEncontrado as erro:
            raise HTTPException(404, "Este corte não existe no projeto.") from erro
        except LimiteInvalido as erro:
            raise HTTPException(422, str(erro)) from erro
        except (TypeError, KeyError, ValueError) as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        return _resposta_do_short(pastas, id_do_corte)

    async def legenda(request: Request) -> JSONResponse:
        corpo = await _corpo(request)

        def fazer(p, c, d):
            if "frase" in d:  # uma frase só, corrigida na própria legenda que acompanha o áudio
                return edicao.corrigir_frase(
                    p, c, int(d["frase"]), str(d["antes"]), str(d["texto"])
                )
            return edicao.corrigir_legenda(p, c, str(d["texto"]))

        return await run_in_threadpool(_mudar, request, corpo, fazer)

    async def pontas(request: Request) -> JSONResponse:
        corpo = await _corpo(request)

        def fazer(p, c, d):
            if "indices" in d:  # o rascunho do celular (início, fim e borracha) de uma vez
                return edicao.definir_palavras(p, c, list(d["indices"]))
            if "passo" in d:
                return edicao.mover_ponta(p, c, str(d["ponta"]), int(d["passo"]))
            primeira = None if d.get("primeira") is None else int(d["primeira"])
            ultima = None if d.get("ultima") is None else int(d["ultima"])
            return edicao.mudar_pontas(p, c, primeira=primeira, ultima=ultima)

        return await run_in_threadpool(_mudar, request, corpo, fazer)

    async def marcar(request: Request) -> JSONResponse:
        corpo = await _corpo(request)
        return await run_in_threadpool(
            _mudar, request, corpo, lambda p, c, d: edicao.marcar(p, c, bool(d["incluir"]))
        )

    def legenda_em_texto(request: Request) -> PlainTextResponse:
        """A legenda do Short num arquivo de texto, com o tempo de cada frase (para baixar)."""
        from spurgeon.remoto.postagem import sermao_do_projeto
        from spurgeon.shorts.exportar import (
            legenda_no_tempo_do_short,
            nome_do_arquivo,
            texto_da_legenda,
        )
        from spurgeon.transcricao.formato import Transcricao

        pastas = _pastas(request)
        short = next(
            (
                s
                for s in dados.shorts_no_disco(pastas)
                if s.corte.id == request.path_params["corte"]
            ),
            None,
        )
        if short is None:
            raise HTTPException(404, "Este corte não existe no projeto.")
        try:
            palavras = Transcricao.carregar(pastas.transcricao).palavras
        except (OSError, ValueError) as erro:
            raise HTTPException(404, "A transcrição deste projeto não foi encontrada.") from erro
        legenda = legenda_no_tempo_do_short(palavras, short.corte)
        texto = texto_da_legenda(short.corte, legenda, sermao=sermao_do_projeto(pastas),
                                 numero=short.numero)  # fmt: skip
        nome = nome_do_arquivo(short.corte, short.numero)
        return PlainTextResponse(
            texto, media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
        )  # fmt: skip

    return [
        Route("/api/projetos/{projeto}/shorts/{corte:int}/legenda.txt", legenda_em_texto),
        Route("/api/projetos/{projeto}/sessao", tomar, methods=["POST"]),
        Route("/api/projetos/{projeto}/sessao/soltar", soltar, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/edicao", para_editar),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/legenda", legenda, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/pontas", pontas, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/marcar", marcar, methods=["POST"]),
    ]
