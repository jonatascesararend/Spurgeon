"""Quadros de exemplo do vídeo escolhido, partilhados pelas prévias (corte, zoom e tratamento).

Cada quadro é extraído uma vez, em segundo plano, já em SDR, e fica numa pasta temporária. A tela
pede por instante (em segundos do vídeo original) e recebe de volta o arquivo `.nut` (que o FFmpeg
lê para aplicar o tratamento) e a imagem para mostrar.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from spurgeon.core.erros import ErroFFmpeg
from spurgeon.core.ffmpeg import InfoMidia
from spurgeon.render import previa
from spurgeon.ui.assincrono import Tarefas

#: o quadro de exemplo usado por padrão pelo zoom e pelo tratamento (fração da parte usada)
POSICOES_DE_EXEMPLO = (0.25, 0.5, 0.75, 0.1, 0.9)


class QuadroDeExemplo:
    """Um quadro extraído: onde está o `.nut` e a imagem original (RGB) para mostrar."""

    def __init__(self, instante_s: float, arquivo: Path, imagem: Image.Image):
        self.instante_s = instante_s
        self.arquivo = arquivo
        self.imagem = imagem


class FonteDeQuadros:
    def __init__(self, tarefas: Tarefas, pasta: Path | None = None):
        self._tarefas = tarefas
        self._pasta_base = pasta or Path(tempfile.gettempdir()) / "spurgeon_previas"
        self._pasta: Path | None = None
        self.video: Path | None = None
        self.info: InfoMidia | None = None
        self._janela: tuple[float, float | None] = (0.0, None)
        self._cache: dict[float, QuadroDeExemplo] = {}
        self._trava = threading.Lock()
        self._ouvintes: list[Callable[[], None]] = []

    # --- o vídeo ------------------------------------------------------------------------------
    def ao_mudar_o_video(self, ouvinte: Callable[[], None]) -> None:
        self._ouvintes.append(ouvinte)

    @property
    def pronta(self) -> bool:
        return self.video is not None and self.info is not None and self.info.tem_video

    def definir_video(self, video: Path | None, info: InfoMidia | None) -> None:
        """Troca o vídeo (ou o esquece, com `None`); os quadros guardados não valem mais."""
        self.video, self.info = video, info
        self._cache.clear()
        self._pasta = None
        if video is not None and info is not None:
            marca = hashlib.sha1(f"{video}|{info.duracao_s}".encode()).hexdigest()[:10]
            self._pasta = self._pasta_base / marca
        for ouvinte in self._ouvintes:
            ouvinte()

    def definir_janela(self, inicio_s: float, fim_s: float | None) -> None:
        """O trecho usado do vídeo: os quadros de exemplo saem de dentro dele."""
        self._janela = (max(0.0, inicio_s), fim_s or None)

    def janela_util(self) -> tuple[float, float]:
        duracao = self.info.duracao_s if self.info else 0.0
        inicio, fim = self._janela
        fim = min(fim, duracao) if fim else duracao
        return min(inicio, fim), fim

    def instante_de_exemplo(self, indice: int = 0) -> float:
        inicio, fim = self.janela_util()
        fracao = POSICOES_DE_EXEMPLO[indice % len(POSICOES_DE_EXEMPLO)]
        return round(inicio + (fim - inicio) * fracao, 2)

    def caminho_temporario(self, nome: str) -> Path:
        """Um arquivo de trabalho ao lado dos quadros do vídeo atual."""
        pasta = self._pasta or self._pasta_base
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta / nome

    def limpar(self) -> None:
        """Apaga os quadros temporários (ao fechar o aplicativo)."""
        shutil.rmtree(self._pasta_base, ignore_errors=True)

    # --- extração -----------------------------------------------------------------------------
    def _extrair(self, instante_s: float) -> QuadroDeExemplo:
        """Uma extração por vez: o zoom, o tratamento e o corte pedem o mesmo quadro juntos, e
        dois FFmpeg gravando o mesmo arquivo se atrapalhariam."""
        with self._trava:
            guardado = self._cache.get(instante_s)
            if guardado is not None:
                return guardado
            video, info, pasta = self.video, self.info, self._pasta
            if video is None or info is None or pasta is None:
                raise ErroFFmpeg([], 1, "nenhum vídeo escolhido")
            base = f"quadro_{round(instante_s * 100):08d}"
            nut, png = pasta / f"{base}.nut", pasta / f"{base}.png"
            if not nut.exists():
                previa.extrair_quadro(video, info, instante_s, nut)
            if not png.exists():
                previa.quadro_para_png(nut, png)
            with Image.open(png) as aberta:
                imagem = aberta.convert("RGB")
            quadro = QuadroDeExemplo(instante_s, nut, imagem)
            if self.video is video:  # o vídeo não foi trocado enquanto o quadro saía
                self._cache[instante_s] = quadro
            return quadro

    def quadro(
        self,
        instante_s: float,
        ao_pronto: Callable[[QuadroDeExemplo], None],
        ao_falhar: Callable[[Exception], None] | None = None,
        *,
        chave: str = "quadro",
    ) -> None:
        """Extrai (em segundo plano) o quadro do instante e entrega na interface."""
        if not self.pronta:
            return
        self._tarefas.rodar(lambda: self._extrair(instante_s), ao_pronto, ao_falhar, chave=chave)

    def quadros_para_sugestao(
        self,
        ao_pronto: Callable[[list[Image.Image]], None],
        ao_falhar: Callable[[Exception], None] | None = None,
    ) -> None:
        """Alguns quadros espalhados pelo trecho usado (para a sugestão automática de cor)."""
        if not self.pronta:
            return
        instantes = [self.instante_de_exemplo(i) for i in range(len(POSICOES_DE_EXEMPLO))]
        self._tarefas.rodar(
            lambda: [self._extrair(t).imagem for t in instantes],
            ao_pronto,
            ao_falhar,
            chave="sugestao",
        )
