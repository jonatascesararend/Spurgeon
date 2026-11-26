"""Um pedaço do som do sermão, na linha do tempo do vídeo original, para o ajuste fino da legenda.

O ajuste fino acerta o tempo de cada palavra em segundos do vídeo original; para isso a pessoa ouve
o trecho como ele é no sermão (com o que os saltos tiram e sem encurtar pausas): o segundo 0 do
arquivo é o `de` pedido. Para o PC sai em `.wav` (mono, 16 bits), que o `winsound` toca; devagar
(`velocidade` < 1), o `atempo` estica o som sem mudar a voz de tom. Para o celular, `.m4a` (AAC, bem
menor pela rede), e quem deixa mais devagar é o próprio navegador.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from spurgeon.core.estado import carregar_estado
from spurgeon.core.ffmpeg import executar_ffmpeg
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render import filtros as f

DURACAO_MAXIMA_S = 240.0  # um Short tem até 3 minutos; sobra para a folga das pontas
VELOCIDADES = (1.0, 0.75, 0.5)
TAXA = 44100


class SemSom(LookupError):
    """O áudio tratado e o vídeo original não estão mais no PC."""


def fonte_do_som(pastas: PastasProjeto) -> Path:
    """O áudio tratado do projeto, senão o vídeo original."""
    estado = carregar_estado(pastas.raiz)
    if estado is not None:
        tratado = pastas.audio_final(estado.opcoes)
        if tratado.is_file():
            return tratado
        if estado.opcoes.trilha_externa is not None:  # o som de fora substitui o do vídeo
            return estado.opcoes.trilha_externa
        original = Path(estado.entrada.caminho)
        if original.is_file():
            return original
    raise SemSom(
        "O som do sermão não está mais no PC (o vídeo original e o áudio tratado foram "
        "apagados ou movidos): o ajuste fino precisa dele para tocar as palavras."
    )


def argumentos(
    fonte: Path, de: float, ate: float, saida: Path, velocidade: float, formato: str = "wav"
) -> list[str]:
    filtro = "aformat=channel_layouts=mono"
    if velocidade != 1.0:
        filtro += f",atempo={f.numero_ffmpeg(velocidade)}"
    codificacao = (
        ["-c:a", "pcm_s16le", "-f", "wav"]
        if formato == "wav"
        else ["-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-f", "mp4"]
    )
    return [
        "-y", "-ss", f.numero_ffmpeg(max(0.0, de)), "-t", f.numero_ffmpeg(ate - max(0.0, de)),
        "-i", str(fonte), "-vn", "-af", filtro, "-ar", str(TAXA), *codificacao, str(saida),
    ]  # fmt: skip


def wav_do_trecho(
    fonte: Path,
    de: float,
    ate: float,
    saida: Path,
    *,
    velocidade: float = 1.0,
    formato: str = "wav",
    rodar: Callable[..., object] | None = None,
) -> Path:
    """Grava em `saida` o som de `de` a `ate` (segundos do vídeo original) e devolve `saida`."""
    if velocidade not in VELOCIDADES:
        raise ValueError(f"Velocidade inválida: {velocidade}")
    de = max(0.0, de)
    if not ate > de or ate - de > DURACAO_MAXIMA_S:
        raise ValueError("Trecho de áudio inválido.")
    saida.parent.mkdir(parents=True, exist_ok=True)
    parcial = saida.with_name(saida.name + ".parcial")
    (rodar or executar_ffmpeg)(
        argumentos(fonte, de, ate, parcial, velocidade, formato), duracao_s=ate - de
    )
    parcial.replace(saida)
    return saida
