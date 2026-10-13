"""Limpeza de ruído com o binário oficial do DeepFilterNet (`deep-filter`).

O pacote Python `deepfilternet` não funciona com o torch atual, então usamos o executável
das releases oficiais, baixado uma vez para `modelos/ferramentas/` e conferido por SHA-256.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from spurgeon.core.config import ConfigApp
from spurgeon.core.erros import Cancelado, ErroFerramenta
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.core.processos import vigiar_processo
from spurgeon.core.sinais import SinalControle

VERSAO = "0.5.6"
URL = (
    f"https://github.com/Rikorose/DeepFilterNet/releases/download/v{VERSAO}/"
    f"deep-filter-{VERSAO}-x86_64-pc-windows-msvc.exe"
)
SHA256 = "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915"
# Medido nesta máquina: 0,12 a 0,14 vezes a duração do áudio. O fator da estimativa é
# pessimista de propósito, para a barra não chegar ao fim antes do processo.
FATOR_TEMPO_ESTIMADO = 0.2
_PROGRESSO_MAXIMO_ESTIMADO = 0.97
_BLOCO = 256 * 1024


def caminho_deep_filter(config: ConfigApp) -> Path:
    return config.pastas.modelos / "ferramentas" / "deep-filter.exe"


def sha256_de(caminho: Path) -> str:
    h = hashlib.sha256()
    with Path(caminho).open("rb") as f:
        while bloco := f.read(1024 * 1024):
            h.update(bloco)
    return h.hexdigest()


def deep_filter_instalado(config: ConfigApp, sha256: str = SHA256) -> bool:
    destino = caminho_deep_filter(config)
    return destino.is_file() and sha256_de(destino) == sha256


def baixar_deep_filter(
    config: ConfigApp,
    *,
    url: str = URL,
    sha256: str = SHA256,
    ao_progresso: Callable[[float], None] | None = None,
    cancelar: SinalControle | None = None,
) -> Path:
    """Garante o `deep-filter.exe` em `modelos/ferramentas/`, baixando e conferindo o hash."""
    destino = caminho_deep_filter(config)
    if deep_filter_instalado(config, sha256):
        return destino
    if url == URL and sys.platform != "win32":
        raise ErroFerramenta("Só há binário do deep-filter para Windows nesta versão.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".baixando")
    try:
        requisicao = urllib.request.Request(url, headers={"User-Agent": "spurgeon"})
        with urllib.request.urlopen(requisicao, timeout=30) as resposta, parcial.open("wb") as f:
            total = int(resposta.headers.get("Content-Length") or 0)
            baixado = 0
            while bloco := resposta.read(_BLOCO):
                if cancelar is not None and cancelar.is_set():
                    raise Cancelado()
                f.write(bloco)
                baixado += len(bloco)
                if ao_progresso and total:
                    ao_progresso(baixado / total)
    except Cancelado:
        parcial.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as erro:
        parcial.unlink(missing_ok=True)
        raise ErroFerramenta(
            f"Não foi possível baixar o deep-filter ({erro}). Baixe manualmente em {url} "
            f"e salve como {destino}."
        ) from erro
    if sha256_de(parcial) != sha256:
        parcial.unlink(missing_ok=True)
        raise ErroFerramenta(
            "O deep-filter baixado não confere com o SHA-256 esperado e foi descartado. "
            "Tente de novo; se persistir, baixe manualmente da página oficial."
        )
    os.replace(parcial, destino)
    return destino


def rodar_deep_filter(
    exe: Path,
    entrada: Path,
    pasta_saida: Path,
    *,
    duracao_s: float,
    ao_progresso: Callable[[float], None] | None = None,
    cancelar: SinalControle | None = None,
    pausar: SinalControle | None = None,
    ao_pausa: Callable[[bool], None] | None = None,
) -> Path:
    """Limpa `entrada` (WAV) e devolve o arquivo gerado em `pasta_saida`.

    `-D` compensa o atraso do filtro, para o áudio continuar alinhado ao vídeo. A ferramenta
    não informa progresso, então ele é estimado pelo tempo de trabalho.
    """
    pasta_saida.mkdir(parents=True, exist_ok=True)
    comando = [str(exe), "-D", "-o", str(pasta_saida), str(entrada)]
    with tempfile.TemporaryFile() as erros:
        processo = subprocess.Popen(
            comando,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=erros,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        trabalhado = 0.0
        ultimo = time.monotonic()

        def estimar() -> None:
            nonlocal trabalhado, ultimo
            agora = time.monotonic()
            trabalhado += min(agora - ultimo, 1.0)  # a pausa não conta
            ultimo = agora
            if ao_progresso:
                estimativa = trabalhado / (duracao_s * FATOR_TEMPO_ESTIMADO)
                ao_progresso(min(_PROGRESSO_MAXIMO_ESTIMADO, estimativa))

        codigo = vigiar_processo(
            processo, cancelar=cancelar, pausar=pausar, ao_pausa=ao_pausa, ao_tick=estimar
        )
        erros.seek(0)
        texto_erro = erros.read().decode("utf-8", errors="replace").strip()
    saida = pasta_saida / entrada.name
    if codigo != 0 or not saida.is_file():
        raise ErroFerramenta(f"O deep-filter falhou (código {codigo}). {texto_erro}".strip())
    return saida


class LimparAudio(Etapa):
    id = "limpar_audio"
    nome = "Limpando o ruído do áudio (DeepFilterNet)"
    depende_de = ("extrair_audio",)
    peso = 8.0

    def ativa(self, opcoes) -> bool:
        return opcoes.limpar_audio

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.audio_limpo]

    def executar(self, ctx: ContextoProjeto) -> None:
        if not deep_filter_instalado(ctx.config):
            ctx.log.info("Baixando o deep-filter %s (cerca de 26 MB, só na primeira vez)…", VERSAO)
            baixar_deep_filter(ctx.config, cancelar=ctx.cancelar)
            ctx.log.info("deep-filter instalado e conferido por SHA-256.")
        exe = caminho_deep_filter(ctx.config)

        entrada = ctx.pastas.audio_original
        duracao = sondar(entrada).duracao_s
        ctx.log.info(
            "Limpando %s de áudio; estimativa: %s (nesta máquina leva ~0,13 vezes a duração).",
            formatar_duracao(duracao),
            formatar_duracao(duracao * 0.13),
        )
        pasta_saida = ctx.pastas.work / "deep_filter"
        shutil.rmtree(pasta_saida, ignore_errors=True)
        saida = rodar_deep_filter(
            exe,
            entrada,
            pasta_saida,
            duracao_s=duracao,
            ao_progresso=ctx.progresso,
            cancelar=ctx.cancelar,
            pausar=ctx.pausar,
            ao_pausa=ctx.avisar_pausa_de_processo,
        )
        duracao_limpa = sondar(saida).duracao_s
        if abs(duracao_limpa - duracao) > 1.0:
            raise ErroFerramenta(
                f"O áudio limpo tem {duracao_limpa:.1f} s e o original {duracao:.1f} s; "
                "o resultado foi descartado para não desalinhar os tempos."
            )
        os.replace(saida, ctx.pastas.audio_limpo)
        shutil.rmtree(pasta_saida, ignore_errors=True)
        ctx.log.info("Áudio limpo gerado: %s.", ctx.pastas.audio_limpo.name)
