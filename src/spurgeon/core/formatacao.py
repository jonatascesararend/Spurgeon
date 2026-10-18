"""Formatação de números para mostrar ao usuário (log e interface)."""

from __future__ import annotations

import re


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


_TEMPO = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d{1,2}(?:[.,]\d+)?)\s*$|^\s*(\d+(?:[.,]\d+)?)\s*$")


def ler_tempo(texto: str) -> float:
    """`8:21,3`, `1:02:05.5` ou `501.3` → segundos. Levanta ValueError se não for um tempo."""
    achado = _TEMPO.match(texto)
    if not achado:
        raise ValueError(f"Tempo inválido: {texto!r}. Use minutos:segundos, por exemplo 8:21,3.")
    horas, minutos, segundos, so_segundos = achado.groups()
    if so_segundos is not None:
        return float(so_segundos.replace(",", "."))
    return int(horas or 0) * 3600 + int(minutos) * 60 + float(segundos.replace(",", "."))


def formatar_tempo_preciso(segundos: float, casas: int = 1) -> str:
    """501,3 → `8:21,3`; 3725,45 → `1:02:05,5` (décimos de segundo por padrão, vírgula decimal)."""
    escala = 10**casas
    partes = round(segundos * escala)
    inteiros, fracao = divmod(partes, escala)
    horas, resto = divmod(inteiros, 3600)
    minutos, seg = divmod(resto, 60)
    base = f"{horas}:{minutos:02d}:{seg:02d}" if horas else f"{minutos}:{seg:02d}"
    return f"{base},{fracao:0{casas}d}"
