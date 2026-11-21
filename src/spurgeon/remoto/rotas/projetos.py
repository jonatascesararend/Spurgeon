"""Projetos, andamento e Shorts já renderizados (Fase R1: só ver).

Ainda por vir aqui (Remoto.md, Fases R2 e R5): iniciar, pausar, cancelar e aprovar os cortes.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.remoto import configuracao, dados, limpeza, novo, sessao
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
        resposta["tem_transcricao"] = pastas.transcricao.is_file()
        resposta["baixado"] = novo.video_baixado(pastas)
        resposta["do_canal"] = bool(novo.ler_origem(pastas).get("video_id"))
        resposta["nao_comecou"] = configuracao.nao_comecou(pastas)
        tarefa = ctx.novos.atual
        deste = tarefa is not None and tarefa.projeto == pastas.raiz.name
        resposta["tarefa"] = tarefa.para_json() if deste else None
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

    def legenda_do_sermao(request: Request) -> PlainTextResponse:
        """A legenda do sermão inteiro num arquivo de texto, com o tempo de cada frase."""
        from spurgeon.core.projeto import ler_nome_e_data
        from spurgeon.shorts.exportar import nome_do_arquivo_do_sermao, texto_da_legenda_do_sermao
        from spurgeon.transcricao.formato import Transcricao

        pastas = _pastas(request)
        try:
            transcricao = Transcricao.carregar(pastas.transcricao)
        except (OSError, ValueError) as erro:
            raise HTTPException(
                404, "Este projeto ainda não tem a transcrição do sermão."
            ) from erro
        lido = ler_nome_e_data(pastas.raiz)
        sermao = lido[0] if lido else pastas.raiz.name
        texto = texto_da_legenda_do_sermao(
            transcricao.palavras, sermao=sermao, duracao_s=transcricao.duracao_s
        )
        nome = nome_do_arquivo_do_sermao(sermao)
        return PlainTextResponse(
            texto, media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
        )  # fmt: skip

    # --- opções do projeto e "Começar" (`remoto/configuracao.py`, `remoto/novo.py`) -------------
    def opcoes(request: Request) -> JSONResponse:
        try:
            return JSONResponse(configuracao.configuracao(_pastas(request)))
        except configuracao.OpcaoInvalida as erro:
            raise HTTPException(409, str(erro)) from erro

    async def mudar_opcoes(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        if not isinstance(corpo, dict):
            raise HTTPException(400, "Pedido inválido.")
        try:
            return JSONResponse(configuracao.mudar(pastas, corpo))
        except configuracao.OpcaoInvalida as erro:
            raise HTTPException(422, str(erro)) from erro

    def comecar(request: Request) -> JSONResponse:
        """Baixa o vídeo do canal (se ainda não veio) e começa o processamento com as opções."""
        pastas = _pastas(request)
        try:
            tarefa = ctx.novos.comecar(pastas)
        except novo.NovoInvalido as erro:
            raise HTTPException(409, str(erro)) from erro
        return JSONResponse(tarefa.para_json())

    # --- liberar espaço (`remoto/limpeza.py`) ----------------------------------------------------
    def _impedimento(pastas, *, apagando: bool) -> str:
        if motivo := ctx.fila.ocupada():
            return f"{motivo} Espere terminar para liberar espaço."
        envio = ctx.postagens.atual
        if ctx.postagens.ocupada() and envio is not None and envio.projeto == pastas.raiz.name:
            return "O PC está postando um Short deste projeto. Espere terminar."
        if ctx.criacoes.ocupada():
            return "A IA está criando um Short no PC. Espere terminar."
        if apagando and sessao.janela_com_o_projeto(pastas):
            return "Este projeto está aberto na janela do PC. Feche-o lá e tente de novo."
        return ""

    def espaco(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        resposta = limpeza.espaco_do_projeto(pastas, ctx.pasta_projetos)
        resposta["impedimento_varrer"] = _impedimento(pastas, apagando=False)
        resposta["impedimento_apagar"] = _impedimento(pastas, apagando=True)
        return JSONResponse(resposta)

    async def _confirmado(request: Request, pastas) -> None:
        try:
            corpo = json.loads(await request.body() or b"{}")
        except ValueError as erro:
            raise HTTPException(400, "Pedido inválido.") from erro
        if not isinstance(corpo, dict) or corpo.get("confirmar") != pastas.raiz.name:
            raise HTTPException(400, "Confirme com o nome do projeto.")

    async def varrer(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        await _confirmado(request, pastas)
        if motivo := _impedimento(pastas, apagando=False):
            raise HTTPException(409, motivo)

        def fazer():
            id_da_sessao = sessao.tomar(pastas)  # a janela do PC salva e trava o Studio antes
            try:
                sessao.esperar_o_pc(pastas, id_da_sessao)
                return limpeza.varrer(pastas, ctx.pasta_projetos)
            finally:
                sessao.soltar(pastas)

        resultado = await run_in_threadpool(fazer)
        return JSONResponse(resultado.para_json())

    async def apagar(request: Request) -> JSONResponse:
        pastas = _pastas(request)
        await _confirmado(request, pastas)
        if motivo := _impedimento(pastas, apagando=True):
            raise HTTPException(409, motivo)
        resultado = await run_in_threadpool(limpeza.apagar_projeto, pastas, ctx.pasta_projetos)
        return JSONResponse(resultado.para_json())

    return [
        Route("/api/projetos/{projeto}/opcoes", opcoes),
        Route("/api/projetos/{projeto}/opcoes", mudar_opcoes, methods=["POST"]),
        Route("/api/projetos/{projeto}/comecar", comecar, methods=["POST"]),
        Route("/api/projetos/{projeto}/espaco", espaco),
        Route("/api/projetos/{projeto}/varrer", varrer, methods=["POST"]),
        Route("/api/projetos/{projeto}/apagar", apagar, methods=["POST"]),
        Route("/api/projetos/{projeto}/legenda.txt", legenda_do_sermao),
        Route("/api/projetos/{projeto}/processar", processar, methods=["POST"]),
        Route("/api/projetos/{projeto}/aprovar", aprovar, methods=["POST"]),
        Route("/api/projetos/{projeto}/renderizar", renderizar, methods=["POST"]),
        Route("/api/projetos", lista),
        Route("/api/projetos/{projeto}", detalhe),
        Route("/api/projetos/{projeto}/andamento", andamento),
        Route("/api/projetos/{projeto}/shorts", shorts),
        Route("/api/projetos/{projeto}/shorts/{corte:int}/video", video_do_short),
    ]
