"""Detecção de hardware e recomendação de modelos. Só recomenda; nunca bloqueia."""

from __future__ import annotations

import csv
import io
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# O CUDA 12 (exigido pelo CTranslate2 atual) só atende GPUs Maxwell (5.0) ou mais novas.
_CAPACIDADE_CUDA_MINIMA = 5.0
# RAM "de 16 GB" aparece como ~15,8 GiB no Windows; o limite fica um pouco abaixo.
_RAM_MINIMA_LLM_8B_GB = 15.0


@dataclass(frozen=True)
class Gpu:
    nome: str
    fabricante: str  # nvidia | amd | intel | outro
    vram_mb: int | None = None
    driver: str | None = None
    compute_cap: float | None = None


@dataclass(frozen=True)
class PerfilHardware:
    cpu_nome: str
    nucleos_fisicos: int
    threads_logicos: int
    ram_total_gb: float
    ram_livre_gb: float
    disco_livre_gb: float
    gpus: tuple[Gpu, ...]
    cuda_utilizavel: bool
    motivo_cuda: str


@dataclass(frozen=True)
class Recomendacao:
    whisper_modelo: str
    whisper_dispositivo: str  # cuda | cpu
    whisper_compute_type: str
    llm_modelo: str
    avisos: tuple[str, ...]


def _executar(comando: list[str], timeout: float = 15) -> str | None:
    try:
        r = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_SEM_JANELA,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _fabricante(nome: str) -> str:
    n = nome.lower()
    if any(t in n for t in ("nvidia", "geforce", "quadro", "rtx", "gtx", "tesla")):
        return "nvidia"
    if any(t in n for t in ("amd", "radeon", "ati ")):
        return "amd"
    if "intel" in n:
        return "intel"
    return "outro"


def _achar_nvidia_smi() -> Path | None:
    achado = shutil.which("nvidia-smi")
    if achado:
        return Path(achado)
    candidatos = [
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
    ]  # fmt: skip
    return next((c for c in candidatos if c.exists()), None)


def _gpus_nvidia_smi() -> list[Gpu]:
    exe = _achar_nvidia_smi()
    if exe is None:
        return []
    # `compute_cap` só existe em drivers mais novos; sem ele, tenta de novo sem o campo.
    saida = _executar([str(exe), "--query-gpu=name,memory.total,driver_version,compute_cap",
                       "--format=csv,noheader,nounits"])  # fmt: skip
    if saida is None:
        saida = _executar([str(exe), "--query-gpu=name,memory.total,driver_version",
                           "--format=csv,noheader,nounits"])  # fmt: skip
    gpus = []
    for linha in csv.reader(io.StringIO(saida or ""), skipinitialspace=True):
        if len(linha) < 3:
            continue
        try:
            vram = int(float(linha[1]))
        except ValueError:
            vram = None
        try:
            capacidade = float(linha[3]) if len(linha) > 3 else None
        except ValueError:
            capacidade = None
        gpus.append(Gpu(linha[0], "nvidia", vram, linha[2], capacidade))
    return gpus


def _gpus_windows() -> list[Gpu]:
    if sys.platform != "win32":
        return []
    saida = _executar([
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, DriverVersion | ConvertTo-Json -Compress",
    ])  # fmt: skip
    if not saida or not saida.strip():
        return []
    try:
        dados = json.loads(saida)
    except ValueError:
        return []
    gpus = []
    for item in [dados] if isinstance(dados, dict) else dados:
        nome = (item.get("Name") or "").strip()
        if not nome or "microsoft" in nome.lower():
            continue
        ram = item.get("AdapterRAM")
        # O WMI guarda a VRAM em 32 bits: placas de 4 GB ou mais aparecem truncadas.
        vram = int(ram / 1024**2) if isinstance(ram, int) and 0 < ram < 4 * 1024**3 - 1 else None
        gpus.append(Gpu(nome, _fabricante(nome), vram, item.get("DriverVersion")))
    return gpus


def detectar_gpus() -> list[Gpu]:
    gpus = _gpus_nvidia_smi()
    wmi = _gpus_windows()
    if not gpus:
        return wmi
    return gpus + [g for g in wmi if g.fabricante != "nvidia"]


