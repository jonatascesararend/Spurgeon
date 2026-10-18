"""Quadros e trechos curtos do vídeo para a tela: onde o corte começa, o zoom e o tratamento.

Nada aqui passa pelo pipeline: são pequenos comandos do FFmpeg que a interface chama em segundo
plano. Os quadros saem já em SDR (a mesma conversão do render) e ficam guardados em `.nut` com
yuv420p sem perdas, o formato em que o tratamento de imagem roda de verdade; assim a prévia do
"antes e depois" usa os mesmos filtros e a mesma matemática do vídeo final.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.ffmpeg import InfoMidia, executar_ffmpeg
from spurgeon.core.sinais import SinalControle
from spurgeon.render import filtros as f
from spurgeon.render.imagem import (
    filtro_ampliar_zoom,
    filtro_recorte_do_zoom,
    filtro_tratamento,
)
from spurgeon.render.legenda import (
    EstiloLegenda,
    PalavraDaLegenda,
    montar_ass,
    palavras_do_short,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import palavras_com_a_legenda
from spurgeon.transcricao.formato import Palavra

LARGURA_DO_QUADRO = 960
LARGURA_DA_PREVIA = 640
DURACAO_DA_PREVIA_S = 15.0
_COR_BT709 = [
    "-color_primaries", "bt709", "-color_trc", "bt709",
    "-colorspace", "bt709", "-color_range", "tv",
]  # fmt: skip


def _largura_par(largura: int) -> int:
    return max(2, largura // 2 * 2)


def instantes_de_exemplo(duracao_s: float, quantos: int = 5) -> list[float]:
    """Instantes espalhados pelo vídeo (sem as pontas, onde costuma haver tela preta)."""
    if duracao_s <= 0:
        return [0.0]
    return [round(duracao_s * (i + 1) / (quantos + 1), 2) for i in range(quantos)]


def extrair_quadro(
    video: Path,
    info: InfoMidia,
    instante_s: float,
    destino: Path,
    *,
    largura: int = LARGURA_DO_QUADRO,
) -> Path:
    """Um quadro em SDR (BT.709, yuv420p), reduzido para `largura` px, em `destino` (`.nut`)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial.nut")
    filtro = f"{f.cadeia_para_sdr(info)},scale={_largura_par(largura)}:-2:flags=lanczos,setsar=1"
    executar_ffmpeg(
        ["-y", "-ss", f.numero_ffmpeg(max(0.0, instante_s)), "-i", video, "-frames:v", "1",
         "-an", "-vf", filtro, "-c:v", "rawvideo", "-pix_fmt", "yuv420p", *_COR_BT709, parcial],
    )  # fmt: skip
    parcial.replace(destino)
    return destino


def quadro_para_png(
    quadro: Path, destino: Path, *, tratamento: TratamentoImagem | None = None
) -> Path:
    """Converte o quadro `.nut` em PNG, passando antes pelo tratamento de imagem (se houver)."""
    filtros = []
    cadeia = filtro_tratamento(tratamento) if tratamento is not None else None
    if cadeia:
        filtros.append(cadeia)
    filtros.append("format=rgb24")
    parcial = destino.with_name(destino.name + ".parcial.png")
    executar_ffmpeg(
        ["-y", "-i", quadro, "-frames:v", "1", "-vf", ",".join(filtros), "-f", "image2", parcial],
    )
    parcial.replace(destino)
    return destino


#: o que a legenda de mentira diz na prévia do Short: duas linhas, como as de verdade
PALAVRAS_DA_LEGENDA_DE_EXEMPLO = ("As", "legendas", "ficarão", "posicionadas", "aqui")


def preparar_fontes_da_previa(pasta_fontes: Path, pasta_de_trabalho: Path) -> None:
    """Copia as fontes para `pasta_de_trabalho/fontes`, de onde o filtro `ass` as lê (uma vez)."""
    destino = pasta_de_trabalho / "fontes"
    destino.mkdir(parents=True, exist_ok=True)
    for fonte in pasta_fontes.glob("*"):
        if fonte.suffix.lower() in {".ttf", ".otf"} and not (destino / fonte.name).exists():
            shutil.copy2(fonte, destino / fonte.name)


