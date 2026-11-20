"""Conversa com o Tailscale deste PC pela linha de comando dele (Remoto.md, R5 e S8).

Só o necessário: a conta dona do PC, o endereço `https://<pc>.<rede>.ts.net/`, se o HTTPS está
ligado, e publicar a porta do servidor na rede privada (`tailscale serve`, que fica guardado no
Tailscale e volta sozinho depois de reiniciar o PC).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

CAMINHO_PADRAO = Path(r"C:\Program Files\Tailscale\tailscale.exe")
_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErroTailscale(RuntimeError):
    pass


def localizar() -> Path | None:
    achado = shutil.which("tailscale")
    if achado:
        return Path(achado)
    return CAMINHO_PADRAO if sys.platform == "win32" and CAMINHO_PADRAO.is_file() else None


def _rodar(*args: str, timeout: float = 30) -> str:
    exe = localizar()
    if exe is None:
        raise ErroTailscale("O Tailscale não está instalado neste PC.")
    try:
        feito = subprocess.run(
            [str(exe), *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=_SEM_JANELA, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as erro:
        raise ErroTailscale(f"O Tailscale não respondeu ({erro}).") from erro
    if feito.returncode != 0:
        raise ErroTailscale(
            (feito.stderr or feito.stdout).strip() or "o Tailscale recusou o pedido"
        )
    return feito.stdout


def status() -> dict[str, Any]:
    return json.loads(_rodar("status", "--json"))


def conta_do_pc(dados: dict[str, Any]) -> str:
    """O login (e-mail) da conta dona deste PC no Tailscale, em minúsculas; "" se não se sabe."""
    eu = dados.get("Self") or {}
    usuario = (dados.get("User") or {}).get(str(eu.get("UserID", "")), {})
    return str(usuario.get("LoginName", "")).strip().lower()


def endereco_do_pc(dados: dict[str, Any]) -> str:
    nome = str((dados.get("Self") or {}).get("DNSName", "")).rstrip(".")
    return f"https://{nome}/" if nome else ""


def https_ligado(dados: dict[str, Any]) -> bool:
    return bool(dados.get("CertDomains"))


def porta_publicada(porta: int) -> bool:
    """Se o `tailscale serve` já leva o HTTPS da rede privada para esta porta."""
    try:
        texto = _rodar("serve", "status", "--json")
    except ErroTailscale:
        return False
    return f"127.0.0.1:{porta}" in texto


def publicar(porta: int) -> None:
    _rodar("serve", "--bg", "--https=443", f"http://127.0.0.1:{porta}")


def despublicar() -> None:
    _rodar("serve", "--https=443", "off")
