"""O servidor web do controle remoto: a API (`rotas/`) e o app do celular (`web/`).

Escuta só em `127.0.0.1`, numa porta exclusiva. No Windows, o padrão (`SO_REUSEADDR`) deixa dois
programas na mesma porta sem erro, e quem responde é o outro (aconteceu na R0.1). O
`tailscale serve` leva o HTTPS da rede privada até ela. Toda chamada passa pelo `Porteiro`: conta do
Tailscale permitida e, dormindo, só o que acorda.
"""

from __future__ import annotations

import io
import logging
import socket
import sys
from functools import cache
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from spurgeon.core.config import raiz_repositorio
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.rotas import canal, cortes, livre, postagem, previa, projetos, sistema
from spurgeon.remoto.seguranca import autorizado

log = logging.getLogger(__name__)

PASTA_WEB = Path(__file__).parent / "web"
TAMANHOS_DO_ICONE = (192, 512)
#: o que continua valendo com o servidor dormindo (para acordar e ver que ele dorme)
_LIVRES_DORMINDO = ("/api/sistema", "/api/avisos", "/api/processamento")


class Porteiro:
    """Barra quem não é do Tailscale permitido e, dormindo, a API (menos o que acorda)."""

    def __init__(self, app: ASGIApp, ctx: Contexto):
        self.app = app
        self.ctx = ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabecalhos = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        if not autorizado(cabecalhos, self.ctx.config.usuarios):
            resposta: Response = PlainTextResponse(
                "Acesso só pela conta do Tailscale autorizada neste PC.", status_code=403
            )
            await resposta(scope, receive, send)
            return
        caminho: str = scope["path"]
        if (
            self.ctx.config.dormindo
            and caminho.startswith("/api/")
            and not caminho.startswith(_LIVRES_DORMINDO)
        ):
            resposta = JSONResponse(
                {"erro": "dormindo", "mensagem": "O PC está dormindo. Acorde-o para usar."},
                status_code=503,
            )
            await resposta(scope, receive, send)
            return
        await self.app(scope, receive, send)


@cache
def icone_png(tamanho: int) -> bytes:
    """O ícone do Studio (`recursos/studio_spurgeon.ico`) no tamanho que o celular pede."""
    from PIL import Image

    caminho = raiz_repositorio() / "recursos" / "studio_spurgeon.ico"
    with Image.open(caminho) as ico:
        maior = max(ico.info.get("sizes", {ico.size}), key=lambda s: s[0])
        ico.size = maior
        imagem = ico.convert("RGBA").resize((tamanho, tamanho), Image.LANCZOS)
    saida = io.BytesIO()
    imagem.save(saida, format="PNG")
    return saida.getvalue()


def _icone(request: Request) -> Response:
    tamanho = request.path_params["tamanho"]
    if tamanho not in TAMANHOS_DO_ICONE:
        raise HTTPException(404)
    try:
        return Response(icone_png(tamanho), media_type="image/png")
    except OSError as erro:
        raise HTTPException(404) from erro


async def _erro_http(_request: Request, erro: Exception) -> Response:
    assert isinstance(erro, HTTPException)
    return JSONResponse({"erro": erro.detail}, status_code=erro.status_code)


def criar_app(ctx: Contexto) -> ASGIApp:
    rotas = [
        *sistema.rotas(ctx),
        *projetos.rotas(ctx),
        *previa.rotas(ctx),
        *cortes.rotas(ctx),
        *livre.rotas(ctx),
        *postagem.rotas(ctx),
        *canal.rotas(ctx),
        Route("/icone-{tamanho:int}.png", _icone),
        Mount("/", app=StaticFiles(directory=PASTA_WEB, html=True), name="web"),
    ]
    app = Starlette(routes=rotas, exception_handlers={HTTPException: _erro_http})
    return Porteiro(app, ctx)


def socket_exclusivo(porta: int, host: str = "127.0.0.1") -> socket.socket:
    """Liga a porta só para este programa; OSError se outro já a usa."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, porta))
    except OSError:
        sock.close()
        raise
    return sock


def servir(ctx: Contexto, porta: int, *, ao_iniciar=None, ao_terminar=None) -> None:
    """Roda até `ctx.encerrar()` (pelo celular) ou Ctrl+C. OSError se a porta está ocupada."""
    import uvicorn

    sock = socket_exclusivo(porta)
    config = uvicorn.Config(
        criar_app(ctx), log_config=None, access_log=False, lifespan="off",
        timeout_graceful_shutdown=5,
    )  # fmt: skip
    servidor = uvicorn.Server(config)
    ctx.encerrar = lambda: setattr(servidor, "should_exit", True)
    if ao_iniciar is not None:
        ao_iniciar()
    log.info("Controle remoto ouvindo em http://127.0.0.1:%s", porta)
    try:
        servidor.run(sockets=[sock])
    finally:
        if ao_terminar is not None:
            ao_terminar()
        sock.close()
        log.info("Controle remoto encerrado.")
