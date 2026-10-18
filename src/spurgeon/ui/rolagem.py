"""Quadro rolável com a roda do mouse mais rápida e suave.

O `CTkScrollableFrame` anda 20 px por giro da roda, de uma vez, e em telas cheias de widgets isso
deixa rastros na imagem (o texto aparece repetido enquanto se rola). Aqui cada giro soma uma
distância-alvo e a tela anda até ela em passos curtos, desacelerando no fim; entre um passo e outro
o Tk termina de desenhar, e os rastros somem.
"""

from __future__ import annotations

import contextlib
import math
import sys
import time
import tkinter as tk

import customtkinter as ctk

PIXELS_POR_GIRO = 80  # o padrão do CTkScrollableFrame é 20
CONSTANTE_DE_TEMPO_S = 0.07  # quanto menor, mais seca a parada
INTERVALO_MS = 12
PASSO_MAXIMO_PX = 70  # por quadro: mais que isso o Tk deixa rastros
PASSO_DO_ARRASTO_PX = 160  # idem, ao arrastar a barra (o arrasto é mais rápido que a roda)
_LIMITE_DE_ACUMULO_PX = 4000


class QuadroRolavel(ctk.CTkScrollableFrame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._falta_px = 0.0  # quanto ainda falta rolar (positivo = para baixo)
        self._resto_px = 0.0  # fração de pixel que ficou do passo anterior
        self._ultimo_passo = 0.0
        self._animacao: str | None = None
        self._alvo_do_arrasto = 0.0
        self._arrasto: str | None = None
        # arrastar a barra manda saltos grandes de uma vez; aqui eles viram passos curtos
        self._scrollbar.configure(command=self._arrastar)

    # --- barra de rolagem
    def _arrastar(self, *args) -> None:
        """O comando da barra: `moveto f` (arrasto) anda em passos curtos; o resto é padrão."""
        if args and args[0] == "moveto":
            self._alvo_do_arrasto = float(args[1])
            if self._arrasto is None:
                self._arrasto = self.after(INTERVALO_MS, self._seguir_o_arrasto)
        else:
            self._parent_canvas.yview(*args)

    def _seguir_o_arrasto(self) -> None:
        self._arrasto = None
        canvas = self._parent_canvas
        topo, base = canvas.yview()
        visivel = base - topo
        if visivel >= 1.0:
            return
        total_px = max(1.0, canvas.winfo_height() / visivel)
        alvo = min(max(self._alvo_do_arrasto, 0.0), 1.0 - visivel)
        falta = alvo - topo
        passo = max(-PASSO_DO_ARRASTO_PX / total_px, min(PASSO_DO_ARRASTO_PX / total_px, falta))
        with contextlib.suppress(tk.TclError):
            canvas.yview_moveto(topo + passo)
            canvas.update_idletasks()  # desenha este passo antes do próximo
            if abs(falta - passo) * total_px >= 1:
                self._arrasto = self.after(INTERVALO_MS, self._seguir_o_arrasto)

    # --- roda do mouse ------------------------------------------------------------------------
    def _mouse_wheel_all(self, event) -> None:  # nome do CTkScrollableFrame
        if not self._check_if_valid_scroll(event.widget):
            return
        if self._shift_pressed or not sys.platform.startswith("win"):
            super()._mouse_wheel_all(event)  # horizontal, ou outro sistema: o comportamento padrão
            return
        self.rolar_giro(-event.delta / 120)

    def rolar_giro(self, giros: float) -> None:
        """Soma `giros` giros da roda (positivo = para baixo) à distância que falta rolar."""
        if self._parent_canvas.yview() == (0.0, 1.0):
            return
        if giros * self._falta_px < 0:  # mudou de sentido: descarta o que faltava
            self._falta_px = 0.0
        limite = _LIMITE_DE_ACUMULO_PX
        self._falta_px = max(-limite, min(limite, self._falta_px + giros * PIXELS_POR_GIRO))
        if self._animacao is None:
            self._ultimo_passo = time.monotonic()
            self._animacao = self.after(INTERVALO_MS, self._passo)

    def _passo(self) -> None:
        self._animacao = None
        agora = time.monotonic()
        dt = min(0.05, max(0.001, agora - self._ultimo_passo))
        self._ultimo_passo = agora
        andar = self._falta_px * (1 - math.exp(-dt / CONSTANTE_DE_TEMPO_S)) + self._resto_px
        andar = max(-PASSO_MAXIMO_PX, min(PASSO_MAXIMO_PX, andar))
        inteiro = int(andar)  # em direção ao zero
        self._resto_px = andar - inteiro
        if inteiro == 0 and abs(self._falta_px) < 1:
            self._falta_px = self._resto_px = 0.0
            return
        if inteiro:
            antes = self._parent_canvas.yview()
            with contextlib.suppress(tk.TclError):
                self._parent_canvas.yview_scroll(inteiro, "units")
                self._parent_canvas.update_idletasks()  # desenha este passo antes do próximo
            if self._parent_canvas.yview() == antes:  # chegou ao topo ou ao fim
                self._falta_px = self._resto_px = 0.0
                return
            self._falta_px -= inteiro
        with contextlib.suppress(tk.TclError):
            self._animacao = self.after(INTERVALO_MS, self._passo)

    def destroy(self) -> None:
        if self._arrasto is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._arrasto)
            self._arrasto = None
        if self._animacao is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._animacao)
            self._animacao = None
        super().destroy()
