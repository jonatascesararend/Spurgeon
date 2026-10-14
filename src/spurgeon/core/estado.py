"""estado.json: em que etapa cada processamento está, para retomar de onde parou."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import OpcoesProcessamento

NOME_ARQUIVO_ESTADO = "estado.json"
VERSAO_ESTADO = 1
_BLOCO_HASH = 4 * 1024 * 1024


class StatusEtapa(StrEnum):
    PENDENTE = "pendente"
    EM_ANDAMENTO = "em_andamento"
    AGUARDANDO_REVISAO = "aguardando_revisao"
    CONCLUIDA = "concluida"
    ERRO = "erro"
    CANCELADA = "cancelada"


def agora() -> datetime:
    return datetime.now().astimezone()


def hash_rapido(caminho: Path) -> str:
    """SHA-256 do tamanho + primeiros e últimos 4 MB: identifica o vídeo sem ler o arquivo todo."""
    caminho = Path(caminho)
    tamanho = caminho.stat().st_size
    h = hashlib.sha256(str(tamanho).encode())
    with caminho.open("rb") as f:
        h.update(f.read(_BLOCO_HASH))
        f.seek(max(_BLOCO_HASH, tamanho - _BLOCO_HASH))
        h.update(f.read(_BLOCO_HASH))
    return h.hexdigest()


class EntradaVideo(BaseModel):
    caminho: str
    tamanho: int
    hash_rapido: str

    @classmethod
    def de_arquivo(cls, caminho: Path) -> EntradaVideo:
        caminho = Path(caminho)
        return cls(
            caminho=str(caminho),
            tamanho=caminho.stat().st_size,
            hash_rapido=hash_rapido(caminho),
        )


class EstadoEtapa(BaseModel):
    id: str
    status: StatusEtapa = StatusEtapa.PENDENTE
    progresso: float = 0.0
    inicio: datetime | None = None
    fim: datetime | None = None
    artefatos: list[str] = Field(default_factory=list)
    assinatura: str | None = None
    erro: str | None = None

    def zerar(self) -> None:
        self.status = StatusEtapa.PENDENTE
        self.progresso = 0.0
        self.inicio = None
        self.fim = None
        self.artefatos = []
        self.assinatura = None
        self.erro = None


class Estado(BaseModel):
    versao: int = VERSAO_ESTADO
    entrada: EntradaVideo
    opcoes: OpcoesProcessamento = Field(default_factory=OpcoesProcessamento)
    etapas: list[EstadoEtapa] = Field(default_factory=list)
    #: registros das etapas que não fazem parte do processamento de agora (outro modo ou outras
    #: opções); ficam guardados para não se perder o que já foi feito quando voltarem a valer
    etapas_guardadas: list[EstadoEtapa] = Field(default_factory=list)
    criado_em: datetime = Field(default_factory=agora)
    atualizado_em: datetime = Field(default_factory=agora)

    def etapa(self, id_etapa: str) -> EstadoEtapa | None:
        return next((e for e in self.etapas if e.id == id_etapa), None)


def carregar_estado(pasta_projeto: Path) -> Estado | None:
    arquivo = Path(pasta_projeto) / NOME_ARQUIVO_ESTADO
    if not arquivo.exists():
        return None
    return Estado.model_validate(ler_json(arquivo))


def salvar_estado(pasta_projeto: Path, estado: Estado) -> None:
    estado.atualizado_em = agora()
    escrever_json_atomico(Path(pasta_projeto) / NOME_ARQUIVO_ESTADO, estado.model_dump(mode="json"))
