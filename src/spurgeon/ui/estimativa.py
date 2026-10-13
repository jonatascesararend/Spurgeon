"""Tempo decorrido e tempo restante estimado de um processamento."""

from __future__ import annotations

import time
from collections.abc import Callable

from spurgeon.core.formatacao import formatar_duracao

__all__ = ["EstimadorTempo", "formatar_duracao", "formatar_restante"]

_FRACAO_MINIMA = 0.02  # antes disso a estimativa oscila demais
_DECORRIDO_MINIMO_S = 5.0
_ALISAMENTO = 0.3  # peso da medida mais recente na média móvel


class EstimadorTempo:
    """Estima o que falta a partir da velocidade observada, sem contar o tempo em pausa."""

    def __init__(self, relogio: Callable[[], float] = time.monotonic):
        self._relogio = relogio
        self._inicio: float | None = None
        self._fracao_inicial = 0.0
        self._pausado_desde: float | None = None
        self._tempo_pausado = 0.0
        self._duracao_estimada: float | None = None

    def iniciar(self, fracao_inicial: float = 0.0) -> None:
        """`fracao_inicial` é o que já estava pronto (ao retomar um projeto)."""
        self._inicio = self._relogio()
        self._fracao_inicial = fracao_inicial
        self._pausado_desde = None
        self._tempo_pausado = 0.0
        self._duracao_estimada = None

    def pausar(self) -> None:
        if self._inicio is not None and self._pausado_desde is None:
            self._pausado_desde = self._relogio()

    def retomar(self) -> None:
        if self._pausado_desde is not None:
            self._tempo_pausado += self._relogio() - self._pausado_desde
            self._pausado_desde = None

    def decorrido(self) -> float:
        if self._inicio is None:
            return 0.0
        fim = self._pausado_desde if self._pausado_desde is not None else self._relogio()
        return max(0.0, fim - self._inicio - self._tempo_pausado)

    def atualizar(self, fracao_total: float) -> None:
        feito = fracao_total - self._fracao_inicial
        decorrido = self.decorrido()
        if feito < _FRACAO_MINIMA or decorrido < _DECORRIDO_MINIMO_S:
            return
        medida = decorrido / feito * (1.0 - self._fracao_inicial)
        if self._duracao_estimada is None:
            self._duracao_estimada = medida
        else:
            self._duracao_estimada += _ALISAMENTO * (medida - self._duracao_estimada)

    def restante(self, fracao_total: float) -> float | None:
        """Segundos que faltam, ou None enquanto ainda não dá para estimar."""
        if self._duracao_estimada is None:
            return None
        restante_fracao = max(0.0, 1.0 - fracao_total)
        base = 1.0 - self._fracao_inicial
        return self._duracao_estimada * restante_fracao / base if base > 0 else 0.0


def formatar_restante(segundos: float | None) -> str:
    if segundos is None:
        return "calculando…"
    if segundos < 60:
        return "menos de 1 min"
    if segundos < 3600:
        return f"cerca de {round(segundos / 60)} min"
    horas, resto = divmod(int(segundos), 3600)
    return f"cerca de {horas} h {resto // 60:02d} min"


# --- estimativa antes de começar ------------------------------------------------------------
# Segundos de processamento por segundo de vídeo, medidos nesta máquina (i7-6800K, só CPU) com o
# sermão real de 65 min. Valem como ordem de grandeza: outra máquina muda tudo.
_EXTRAIR_E_MEDIR = 0.05
_LIMPAR_AUDIO = 0.13
_NORMALIZAR = 0.03
_PAUSAS_E_LEGENDAS = 0.03
_IA_DOS_SHORTS = {"4b": 0.5}  # qwen3:4b; os demais usam o valor do qwen3:8b abaixo
_IA_PADRAO = 0.9
_WHISPER = {
    "tiny": 0.05, "base": 0.1, "small": 0.3, "medium": 0.9,
    "large-v3-turbo": 0.74, "large-v3": 1.9,
}  # fmt: skip
_RENDER_HDR = {
    "ultrafast": 0.8, "superfast": 0.82, "veryfast": 0.89, "faster": 0.95, "fast": 1.0,
    "medium": 1.12, "slow": 1.51, "slower": 2.3, "veryslow": 3.78,
}  # fmt: skip
_FATOR_SDR = 0.75  # sem a conversão HDR→SDR o filtro pesa menos
_FRACAO_DOS_SHORTS = 0.3  # os Shorts somam ~20 min de vídeo para um sermão de ~65 min


def estimar_render_s(duracao_s: float, preset: str, *, hdr: bool, shorts: bool = False) -> float:
    fator = _RENDER_HDR.get(preset, 1.12) * (1.0 if hdr else _FATOR_SDR)
    return duracao_s * fator * (_FRACAO_DOS_SHORTS if shorts else 1.0)


def estimar_tempo_total(duracao_s: float, opcoes, *, hdr: bool) -> tuple[float, float]:
    """(tempo total, só a renderização) estimados, em segundos, para um vídeo desta duração."""
    por_segundo = _EXTRAIR_E_MEDIR + _PAUSAS_E_LEGENDAS
    por_segundo += _LIMPAR_AUDIO if opcoes.limpar_audio else 0.0
    por_segundo += _NORMALIZAR if opcoes.normalizar_volume else 0.0
    por_segundo += _WHISPER.get(opcoes.whisper_modelo, 0.74)
    if opcoes.gerar_shorts:
        modelo = opcoes.ollama_modelo.lower()
        por_segundo += next((v for k, v in _IA_DOS_SHORTS.items() if k in modelo), _IA_PADRAO)
    preset = opcoes.preset_qualidade().preset
    render = 0.0
    if opcoes.gerar_video_longo:
        render += estimar_render_s(duracao_s, preset, hdr=hdr)
    if opcoes.gerar_shorts:
        render += estimar_render_s(duracao_s, preset, hdr=hdr, shorts=True)
    return duracao_s * por_segundo + render, render


def formatar_estimativa(total_s: float, render_s: float) -> str:
    if total_s < 600:
        return f"Tempo estimado nesta máquina: cerca de {max(1, round(total_s / 60))} min."
    texto = f"Tempo estimado nesta máquina: {formatar_restante(total_s)}"
    if render_s >= 1800:
        texto += f" (só a renderização: {formatar_restante(render_s).replace('cerca de ', '')})"
    return texto + "."
