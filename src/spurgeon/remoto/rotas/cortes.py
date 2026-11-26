"""Editar pelo celular (Fase R2): a trava do projeto, a legenda, as pontas e marcar/desmarcar.

Toda mudança exige a trava do celular valendo (`remoto/sessao.py`): sem ela a janela do PC pode
estar com o mesmo corte aberto. O app renova a trava sozinho; se ela venceu, a resposta é 409 com
`"erro": "sem_trava"` e o app a toma de novo. Também não se edita enquanto o PC ainda está
decidindo os cortes (uma etapa de análise rodando reescreve o `cortes_shorts.json`).
"""

from __future__ import annotations

import json
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.remoto import dados, edicao, sessao
from spurgeon.remoto.contexto import Contexto
from spurgeon.render import trecho_de_audio
from spurgeon.shorts import revisao_gemini
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
        # o mesmo resumo da lista (com a marca da fila e o YouTube): a tela do Short o acompanha
        for resumo in dados.shorts(pastas):
            if resumo["id"] == id_do_corte:
                return JSONResponse(resumo)
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

    async def opiniao(request: Request) -> JSONResponse:
        """👍 / 👎 do Short (`opiniao`: "bom", "ruim" ou "" para tirar)."""
        corpo = await _corpo(request)

        def fazer(p, c, d):
            valor = d["opiniao"]
            if not isinstance(valor, str):
                raise TypeError("opiniao")
            return edicao.dar_opiniao(p, c, valor)

        return await run_in_threadpool(_mudar, request, corpo, fazer)

    # --- ajuste fino da legenda ----------------------------------------------------------------
    def ajuste_fino(request: Request) -> JSONResponse:
        try:
            return JSONResponse(
                edicao.para_o_ajuste_fino(_pastas(request), request.path_params["corte"])
            )
        except edicao.CorteNaoEncontrado as erro:
            raise HTTPException(404, "Este corte não existe no projeto.") from erro
        except (OSError, ValueError) as erro:
            raise HTTPException(404, "A transcrição deste projeto não foi encontrada.") from erro

    async def salvar_ajuste_fino(request: Request) -> JSONResponse:
        corpo = await _corpo(request)
        return await run_in_threadpool(
            _mudar, request, corpo,
            lambda p, c, d: edicao.salvar_ajuste_fino(p, c, d["palavras"]),
        )  # fmt: skip

    async def desfazer_ajuste_fino(request: Request) -> JSONResponse:
        return await run_in_threadpool(
            _mudar, request, {}, lambda p, c, _d: edicao.desfazer_ajuste_fino(p, c)
        )

    def _som_do_ajuste(request: Request) -> FileResponse:
        pastas = _pastas(request)
        try:
            arquivo = edicao.som_do_ajuste_fino(pastas, request.path_params["corte"])
        except edicao.CorteNaoEncontrado as erro:
            raise HTTPException(404, "Este corte não existe no projeto.") from erro
        except trecho_de_audio.SemSom as erro:
            raise HTTPException(409, str(erro)) from erro
        except (ErroSpurgeon, OSError, ValueError) as erro:
            raise HTTPException(500, f"Não consegui montar o som do trecho: {erro}") from erro
        return FileResponse(arquivo, media_type="audio/mp4")

    async def som_do_ajuste(request: Request) -> FileResponse:
        return await run_in_threadpool(_som_do_ajuste, request)

    def _revisar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        _pode_editar(pastas)
        config = ctx.gemini()
        if config is None:
            raise HTTPException(
                409,
                "Sem a chave do Gemini: cole a chave na aba Integrações do programa no PC.",
            )
        id_do_corte = request.path_params["corte"]
        try:
            edicao.revisar_com_gemini(pastas, id_do_corte, config)
        except edicao.CorteNaoEncontrado as erro:
            raise HTTPException(404, "Este corte não existe no projeto.") from erro
        except revisao_gemini.TextoCurto as erro:
            raise HTTPException(422, str(erro)) from erro
        except ErroSpurgeon as erro:  # a API do Gemini recusou ou não respondeu
            raise HTTPException(502, str(erro)) from erro
        return _resposta_do_short(pastas, id_do_corte)

    async def gemini(request: Request) -> JSONResponse:
        """O Gemini revisa este Short (leva de alguns segundos a um minuto)."""
        return await run_in_threadpool(_revisar, request)

    async def pausas(request: Request) -> JSONResponse:
        """O corte das pausas deste Short (`pausa_s`: 0 desliga)."""
        corpo = await _corpo(request)

        def fazer(p, c, d):
            valor = d["pausa_s"]
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                raise TypeError("pausa_s")
            return edicao.mudar_pausas(p, c, float(valor))

        return await run_in_threadpool(_mudar, request, corpo, fazer)

    async def textos(request: Request) -> JSONResponse:
        """Título, descrição e hashtags (cada um só se vier no pedido)."""
        corpo = await _corpo(request)

        def fazer(p, c, d):
            def texto(nome):
                return None if d.get(nome) is None else str(d[nome])

            hashtags = d.get("hashtags")
            if hashtags is not None and not isinstance(hashtags, (str, list)):
                raise TypeError("hashtags")
            return edicao.mudar_textos(
                p, c, titulo=texto("titulo"), descricao=texto("descricao"), hashtags=hashtags,
                mexer_em_arquivos=not ctx.fila.ocupada(),
            )  # fmt: skip

        return await run_in_threadpool(_mudar, request, corpo, fazer)

    return [
        Route("/api/projetos/{projeto}/shorts/{corte:int}/textos", textos, methods=["POST"]),
        Route("/api/projetos/{projeto}/sessao", tomar, methods=["POST"]),
        Route("/api/projetos/{projeto}/sessao/soltar", soltar, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/edicao", para_editar),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/legenda", legenda, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/pontas", pontas, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/marcar", marcar, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/pausas", pausas, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/opiniao", opiniao, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/gemini", gemini, methods=["POST"]),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/ajuste_fino", ajuste_fino),
        Route(
            "/api/projetos/{projeto}/shorts/{corte:int}/ajuste_fino",
            salvar_ajuste_fino,
            methods=["POST"],
        ),
        Route(
            "/api/projetos/{projeto}/shorts/{corte:int}/ajuste_fino/desfazer",
            desfazer_ajuste_fino,
            methods=["POST"],
        ),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/ajuste_fino/som", som_do_ajuste),
    ]
