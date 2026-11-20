"""Pasta de cada projeto: nome, estrutura, criação e retomada."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from spurgeon.core.arquivos import ler_json
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.erros import ErroConfiguracao, ErroFFmpeg, ErroSpurgeon, ProjetoConflito
from spurgeon.core.estado import (
    NOME_ARQUIVO_ESTADO,
    EntradaVideo,
    Estado,
    StatusEtapa,
    carregar_estado,
    salvar_estado,
)
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

    @property
    def video_com_vinhetas(self) -> Path:
        return self.raiz / "video_longo_com_vinhetas.mp4"

    @property
    def srt_com_vinhetas(self) -> Path:
        return self.raiz / "legendas_video_longo_com_vinhetas.srt"

    @property
    def video_longo_info(self) -> Path:
        """O que o vídeo longo tem (introdução, vinheta) e com que qualidade foi renderizado."""
        return self.work / "video_longo_info.json"

    @property
    def audio_original(self) -> Path:
        return self.work / "audio_original.wav"

    @property
    def audio_limpo(self) -> Path:
        return self.work / "audio_limpo.wav"

    @property
    def audio_normalizado(self) -> Path:
        return self.work / "audio_normalizado.wav"

    @property
    def niveis(self) -> Path:
        return self.work / "niveis.json"

    @property
    def candidatos(self) -> Path:
        return self.work / "candidatos.json"

    @property
    def avaliacoes(self) -> Path:
        return self.work / "avaliacoes.json"

    @property
    def cortes_shorts(self) -> Path:
        return self.work / "cortes_shorts.json"

    @property
    def shorts_renderizados(self) -> Path:
        return self.work / "shorts_renderizados.json"

    @property
    def metadados(self) -> Path:
        return self.work / "metadados_shorts.json"

    @property
    def pausas(self) -> Path:
        return self.work / "pausas.json"

    @property
    def cortes(self) -> Path:
        return self.work / "cortes.json"

    def audio_base(self, opcoes: OpcoesProcessamento) -> Path:
        """O áudio sem o volume ajustado: o limpo, se a limpeza foi pedida; senão o original."""
        return self.audio_limpo if opcoes.limpar_audio else self.audio_original

    def audio_final(self, opcoes: OpcoesProcessamento) -> Path:
        """O áudio que vai para o vídeo longo e para a transcrição."""
        return self.audio_normalizado if opcoes.normalizar_volume else self.audio_base(opcoes)

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


def ler_nome_e_data(pasta: Path) -> tuple[str, date] | None:
    """`2026-10-04_Miqueias_2._6-11` → (`Miqueias 2. 6-11`, data); None se o nome foge do padrão."""
    achado = re.match(r"^(\d{4}-\d{2}-\d{2})_(.+)$", Path(pasta).name)
    if achado is None:
        return None
    try:
        dia = date.fromisoformat(achado.group(1))
    except ValueError:
        return None
    return achado.group(2).replace("_", " ").strip(), dia


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
    """Apaga o que é grande e se refaz (áudios, partes do vídeo, legendas ASS, fontes).

    Ficam os `.json` de `_work/` (a análise: candidatos, notas, cortes e títulos dos Shorts, o que
    já foi renderizado): são poucos MB e deixam reabrir o projeto sem refazer a análise.
    """
    if not pastas.work.exists():
        return
    for item in pastas.work.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        elif item.suffix.lower() != ".json":
            item.unlink(missing_ok=True)
    with contextlib.suppress(OSError):  # sem nenhum json a pasta some (rmdir só remove vazia)
        pastas.work.rmdir()


def abrir_pasta(caminho: Path) -> None:
    """Abre a pasta no Explorer (só Windows)."""
    if sys.platform == "win32":
        os.startfile(caminho)


def _frase_dos_shorts(pastas: PastasProjeto) -> str:
    """`12 Shorts sugeridos, 9 renderizados`."""
    try:
        sugeridos = len(ler_json(pastas.cortes_shorts))
    except (OSError, ValueError):
        sugeridos = 0
    feitos = sum(
        1
        for e in _registro_dos_shorts(pastas).values()
        if (pastas.shorts / e.get("arquivo", "")).is_file()
    )
    return f"{sugeridos} Shorts sugeridos, {feitos} renderizados"


def descrever_projeto(pastas: PastasProjeto) -> str:
    """O que o projeto já tem, em uma linha (ao abrir um projeto existente)."""
    partes: list[str] = []
    if pastas.transcricao.exists():
        partes.append("transcrição")
    if pastas.srt.exists():
        partes.append("legendas .srt")
    if pastas.video_longo.exists():
        partes.append("vídeo longo")
    if pastas.video_com_vinhetas.exists():
        partes.append("vídeo com introdução e vinheta")
    if pastas.cortes_shorts.exists():
        partes.append(_frase_dos_shorts(pastas))
    return (
        "Já existe: " + ", ".join(partes) + "." if partes else "O projeto ainda não tem resultados."
    )


def situacao_do_projeto(estado: Estado) -> str:
    """Em que pé o processamento do projeto está, em poucas palavras (para a lista de projetos)."""
    etapas = estado.etapas
    if not etapas:
        return "Ainda não começou"
    status = [e.status for e in etapas]
    if StatusEtapa.ERRO in status:
        return "Parou com erro"
    if StatusEtapa.AGUARDANDO_REVISAO in status:
        return "Esperando a sua revisão"
    if StatusEtapa.EM_ANDAMENTO in status:
        return "Em andamento (ou interrompido)"
    if StatusEtapa.CANCELADA in status:
        return "Cancelado"
    feitas = status.count(StatusEtapa.CONCLUIDA)
    if feitas == len(etapas):
        return "Concluído"
    if feitas == 0:
        return "Ainda não começou"
    return f"{feitas} de {len(etapas)} etapas prontas"


@dataclass(frozen=True)
class ProjetoExistente:
    """Um projeto que já está na pasta de projetos, como a lista de abertura o mostra."""

    pasta: Path
    nome: str
    data: date  # a da gravação, do nome da pasta
    modificado: float  # quando o estado.json mudou pela última vez (segundos desde 1970)
    situacao: str
    detalhes: str  # o que já existe (vídeo longo, Shorts…), curto o bastante para uma linha


def resumo_dos_resultados(pastas: PastasProjeto) -> str:
    """O que o projeto já tem, em poucas palavras: só o mais adiantado (a lista de abertura)."""
    partes: list[str] = []
    if pastas.video_com_vinhetas.exists():
        partes.append("vídeo com introdução e vinheta")
    elif pastas.video_longo.exists():
        partes.append("vídeo longo")
    if pastas.cortes_shorts.exists():
        partes.append(_frase_dos_shorts(pastas))
    if not partes and pastas.transcricao.exists():
        partes.append("só a transcrição")
    return " · ".join(partes) if partes else "sem resultados ainda"


def listar_projetos(pasta_projetos: Path, *, limite: int = 60) -> list[ProjetoExistente]:
    """Os projetos da pasta, o que mexeu por último primeiro.

    Só entra pasta que se chama `AAAA-MM-DD_Nome` e tem um `estado.json` que se lê: é o mesmo
    critério de "Abrir projeto…", então tudo o que a lista mostra abre. Uma pasta que não é projeto
    ou está estragada é pulada em silêncio, e a pasta de projetos que não existe dá lista vazia.
    """
    try:
        candidatas = [p for p in Path(pasta_projetos).iterdir() if p.is_dir()]
    except OSError:
        return []
    achados: list[ProjetoExistente] = []
    for pasta in candidatas:
        identificado = ler_nome_e_data(pasta)
        arquivo = pasta / NOME_ARQUIVO_ESTADO
        if identificado is None or not arquivo.is_file():
            continue
        try:
            estado = carregar_estado(pasta)
            modificado = arquivo.stat().st_mtime
        except (OSError, ValueError, ErroSpurgeon):
            continue
        if estado is None:
            continue
        nome, dia = identificado
        achados.append(
            ProjetoExistente(
                pasta, nome, dia, modificado, situacao_do_projeto(estado),
                resumo_dos_resultados(PastasProjeto(pasta)),
            )
        )  # fmt: skip
    achados.sort(key=lambda p: p.modificado, reverse=True)
    return achados[:limite]


def _registro_dos_shorts(pastas: PastasProjeto) -> dict[str, dict]:
    try:
        dados = ler_json(pastas.shorts_renderizados)
    except (OSError, ValueError):
        return {}
    return (
        {k: v for k, v in dados.items() if isinstance(v, dict)} if isinstance(dados, dict) else {}
    )