def previa_do_short(
    quadro: Path,
    info: InfoMidia,
    tamanho_do_quadro: tuple[int, int],
    destino: Path,
    *,
    deslocamento_pct: float,
    posicao_legenda: float,
    enquadramento: Enquadramento | None = None,
    zoom_do_short: Enquadramento | None = None,
    tratamento: TratamentoImagem | None = None,
    pasta_fontes: Path | None = None,
) -> Path:
    """Como o Short fica, em PNG de 1080×1920, a partir do quadro `.nut` (já em SDR).

    É o filtro do render (`recorte_vertical`, tratamento, ampliação e o `ass` da legenda), só que
    sem a conversão para SDR, que o quadro já passou. A legenda é o texto de exemplo, montada pelo
    mesmo `montar_ass` dos Shorts de verdade, então tamanho, contorno e altura são os reais.
    `pasta_fontes` é onde está a Montserrat; sem ela o FFmpeg usa uma fonte do sistema.
    `tamanho_do_quadro` (largura, altura) é o do `.nut`, reduzido: os recortes saem em proporção.
    """
    pasta = destino.parent
    pasta.mkdir(parents=True, exist_ok=True)
    medidas = replace(info, largura=tamanho_do_quadro[0], altura=tamanho_do_quadro[1])
    etapas = [
        "setpts=PTS-STARTPTS",
        f.recorte_vertical(medidas, deslocamento_pct, enquadramento, zoom_do_short),
    ]
    cor = filtro_tratamento(tratamento) if tratamento is not None else None
    if cor:
        etapas.append(cor)
    etapas += [f"scale={f.LARGURA_SHORT}:{f.ALTURA_SHORT}:flags=lanczos", "setsar=1"]
    if pasta_fontes is not None:
        preparar_fontes_da_previa(pasta_fontes, pasta)
    palavras = [
        PalavraDaLegenda(texto, float(i), float(i + 1))
        for i, texto in enumerate(PALAVRAS_DA_LEGENDA_DE_EXEMPLO)
    ]
    ass = pasta / f"{destino.stem}.ass"
    ass.write_text(
        montar_ass(palavras, EstiloLegenda(posicao_vertical=posicao_legenda)), encoding="utf-8"
    )
    etapas.append(f"ass={ass.name}:fontsdir=fontes")
    etapas.append("format=rgb24")
    parcial = destino.with_name(destino.name + ".parcial.png")
    try:
        executar_ffmpeg(
            [
                "-y",
                "-i",
                quadro,
                "-frames:v",
                "1",
                "-vf",
                ",".join(etapas),
                "-f",
                "image2",
                parcial,
            ],
            cwd=pasta,
        )
        parcial.replace(destino)
    finally:
        ass.unlink(missing_ok=True)
        parcial.unlink(missing_ok=True)
    return destino


LARGURA_DA_PREVIA_DO_SHORT = 270  # 9:16 em miniatura: um quarto do Short de verdade
ALTURA_DA_PREVIA_DO_SHORT = 480
FPS_DA_PREVIA_DO_SHORT = 12
AUDIO_DA_PREVIA_HZ = 22050


@dataclass(frozen=True)
class PreviaDoShort:
    video: Path  # mp4 de baixa qualidade, sem som
    audio: Path | None  # wav mono, o som do trecho (None se o vídeo não tem áudio)
    duracao_s: float


