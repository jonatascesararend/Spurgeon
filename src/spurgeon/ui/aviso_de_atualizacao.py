"""O aviso de atualização, no alto da janela: uma faixa amarela que pisca enquanto há versão nova.

A janela chama `mostrar(info)` quando a consulta ao GitHub acha uma versão mais nova. A faixa pisca
(entre o amarelo vivo e um âmbar escuro) até a pessoa clicar em "Atualizar agora" ou dispensar com ✕
(volta na próxima vez que o programa abrir). Durante a instalação ela fica fixa, com o andamento.
"""

from __future__ import annotations

import contextlib
import tkinter
from collections.abc import Callable

import customtkinter as ctk

from spurgeon.core.atualizacao import InfoAtualizacao
from spurgeon.ui import dicas

INTERVALO_DO_PISCAR_MS = 600
#: (fundo, texto) de cada fase do piscar e do botão
_ACESO = ("#ffd43b", "#1b1b1b")
_APAGADO = ("#8a6a00", "#fff4c2")
_COR_DO_BOTAO = ("#1b1b1b", "#ffd43b", "#333333")  # fundo, texto, fundo sob o mouse
_DICA_DO_AVISO = (
    "Há uma versão nova do Studio Spurgeon no GitHub. Clique para baixar e instalar: seus "
    "projetos, modelos e configurações não são mexidos, e o programa reabre sozinho."
)


class AvisoDeAtualizacao(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        ao_atualizar: Callable[[InfoAtualizacao], None],
        ao_dispensar: Callable[[], None] | None = None,
        intervalo_ms: int = INTERVALO_DO_PISCAR_MS,
    ):
        super().__init__(master, corner_radius=6, fg_color=_ACESO[0], height=36)
        self._ao_atualizar = ao_atualizar
        self._ao_dispensar = ao_dispensar
        self._intervalo_ms = intervalo_ms
        self._info: InfoAtualizacao | None = None
        self._piscando = False
        self._aceso = True
        self._depois: str | None = None
        self.rotulo = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            text_color=_ACESO[1],
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.rotulo.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=4)
        self.botao = ctk.CTkButton(
            self, text="Atualizar agora", width=130, height=26, command=self._clicou,
            fg_color=_COR_DO_BOTAO[0], text_color=_COR_DO_BOTAO[1], hover_color=_COR_DO_BOTAO[2],
        )  # fmt: skip
        self.botao.pack(side="left", padx=4, pady=4)
        self.fechar = ctk.CTkButton(
            self, text="✕", width=28, height=26, command=self.dispensar, fg_color="transparent",
            text_color=_ACESO[1], hover_color="#e0b800",
        )  # fmt: skip
        self.fechar.pack(side="left", padx=(0, 6), pady=4)
        with contextlib.suppress(tkinter.TclError):
            self.rotulo.configure(cursor="hand2")
        self.rotulo.bind("<Button-1>", lambda _e: self._clicou())
        dicas.dica(self.rotulo, _DICA_DO_AVISO)
        dicas.dica(self.botao, "Baixa a versão nova do GitHub, instala e reabre o programa.")
        dicas.dica(self.fechar, "Esconder este aviso (ele volta na próxima vez que abrir).")

    # --- estado -------------------------------------------------------------------------------
    @property
    def info(self) -> InfoAtualizacao | None:
        return self._info

    @property
    def piscando(self) -> bool:
        return self._piscando

    def mostrar(self, info: InfoAtualizacao) -> None:
        """Mostra o aviso da versão `info` e começa a piscar."""
        self._info = info
        self.rotulo.configure(
            text=f"⬆ Atualização disponível: versão {info.versao} (você usa a {info.versao_atual})"
        )
        self.botao.configure(state="normal")
        self.pack(fill="x", pady=(0, 8))
        self._piscar_de_novo()

    def ocultar(self) -> None:
        self._parar()
        self._info = None
        self.pack_forget()

    def dispensar(self) -> None:
        self.ocultar()
        if self._ao_dispensar is not None:
            self._ao_dispensar()

    def mostrar_andamento(self, texto: str) -> None:
        """Durante a instalação: a faixa fica fixa, sem botão, dizendo o que está acontecendo."""
        self._parar()
        self._aceso = True
        self._pintar()
        self.rotulo.configure(text=texto)
        self.botao.configure(state="disabled")
        self.fechar.configure(state="disabled")

    def voltar_ao_aviso(self) -> None:
        """A instalação falhou: o aviso volta, piscando, para a pessoa poder tentar de novo."""
        self.fechar.configure(state="normal")
        if self._info is not None:
            self.mostrar(self._info)

    # --- piscar -------------------------------------------------------------------------------
    def _piscar_de_novo(self) -> None:
        self._parar()
        self._aceso = True
        self._pintar()
        self._piscando = True
        self._depois = self.after(self._intervalo_ms, self._alternar)

    def _parar(self) -> None:
        self._piscando = False
        if self._depois is not None:
            with contextlib.suppress(tkinter.TclError, ValueError):
                self.after_cancel(self._depois)
            self._depois = None

    def _alternar(self) -> None:
        self._depois = None
        if not self._piscando:
            return
        self._aceso = not self._aceso
        self._pintar()
        with contextlib.suppress(tkinter.TclError):
            self._depois = self.after(self._intervalo_ms, self._alternar)

    def _pintar(self) -> None:
        fundo, texto = _ACESO if self._aceso else _APAGADO
        with contextlib.suppress(tkinter.TclError):
            self.configure(fg_color=fundo)
            self.rotulo.configure(text_color=texto)
            self.fechar.configure(text_color=texto)

    def _clicou(self) -> None:
        if self._info is not None and str(self.botao.cget("state")) != "disabled":
            self._ao_atualizar(self._info)

    def destroy(self) -> None:
        self._parar()
        super().destroy()
