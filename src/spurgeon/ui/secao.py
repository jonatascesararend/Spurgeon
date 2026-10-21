"""Seção que recolhe: um cabeçalho que se clica para mostrar ou esconder o conteúdo.

Os painéis de corte, zoom e legenda dos Shorts têm prévias grandes; recolhidos, a coluna de opções
fica curta (menos para rolar, menos widgets na tela) e a pessoa abre só o que vai usar.
"""

from __future__ import annotations

import contextlib
import tkinter

import customtkinter as ctk

from spurgeon.ui import dicas

_FUNDO_DO_CABECALHO = ("gray85", "gray22")


class SecaoRecolhivel(ctk.CTkFrame):
    def __init__(self, master, titulo: str, *, aberta: bool = False, ajuda: dicas.Texto = ""):
        super().__init__(master, fg_color="transparent")
        self._titulo = titulo
        self._aberta = False
        self._cabecalho = ctk.CTkButton(
            self,
            text="",
            anchor="w",
            height=30,
            fg_color=_FUNDO_DO_CABECALHO,
            text_color=("gray10", "gray90"),
            hover_color=("gray78", "gray28"),
            font=ctk.CTkFont(weight="bold"),
            command=self.alternar,
        )
        self._cabecalho.pack(fill="x", padx=8, pady=(4, 0))
        dicas.dica(
            self._cabecalho,
            lambda: "Recolhe esta seção." if self._aberta else "Abre os ajustes desta seção.",
        )
        self.icone: dicas.IconeDeAjuda | None = None
        if ajuda:
            # a bolinha "?" fica por cima do cabeçalho, na ponta direita; clicar nela não recolhe
            self.icone = dicas.IconeDeAjuda(self, ajuda, bg_color=_FUNDO_DO_CABECALHO)
            with contextlib.suppress(tkinter.TclError):
                self.icone.place(in_=self._cabecalho, relx=1.0, x=-10, rely=0.5, anchor="e")
                self.icone.lift()
        self.corpo = ctk.CTkFrame(self, fg_color="transparent")
        self.definir(aberta)

    @property
    def aberta(self) -> bool:
        return self._aberta

    @property
    def cabecalho(self) -> ctk.CTkButton:
        return self._cabecalho

    def alternar(self) -> None:
        self.definir(not self._aberta)

    def definir(self, aberta: bool) -> None:
        self._aberta = aberta
        self._cabecalho.configure(text=f"{'▾' if aberta else '▸'}  {self._titulo}")
        if aberta:
            self.corpo.pack(fill="x")
        else:
            self.corpo.pack_forget()
