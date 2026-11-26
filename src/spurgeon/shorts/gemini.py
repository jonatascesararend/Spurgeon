"""A segunda revisão dos Shorts pela API do Google Gemini (opcional).

Depois que a IA do PC (Ollama) corta, dá nota e escreve título e descrição, o Gemini pode conferir
de novo os Shorts gerados automaticamente. Sem chave, nada muda: o processo segue como sempre.

A chave fica no `Cofre` (criptografada pelo Windows), na conta `gemini`, e é colada na aba
Integrações. Este módulo só usa a biblioteca padrão (`urllib`), sem pacote novo.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from spurgeon.core.erros import ErroFerramenta
from spurgeon.publicacao.cofre import Cofre

CONTA = "gemini"
CAMPO_CHAVE = "chave"
CAMPO_MODELO = "modelo"
#: o apelido que o Google mantém apontando para o Flash mais novo (rápido e barato)
MODELO_PADRAO = "gemini-flash-latest"
#: quando o modelo escolhido está sobrecarregado (503), tenta este, mais leve e quase sempre livre
MODELO_RESERVA = "gemini-flash-lite-latest"
URL_DA_API = "https://generativelanguage.googleapis.com/v1beta"
TEMPO_LIMITE_S = 30
#: erros passageiros do lado do Google: vale esperar um pouco e pedir de novo
_PASSAGEIROS = frozenset({500, 502, 503, 504})
_ESPERAS_S = (2.0, 6.0)  # duas novas tentativas, com espera crescente
_dormir = time.sleep  # os testes trocam por uma função que não espera


class ErroDoGemini(ErroFerramenta):
    """A API do Gemini recusou o pedido ou não respondeu."""

    def __init__(self, mensagem: str, *, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo

    @property
    def sobrecarregado(self) -> bool:
        return self.codigo in _PASSAGEIROS


@dataclass(frozen=True)
class Configuracao:
    chave: str
    modelo: str = MODELO_PADRAO


def configuracao(cofre: Cofre) -> Configuracao | None:
    """A chave e o modelo guardados, ou `None` se não há chave (a revisão fica desligada)."""
    chave = cofre.obter(CONTA, CAMPO_CHAVE).strip()
    if not chave:
        return None
    return Configuracao(chave, cofre.obter(CONTA, CAMPO_MODELO).strip() or MODELO_PADRAO)


def _nome_do_modelo(modelo: str) -> str:
    return modelo.removeprefix("models/")


def _pedir(
    caminho: str, chave: str, corpo: dict[str, Any] | None = None, *, tempo_limite: float
) -> dict[str, Any]:
    """Um pedido à API; nos erros passageiros (503 "alta demanda"…) espera e tenta de novo."""
    for espera in (*_ESPERAS_S, None):
        try:
            return _pedir_uma_vez(caminho, chave, corpo, tempo_limite=tempo_limite)
        except ErroDoGemini as erro:
            if not erro.sobrecarregado or espera is None:
                raise
            _dormir(espera)
    raise AssertionError("inalcançável")  # pragma: no cover


def _pedir_uma_vez(
    caminho: str, chave: str, corpo: dict[str, Any] | None, *, tempo_limite: float
) -> dict[str, Any]:
    dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
    pedido = urllib.request.Request(
        f"{URL_DA_API}/{caminho}",
        data=dados,
        method="GET" if corpo is None else "POST",
        headers={"x-goog-api-key": chave, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=tempo_limite) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        raise ErroDoGemini(
            _explicar(erro.code, _mensagem_do_google(erro)), codigo=erro.code
        ) from erro
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroDoGemini(
            f"Não foi possível falar com o Gemini (sem internet ou o Google não respondeu): {erro}"
        ) from erro
    except ValueError as erro:
        raise ErroDoGemini("O Gemini respondeu algo que não é JSON.") from erro


def _mensagem_do_google(erro: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(erro.read().decode("utf-8"))["error"]["message"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _explicar(codigo: int, mensagem: str) -> str:
    detalhe = f" ({mensagem})" if mensagem else ""
    if codigo in (400, 401, 403) and "key" in mensagem.lower():
        return f"A chave do Gemini não foi aceita: confira se colou a chave inteira{detalhe}."
    if codigo == 403:
        return f"O Google recusou o acesso com esta chave{detalhe}."
    if codigo == 404:
        return f"Esse modelo do Gemini não existe ou não está liberado para a chave{detalhe}."
    if codigo in _PASSAGEIROS:
        return (
            f"O Gemini está com alta demanda agora (erro {codigo}) e não respondeu nem depois de "
            f"tentar de novo; é passageiro, tente daqui a alguns minutos{detalhe}."
        )
    if codigo == 429:
        return f"O limite de uso grátis do Gemini acabou por agora; tente mais tarde{detalhe}."
    return f"O Gemini respondeu com erro {codigo}{detalhe}."


def modelos_disponiveis(chave: str, *, tempo_limite: float = TEMPO_LIMITE_S) -> list[str]:
    """Os modelos que esta chave pode usar para gerar texto (confere a chave de quebra)."""
    resposta = _pedir("models?pageSize=1000", chave, tempo_limite=tempo_limite)
    return [
        _nome_do_modelo(str(m.get("name", "")))
        for m in resposta.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def gerar(
    config: Configuracao,
    texto: str,
    *,
    sistema: str = "",
    esquema: dict[str, Any] | None = None,
    temperatura: float = 0.2,
    tempo_limite: float = 120,
) -> str:
    """Manda um pedido ao modelo e devolve o texto da resposta (JSON quando há `esquema`)."""
    return responder(
        config, texto, sistema=sistema, esquema=esquema, temperatura=temperatura,
        tempo_limite=tempo_limite,
    )[0]  # fmt: skip


def responder(
    config: Configuracao,
    texto: str,
    *,
    sistema: str = "",
    esquema: dict[str, Any] | None = None,
    temperatura: float = 0.2,
    tempo_limite: float = 120,
) -> tuple[str, str]:
    """O texto da resposta e o modelo que respondeu (o reserva, se o escolhido estava lotado)."""
    corpo: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": texto}]}],
        "generationConfig": {"temperature": temperatura},
    }
    if sistema:
        corpo["systemInstruction"] = {"parts": [{"text": sistema}]}
    if esquema is not None:
        corpo["generationConfig"]["responseMimeType"] = "application/json"
        corpo["generationConfig"]["responseSchema"] = esquema
    modelo = _nome_do_modelo(config.modelo)
    try:
        resposta = _pedir(
            f"models/{modelo}:generateContent", config.chave, corpo, tempo_limite=tempo_limite
        )
    except ErroDoGemini as erro:
        if not erro.sobrecarregado or modelo == MODELO_RESERVA:
            raise
        modelo = MODELO_RESERVA
        resposta = _pedir(
            f"models/{modelo}:generateContent", config.chave, corpo, tempo_limite=tempo_limite
        )
    try:
        partes = resposta["candidates"][0]["content"]["parts"]
        return "".join(str(p.get("text", "")) for p in partes), modelo
    except (KeyError, IndexError, TypeError) as erro:
        motivo = resposta.get("promptFeedback", {}).get("blockReason", "")
        raise ErroDoGemini(
            "O Gemini não devolveu resposta" + (f" (bloqueio: {motivo})." if motivo else ".")
        ) from erro


def testar(config: Configuracao) -> str:
    """Confere a chave e o modelo com um pedido mínimo; devolve um resumo para a tela."""
    modelos = modelos_disponiveis(config.chave)
    nome = _nome_do_modelo(config.modelo)
    if modelos and nome not in modelos:
        flash = [m for m in modelos if "flash" in m][:4]
        sugestao = f" Alguns que ela aceita: {', '.join(flash)}." if flash else ""
        raise ErroDoGemini(f"A chave funciona, mas não tem o modelo “{nome}”.{sugestao}")
    resposta, usado = responder(
        config, "Responda só com a palavra: ok", temperatura=0, tempo_limite=60
    )
    if not resposta.strip():
        raise ErroDoGemini("A chave funciona, mas o modelo respondeu vazio.")
    if usado != nome:
        return (
            f"chave aceita; o {nome} está com alta demanda agora, então respondeu o reserva "
            f"{usado} (o programa faz essa troca sozinho)"
        )
    return f"chave aceita, modelo {nome} respondendo"
