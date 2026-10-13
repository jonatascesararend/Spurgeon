"""Pedaços de filtro e de argumentos do FFmpeg usados na renderização (só montam texto)."""

from __future__ import annotations

from fractions import Fraction

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.ffmpeg import InfoMidia

LARGURA_SHORT = 1080
ALTURA_SHORT = 1920
TAXA_AUDIO = 48000
BITRATE_AUDIO = "256k"
_TAXAS_PADRAO = (
    Fraction(24000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30000, 1001),
    Fraction(30),
    Fraction(50),
    Fraction(60000, 1001),
    Fraction(60),
)
_ROTULO_BT709 = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
_ENTRADA_HDR = {
    "arib-std-b67": "tin=arib-std-b67",  # HLG: o que o iPhone grava
    "smpte2084": "tin=smpte2084",  # HDR10 (PQ)
}


def fps_constante(fps: float | None) -> Fraction:
    """A taxa de quadros fixa da saída: a da origem, trocada pela padrão mais próxima."""
    if not fps or fps <= 0:
        return Fraction(30000, 1001)
    for padrao in _TAXAS_PADRAO:
        if abs(float(padrao) - fps) < 0.02:
            return padrao
    return Fraction(fps).limit_denominator(1001)


def texto_fps(taxa: Fraction) -> str:
    return f"{taxa.numerator}/{taxa.denominator}"


def cadeia_para_sdr(info: InfoMidia) -> str:
    """Filtros que deixam o vídeo em SDR BT.709, 8 bits (yuv420p).

    Origem HDR (HLG ou PQ): converte para luz linear, mapeia as cores para BT.709, comprime os
    brilhos acima do branco com `hable` e volta para a curva BT.709. Sem isso, o H.264 de 8 bits
    sai com as cores lavadas. O `desat=0` mantém as cores vivas (a cruz dourada do púlpito perde
    a cor com o valor padrão), ao custo de uma leve tinta nas telas de projeção muito brilhantes.
    Origem SDR: só garante o formato de pixel e marca as cores como BT.709.
    """
    if not info.hdr:
        return f"format=yuv420p,{_ROTULO_BT709}"
    entrada = _ENTRADA_HDR[info.transferencia or "arib-std-b67"]
    return (
        f"zscale={entrada}:pin=bt2020:min=bt2020nc:rin=tv:t=linear:npl=100,"
        "format=gbrpf32le,"
        "zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0,"
        "zscale=t=bt709:m=bt709:r=tv,"
        "format=yuv420p"
    )


def recorte_vertical(info: InfoMidia, deslocamento_pct: float = 0.0) -> str:
    """O recorte 9:16 central (ou deslocado) de um vídeo horizontal, em pixels pares.

    Para um vídeo de 1080p, 608×1080 saem do meio da imagem, onde fica o pregador. O deslocamento
    é uma porcentagem da largura do vídeo (positivo = para a direita).
    """
    largura, altura = info.largura or 1920, info.altura or 1080
    if largura * ALTURA_SHORT <= altura * LARGURA_SHORT:  # já é vertical (ou quase)
        return f"crop={largura // 2 * 2}:{altura // 2 * 2}:0:0"
    corte_l = round(altura * LARGURA_SHORT / ALTURA_SHORT / 2) * 2
    corte_l = min(corte_l, largura // 2 * 2)
    x = (largura - corte_l) / 2 + largura * deslocamento_pct / 100
    x = int(min(max(x, 0), largura - corte_l)) // 2 * 2
    return f"crop={corte_l}:{altura // 2 * 2}:{x}:0"


def argumentos_video(
    opcoes: OpcoesProcessamento, *, threads: int | None = None, nvenc: bool = False
) -> list[str]:
    """O codec de vídeo conforme a qualidade escolhida (x264, ou NVENC se pedido e disponível)."""
    preset = opcoes.preset_qualidade()
    if nvenc:
        args = ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "vbr", "-cq", str(preset.crf)]
        args += ["-b:v", "0", "-profile:v", "high"]
    else:
        args = ["-c:v", "libx264", "-preset", preset.preset, "-crf", str(preset.crf)]
        args += ["-profile:v", "high"]
    args += ["-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709"]
    args += ["-colorspace", "bt709", "-color_range", "tv"]
    if threads:
        args += ["-threads", str(threads)]
    return args


def argumentos_audio() -> list[str]:
    return ["-c:a", "aac", "-b:a", BITRATE_AUDIO, "-ar", str(TAXA_AUDIO), "-ac", "2"]


_GANHO_DUAL_MONO = "0.7071067811865476"  # −3,01 dB: metade da potência em cada canal


def filtro_audio_estereo(canais: int | None, *, compensar_mono: bool = False) -> str:
    """Leva qualquer áudio a estéreo de 48 kHz; mono vira dois canais iguais.

    Dois canais iguais soam 3 dB mais altos na medição de loudness (BS.1770) do que o mono de
    onde saíram. Se o volume do mono JÁ foi ajustado para um alvo (normalizar volume), use
    `compensar_mono=True`: cada canal recebe −3 dB e o estéreo entregue fica no alvo.
    """
    base = f"aresample={TAXA_AUDIO}"
    if canais == 1:
        ganho = f"{_GANHO_DUAL_MONO}*" if compensar_mono else ""
        return f"{base},pan=stereo|c0={ganho}c0|c1={ganho}c0"
    return f"{base},aformat=channel_layouts=stereo"


def numero_ffmpeg(valor: float) -> str:
    """Segundos em texto para dentro de um filtro (ponto decimal, sem notação científica)."""
    texto = f"{valor:.6f}".rstrip("0").rstrip(".")
    return texto or "0"


# Tempo relativo de um vídeo de ~1 h (HDR→SDR incluído) em cada preset do x264, medido nesta
# máquina e dividido por ~2 para ficar na mesma escala dos pesos das outras etapas.
_PESO_POR_PRESET = {
    "ultrafast": 25.0, "superfast": 26.0, "veryfast": 29.0, "faster": 31.0, "fast": 33.0,
    "medium": 36.5, "slow": 49.0, "slower": 75.0, "veryslow": 123.0,
}  # fmt: skip


def peso_de_encode(opcoes: OpcoesProcessamento) -> float:
    """Quanto a renderização pesa no progresso total: depende da qualidade escolhida."""
    return _PESO_POR_PRESET.get(opcoes.preset_qualidade().preset, 36.5)


# Bitrate medido nesta máquina com o sermão real (1080p, x264 veryslow, CRF 16): 5,5 Mb/s para
# o vídeo horizontal; o vertical (recorte ampliado, com legenda) mede 9,4 Mb/s. Cada +6 de
# CRF corta o bitrate pela metade.
MBPS_EM_CRF16 = 5.5
MBPS_EM_CRF16_VERTICAL = 10.0


def estimar_tamanho_bytes(duracao_s: float, crf: int, *, vertical: bool = False) -> int:
    """Quanto um vídeo desta duração ocupa em disco com esse CRF (ordem de grandeza)."""
    base = MBPS_EM_CRF16_VERTICAL if vertical else MBPS_EM_CRF16
    return int(duracao_s * base * 2 ** ((16 - crf) / 6) * 1e6 / 8)
