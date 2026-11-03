"""Atualização do programa pelo GitHub: ver se há versão nova e trocá-la, sem precisar de git.

O programa chega ao computador como uma pasta copiada (ou um ZIP), sem git. A verificação lê o
`__version__` de `src/spurgeon/__init__.py` na branch `main` (um arquivo pequeno, sem a API do
GitHub e sem limite de consultas) e compara com o da versão em uso. A atualização baixa o ZIP da
`main`, confere, troca só os arquivos do programa (código, scripts e documentos) e deixa em paz
tudo que é da pessoa: projetos, modelos, vídeos, a marca d'água, o vocabulário…

Para uma versão chegar às pessoas, suba `__version__` ao publicar: é ela que acusa a novidade.
Nada aqui importa a interface; a janela só chama e mostra.
"""

from __future__ import annotations

import filecmp
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from spurgeon.core.erros import ErroSpurgeon

REPOSITORIO = "jonatascesararend/Spurgeon"
URL_DA_VERSAO = f"https://raw.githubusercontent.com/{REPOSITORIO}/main/src/spurgeon/__init__.py"
URL_DO_ZIP = f"https://github.com/{REPOSITORIO}/archive/refs/heads/main.zip"
URL_DA_PAGINA = f"https://github.com/{REPOSITORIO}"
TEMPO_LIMITE_S = 8.0
_TEMPO_LIMITE_DO_ZIP_S = 30.0

#: onde a atualização trabalha (baixa, extrai e guarda a cópia do que trocou); fora do git
PASTA_DE_TRABALHO = Path("modelos") / "ferramentas" / "atualizacao"
#: o que a atualização troca: o código e, na raiz, scripts, documentos e arquivos do projeto
PASTAS_DO_PROGRAMA = ("src",)
PADROES_DA_RAIZ = (
    "*.bat", "*.ps1", "*.md", "*.txt", "pyproject.toml", "uv.lock",
    ".gitignore", ".gitattributes", ".pre-commit-config.yaml",
)  # fmt: skip
#: trocar um destes muda as bibliotecas: depois da atualização é preciso rodar o instalador
ARQUIVOS_QUE_PEDEM_INSTALAR = ("pyproject.toml", "uv.lock")
#: de `recursos/` só entra o que ainda não existe (vocabulário, marca e vinhetas são da pessoa)
PASTA_DE_RECURSOS = "recursos"

Versao = tuple[int, ...]
#: o andamento do download quando o servidor não diz o tamanho do arquivo
DESCONHECIDO = -1.0


class ErroDeAtualizacao(ErroSpurgeon):
    """Não foi possível consultar ou instalar a atualização (a mensagem diz o que fazer)."""


@dataclass(frozen=True)
class InfoAtualizacao:
    versao: str  # a do GitHub
    versao_atual: str  # a que está em uso
    url_do_zip: str = URL_DO_ZIP


@dataclass(frozen=True)
class ResultadoDaAtualizacao:
    versao: str
    trocados: int  # arquivos que mudaram
    novos: int  # arquivos que não existiam
    precisa_instalar: bool  # as bibliotecas mudaram: rode o instalador antes de abrir


# --- versões -------------------------------------------------------------------------------
def versao_em_uso() -> str:
    from spurgeon import __version__

    return __version__


def numeros_da_versao(texto: str) -> Versao | None:
    """`1.7.0` → `(1, 7, 0)`; None se não for só números separados por ponto."""
    texto = texto.strip()
    if not re.fullmatch(r"\d+(\.\d+)*", texto):
        return None
    return tuple(int(parte) for parte in texto.split("."))


