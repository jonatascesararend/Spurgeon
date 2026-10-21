"""O dicionário personalizado: os termos que a pessoa guardou como "está certa" (ou escreveu).

É o arquivo de vocabulário teológico (`recursos/vocabulario_teologico.txt`): o verificador de
português não marca esses termos como erro e a transcrição troca por eles palavras parecidas de
baixa confiança. Aqui a pessoa vê a lista, acrescenta um termo e tira os que guardou errado.
Cada mudança grava o arquivo na hora e avisa quem abriu a janela (`ao_mudar`), para o verificador
enxergar o vocabulário novo.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from spurgeon.transcricao.vocabulario import (
    acrescentar_ao_vocabulario,
    carregar_vocabulario,
    remover_do_vocabulario,
)
from spurgeon.ui.dicas import dica

_COR_SUAVE = "gray60"
_COR_FUNDO_LISTA = "#2b2b2b"
_COR_TEXTO_LISTA = "#dce4ee"
_COR_SELECAO = "#1f538d"
_COR_OK = "#4caf72"
_COR_AVISO = "#e0a030"


class DicionarioPersonalizado(ctk.CTkToplevel):
    def __init__(self, janela, caminho: Path, *, ao_mudar: Callable[[], None] | None = None):
        super().__init__(janela)
        self._caminho = Path(caminho)
        self._ao_mudar = ao_mudar
        self._termos: list[str] = []
        self._visiveis: list[str] = []
        self.title("Dicionário personalizado")
        self.geometry("520x600")
        self.minsize(420, 420)
        with contextlib.suppress(tk.TclError):
            self.transient(janela)
        self._montar()
        self.recarregar()
        self.after(150, self._focar)

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        ctk.CTkLabel(
            self,
            text="Dicionário personalizado",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            self,
            text="Os termos que o verificador de português aceita como certos e que a transcrição "
            "usa para corrigir palavras parecidas. Os que você guarda com “Está certa: guardar no "
            "vocabulário” entram aqui; tire os que guardou por engano.",
            anchor="w",
            justify="left",
            wraplength=480,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="ew", padx=16)

        busca = ctk.CTkFrame(self, fg_color="transparent")
        busca.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 4))
        busca.columnconfigure(0, weight=1)
        self._var_busca = ctk.StringVar()
        self._campo_busca = ctk.CTkEntry(
            busca, textvariable=self._var_busca, placeholder_text="Procurar na lista"
        )
        self._campo_busca.grid(row=0, column=0, sticky="ew")
        self._var_busca.trace_add("write", lambda *_a: self._filtrar())
        dica(self._campo_busca, "Mostra só os termos que têm o que você digitar.")
        self._contagem = ctk.CTkLabel(busca, text="", text_color=_COR_SUAVE, width=110, anchor="e")
        self._contagem.grid(row=0, column=1, padx=(8, 0))

        moldura = ctk.CTkFrame(self, fg_color="transparent")
        moldura.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 6))
        moldura.columnconfigure(0, weight=1)
        moldura.rowconfigure(0, weight=1)
        self._lista = tk.Listbox(
            moldura,
            selectmode="extended",
            activestyle="none",
            exportselection=False,
            bg=_COR_FUNDO_LISTA,
            fg=_COR_TEXTO_LISTA,
            selectbackground=_COR_SELECAO,
            highlightthickness=0,
            borderwidth=0,
            font=("Segoe UI", 11),
        )
        self._lista.grid(row=0, column=0, sticky="nsew")
        rolagem = ctk.CTkScrollbar(moldura, command=self._lista.yview)
        rolagem.grid(row=0, column=1, sticky="ns")
        self._lista.configure(yscrollcommand=rolagem.set)
        self._lista.bind("<<ListboxSelect>>", lambda _e: self._atualizar_botoes())
        self._lista.bind("<Delete>", lambda _e: self.remover_selecionados())

        self._situacao = ctk.CTkLabel(self, text="", anchor="w", text_color=_COR_SUAVE)
        self._situacao.grid(row=4, column=0, sticky="ew", padx=16)

        novo = ctk.CTkFrame(self, fg_color="transparent")
        novo.grid(row=5, column=0, sticky="ew", padx=16, pady=(6, 4))
        novo.columnconfigure(0, weight=1)
        self._var_novo = ctk.StringVar()
        self._campo_novo = ctk.CTkEntry(
            novo, textvariable=self._var_novo, placeholder_text="Termo novo (como deve ser escrito)"
        )
        self._campo_novo.grid(row=0, column=0, sticky="ew")
        self._campo_novo.bind("<Return>", lambda _e: self.adicionar())
        dica(
            self._campo_novo,
            "Escreva o termo do jeito que quer ver no texto (maiúsculas e acentos contam). "
            "Termos com mais de uma palavra são aceitos.",
        )
        self._botao_adicionar = ctk.CTkButton(
            novo, text="Adicionar", width=100, command=self.adicionar
        )
        self._botao_adicionar.grid(row=0, column=1, padx=(8, 0))
        dica(self._botao_adicionar, "Guarda o termo no dicionário (se ainda não estiver lá).")

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 14))
        self._botao_remover = ctk.CTkButton(
            rodape,
            text="Remover selecionados",
            width=170,
            fg_color=("#b04040", "#8c3030"),
            hover_color=("#963535", "#702626"),
            command=self.remover_selecionados,
            state="disabled",
        )
        self._botao_remover.pack(side="left")
        dica(
            self._botao_remover,
            "Tira do dicionário os termos escolhidos (Ctrl ou Shift escolhem vários). Depois "
            "disso o verificador volta a marcar a palavra, se ela estiver errada.",
        )
        ctk.CTkButton(
            rodape, text="Fechar", width=100, fg_color=("gray70", "gray30"), command=self.destroy
        ).pack(side="right")

    def _focar(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.lift()
            self._campo_novo.focus_set()

    # --- a lista ------------------------------------------------------------------------------
    @property
    def termos(self) -> list[str]:
        """Os termos do arquivo agora, na ordem dele."""
        return list(self._termos)

    def recarregar(self) -> None:
        self._termos = carregar_vocabulario(self._caminho)
        self._filtrar()

    def _filtrar(self) -> None:
        procurado = self._var_busca.get().strip().casefold()
        self._visiveis = [t for t in self._termos if not procurado or procurado in t.casefold()]
        self._lista.delete(0, "end")
        for termo in self._visiveis:
            self._lista.insert("end", termo)
        total = len(self._termos)
        mostrados = len(self._visiveis)
        self._contagem.configure(
            text=f"{total} termos" if mostrados == total else f"{mostrados} de {total}"
        )
        self._atualizar_botoes()

    def _atualizar_botoes(self) -> None:
        self._botao_remover.configure(state="normal" if self._lista.curselection() else "disabled")

    # --- ações --------------------------------------------------------------------------------
    def adicionar(self) -> bool:
        termo = " ".join(self._var_novo.get().split())
        if not termo:
            return False
        if not acrescentar_ao_vocabulario(self._caminho, termo):
            self._situacao.configure(
                text=f"“{termo}” já está no dicionário.", text_color=_COR_AVISO
            )
            return False
        self._var_novo.set("")
        self.recarregar()
        self._situacao.configure(text=f"“{termo}” guardado.", text_color=_COR_OK)
        self._avisar()
        return True

    def remover_selecionados(self) -> int:
        escolhidos = [self._visiveis[i] for i in self._lista.curselection()]
        if not escolhidos:
            return 0
        removidas = remover_do_vocabulario(self._caminho, escolhidos)
        self.recarregar()
        nome = f"“{escolhidos[0]}”" if len(escolhidos) == 1 else f"{len(escolhidos)} termos"
        self._situacao.configure(text=f"{nome} removido(s).", text_color=_COR_OK)
        if removidas:
            self._avisar()
        return removidas

    def _avisar(self) -> None:
        if self._ao_mudar is not None:
            self._ao_mudar()
