"""Wrapper do FFmpeg e do ffprobe.

Sempre com lista de argumentos (sem shell). Quem chama deve gravar a saída num nome
temporário e renomear só no fim, pois um cancelamento deixa o arquivo pela metade.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from pathlib import Path

from spurgeon.core.erros import ErroConfiguracao, ErroFFmpeg
from spurgeon.core.processos import vigiar_processo
from spurgeon.core.sinais import SinalControle

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_AJUDA_INSTALAR = "winget install Gyan.FFmpeg"


@cache
def localizar_binario(nome: str) -> Path:
    """Acha `ffmpeg` ou `ffprobe`: variável SPURGEON_<NOME>, PATH, ou pasta do winget."""
    manual = os.environ.get(f"SPURGEON_{nome.upper()}")
    if manual and Path(manual).exists():
        return Path(manual)
    achado = shutil.which(nome)
    if achado:
        return Path(achado)
    pacotes = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for candidato in sorted(pacotes.glob(f"Gyan.FFmpeg*/**/bin/{nome}.exe")):
        return candidato
    raise ErroConfiguracao(f"{nome} não encontrado. Instale com: {_AJUDA_INSTALAR}")


def _rodar(comando: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        comando,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=_SEM_JANELA,
    )


def executar_ffmpeg(
    args: Sequence[str | Path],
    *,
    duracao_s: float | None = None,
    ao_progresso: Callable[[float], None] | None = None,
    cancelar: SinalControle | None = None,
    pausar: SinalControle | None = None,
    ao_pausa: Callable[[bool], None] | None = None,
    cwd: Path | None = None,
    max_linhas_erro: int = 400,
) -> str:
    """Roda o FFmpeg e devolve o que ele escreveu no stderr (as medições dos filtros, por ex.).

    `ao_progresso` recebe a fração 0..1 (precisa de `duracao_s`). Cancelar o encerra
    (`Cancelado`); pausar o suspende de verdade até pedirem para retomar.
    """
    comando = [
        str(localizar_binario("ffmpeg")),
        "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1",
        *map(str, args),
    ]  # fmt: skip
    processo = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_SEM_JANELA,
    )
    erros: deque[str] = deque(maxlen=max_linhas_erro)
    ultima_fracao: list[float] = []  # lista de 0 ou 1 item, atualizada pela thread de leitura

    def ler_progresso() -> None:
        for linha in processo.stdout:
            chave, _, valor = linha.strip().partition("=")
            # Nos primeiros instantes o valor vem como "N/A" (ou negativo); só conta número.
            if chave == "out_time_us" and duracao_s and valor.isdigit():
                ultima_fracao[:] = [min(1.0, int(valor) / 1e6 / duracao_s)]

    def ler_erros() -> None:
        for linha in processo.stderr:
            erros.append(linha.rstrip("\n"))

    # stdout e stderr são lidos em paralelo; senão o FFmpeg trava quando o buffer do pipe enche.
    leitores = [threading.Thread(target=f, daemon=True) for f in (ler_progresso, ler_erros)]
    for leitor in leitores:
        leitor.start()

    enviada: list[float] = []

    def avisar_progresso() -> None:
        if ao_progresso and ultima_fracao and ultima_fracao != enviada:
            enviada[:] = ultima_fracao
            ao_progresso(ultima_fracao[0])

    codigo = vigiar_processo(
        processo, cancelar=cancelar, pausar=pausar, ao_pausa=ao_pausa, ao_tick=avisar_progresso
    )
    for leitor in leitores:
        leitor.join(timeout=5)
    avisar_progresso()
    if codigo != 0:
        raise ErroFFmpeg(comando, codigo, "\n".join(erros))
    return "\n".join(erros)


@dataclass(frozen=True)
class InfoMidia:
    caminho: Path
    duracao_s: float
    tem_video: bool
    tem_audio: bool
    largura: int | None = None
    altura: int | None = None
    fps: float | None = None
    codec_video: str | None = None
    codec_audio: str | None = None
    canais_audio: int | None = None
    taxa_audio: int | None = None
    creation_time: str | None = None


def sondar(caminho: Path) -> InfoMidia:
    """Lê duração, resolução, fps, codecs e data de criação com o ffprobe."""
    comando = [
        str(localizar_binario("ffprobe")),
        "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(caminho),
    ]  # fmt: skip
    resultado = _rodar(comando, timeout=60)
    if resultado.returncode != 0:
        raise ErroFFmpeg(comando, resultado.returncode, resultado.stderr)
    dados = json.loads(resultado.stdout)
    streams = dados.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    formato = dados.get("format", {})
    duracao = formato.get("duration") or (video or audio or {}).get("duration") or 0
    tags = {**(video or {}).get("tags", {}), **formato.get("tags", {})}
    return InfoMidia(
        caminho=Path(caminho),
        duracao_s=float(duracao),
        tem_video=video is not None,
        tem_audio=audio is not None,
        largura=video.get("width") if video else None,
        altura=video.get("height") if video else None,
        fps=_fps(video.get("r_frame_rate")) if video else None,
        codec_video=video.get("codec_name") if video else None,
        codec_audio=audio.get("codec_name") if audio else None,
        canais_audio=audio.get("channels") if audio else None,
        taxa_audio=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        creation_time=tags.get("creation_time"),
    )


def _fps(texto: str | None) -> float | None:
    try:
        valor = Fraction(texto or "0/1")
    except (ValueError, ZeroDivisionError):
        return None
    return float(valor) if valor > 0 else None


def versao_ffmpeg() -> str:
    resultado = _rodar([str(localizar_binario("ffmpeg")), "-version"], timeout=30)
    return resultado.stdout.splitlines()[0] if resultado.stdout else "desconhecida"


@cache
def filtros_disponiveis() -> frozenset[str]:
    resultado = _rodar([str(localizar_binario("ffmpeg")), "-hide_banner", "-filters"], timeout=30)
    # As colunas de flags variam entre versões do FFmpeg (2 ou 3 caracteres).
    return frozenset(re.findall(r"^\s*[TSC.]{2,3}\s+(\S+)\s", resultado.stdout, flags=re.MULTILINE))


@cache
def codificadores_disponiveis() -> frozenset[str]:
    resultado = _rodar([str(localizar_binario("ffmpeg")), "-hide_banner", "-encoders"], timeout=30)
    return frozenset(
        re.findall(r"^\s*[VAS][A-Z.]{5}\s+(\S+)\s", resultado.stdout, flags=re.MULTILINE)
    )


@cache
def nvenc_funciona() -> bool:
    """Teste real: codifica um quadro com h264_nvenc. Falha com GPU/driver sem suporte."""
    if "h264_nvenc" not in codificadores_disponiveis():
        return False
    comando = [
        str(localizar_binario("ffmpeg")), "-hide_banner", "-nostdin",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
        "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
    ]  # fmt: skip
    try:
        return _rodar(comando, timeout=30).returncode == 0
    except subprocess.TimeoutExpired:
        return False
