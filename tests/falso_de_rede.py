"""Um transporte de mentira para os testes da publicação: nenhum teste toca a internet."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from spurgeon.publicacao.rede import Resposta


def resposta(codigo: int = 200, dados: Any = None, cabecalhos: dict[str, str] | None = None):
    """Uma resposta com corpo JSON (ou vazio, se `dados` for None)."""
    corpo = b"" if dados is None else json.dumps(dados).encode("utf-8")
    return Resposta(codigo, {k.lower(): v for k, v in (cabecalhos or {}).items()}, corpo)


@dataclass
class Chamada:
    metodo: str
    url: str
    cabecalhos: dict[str, str]
    corpo: bytes | None

    @property
    def json(self) -> Any:
        return json.loads((self.corpo or b"").decode("utf-8"))

    @property
    def formulario(self) -> dict[str, str]:
        return {
            k: v[0] for k, v in urllib.parse.parse_qs((self.corpo or b"").decode("utf-8")).items()
        }

    @property
    def consulta(self) -> dict[str, str]:
        partes = urllib.parse.urlsplit(self.url)
        return {k: v[0] for k, v in urllib.parse.parse_qs(partes.query).items()}


@dataclass
class _Rota:
    metodo: str
    trecho: str
    destino: Any  # Resposta, lista de Resposta (consumida na ordem; a última se repete) ou função


@dataclass
class TransporteFalso:
    """Responde por rota (método + trecho da URL). Pedido sem rota faz o teste falhar."""

    rotas: list[_Rota] = field(default_factory=list)
    chamadas: list[Chamada] = field(default_factory=list)

    def responder(self, metodo: str, trecho: str, destino) -> TransporteFalso:
        self.rotas.append(_Rota(metodo.upper(), trecho, destino))
        return self

    def pedir(self, metodo, url, *, cabecalhos=None, corpo=None, timeout=60.0):
        self.chamadas.append(Chamada(metodo.upper(), url, dict(cabecalhos or {}), corpo))
        for rota in self.rotas:
            if rota.metodo == metodo.upper() and rota.trecho in url:
                destino = rota.destino
                if isinstance(destino, list):
                    return destino.pop(0) if len(destino) > 1 else destino[0]
                if callable(destino):
                    return destino(self.chamadas[-1])
                return destino
        raise AssertionError(f"pedido não previsto: {metodo} {url}")

    def chamadas_de(self, metodo: str, trecho: str) -> list[Chamada]:
        return [c for c in self.chamadas if c.metodo == metodo.upper() and trecho in c.url]


class Relogio:
    """Um relógio que o teste move."""

    def __init__(self, agora: datetime):
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora


def sem_dormir(monkeypatch, *modulos) -> None:
    """Troca o `time.sleep` dos módulos por nada (os testes não esperam de verdade)."""
    for modulo in modulos:
        monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)


EnviarFalso = Callable[[Chamada], Any]
