"""Formatação de números para mostrar ao usuário (log e interface)."""

from __future__ import annotations


def formatar_duracao(segundos: float) -> str:
    """1 h 05 min, 12 min 05 s, 45 s."""
    total = round(segundos)
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas} h {minutos:02d} min"
    if minutos:
        return f"{minutos} min {seg:02d} s"
    return f"{seg} s"


def resumir_linha_log(texto: str) -> str:
    """`2026-10-12 19:15:03 | INFO | etapa | mensagem` → `19:15:03  mensagem`."""
    partes = texto.split(" | ", 3)
    if len(partes) == 4 and len(partes[0]) >= 19:
        return f"{partes[0][11:19]}  {partes[3]}"
    return texto


def formatar_numero(valor: float, casas: int = 1) -> str:
    """Número com vírgula decimal: 10,5."""
    return f"{valor:.{casas}f}".replace(".", ",")
