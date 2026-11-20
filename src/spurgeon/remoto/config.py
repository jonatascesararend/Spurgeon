"""Configuração do controle remoto: um arquivo próprio (`remoto.json`) ao lado do `config.toml`.

Fica fora do `ConfigApp` de propósito: o branch `controle-remoto` mexe o mínimo no código da `main`
(Remoto.md, seção 4). Segredos (chave dos avisos, inscrições do celular) NÃO ficam aqui: vão para o
`Cofre` (`remoto/avisos.py`).
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
from pydantic import BaseModel, Field

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import NOME_APP

NOME_DO_ARQUIVO = "remoto.json"
#: porta incomum, só em 127.0.0.1; o `tailscale serve` a publica na rede privada (Remoto.md, S8)
PORTA_PADRAO = 18765
#: de quanto em quanto tempo o vigia olha os projetos para mandar os avisos
INTERVALO_DO_VIGIA_S = 15.0


class ConfigRemoto(BaseModel):
    porta: int = PORTA_PADRAO
    #: as contas do Tailscale que podem usar o app (o cabeçalho `Tailscale-User-Login`).
    #: Vazia, ninguém entra: `spurgeon remoto configurar` põe a conta dona deste PC.
    usuarios: list[str] = Field(default_factory=list)
    #: "Dormir" pelo celular: o servidor só aceita "acordar" e não manda avisos (Remoto.md, S1)
    dormindo: bool = False
    avisos_ligados: bool = True
    #: o endereço do app na rede privada (`https://<pc>.<rede>.ts.net/`), só para mostrar;
    #: preenchido por `configurar`
    endereco: str = ""


def caminho_config_remoto() -> Path:
    return platformdirs.user_config_path(NOME_APP, appauthor=False) / NOME_DO_ARQUIVO


def carregar_config_remoto(caminho: Path | None = None) -> ConfigRemoto:
    caminho = caminho or caminho_config_remoto()
    try:
        return ConfigRemoto.model_validate(ler_json(caminho))
    except (OSError, ValueError):
        return ConfigRemoto()


def salvar_config_remoto(config: ConfigRemoto, caminho: Path | None = None) -> Path:
    caminho = caminho or caminho_config_remoto()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(caminho, config.model_dump())
    return caminho