def _avaliar_cuda(gpus: list[Gpu]) -> tuple[bool, str]:
    # A palavra final é do CTranslate2 (o que o faster-whisper usa), quando instalado.
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return True, "confirmado pelo CTranslate2"
        motivo = "o CTranslate2 não encontrou GPU CUDA utilizável (podem faltar cuBLAS/cuDNN 12)"
    except Exception:
        motivo = None
    nvidia = [g for g in gpus if g.fabricante == "nvidia"]
    if not nvidia:
        return False, "sem GPU NVIDIA"
    if motivo:
        return False, motivo
    capacidades = [g.compute_cap for g in nvidia if g.compute_cap is not None]
    if capacidades:
        if max(capacidades) >= _CAPACIDADE_CUDA_MINIMA:
            return True, f"capacidade CUDA {max(capacidades)} (a confirmar com o CTranslate2)"
        return False, (
            f"capacidade CUDA {max(capacidades)} está abaixo do mínimo "
            f"({_CAPACIDADE_CUDA_MINIMA}) do CUDA 12"
        )
    return False, "não foi possível confirmar a compatibilidade CUDA da GPU NVIDIA"


def _nome_cpu() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            chave = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, chave) as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "CPU desconhecida"


def detectar_hardware(pasta_disco: Path | None = None) -> PerfilHardware:
    memoria = psutil.virtual_memory()
    logicos = psutil.cpu_count(logical=True) or 1
    fisicos = psutil.cpu_count(logical=False) or logicos
    alvo = Path(pasta_disco) if pasta_disco else Path.home()
    while not alvo.exists() and alvo != alvo.parent:
        alvo = alvo.parent
    gpus = detectar_gpus()
    cuda, motivo = _avaliar_cuda(gpus)
    return PerfilHardware(
        cpu_nome=_nome_cpu(),
        nucleos_fisicos=fisicos,
        threads_logicos=logicos,
        ram_total_gb=round(memoria.total / 1024**3, 1),
        ram_livre_gb=round(memoria.available / 1024**3, 1),
        disco_livre_gb=round(shutil.disk_usage(alvo).free / 1024**3, 1),
        gpus=tuple(gpus),
        cuda_utilizavel=cuda,
        motivo_cuda=motivo,
    )


def threads_para_uso(perfil: PerfilHardware, percentual: int) -> int:
    """Quantos threads usar para o limite escolhido (75% ou 100%); nunca menos que 1."""
    return max(1, round(perfil.threads_logicos * percentual / 100))


def _vram_gb(perfil: PerfilHardware) -> float:
    nvidia = [g.vram_mb for g in perfil.gpus if g.fabricante == "nvidia" and g.vram_mb]
    return max(nvidia) / 1024 if nvidia else 0.0


def recomendar(perfil: PerfilHardware) -> Recomendacao:
    """Modelo do Whisper e do LLM para o hardware. Valores iniciais, a validar com benchmark."""
    avisos: list[str] = []
    vram = _vram_gb(perfil)

    if perfil.cuda_utilizavel and vram >= 6:
        whisper = ("large-v3", "cuda", "float16" if vram >= 8 else "int8_float16")
        llm = "qwen3:8b"
        if vram >= 12:
            avisos.append("A GPU comporta um LLM maior; o padrão continua qwen3:8b.")
    elif perfil.cuda_utilizavel and vram >= 3:
        whisper = ("large-v3-turbo", "cuda", "int8_float16")
        llm = "qwen3:8b" if vram >= 6 else "qwen3:4b"
    else:
        whisper = ("large-v3-turbo", "cpu", "int8")
        if perfil.threads_logicos < 4:
            whisper = ("small", "cpu", "int8")
            avisos.append("Poucos threads: o Whisper small é o que cabe em tempo razoável.")
        avisos.append(f"Sem GPU utilizável ({perfil.motivo_cuda}): tudo roda na CPU, "
                      "e o tempo pode superar bastante a duração do vídeo.")  # fmt: skip
        if perfil.ram_total_gb >= _RAM_MINIMA_LLM_8B_GB and perfil.threads_logicos >= 6:
            llm = "qwen3:8b"
        elif perfil.ram_total_gb >= 8:
            llm = "qwen3:4b"
        else:
            llm = "qwen3:1.7b"
            avisos.append("Pouca RAM: o LLM será pequeno e a seleção de cortes, mais fraca.")
    return Recomendacao(whisper[0], whisper[1], whisper[2], llm, tuple(avisos))
