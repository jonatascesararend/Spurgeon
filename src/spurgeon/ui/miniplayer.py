"""Mini player da prévia de um Short: imagem, som, botão de tocar e barra de posição.

Os quadros vêm de um FFmpeg que decodifica o `.mp4` da prévia para RGB cru (um processo por vez,
que se refaz a cada salto na barra); o som é o `.wav` ao lado, tocado pelo `winsound` do Windows a
partir do ponto certo. A imagem acompanha o relógio, não o contrário: se a máquina atrasa, quadros
são pulados em vez de a prévia ficar mais lenta que o som.
"""

from __future__ import annotations

import contextlib
import queue
import subprocess
import threading
import time
import tkinter as tk
import wave
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from spurgeon.core.ffmpeg import localizar_binario
from spurgeon.render.previa import (
    ALTURA_DA_PREVIA_DO_SHORT,
    FPS_DA_PREVIA_DO_SHORT,
    LARGURA_DA_PREVIA_DO_SHORT,
    PreviaDoShort,
)
from spurgeon.ui.dicas import dica

try:  # o som só existe no Windows (o aplicativo é só para ele)
    import winsound
except ImportError:  # pragma: no cover
    winsound = None  # type: ignore[assignment]

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TAMANHO_DO_QUADRO = LARGURA_DA_PREVIA_DO_SHORT * ALTURA_DA_PREVIA_DO_SHORT * 3
_INTERVALO_MS = 15
_ATRASO_DO_SALTO_MS = 160
_COR_SUAVE = "gray60"


def formatar_posicao(segundos: float) -> str:
    minutos, seg = divmod(int(max(0.0, segundos)), 60)
    return f"{minutos}:{seg:02d}"


def fatiar_wav(origem: Path, inicio_s: float, destino: Path) -> None:
    """Grava em `destino` o som de `origem` a partir de `inicio_s`."""
    with wave.open(str(origem), "rb") as entrada:
        entrada.setpos(min(int(inicio_s * entrada.getframerate()), entrada.getnframes()))
        dados = entrada.readframes(entrada.getnframes() - entrada.tell())
        with wave.open(str(destino), "wb") as saida:
            saida.setnchannels(entrada.getnchannels())
            saida.setsampwidth(entrada.getsampwidth())
            saida.setframerate(entrada.getframerate())
            saida.writeframes(dados)


