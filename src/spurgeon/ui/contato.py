"""O aviso do canto superior direito: sugestões e contribuições (Pix), com o e-mail clicável.

O endereço é um link `mailto:`: o clique abre o programa de e-mail da pessoa; o botão direito copia
o endereço (que também é a chave Pix), para quem não tem programa de e-mail configurado.
"""

from __future__ import annotations

import contextlib
import tkinter
import webbrowser
from collections.abc import Callable

import customtkinter as ctk

from spurgeon.ui import dicas

EMAIL_DE_CONTATO = "jonatascesararend@gmail.com"
LINK_DE_CONTATO = f"mailto:{EMAIL_DE_CONTATO}"
TEXTO_DO_AVISO = "Sugestões ou contribuições (Pix): "
_COR_DO_TEXTO = "gray55"
_COR_DO_LINK = ("#1a6fd1", "#5aa9ff")
_COR_DO_LINK_SOB_O_MOUSE = ("#0d4a96", "#9cc9ff")
_AVISO_DA_COPIA_MS = 2200
_DICA = (
    "Clique para escrever um e-mail com sugestões ou contribuições. "
    "Botão direito: copiar o endereço (a chave Pix)."
)


def abrir_link(link: str) -> None:
    """Abre o link no programa padrão do Windows (o de e-mail, no caso de `mailto:`)."""
    webbrowser.open(link)


class AvisoDeContato(ctk.CTkFrame):
    """O texto "Sugestões ou contribuições (Pix): " seguido do e-mail, que é um link `mailto:`."""

    def __init__(self, master, *, abrir: Callable[[str], None] = abrir_link, **opcoes):
        super().__init__(master, fg_color="transparent", **opcoes)
        self._abrir = abrir
        self.rotulo = ctk.CTkLabel(
            self,
            text=TEXTO_DO_AVISO,
            text_color=_COR_DO_TEXTO,
            font=ctk.CTkFont(size=12),
            pady=0,
        )
        self.rotulo.pack(side="left")
        self.link = ctk.CTkLabel(
            self,
            text=EMAIL_DE_CONTATO,
            text_color=_COR_DO_LINK,
            font=ctk.CTkFont(size=12, underline=True),
            pady=0,
        )
        self.link.pack(side="left")
        with contextlib.suppress(tkinter.TclError):
            self.link.configure(cursor="hand2")
        self.link.bind("<Button-1>", self._clicou)
        self.link.bind("<Button-3>", self._copiar)
        self.link.bind(
            "<Enter>", lambda _e: self.link.configure(text_color=_COR_DO_LINK_SOB_O_MOUSE)
        )
        self.link.bind("<Leave>", lambda _e: self.link.configure(text_color=_COR_DO_LINK))
        dicas.dica(self.link, _DICA)
        dicas.dica(self.rotulo, _DICA)

    def _clicou(self, _evento=None) -> None:
        self._abrir(LINK_DE_CONTATO)

    def _copiar(self, _evento=None) -> None:
        self.clipboard_clear()
        self.clipboard_append(EMAIL_DE_CONTATO)
        self.rotulo.configure(text="Endereço copiado: ")
        self.after(_AVISO_DA_COPIA_MS, self._restaurar)

    def _restaurar(self) -> None:
        # a janela pode ter sido fechada nesse meio tempo
        with contextlib.suppress(tkinter.TclError):
            self.rotulo.configure(text=TEXTO_DO_AVISO)