def ler_versao(codigo: str) -> Versao | None:
    """A `__version__` de um `__init__.py`."""
    achada = re.search(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", codigo, re.MULTILINE)
    return numeros_da_versao(achada.group(1)) if achada else None


def e_mais_nova(candidata: Versao, atual: Versao) -> bool:
    """`1.7` e `1.7.0` são a mesma versão: completa com zeros antes de comparar."""
    tamanho = max(len(candidata), len(atual))

    def completar(versao: Versao) -> Versao:
        return versao + (0,) * (tamanho - len(versao))

    return completar(candidata) > completar(atual)


# --- consultar -----------------------------------------------------------------------------
def _abrir_url(url: str, timeout: float) -> bytes:
    pedido = urllib.request.Request(
        url, headers={"User-Agent": f"StudioSpurgeon/{versao_em_uso()}"}
    )
    with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
        return resposta.read(65_536)


def consultar_atualizacao(
    *,
    versao_atual: str | None = None,
    abrir: Callable[[str, float], bytes] | None = None,
    timeout: float = TEMPO_LIMITE_S,
) -> InfoAtualizacao | None:
    """Pergunta ao GitHub qual é a versão mais nova. Devolve None se este programa já a tem.

    Levanta `ErroDeAtualizacao` se não deu para consultar (sem internet, GitHub fora do ar):
    quem chama decide se isso aparece (na abertura, não: o programa funciona offline).
    """
    atual = versao_atual or versao_em_uso()
    try:
        codigo = (abrir or _abrir_url)(URL_DA_VERSAO, timeout).decode("utf-8", "replace")
    except (OSError, ValueError) as erro:  # URLError, HTTPError e o tempo esgotado são OSError
        raise ErroDeAtualizacao(f"Não foi possível consultar o GitHub ({erro}).") from erro
    remota, local = ler_versao(codigo), numeros_da_versao(atual)
    if remota is None:
        raise ErroDeAtualizacao("A resposta do GitHub não traz o número da versão.")
    if local is None or not e_mais_nova(remota, local):
        return None
    return InfoAtualizacao(".".join(map(str, remota)), atual)


# --- instalar ------------------------------------------------------------------------------
def motivo_de_nao_atualizar_sozinho(raiz: Path) -> str | None:
    """Por que a atualização automática não vale nesta pasta (None = pode)."""
    if (raiz / ".git").exists():
        return (
            "Esta pasta é um clone do git: atualize com `git pull`, para não misturar o que "
            "você alterou com a versão nova."
        )
    if not os.access(raiz / "src", os.W_OK):
        return "O programa não tem permissão para gravar na própria pasta."
    return None


def baixar_zip(
    destino: Path,
    *,
    url: str = URL_DO_ZIP,
    ao_progresso: Callable[[float], None] | None = None,
    abrir: Callable[[str, float], object] | None = None,
) -> None:
    """Baixa o ZIP para `destino` (num nome provisório, trocado no fim).

    `ao_progresso(fração)` vai de 0 a 1 e termina em 1. O GitHub serve o ZIP da `main` sem o
    tamanho (`Content-Length`): então a fração não se sabe e o aviso é `DESCONHECIDO` (-1).
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".baixando")
    pedido = urllib.request.Request(
        url, headers={"User-Agent": f"StudioSpurgeon/{versao_em_uso()}"}
    )
    try:
        with (
            (abrir or _abrir_fluxo)(pedido, _TEMPO_LIMITE_DO_ZIP_S) as resposta,
            parcial.open("wb") as saida,
        ):
            total = int(resposta.headers.get("Content-Length") or 0)
            feito = 0
            while bloco := resposta.read(262_144):
                saida.write(bloco)
                feito += len(bloco)
                if ao_progresso is not None:
                    ao_progresso(min(1.0, feito / total) if total else DESCONHECIDO)
    except (OSError, ValueError) as erro:
        parcial.unlink(missing_ok=True)
        raise ErroDeAtualizacao(
            f"Não foi possível baixar a atualização ({erro}). Confira a internet e tente de novo."
        ) from erro
    parcial.replace(destino)
    if ao_progresso is not None:
        ao_progresso(1.0)  # acabou (e quem não sabia o tamanho fica sabendo agora)


def _abrir_fluxo(pedido, timeout: float):
    return urllib.request.urlopen(pedido, timeout=timeout)


def _extrair(zip_path: Path, destino: Path) -> Path:
    """Extrai o ZIP (recusando caminhos que saem da pasta) e devolve a pasta com o programa."""
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as zip_:
            base = destino.resolve()
            for membro in zip_.namelist():
                if not (destino / membro).resolve().is_relative_to(base):
                    raise ErroDeAtualizacao("O arquivo baixado tem caminhos inválidos; descartado.")
            zip_.extractall(destino)
    except zipfile.BadZipFile as erro:
        raise ErroDeAtualizacao("O arquivo baixado está corrompido. Tente de novo.") from erro
    pastas = [p for p in destino.iterdir() if p.is_dir()]
    if not (destino / "src").exists() and len(pastas) == 1:
        return pastas[0]  # o GitHub põe tudo numa pasta "Spurgeon-main"
    return destino


#: arquivos de texto: ler com o fim de linha do Windows (CRLF) ou do Git (LF) é o mesmo conteúdo
_TEXTOS = frozenset(
    {".py", ".toml", ".lock", ".md", ".txt", ".bat", ".ps1", ".yaml", ".yml", ".json"}
)


def _mesmo_conteudo(novo: Path, atual: Path) -> bool:
    if filecmp.cmp(novo, atual, shallow=False):
        return True
    if novo.suffix.lower() in _TEXTOS:
        return novo.read_bytes().replace(b"\r\n", b"\n") == atual.read_bytes().replace(
            b"\r\n", b"\n"
        )
    return False


def _faz_parte(relativo: Path, raiz: Path) -> bool:
    """O arquivo é do programa (e a atualização o troca)? O código, os scripts e documentos da raiz,
    e os recursos que a pessoa ainda não tem; o resto (projetos, modelos…) é dela."""
    partes = relativo.parts
    if partes[0] in PASTAS_DO_PROGRAMA:
        return True
    if len(partes) == 1:
        return any(fnmatch.fnmatch(relativo.name, padrao) for padrao in PADROES_DA_RAIZ)
    return partes[0] == PASTA_DE_RECURSOS and not (raiz / relativo).exists()


def _plano(origem: Path, raiz: Path) -> list[Path]:
    """Os arquivos (caminhos relativos) que a atualização põe na pasta do programa. O
    `__init__.py` do pacote, que tem a versão, vai por último: uma queda no meio deixa a versão
    antiga e a próxima abertura tenta de novo."""
    plano: list[Path] = []
    for caminho in sorted(origem.rglob("*")):
        if not caminho.is_file():
            continue
        relativo = caminho.relative_to(origem)
        partes = relativo.parts
        if "__pycache__" in partes or relativo.suffix == ".pyc":
            continue
        if _faz_parte(relativo, raiz):
            plano.append(relativo)
    versao = Path("src") / "spurgeon" / "__init__.py"
    return [p for p in plano if p != versao] + ([versao] if versao in plano else [])


def aplicar_zip(
    zip_path: Path, raiz: Path, *, versao_atual: str | None = None
) -> ResultadoDaAtualizacao:
    """Troca os arquivos do programa pelos do ZIP. Tudo ou nada: se algo falhar no meio, o que já
    foi trocado volta ao que era (a cópia fica em `PASTA_DE_TRABALHO/anterior`)."""
    trabalho = raiz / PASTA_DE_TRABALHO
    extraido, anterior = trabalho / "extraido", trabalho / "anterior"
    try:
        origem = _extrair(zip_path, extraido)
        inicio = origem / "src" / "spurgeon" / "__init__.py"
        if not inicio.is_file():
            raise ErroDeAtualizacao("O arquivo baixado não é o Studio Spurgeon.")
        nova = ler_versao(inicio.read_text(encoding="utf-8"))
        atual = numeros_da_versao(versao_atual or versao_em_uso())
        if nova is None or atual is None or not e_mais_nova(nova, atual):
            raise ErroDeAtualizacao("A versão baixada não é mais nova que a que está em uso.")
        return _trocar(origem, raiz, anterior, ".".join(map(str, nova)))
    finally:
        shutil.rmtree(extraido, ignore_errors=True)


def _sem_a_versao_do_programa(caminho: Path) -> dict | None:
    """O `pyproject.toml` ou o `uv.lock` lido, sem a versão do próprio programa (que muda a cada
    versão e não pede instalação). None se não deu para ler."""
    try:
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(dados.get("project"), dict):
        dados["project"].pop("version", None)
    for pacote in dados.get("package", []):
        if isinstance(pacote, dict) and pacote.get("name") == "spurgeon":
            pacote.pop("version", None)
    return dados


def _bibliotecas_mudaram(origem: Path, raiz: Path) -> bool:
    """A versão nova pede bibliotecas que a pasta ainda não tem? Compara o que o `pyproject.toml` e
    o `uv.lock` dizem das bibliotecas, sem a versão do próprio programa."""
    for nome in ARQUIVOS_QUE_PEDEM_INSTALAR:
        novo, atual = origem / nome, raiz / nome
        if not novo.is_file():
            continue
        if not atual.is_file():
            return True
        lido_novo, lido_atual = _sem_a_versao_do_programa(novo), _sem_a_versao_do_programa(atual)
        if lido_novo is None or lido_atual is None:
            if not _mesmo_conteudo(novo, atual):
                return True
        elif lido_novo != lido_atual:
            return True
    return False


def _trocar(origem: Path, raiz: Path, anterior: Path, versao: str) -> ResultadoDaAtualizacao:
    if anterior.exists():
        shutil.rmtree(anterior)
    pede_instalacao = _bibliotecas_mudaram(origem, raiz)  # antes de os arquivos serem trocados
    feitos: list[tuple[Path, Path | None]] = []  # (arquivo trocado, cópia do que havia nele)
    trocados: list[Path] = []
    novos = 0
    try:
        for relativo in _plano(origem, raiz):
            novo, destino = origem / relativo, raiz / relativo
            copia: Path | None = None
            if destino.exists():
                if _mesmo_conteudo(novo, destino):
                    continue  # já é igual
                copia = anterior / relativo
                copia.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destino, copia)
                trocados.append(relativo)
            else:
                novos += 1
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(novo, destino)
            feitos.append((destino, copia))
    except OSError as erro:
        for destino, copia in reversed(feitos):  # desfaz o que já tinha sido trocado
            if copia is not None:
                shutil.copy2(copia, destino)
            else:
                destino.unlink(missing_ok=True)
        raise ErroDeAtualizacao(
            f"A atualização não pôde ser instalada ({erro}); nada foi alterado. Feche outros "
            "programas que usem a pasta do Studio Spurgeon e tente de novo."
        ) from erro
    return ResultadoDaAtualizacao(versao, len(trocados), novos, pede_instalacao)


def baixar_e_atualizar(
    raiz: Path,
    info: InfoAtualizacao,
    *,
    ao_progresso: Callable[[float], None] | None = None,
    abrir: Callable[[str, float], object] | None = None,
) -> ResultadoDaAtualizacao:
    """Baixa a versão de `info` e a instala em `raiz`."""
    if motivo := motivo_de_nao_atualizar_sozinho(raiz):
        raise ErroDeAtualizacao(motivo)
    arquivo = raiz / PASTA_DE_TRABALHO / "spurgeon-main.zip"
    baixar_zip(arquivo, url=info.url_do_zip, ao_progresso=ao_progresso, abrir=abrir)
    try:
        return aplicar_zip(arquivo, raiz, versao_atual=info.versao_atual)
    finally:
        arquivo.unlink(missing_ok=True)


# --- reabrir -------------------------------------------------------------------------------
def reabrir_programa(raiz: Path, *, instalar: bool, pid: int | None = None) -> None:
    """Abre o programa de novo depois da atualização, sem esperar por ele (o atual vai fechar).

    Se as bibliotecas mudaram (`instalar`), roda antes o `instalar.bat`, numa janela, que espera
    este programa fechar (`-AguardarPid`), atualiza o ambiente e abre o programa (`-Abrir`).
    """
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    solto = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    if instalar:
        comando = [
            "cmd.exe", "/c", str(raiz / "instalar.bat"),
            "-Silencioso", "-SemModelos", "-SemOllama", "-SemAtalhos",
            "-AguardarPid", str(pid or os.getpid()), "-Abrir",
        ]  # fmt: skip
        subprocess.Popen(
            comando, cwd=raiz, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        )
        return
    subprocess.Popen(
        [sys.executable, "-m", "spurgeon", "gui"],
        cwd=raiz,
        creationflags=solto | sem_janela,
        close_fds=True,
    )
