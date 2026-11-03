"""A conversa HTTP com as redes: um `Transporte` simples que os testes trocam por um falso."""

from __future__ import annotations

import json
import mimetypes
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from spurgeon.core.erros import Cancelado
from spurgeon.publicacao.erros import ErroDeRede

TEMPO_LIMITE_S = 60.0
#: o envio de um pedaço do vídeo pode demorar: a conexão do usuário pode ser lenta
TEMPO_LIMITE_DO_ENVIO_S = 300.0
ESPERAS_ENTRE_TENTATIVAS_S = (2.0, 6.0, 15.0)

T = TypeVar("T")


@dataclass
class Resposta:
    codigo: int
    cabecalhos: dict[str, str] = field(default_factory=dict)  # chaves em minúsculas
    corpo: bytes = b""

    @property
    def texto(self) -> str:
        return self.corpo.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.corpo.decode("utf-8")) if self.corpo.strip() else {}

    @property
    def ok(self) -> bool:
        return 200 <= self.codigo < 300


class Transporte(Protocol):
    def pedir(
        self,
        metodo: str,
        url: str,
        *,
        cabecalhos: dict[str, str] | None = None,
        corpo: bytes | None = None,
        timeout: float = TEMPO_LIMITE_S,
    ) -> Resposta: ...


class TransporteUrllib:
    """O transporte de verdade (biblioteca padrão: o aplicativo não ganha dependência nova)."""

    def pedir(
        self,
        metodo: str,
        url: str,
        *,
        cabecalhos: dict[str, str] | None = None,
        corpo: bytes | None = None,
        timeout: float = TEMPO_LIMITE_S,
    ) -> Resposta:
        pedido = urllib.request.Request(url, data=corpo, method=metodo.upper())
        for nome, valor in (cabecalhos or {}).items():
            pedido.add_header(nome, valor)
        if "user-agent" not in {n.lower() for n in (cabecalhos or {})}:
            pedido.add_header("User-Agent", "StudioSpurgeon")
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                return _montar(resposta.status, resposta.headers, resposta.read())
        except urllib.error.HTTPError as erro:  # 4xx e 5xx também são respostas
            return _montar(erro.code, erro.headers, erro.read())
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            motivo = getattr(erro, "reason", erro)
            raise ErroDeRede(f"Sem conexão com {_dominio(url)} ({motivo}).") from erro


def _montar(codigo: int, cabecalhos, corpo: bytes) -> Resposta:
    return Resposta(codigo, {k.lower(): v for k, v in cabecalhos.items()}, corpo)


def _dominio(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


# --- pedidos prontos --------------------------------------------------------------------------
def codificar_formulario(dados: dict[str, Any]) -> bytes:
    """`chave=valor&…`: bool vira true/false e o que for None é omitido."""
    limpos = {
        k: ("true" if v is True else "false" if v is False else v)
        for k, v in dados.items()
        if v is not None
    }
    return urllib.parse.urlencode(limpos).encode("utf-8")


def mensagem_da_resposta(resposta: Resposta) -> str:
    """O texto de erro que a rede devolveu (Google, Meta e TikTok usam formatos diferentes)."""
    try:
        dados = resposta.json()
    except ValueError:
        return resposta.texto.strip()[:300]
    if isinstance(dados, dict):
        erro = dados.get("error")
        if isinstance(erro, dict):
            partes = [erro.get("message"), erro.get("error_user_msg"), erro.get("code")]
            if erro.get("errors"):  # Google: lista de erros com `reason`
                partes += [e.get("reason") for e in erro["errors"] if isinstance(e, dict)]
            return " | ".join(str(p) for p in partes if p)
        if isinstance(erro, str):  # OAuth: {"error": "invalid_grant", "error_description": …}
            return " | ".join(filter(None, [erro, dados.get("error_description")]))
        mensagem = dados.get("message") or dados.get("error_description")
        if mensagem:
            return str(mensagem)
    return resposta.texto.strip()[:300]


def exigir_ok(resposta: Resposta, contexto: str) -> Resposta:
    """Devolve a resposta se deu certo; senão levanta `ErroDeRede` já com o motivo."""
    if resposta.ok:
        return resposta
    detalhe = mensagem_da_resposta(resposta)
    raise ErroDeRede(
        f"{contexto}: {detalhe or 'a rede recusou o pedido'} (código {resposta.codigo}).",
        codigo=resposta.codigo,
        corpo=resposta.texto[:2000],
    )


def pedir_json(
    transporte: Transporte,
    metodo: str,
    url: str,
    contexto: str,
    *,
    json_corpo: Any = None,
    formulario: dict[str, Any] | None = None,
    cabecalhos: dict[str, str] | None = None,
    timeout: float = TEMPO_LIMITE_S,
) -> Any:
    """Um pedido com corpo JSON ou de formulário; devolve o JSON da resposta."""
    cab = dict(cabecalhos or {})
    corpo: bytes | None = None
    if json_corpo is not None:
        corpo = json.dumps(json_corpo, ensure_ascii=False).encode("utf-8")
        cab.setdefault("Content-Type", "application/json; charset=UTF-8")
    elif formulario is not None:
        corpo = codificar_formulario(formulario)
        cab.setdefault("Content-Type", "application/x-www-form-urlencoded")
    resposta = exigir_ok(
        transporte.pedir(metodo, url, cabecalhos=cab, corpo=corpo, timeout=timeout), contexto
    )
    try:
        return resposta.json()
    except ValueError as erro:
        raise ErroDeRede(
            f"{contexto}: resposta inesperada da rede.", codigo=resposta.codigo
        ) from erro


def com_tentativas(
    fazer: Callable[[], T],
    *,
    esperas: tuple[float, ...] = ESPERAS_ENTRE_TENTATIVAS_S,
    dormir: Callable[[float], None] | None = None,
    cancelado: Callable[[], bool] | None = None,
) -> T:
    """Repete `fazer` quando o erro é passageiro (sem conexão, 429, 5xx); o resto sobe direto."""
    for espera in (*esperas, None):
        if cancelado is not None and cancelado():
            raise Cancelado("Cancelado.")
        try:
            return fazer()
        except ErroDeRede as erro:
            if espera is None or not erro.passageiro:
                raise
            (dormir or time.sleep)(espera)
    raise AssertionError("inalcançável")  # pragma: no cover


# --- multipart (Upload-Post) ------------------------------------------------------------------
def montar_multipart(campos: dict[str, Any], arquivos: dict[str, Path]) -> tuple[bytes, str]:
    """O corpo `multipart/form-data` e o `Content-Type` dele. Campos com lista repetem a chave."""
    limite = "----spurgeon" + secrets.token_hex(12)
    partes: list[bytes] = []
    for nome, valor in campos.items():
        valores = valor if isinstance(valor, (list, tuple)) else [valor]
        for item in valores:
            if item is None:
                continue
            texto = "true" if item is True else "false" if item is False else str(item)
            partes.append(
                f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n'.encode()
                + texto.encode("utf-8")
                + b"\r\n"
            )
    for nome, caminho in arquivos.items():
        tipo = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
        cabeca = (
            f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"; '
            f'filename="{caminho.name}"\r\nContent-Type: {tipo}\r\n\r\n'
        )
        partes.append(cabeca.encode() + caminho.read_bytes() + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


# --- envio de arquivo em pedaços ---------------------------------------------------------------
def ler_pedaco(caminho: Path, inicio: int, tamanho: int) -> bytes:
    with caminho.open("rb") as f:
        f.seek(inicio)
        return f.read(tamanho)
