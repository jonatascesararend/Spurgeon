"""Janela de aprovação dos cortes dos Shorts: o editor de cortes numa janela que pausa o fluxo.

Todos os cortes escolhidos já vêm marcados; as reservas (nota baixa ou acima do limite) vêm
desmarcadas. Tudo o que se faz com os cortes (marcar, corrigir título e legenda, ajustar o início e
o fim) está em `studio_shorts.EditorDeCortes`, que é o mesmo da aba "Studio Shorts". O que sobra
marcado ao salvar é o que vira Short.
"""

from __future__ import annotations

import contextlib
from tkinter import TclError

import customtkinter as ctk

from spurgeon.core.config import Pastas
from spurgeon.core.projeto import PastasProjeto
from spurgeon.ui import dialogos
from spurgeon.ui.studio_shorts import (  # noqa: F401 - reexportados: os testes e outros usam daqui
    ORDEM_HORARIO,
    ORDEM_NOTA,
    EditorDeCortes,
    descrever_duracao,
    formatar_nota,
    formatar_tempo,
    formatar_total,
)

_COR_SUAVE = "gray60"


class PainelAprovacaoCortes(ctk.CTkToplevel):
    def __init__(
        self,
        janela: ctk.CTk,
        pastas: PastasProjeto,
        *,
        modal: bool = True,
        pastas_do_app: Pastas | None = None,
    ):
        super().__init__(janela)
        self._pastas = pastas
        self.salvo = False
        self.title("Aprovação dos cortes dos Shorts")
        self._dimensionar()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        config = getattr(janela, "config_app", None)
        self.editor = EditorDeCortes(
            self, pastas, pastas_do_app=pastas_do_app or (config.pastas if config else None)
        )
        self.editor.grid(row=0, column=0, sticky="nsew")
        self._montar_rodape()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Control-s>", lambda _e: self.salvar_e_fechar())
        self.bind("<Alt-Down>", lambda _e: self.editor._andar(1))
        self.bind("<Alt-Up>", lambda _e: self.editor._andar(-1))
        self.bind("<Control-space>", lambda _e: self.editor.alternar_atual())
        if modal:
            self.after(150, self.focus)
            self.grab_set()

    def _dimensionar(self) -> None:
        escala = self._get_window_scaling()
        tela_l, tela_a = self.winfo_screenwidth() / escala, self.winfo_screenheight() / escala
        largura, altura = int(min(1180, tela_l * 0.95)), int(min(820, tela_a * 0.92))
        self.geometry(
            f"{largura}x{altura}+{int((tela_l - largura) / 2)}+{int((tela_a - altura) / 4)}"
        )
        self.minsize(900, 520)

    def _montar_rodape(self) -> None:
        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        ctk.CTkButton(rodape, text="Salvar e fechar", command=self.salvar_e_fechar).pack(
            side="right"
        )
        ctk.CTkButton(
            rodape, text="Cancelar", width=90, fg_color=("gray70", "gray30"), command=self._cancelar
        ).pack(side="right", padx=8)
        ctk.CTkLabel(
            rodape,
            text="Alt+↑/↓ muda de corte · Ctrl+Espaço marca ou desmarca · Ctrl+S salva. "
            "Depois de salvar, clique em “Aprovar e continuar” na janela principal.",
            text_color=_COR_SUAVE,
        ).pack(side="left")

    def salvar_e_fechar(self) -> bool:
        """Grava `cortes_shorts.json`. Devolve False se a pessoa desistiu de salvar."""
        if not self.editor.salvar():
            return False
        self.salvo = True
        self._fechar()
        return True

    def _cancelar(self) -> None:
        self.editor._guardar_detalhe()
        if self.editor.mudou and not dialogos.confirmar(
            self,
            "Descartar as alterações?",
            "Há alterações nos cortes que ainda não foram salvas.",
            sim="Descartar",
            nao="Continuar editando",
        ):
            return
        self._fechar()

    def _fechar(self) -> None:
        with contextlib.suppress(TclError):
            self.grab_release()
        self.destroy()


def abrir_painel_cortes(janela: ctk.CTk, pastas: PastasProjeto) -> None:
    if not pastas.cortes_shorts.exists():
        dialogos.aviso(janela, "Aprovação dos cortes", "Os cortes ainda não foram escolhidos.")
        return
    painel = PainelAprovacaoCortes(janela, pastas)
    janela.wait_window(painel)