def previa_do_corte(
    video: Path,
    info: InfoMidia,
    corte: Corte,
    palavras: Sequence[Palavra],
    destino: Path,
    *,
    deslocamento_pct: float,
    posicao_legenda: float,
    com_legenda: bool,
    enquadramento: Enquadramento | None = None,
    zoom_do_short: Enquadramento | None = None,
    tratamento: TratamentoImagem | None = None,
    pasta_fontes: Path | None = None,
    ao_progresso: Callable[[float], None] | None = None,
    cancelar: SinalControle | None = None,
) -> PreviaDoShort:
    """O Short em miniatura (270×480, 12 quadros por segundo, qualidade bem baixa).

    É a mesma receita do render (recorte 9:16, zoom, HDR→SDR, tratamento e a legenda do `montar_ass`
    com a legenda corrigida), só que o vídeo é reduzido logo depois do recorte, antes de converter
    a cor: assim a conversão trabalha em 270×480 e a prévia sai em uma fração do tempo do Short.
    O som é o do vídeo original (sem limpeza nem normalização) e vai num `.wav` ao lado do `.mp4`.
    A marca d'água não entra.
    """
    pasta = destino.parent
    pasta.mkdir(parents=True, exist_ok=True)
    duracao = max(0.1, corte.duracao)
    etapas = [
        f"fps={FPS_DA_PREVIA_DO_SHORT}",
        f.recorte_vertical(info, deslocamento_pct, enquadramento, zoom_do_short),
        f"scale={LARGURA_DA_PREVIA_DO_SHORT}:{ALTURA_DA_PREVIA_DO_SHORT}:flags=fast_bilinear",
        f.cadeia_para_sdr(info),
    ]
    cor = filtro_tratamento(tratamento) if tratamento is not None else None
    if cor:
        etapas.append(cor)
    etapas.append("setsar=1")
    ass = pasta / f"{destino.stem}.ass"
    faladas = palavras_do_short(palavras_com_a_legenda(palavras, corte), corte.inicio, corte.fim)
    if com_legenda and faladas:
        if pasta_fontes is not None:
            preparar_fontes_da_previa(pasta_fontes, pasta)
        ass.write_text(
            montar_ass(faladas, EstiloLegenda(posicao_vertical=posicao_legenda)), encoding="utf-8"
        )
        etapas.append(f"ass={ass.name}:fontsdir=fontes")
    etapas.append("format=yuv420p")

    parcial = destino.with_name(destino.name + ".parcial")
    audio = destino.with_suffix(".wav")
    parcial_audio = audio.with_name(audio.name + ".parcial")
    saidas: list[str | Path] = [
        "-map", "0:v:0", "-vf", ",".join(etapas), "-an", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "34", "-pix_fmt", "yuv420p", *_COR_BT709, "-movflags", "+faststart", "-f", "mp4",
        parcial,
    ]  # fmt: skip
    if info.tem_audio:
        saidas += [
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(AUDIO_DA_PREVIA_HZ),
            "-c:a", "pcm_s16le", "-f", "wav", parcial_audio,
        ]  # fmt: skip
    try:
        executar_ffmpeg(
            ["-y", "-ss", f.numero_ffmpeg(corte.inicio), "-t", f.numero_ffmpeg(duracao),
             "-i", video, *saidas],
            duracao_s=duracao, ao_progresso=ao_progresso, cancelar=cancelar, cwd=pasta,
        )  # fmt: skip
        parcial.replace(destino)
        if info.tem_audio:
            parcial_audio.replace(audio)
        else:
            audio.unlink(missing_ok=True)
    finally:
        ass.unlink(missing_ok=True)
        parcial.unlink(missing_ok=True)
        parcial_audio.unlink(missing_ok=True)
    return PreviaDoShort(destino, audio if info.tem_audio else None, duracao)


def gerar_previa(
    video: Path,
    info: InfoMidia,
    inicio_s: float,
    destino: Path,
    *,
    duracao_s: float = DURACAO_DA_PREVIA_S,
    enquadramento: Enquadramento | None = None,
    tratamento: TratamentoImagem | None = None,
    largura: int = LARGURA_DA_PREVIA,
) -> Path:
    """Um trecho do vídeo (imagem e som) em baixa resolução, para ver onde o corte cai.

    Com `enquadramento` e `tratamento` o trecho já sai com o zoom e a cor que o vídeo terá.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    largura_origem, altura_origem = (info.largura or 1920), (info.altura or 1080)
    enquadramento = enquadramento or Enquadramento()
    etapas = [f"fps={f.texto_fps(f.fps_constante(info.fps))}"]
    recorte = filtro_recorte_do_zoom(largura_origem, altura_origem, enquadramento)
    if recorte:
        etapas.append(recorte)
    etapas.append(f.cadeia_para_sdr(info))
    ampliar = filtro_ampliar_zoom(largura_origem, altura_origem, enquadramento)
    if ampliar:
        etapas.append(ampliar)
    cor = filtro_tratamento(tratamento) if tratamento is not None else None
    if cor:
        etapas.append(cor)
    etapas.append(f"scale={_largura_par(largura)}:-2:flags=bilinear,setsar=1,format=yuv420p")
    parcial = destino.with_name(destino.name + ".parcial.mp4")
    audio = ["-c:a", "aac", "-b:a", "96k", "-ac", "2"] if info.tem_audio else ["-an"]
    executar_ffmpeg(
        ["-y", "-ss", f.numero_ffmpeg(max(0.0, inicio_s)), "-t", f.numero_ffmpeg(duracao_s),
         "-i", video, "-vf", ",".join(etapas), "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "26", "-pix_fmt", "yuv420p", *audio, "-movflags", "+faststart", "-f", "mp4",
         parcial],
    )  # fmt: skip
    parcial.replace(destino)
    return destino
