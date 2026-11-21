"""Liberar espaço no PC pelo celular: "varrer" um projeto ou apagá-lo de vez.

* **Varrer** apaga o que é pesado e já cumpriu o papel: os Shorts renderizados (`.mp4` de
  `shorts_gerados/`, também em `postados/`, `agendados/` e `desmarcados/`), o vídeo longo pronto, os
  intermediários de `_work/` (como `core.projeto.limpar_intermediarios`: ficam os `.json`) e o vídeo
  original. FICAM a transcrição, os cortes e a análise (`.json`), os `.txt` de cada Short, as
  legendas `.srt`, o `estado.json` e o registro do que foi postado: o projeto continua abrindo, a
  legenda continua exportável, e o calendário continua sabendo o que saiu. Sem o original não dá
  mais para renderizar nem processar (seria preciso baixar o vídeo de novo).
* **Apagar** tira a pasta inteira do projeto e o vídeo original.

O vídeo original pode estar fora da pasta do projeto (em `Downloads/`): só é apagado se for mesmo um
vídeo e se nenhum OUTRO projeto usa o mesmo arquivo. Nada disso roda com um processamento ou uma
postagem em andamento (quem chama confere), e apagar exige a janela do PC fora do projeto.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.estado import carregar_estado
from spurgeon.core.projeto import PastasProjeto

log = logging.getLogger(__name__)

EXTENSOES_DE_VIDEO = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts", ".m2ts", ".ts"}
#: o que a varredura deixa no projeto, para o celular dizer "varrido em…"
NOME_DA_MARCA = "varrido.json"


def _tamanho(caminho: Path) -> int:
    try:
        if caminho.is_dir():
            return sum(f.stat().st_size for f in caminho.rglob("*") if f.is_file())
        return caminho.stat().st_size
    except OSError:
        return 0


def _mesmo_arquivo(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def original_do_projeto(pastas: PastasProjeto) -> Path | None:
    estado = carregar_estado(pastas.raiz)
    if estado is None or not estado.entrada.caminho:
        return None
    caminho = Path(estado.entrada.caminho)
    if not caminho.is_absolute():
        caminho = pastas.raiz / caminho
    return caminho if caminho.is_file() else None


def outros_projetos_que_usam(
    pastas: PastasProjeto, original: Path, pasta_projetos: Path
) -> list[str]:
    """Os nomes dos OUTROS projetos cujo vídeo de entrada é este mesmo arquivo."""
    usam = []
    if not pasta_projetos.is_dir():
        return usam
    for pasta in pasta_projetos.iterdir():
        if not pasta.is_dir() or _mesmo_arquivo(pasta, pastas.raiz):
            continue
        outro = original_do_projeto(PastasProjeto(pasta))
        if outro is not None and _mesmo_arquivo(outro, original):
            usam.append(pasta.name)
    return usam


@dataclass
class Plano:
    """O que sai e quanto ocupa: a caixa de confirmação e a ação usam a mesma lista."""

    arquivos: list[Path] = field(default_factory=list)  # dentro do projeto
    original: Path | None = None  # o vídeo original, se vai ser apagado
    original_fica: str = ""  # por que o original não sai ("" = sai ou não existe)
    bytes_do_projeto: int = 0
    bytes_do_original: int = 0

    @property
    def bytes(self) -> int:
        return self.bytes_do_projeto + self.bytes_do_original

    def para_json(self) -> dict[str, Any]:
        return {
            "arquivos": len(self.arquivos),
            "bytes": self.bytes,
            "bytes_do_projeto": self.bytes_do_projeto,
            "original": self.original.name if self.original else "",
            "bytes_do_original": self.bytes_do_original,
            "original_fica": self.original_fica,
        }


def _plano_do_original(plano: Plano, pastas: PastasProjeto, pasta_projetos: Path) -> None:
    original = original_do_projeto(pastas)
    if original is None:
        return
    if original.suffix.lower() not in EXTENSOES_DE_VIDEO:
        plano.original_fica = f"“{original.name}” não parece um vídeo: fica onde está."
        return
    if outros := outros_projetos_que_usam(pastas, original, pasta_projetos):
        plano.original_fica = f"O vídeo original também é usado por: {', '.join(outros)}. Ele fica."
        return
    plano.original, plano.bytes_do_original = original, _tamanho(original)


def plano_de_varrer(pastas: PastasProjeto, pasta_projetos: Path) -> Plano:
    plano = Plano()
    if pastas.shorts.is_dir():
        plano.arquivos += sorted(f for f in pastas.shorts.rglob("*.mp4") if f.is_file())
    plano.arquivos += [v for v in (pastas.video_longo, pastas.video_com_vinhetas) if v.is_file()]
    if pastas.work.is_dir():  # o que `limpar_intermediarios` apaga
        plano.arquivos += sorted(
            item
            for item in pastas.work.iterdir()
            if item.is_dir() or item.suffix.lower() != ".json"
        )
    plano.bytes_do_projeto = sum(_tamanho(a) for a in plano.arquivos)
    _plano_do_original(plano, pastas, pasta_projetos)
    return plano


def plano_de_apagar(pastas: PastasProjeto, pasta_projetos: Path) -> Plano:
    plano = Plano(arquivos=[pastas.raiz], bytes_do_projeto=_tamanho(pastas.raiz))
    _plano_do_original(plano, pastas, pasta_projetos)
    return plano


@dataclass
class Resultado:
    bytes: int = 0
    apagados: int = 0
    falhas: list[str] = field(default_factory=list)  # arquivos em uso, sem permissão…

    def para_json(self) -> dict[str, Any]:
        return {"bytes": self.bytes, "apagados": self.apagados, "falhas": self.falhas}


def _apagar(caminho: Path, resultado: Resultado) -> None:
    tamanho = _tamanho(caminho)
    try:
        if caminho.is_dir():
            shutil.rmtree(caminho)
        else:
            caminho.unlink(missing_ok=True)
    except OSError as erro:
        log.warning("Não deu para apagar %s: %s", caminho, erro)
        resultado.falhas.append(caminho.name)
        resultado.bytes += tamanho - _tamanho(caminho)  # o rmtree pode ter apagado uma parte
        return
    resultado.bytes += tamanho
    resultado.apagados += 1


def varrer(pastas: PastasProjeto, pasta_projetos: Path, agora: datetime | None = None) -> Resultado:
    plano = plano_de_varrer(pastas, pasta_projetos)
    resultado = Resultado()
    for item in plano.arquivos:
        _apagar(item, resultado)
    if plano.original is not None:
        _apagar(plano.original, resultado)
    marca = {
        "quando": (agora or datetime.now()).strftime("%Y-%m-%dT%H:%M"),
        "bytes": resultado.bytes + _bytes_ja_varridos(pastas),
        "original_apagado": plano.original is not None
        and plano.original.name not in resultado.falhas,
    }
    pastas.work.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(pastas.work / NOME_DA_MARCA, marca)
    log.info("Projeto %s varrido: %d itens, %d bytes.", pastas.raiz.name, resultado.apagados,
             resultado.bytes)  # fmt: skip
    return resultado


def apagar_projeto(pastas: PastasProjeto, pasta_projetos: Path) -> Resultado:
    plano = plano_de_apagar(pastas, pasta_projetos)
    resultado = Resultado()
    if plano.original is not None:
        _apagar(plano.original, resultado)
    _apagar(pastas.raiz, resultado)
    # uma renovação da trava do celular que chegou no meio (`sessao.tomar` cria `_work/`) pode
    # trazer a pasta de volta só com os arquivos da trava: ela não é mais um projeto, sai também
    if pastas.raiz.is_dir() and not pastas.estado.exists():
        shutil.rmtree(pastas.raiz, ignore_errors=True)
    log.info("Projeto %s apagado (%d bytes).", pastas.raiz.name, resultado.bytes)
    return resultado


def _bytes_ja_varridos(pastas: PastasProjeto) -> int:
    marca = ler_marca(pastas)
    return int(marca.get("bytes") or 0) if marca else 0


def ler_marca(pastas: PastasProjeto) -> dict[str, Any] | None:
    try:
        dados = ler_json(pastas.work / NOME_DA_MARCA)
        return dados if isinstance(dados, dict) else None
    except (OSError, ValueError):
        return None


def espaco_do_projeto(pastas: PastasProjeto, pasta_projetos: Path) -> dict[str, Any]:
    """Para a tela do projeto: o que varrer e o que apagar liberariam, e se já foi varrido."""
    return {
        "varrer": plano_de_varrer(pastas, pasta_projetos).para_json(),
        "apagar": plano_de_apagar(pastas, pasta_projetos).para_json(),
        "varrido": ler_marca(pastas),
    }
