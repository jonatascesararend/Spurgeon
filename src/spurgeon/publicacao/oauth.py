"""Entrar numa conta pelo navegador: OAuth de aplicativo de desktop (loopback + PKCE).

O aplicativo abre a página de autorização da rede no navegador de sempre, escuta um endereço local
(`http://127.0.0.1:porta/callback`) e recebe o código quando a pessoa aceita. Nada de URI de
"copie e cole": o Google bloqueia o fluxo manual (OOB) e o TikTok só aceita localhost em desktop.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

from spurgeon.core.erros import Cancelado
from spurgeon.publicacao.erros import ErroDePublicacao

HOST_LOCAL = "127.0.0.1"
TEMPO_PARA_AUTORIZAR_S = 300.0

_PAGINA_OK = (
    "<!doctype html><meta charset='utf-8'><title>Studio Spurgeon</title>"
    "<body style='font-family:Segoe UI,sans-serif;text-align:center;margin-top:15vh'>"
    "<h2>Pronto!</h2><p>A conta foi conectada. Pode fechar esta aba e voltar ao "
    "Studio Spurgeon.</p></body>"
)
_PAGINA_ERRO = (
    "<!doctype html><meta charset='utf-8'><title>Studio Spurgeon</title>"
    "<body style='font-family:Segoe UI,sans-serif;text-align:center;margin-top:15vh'>"
    "<h2>Não foi possível conectar</h2><p>{motivo}</p><p>Volte ao Studio Spurgeon e "
    "tente de novo.</p></body>"
)


def gerar_pkce(codificacao: str = "base64url") -> tuple[str, str]:
    """O `code_verifier` e o `code_challenge` (S256). O TikTok pede o SHA-256 em HEXADECIMAL; o
    resto do mundo (Google, Meta), em base64 sem preenchimento."""
    verificador = secrets.token_urlsafe(64)[:96]  # de 43 a 128 caracteres
    resumo = hashlib.sha256(verificador.encode("ascii")).digest()
    if codificacao == "hex":
        return verificador, resumo.hex()
    return verificador, base64.urlsafe_b64encode(resumo).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class Autorizacao:
    codigo: str
    redirect_uri: str


class _Captura:
    def __init__(self) -> None:
        self.parametros: dict[str, str] | None = None


def _atendente(captura: _Captura, caminho: str):
    class Atendente(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            partes = urllib.parse.urlsplit(self.path)
            if partes.path.rstrip("/") != caminho.rstrip("/"):
                self.send_error(404)
                return
            parametros = {k: v[0] for k, v in urllib.parse.parse_qs(partes.query).items()}
            captura.parametros = parametros
            erro = parametros.get("error_description") or parametros.get("error")
            pagina = _PAGINA_ERRO.format(motivo=erro) if erro else _PAGINA_OK
            corpo = pagina.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *_args) -> None:  # silencioso: o aplicativo não tem console
            return

    return Atendente


def aguardar_codigo(
    montar_url: Callable[[str, str], str],
    *,
    porta: int = 0,
    caminho: str = "/callback",
    abrir: Callable[[str], object] = webbrowser.open,
    tempo_limite_s: float = TEMPO_PARA_AUTORIZAR_S,
    cancelado: Callable[[], bool] | None = None,
) -> Autorizacao:
    """Abre a autorização no navegador e espera o código voltar para o endereço local.

    `montar_url(redirect_uri, estado)` devolve o endereço da página de autorização da rede.
    `porta=0` escolhe uma porta livre (Google, Meta); o TikTok exige uma porta combinada de antemão.
    """
    captura = _Captura()
    try:
        servidor = HTTPServer((HOST_LOCAL, porta), _atendente(captura, caminho))
    except OSError as erro:
        raise ErroDePublicacao(
            f"Não foi possível escutar a porta {porta} deste computador ({erro.strerror or erro}). "
            "Feche o programa que a usa e tente de novo."
        ) from erro
    with servidor:
        servidor.timeout = 0.4
        endereco = f"http://{HOST_LOCAL}:{servidor.server_address[1]}{caminho}"
        estado = secrets.token_urlsafe(16)
        abrir(montar_url(endereco, estado))
        limite = time.monotonic() + tempo_limite_s
        while captura.parametros is None:
            if cancelado is not None and cancelado():
                raise Cancelado("Conexão cancelada.")
            if time.monotonic() > limite:
                raise ErroDePublicacao(
                    "A autorização não terminou a tempo. Clique em Conectar e aceite no navegador."
                )
            servidor.handle_request()
    parametros = captura.parametros
    if parametros.get("error"):
        motivo = parametros.get("error_description") or parametros["error"]
        raise ErroDePublicacao(f"A autorização foi recusada: {motivo}")
    if parametros.get("state") != estado:
        raise ErroDePublicacao(
            "A resposta do navegador não confere com o pedido (state). Tente de novo."
        )
    codigo = parametros.get("code", "")
    if not codigo:
        raise ErroDePublicacao("O navegador voltou sem o código de autorização.")
    return Autorizacao(codigo, endereco)
