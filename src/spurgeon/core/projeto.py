"""Pasta de cada projeto: nome, estrutura, criação e retomada."""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.erros import ErroConfiguracao, ErroFFmpeg, ProjetoConflito
from spurgeon.core.estado import EntradaVideo, Estado, carregar_estado, salvar_estado
from spurgeon.core.ffmpeg import sondar

_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
               *(f"LPT{i}" for i in range(1, 10))}  # fmt: skip
_LIMITE_CAMINHO = 180  # o Windows corta em 260; sobra espaço para os arquivos dentro da pasta


@dataclass(frozen=True)
class PastasProjeto:
    raiz: Path

    @property
    def work(self) -> Path:
        return self.raiz / "_work"

    @property
    def shorts(self) -> Path:
        return self.raiz / "shorts_gerados"

    @property
    def estado(self) -> Path:
        return self.raiz / "estado.json"

    @property
    def log(self) -> Path:
        return self.raiz / "processamento.log"

    @property
    def transcricao(self) -> Path:
        return self.raiz / "transcricao.json"

    @property
    def srt(self) -> Path:
        return self.raiz / "legendas_video_longo.srt"

    @property
    def video_longo(self) -> Path:
        return self.raiz / "video_longo_otimizado.mp4"

    def criar(self) -> None:
        self.work.mkdir(parents=True, exist_ok=True)

    def relativo(self, caminho: Path) -> str:
        """Caminho relativo à raiz do projeto, com barras normais (vai para o estado.json)."""
        return Path(caminho).resolve().relative_to(self.raiz.resolve()).as_posix()


@dataclass(frozen=True)
class ResultadoProjeto:
    pastas: PastasProjeto
    estado: Estado
    retomado: bool


def sanitizar_nome(nome: str, maximo: int = 80) -> str:
    """Nome seguro para pasta/arquivo no Windows; mantém os acentos."""
    texto = unicodedata.normalize("NFC", nome).strip()
    texto = _INVALIDOS.sub("", texto)
    texto = re.sub(r"\s+", "_", texto)
    texto = texto.strip("._")[:maximo].rstrip("._")
    if not texto:
        return "Sem_Nome"
    if texto.split(".")[0].upper() in _RESERVADOS:
        texto = f"_{texto}"
    return texto


def nome_pasta_projeto(data: date, nome: str) -> str:
    return f"{data:%Y-%m-%d}_{sanitizar_nome(nome)}"


def data_gravacao(video: Path) -> date:
    """Data da gravação: metadado `creation_time` (no fuso local); senão, a do arquivo."""
    try:
        criacao = sondar(video).creation_time
        if criacao:
            momento = datetime.fromisoformat(criacao.replace("Z", "+00:00"))
            if momento.year >= 2000:  # relógios de câmera sem acerto gravam 1970 ou 1904
                return momento.astimezone().date()
    except (ErroFFmpeg, ErroConfiguracao, ValueError):
        pass
    return date.fromtimestamp(Path(video).stat().st_mtime)


def criar_ou_retomar_projeto(
    pasta_projetos: Path,
    video: Path,
    nome: str,
    data: date,
    opcoes: OpcoesProcessamento,
) -> ResultadoProjeto:
    """Cria a pasta do projeto, ou a retoma se for o mesmo vídeo.

    Levanta ProjetoConflito se a pasta existe mas é de outro vídeo (ou não é um projeto).
    """
    pastas = PastasProjeto(Path(pasta_projetos) / nome_pasta_projeto(data, nome))
    if len(str(pastas.raiz)) > _LIMITE_CAMINHO:
        raise ErroConfiguracao(
            "O caminho do projeto ficou longo demais para o Windows. "
            "Use um nome menor ou uma pasta de projetos mais curta."
        )
    entrada = EntradaVideo.de_arquivo(video)
    estado = carregar_estado(pastas.raiz) if pastas.raiz.exists() else None
    if estado is not None:
        if estado.entrada.hash_rapido != entrada.hash_rapido:
            raise ProjetoConflito(
                pastas.raiz, "Já existe um projeto com esse nome para outro vídeo"
            )
        estado.entrada = entrada
        estado.opcoes = opcoes
        retomado = True
    else:
        if pastas.raiz.exists() and any(pastas.raiz.iterdir()):
            raise ProjetoConflito(pastas.raiz, "A pasta já existe e não é um projeto do aplicativo")
        estado = Estado(entrada=entrada, opcoes=opcoes)
        retomado = False
    pastas.criar()
    salvar_estado(pastas.raiz, estado)
    return ResultadoProjeto(pastas, estado, retomado)


def limpar_intermediarios(pastas: PastasProjeto) -> None:
    shutil.rmtree(pastas.work, ignore_errors=True)


def abrir_pasta(caminho: Path) -> None:
    """Abre a pasta no Explorer (só Windows)."""
    if sys.platform == "win32":
        os.startfile(caminho)