class _Fluxo:
    """Um FFmpeg lendo quadros RGB de um instante em diante; `fila` recebe os `bytes`, e `None`."""

    def __init__(self, video: Path, inicio_s: float):
        self.fila: queue.Queue[bytes | None] = queue.Queue(maxsize=8)
        self._parado = threading.Event()
        self.inicio_s = inicio_s
        self._processo = subprocess.Popen(
            [
                str(localizar_binario("ffmpeg")),
                "-v",
                "error",
                "-nostdin",
                "-ss",
                f"{inicio_s:.3f}",
                "-i",
                str(video),
                "-an",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=_SEM_JANELA,
        )
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self) -> None:
        try:
            while not self._parado.is_set():
                dados = self._processo.stdout.read(_TAMANHO_DO_QUADRO)
                while 0 < len(dados) < _TAMANHO_DO_QUADRO:  # o pipe pode entregar em pedaços
                    resto = self._processo.stdout.read(_TAMANHO_DO_QUADRO - len(dados))
                    if not resto:
                        dados = b""
                        break
                    dados += resto
                if len(dados) < _TAMANHO_DO_QUADRO:
                    break
                while not self._parado.is_set():
                    try:
                        self.fila.put(dados, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(queue.Full):
                self.fila.put_nowait(None)

    def parar(self) -> None:
        self._parado.set()
        with contextlib.suppress(OSError):
            self._processo.kill()
        with contextlib.suppress(OSError, ValueError):
            self._processo.stdout.close()
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            self._processo.wait(timeout=2)


class MiniPlayer(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self._previa: PreviaDoShort | None = None
        self._fluxo: _Fluxo | None = None
        self._pendente: tuple[int, bytes] | None = None  # o próximo quadro, ainda sem hora
        self._numero = 0  # quantos quadros o fluxo atual já entregou
        self._tocando = False
        self._so_um_quadro = False  # salto com o player parado: mostra o quadro e solta o fluxo
        self._posicao_s = 0.0
        self._t0 = 0.0  # relógio (monotonic) no instante `_posicao_s` do vídeo
        self._salto: str | None = None
        self._ciclo: str | None = None
        self._arquivos_de_som: list[Path] = []
        self._contador_de_som = 0
        self._foto: ImageTk.PhotoImage | None = None
        self._arrastando = False

        self._tela = tk.Label(self, bg="#000000", borderwidth=0, highlightthickness=0)
        self._tela.pack(pady=(0, 4))
        self._var_posicao = tk.StringVar(value="")
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.pack(fill="x")
        barra.columnconfigure(1, weight=1)
        self._botao = ctk.CTkButton(
            barra, text="▶", width=38, height=28, command=self.alternar, state="disabled"
        )
        self._botao.grid(row=0, column=0, padx=(0, 8))
        dica(self._botao, "Toca ou pausa a prévia (com som, se o vídeo tiver).")
        self._barra = ctk.CTkSlider(barra, from_=0, to=1, command=self._ao_arrastar)
        self._barra.set(0)
        self._barra.grid(row=0, column=1, sticky="ew")
        self._rotulo = ctk.CTkLabel(
            barra, textvariable=self._var_posicao, text_color=_COR_SUAVE, width=82, anchor="e"
        )
        self._rotulo.grid(row=0, column=2, padx=(8, 0))
        self._barra.bind("<ButtonPress-1>", lambda _e: self._comecou_a_arrastar(), add="+")
        self._barra.bind("<ButtonRelease-1>", lambda _e: self._terminou_de_arrastar(), add="+")
        self.bind("<Destroy>", self._ao_destruir, add="+")

    # --- carga --------------------------------------------------------------------------------
    @property
    def previa(self) -> PreviaDoShort | None:
        return self._previa

    @property
    def tocando(self) -> bool:
        return self._tocando

    @property
    def posicao(self) -> float:
        return self._posicao_s

    def carregar(self, previa: PreviaDoShort) -> None:
        """Mostra o primeiro quadro da prévia e deixa tudo pronto para tocar."""
        self.parar()
        self._previa = previa
        self._posicao_s = 0.0
        if self._foto is None:
            self._foto = ImageTk.PhotoImage(
                Image.new("RGB", (LARGURA_DA_PREVIA_DO_SHORT, ALTURA_DA_PREVIA_DO_SHORT))
            )
            self._tela.configure(image=self._foto)
        self._barra.configure(to=max(0.1, previa.duracao_s))
        self._barra.set(0)
        self._botao.configure(state="normal")
        self._atualizar_rotulo()
        self._mostrar_quadro_em(0.0)

    def limpar(self) -> None:
        """Esquece a prévia (outro corte foi aberto): para o som e o FFmpeg e apaga a imagem."""
        self.parar()
        self._previa = None
        self._botao.configure(state="disabled")
        self._barra.set(0)
        self._var_posicao.set("")
        if self._foto is not None:
            self._foto.paste(
                Image.new("RGB", (LARGURA_DA_PREVIA_DO_SHORT, ALTURA_DA_PREVIA_DO_SHORT))
            )

    # --- controle -----------------------------------------------------------------------------
    def alternar(self) -> None:
        if self._previa is None:
            return
        if self._tocando:
            self.pausar()
        else:
            self.tocar()

    def tocar(self) -> None:
        if self._previa is None:
            return
        if self._posicao_s >= self._previa.duracao_s - 0.1:
            self._posicao_s = 0.0
        self._tocando = True
        self._botao.configure(text="⏸")
        self._iniciar(self._posicao_s)

    def pausar(self) -> None:
        if not self._tocando:
            return
        self._posicao_s = self._instante()
        self._tocando = False
        self._parar_fluxo()
        self._parar_som()
        self._botao.configure(text="▶")

    def parar(self) -> None:
        """Pausa e solta tudo (som, FFmpeg e temporizadores)."""
        self._tocando = False
        self._so_um_quadro = False
        self._parar_fluxo()
        self._parar_som()
        for nome in (self._salto, self._ciclo):
            if nome is not None:
                with contextlib.suppress(tk.TclError):
                    self.after_cancel(nome)
        self._salto = self._ciclo = None
        with contextlib.suppress(tk.TclError):
            self._botao.configure(text="▶")

    def ir_para(self, segundos: float) -> None:
        """Salta para o instante: continua tocando se estava tocando, senão mostra o quadro."""
        if self._previa is None:
            return
        self._posicao_s = min(max(0.0, segundos), self._previa.duracao_s)
        self._atualizar_rotulo()
        if self._tocando:
            self._iniciar(self._posicao_s)
        else:
            self._mostrar_quadro_em(self._posicao_s)

    # --- barra --------------------------------------------------------------------------------
    def _comecou_a_arrastar(self) -> None:
        self._arrastando = True

    def _terminou_de_arrastar(self) -> None:
        self._arrastando = False

    def _ao_arrastar(self, valor: float) -> None:
        if self._previa is None:
            return
        self._posicao_s = float(valor)
        self._atualizar_rotulo()
        if self._salto is not None:
            self.after_cancel(self._salto)
        self._salto = self.after(_ATRASO_DO_SALTO_MS, lambda: self.ir_para(self._posicao_s))

    def _atualizar_rotulo(self) -> None:
        if self._previa is None:
            return
        self._var_posicao.set(
            f"{formatar_posicao(self._posicao_s)} / {formatar_posicao(self._previa.duracao_s)}"
        )

    # --- quadros e som ------------------------------------------------------------------------
    def _instante(self) -> float:
        """Onde o vídeo está agora, pelo relógio (e não pelo último quadro mostrado)."""
        if not self._tocando or self._previa is None:
            return self._posicao_s
        return min(self._previa.duracao_s, self._posicao_s + (time.monotonic() - self._t0))

    def _iniciar(self, inicio_s: float) -> None:
        """Recomeça a ler quadros (e o som, se estiver tocando) a partir de `inicio_s`."""
        assert self._previa is not None
        self._parar_fluxo()
        self._parar_som()
        self._posicao_s = inicio_s
        self._so_um_quadro = False
        self._pendente = None
        self._numero = 0
        self._fluxo = _Fluxo(self._previa.video, inicio_s)
        if self._tocando:
            self._tocar_som(inicio_s)
        self._t0 = time.monotonic()
        if self._ciclo is None:
            self._ciclo = self.after(_INTERVALO_MS, self._passo)

    def _mostrar_quadro_em(self, inicio_s: float) -> None:
        """Com o player parado: um quadro do instante, sem tocar nada."""
        assert self._previa is not None
        self._so_um_quadro = True
        self._parar_fluxo()
        self._pendente = None
        self._numero = 0
        self._fluxo = _Fluxo(self._previa.video, inicio_s)
        if self._ciclo is None:
            self._ciclo = self.after(_INTERVALO_MS, self._passo)

    def _passo(self) -> None:
        self._ciclo = None
        fluxo = self._fluxo
        if fluxo is None:
            return
        proximo_ciclo = True
        if self._so_um_quadro and not self._tocando:
            try:
                dados = fluxo.fila.get_nowait()
            except queue.Empty:
                dados = b""
            if dados is None:
                self._so_um_quadro = False
                self._parar_fluxo()
                proximo_ciclo = False
            elif dados:
                self._pintar(dados)
                self._so_um_quadro = False
                self._parar_fluxo()
                proximo_ciclo = False
        elif self._tocando:
            self._avancar(fluxo)
            proximo_ciclo = self._fluxo is not None
        if proximo_ciclo:
            with contextlib.suppress(tk.TclError):
                self._ciclo = self.after(_INTERVALO_MS, self._passo)

    def _avancar(self, fluxo: _Fluxo) -> None:
        """Mostra o quadro que já é a hora de ver (pulando os que ficaram para trás)."""
        agora = self._instante()
        ultimo: bytes | None = None
        acabou = False
        while True:
            if self._pendente is None:
                try:
                    dados = fluxo.fila.get_nowait()
                except queue.Empty:
                    break
                if dados is None:
                    acabou = True
                    break
                self._pendente = (self._numero, dados)
                self._numero += 1
            numero, dados = self._pendente
            if fluxo.inicio_s + numero / FPS_DA_PREVIA_DO_SHORT > agora:
                break
            ultimo = dados
            self._pendente = None
        if ultimo is not None:
            self._pintar(ultimo)
        if not self._arrastando:
            self._barra.set(agora)
            self._var_posicao.set(
                f"{formatar_posicao(agora)} / {formatar_posicao(self._previa.duracao_s)}"
            )
        if acabou or agora >= self._previa.duracao_s:
            self._terminou()

    def _terminou(self) -> None:
        self._tocando = False
        self._posicao_s = 0.0
        self._parar_fluxo()
        self._parar_som()
        self._botao.configure(text="▶")
        self._barra.set(0)
        self._atualizar_rotulo()

    def _pintar(self, dados: bytes) -> None:
        if self._foto is None:
            return
        self._foto.paste(
            Image.frombytes("RGB", (LARGURA_DA_PREVIA_DO_SHORT, ALTURA_DA_PREVIA_DO_SHORT), dados)
        )

    def _parar_fluxo(self) -> None:
        fluxo, self._fluxo = self._fluxo, None
        self._pendente = None
        if fluxo is not None:
            fluxo.parar()

    def _tocar_som(self, inicio_s: float) -> None:
        previa = self._previa
        if winsound is None or previa is None or previa.audio is None:
            return
        self._contador_de_som += 1
        arquivo = previa.audio.with_name(f"{previa.audio.stem}_tocando_{self._contador_de_som}.wav")
        try:
            fatiar_wav(previa.audio, inicio_s, arquivo)
            self._arquivos_de_som.append(arquivo)
            winsound.PlaySound(str(arquivo), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except (OSError, RuntimeError, wave.Error):
            pass  # sem som, a imagem continua

    def _parar_som(self) -> None:
        if winsound is not None:
            with contextlib.suppress(RuntimeError):
                winsound.PlaySound(None, winsound.SND_PURGE)
        for arquivo in self._arquivos_de_som:
            arquivo.unlink(missing_ok=True)
        self._arquivos_de_som.clear()

    def _ao_destruir(self, evento) -> None:
        if evento.widget is self:
            self._tocando = False
            self._parar_fluxo()
            self._parar_som()
