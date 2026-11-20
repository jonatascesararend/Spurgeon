"""Avisos no celular por Web Push (Remoto.md, R6).

O celular se inscreve pelo navegador (PWA) e manda ao PC a inscrição: o endereço do serviço de push
do Chrome (FCM) e as duas chaves do navegador. Para avisar, o PC cifra o aviso para o navegador
(`aes128gcm`, RFC 8291, pelo `http-ece`), assina um token VAPID (RFC 8292, ES256) com a chave dele e
faz um POST no endereço. Não há conta no Firebase nem custo. A chave privada e as inscrições ficam
no `Cofre` (DPAPI), nunca no `remoto.json` nem em log.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import http_ece
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDeRede
from spurgeon.publicacao.rede import Resposta, Transporte, TransporteUrllib

log = logging.getLogger(__name__)

CONTA_NO_COFRE = "remoto"
CAMPO_DA_CHAVE = "vapid_privada"
CAMPO_DAS_INSCRICOES = "inscricoes"
#: o "sub" do VAPID (o serviço de push quer um contato); genérico de propósito, nada do autor
CONTATO_VAPID = "mailto:studio-spurgeon@example.invalid"
VALIDADE_DO_TOKEN_S = 12 * 3600
#: quanto tempo o serviço de push guarda o aviso se o celular estiver desligado
TTL_DO_AVISO_S = 24 * 3600
#: o serviço de push responde isto quando a inscrição venceu (app desinstalado, sem permissão)
CODIGOS_DE_INSCRICAO_VENCIDA = (404, 410)


def para_b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def de_b64url(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def nova_chave() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def chave_para_texto(chave: ec.EllipticCurvePrivateKey) -> str:
    return chave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")


def chave_de_texto(texto: str) -> ec.EllipticCurvePrivateKey:
    chave = serialization.load_pem_private_key(texto.encode("ascii"), password=None)
    if not isinstance(chave, ec.EllipticCurvePrivateKey):
        raise ValueError("a chave dos avisos não é uma chave de curva elíptica")
    return chave


def chave_publica_b64(chave: ec.EllipticCurvePrivateKey) -> str:
    """A chave pública como o navegador quer (`applicationServerKey`): ponto não comprimido."""
    ponto = chave.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return para_b64url(ponto)


def _json_compacto(dados: dict[str, Any]) -> bytes:
    return json.dumps(dados, separators=(",", ":")).encode("utf-8")


def token_vapid(chave: ec.EllipticCurvePrivateKey, endpoint: str, agora: float) -> str:
    """O JWT ES256 do VAPID: `aud` é a origem do serviço de push (esquema + domínio)."""
    partes = urlsplit(endpoint)
    cabecalho = {"typ": "JWT", "alg": "ES256"}
    corpo = {
        "aud": f"{partes.scheme}://{partes.netloc}",
        "exp": int(agora) + VALIDADE_DO_TOKEN_S,
        "sub": CONTATO_VAPID,
    }
    assinado = ".".join(
        para_b64url(json.dumps(d, separators=(",", ":")).encode("utf-8"))
        for d in (cabecalho, corpo)
    )
    r, s = decode_dss_signature(chave.sign(assinado.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return f"{assinado}.{para_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def cifrar(dados: bytes, inscricao: dict[str, Any]) -> bytes:
    """Cifra para o navegador da inscrição, com uma chave descartável por aviso."""
    chaves = inscricao["keys"]
    return http_ece.encrypt(
        dados,
        private_key=nova_chave(),
        dh=de_b64url(chaves["p256dh"]),
        auth_secret=de_b64url(chaves["auth"]),
        version="aes128gcm",
    )


def validar_inscricao(inscricao: Any) -> dict[str, Any]:
    """A inscrição como o navegador a manda (`PushSubscription.toJSON()`), conferida e enxuta."""
    if not isinstance(inscricao, dict):
        raise ValueError("inscrição inválida")
    endpoint = inscricao.get("endpoint")
    chaves = inscricao.get("keys")
    if not isinstance(endpoint, str) or urlsplit(endpoint).scheme != "https":
        raise ValueError("a inscrição precisa de um endereço https")
    if not isinstance(chaves, dict) or not all(
        isinstance(chaves.get(c), str) and chaves[c] for c in ("p256dh", "auth")
    ):
        raise ValueError("a inscrição precisa das chaves p256dh e auth")
    try:
        if len(de_b64url(chaves["p256dh"])) != 65 or len(de_b64url(chaves["auth"])) != 16:
            raise ValueError
    except (ValueError, TypeError) as erro:
        raise ValueError("as chaves da inscrição têm o tamanho errado") from erro
    return {"endpoint": endpoint, "keys": {"p256dh": chaves["p256dh"], "auth": chaves["auth"]}}


@dataclass(frozen=True)
class Aviso:
    titulo: str
    texto: str
    #: o que o toque no aviso abre no app (rota do próprio app, como `#/p/<projeto>`)
    url: str = "/"
    #: avisos com a mesma marca se substituem no celular (um por projeto, não uma pilha)
    marca: str = "spurgeon"

    def para_json(self) -> bytes:
        dados = {"titulo": self.titulo, "texto": self.texto, "url": self.url, "marca": self.marca}
        return json.dumps(dados, ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class Entrega:
    endpoint: str
    codigo: int  # 0 = nem chegou ao serviço de push (sem internet)
    detalhe: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.codigo < 300


class Avisos:
    """As inscrições do celular e o envio dos avisos."""

    def __init__(
        self,
        cofre: Cofre | None = None,
        transporte: Transporte | None = None,
        relogio: Callable[[], float] = time.time,
    ):
        self.cofre = cofre or Cofre()
        self.transporte = transporte or TransporteUrllib()
        self.relogio = relogio

    # --- chave ------------------------------------------------------------------------------------
    def chave(self) -> ec.EllipticCurvePrivateKey:
        """A chave VAPID deste PC; criada na primeira vez. Trocá-la invalida as inscrições."""
        guardada = self.cofre.obter(CONTA_NO_COFRE, CAMPO_DA_CHAVE)
        if guardada:
            try:
                return chave_de_texto(guardada)
            except ValueError:
                log.warning("A chave dos avisos estava ilegível; uma nova foi criada.")
        chave = nova_chave()
        self.cofre.definir(CONTA_NO_COFRE, CAMPO_DA_CHAVE, chave_para_texto(chave), segredo=True)
        self.cofre.remover(CONTA_NO_COFRE, CAMPO_DAS_INSCRICOES)  # eram da chave antiga
        return chave

    def chave_publica(self) -> str:
        return chave_publica_b64(self.chave())

    # --- inscrições -------------------------------------------------------------------------------
    def inscricoes(self) -> list[dict[str, Any]]:
        texto = self.cofre.obter(CONTA_NO_COFRE, CAMPO_DAS_INSCRICOES)
        try:
            lista = json.loads(texto) if texto else []
        except ValueError:
            return []
        return [i for i in lista if isinstance(i, dict)] if isinstance(lista, list) else []

    def _gravar(self, lista: list[dict[str, Any]]) -> None:
        self.cofre.definir(
            CONTA_NO_COFRE, CAMPO_DAS_INSCRICOES, json.dumps(lista) if lista else "", segredo=True
        )

    def inscrever(self, inscricao: Any) -> None:
        nova = validar_inscricao(inscricao)
        self.chave()  # garante a chave antes (criar uma nova apaga as inscrições)
        lista = [i for i in self.inscricoes() if i.get("endpoint") != nova["endpoint"]]
        self._gravar([*lista, nova])

    def remover(self, endpoint: str) -> None:
        self._gravar([i for i in self.inscricoes() if i.get("endpoint") != endpoint])

    # --- envio ------------------------------------------------------------------------------------
    def _enviar_um(self, inscricao: dict[str, Any], aviso: Aviso, chave) -> Entrega:
        endpoint = inscricao["endpoint"]
        token = token_vapid(chave, endpoint, self.relogio())
        cabecalhos = {
            "TTL": str(TTL_DO_AVISO_S),
            "Urgency": "normal",
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "Authorization": f"vapid t={token}, k={chave_publica_b64(chave)}",
        }
        try:
            resposta: Resposta = self.transporte.pedir(
                "POST", endpoint, cabecalhos=cabecalhos, corpo=cifrar(aviso.para_json(), inscricao),
                timeout=30,
            )  # fmt: skip
        except ErroDeRede as erro:
            return Entrega(endpoint, 0, str(erro))
        return Entrega(endpoint, resposta.codigo, "" if resposta.ok else resposta.texto[:200])

    def enviar(self, aviso: Aviso) -> list[Entrega]:
        """Manda o aviso a todos os aparelhos inscritos; tira as inscrições que venceram."""
        lista = self.inscricoes()
        if not lista:
            return []
        chave = self.chave()
        entregas = [self._enviar_um(i, aviso, chave) for i in lista]
        vencidas = {e.endpoint for e in entregas if e.codigo in CODIGOS_DE_INSCRICAO_VENCIDA}
        if vencidas:
            self._gravar([i for i in lista if i["endpoint"] not in vencidas])
        for entrega in entregas:
            if not entrega.ok:
                log.warning(
                    "Aviso não entregue (%s): %s", entrega.codigo, entrega.detalhe or "sem resposta"
                )
        return entregas
