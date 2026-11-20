"""A lista de projetos que o programa mostra ao abrir, para continuar de onde parou.

Uma caixa modal com os projetos da pasta de projetos (o que mexeu por último em cima), o último que
estava aberto já marcado. Um clique escolhe, o duplo clique (ou Enter, ou "Abrir") abre; "Começar
sem abrir nenhum" deixa a janela vazia, para processar um vídeo novo. Quem abre é a janela
principal.
"""

from __future__ import annotations

import contextlib
import tkinter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from spurgeon.core.projeto import ProjetoExistente
from spurgeon.ui import dicas
from spurgeon.ui.formulario import formatar_data
from spurgeon.ui.rolagem import QuadroRolavel

_LARGURA_PX = 560
_ALTURA_DA_LINHA_PX = 76  # a de uma linha de projeto, com o espaço entre elas (só p/ a caixa)
_LINHAS_VISIVEIS = 6
_COR_SELECAO = ("#3a7ebf", "#1f538d")
_COR_DA_LINHA = ("gray88", "gray20")
_COR_SUAVE = ("gray30", "gray70")
_COR_DO_ULTIMO = ("#1a6b2e", "#7fd68f")


def _quando(momento: float) -> str:
    """`dd/mm/aaaa às hh:mm` do último movimento no projeto."""
    return datetime.fromtimestamp(momento).strftime("%d/%m/%Y às %H:%M")


class JanelaDeProjetos(ctk.CTkToplevel):
    """A caixa. `escolhido` é a pasta a abrir, ou None se a pessoa preferiu não abrir nenhuma."""

    def __init__(self, janela, projetos: Sequence[ProjetoExistente], *, ultimo: str = "") -> None:
        super().__init__(janela)
        self.title("Studio Spurgeon: seus projetos")
        self.resizable(False, False)
        with contextlib.suppress(tkinter.TclError):
            self.transient(janela)
        self.escolhido: Path | None = None
        self.projetos = list(projetos)
        self.linhas: list[ctk.CTkFrame] = []
        self.indice = 0
        self.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="Abrir um projeto", font=ctk.CTkFont(size=16, weight="bold"), anchor="w"
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            self,
            text="Escolha um para continuar de onde parou, ou comece um vídeo novo.",
            anchor="w",
            justify="left",
            text_color=_COR_SUAVE,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))

        altura = _ALTURA_DA_LINHA_PX * min(len(self.projetos), _LINHAS_VISIVEIS) + 6
        self.quadro = QuadroRolavel(self, width=_LARGURA_PX, height=altura, fg_color="transparent")
        self.quadro.grid(row=2, column=0, sticky="ew", padx=14)
        for posicao, projeto in enumerate(self.projetos):
            self.linhas.append(self._montar_linha(posicao, projeto, ultimo))

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.grid(row=3, column=0, sticky="ew", padx=20, pady=(12, 16))
        self.botao_vazio = ctk.CTkButton(
            botoes,
            text="Começar sem abrir nenhum",
            width=190,
            fg_color=("gray70", "gray30"),
            command=self.sem_abrir,
        )
        self.botao_vazio.pack(side="left")
        self.botao_abrir = ctk.CTkButton(botoes, text="Abrir", width=110, command=self.abrir)
        self.botao_abrir.pack(side="right")
        dicas.dica(
            self.botao_vazio,
            "Fecha esta lista sem abrir nada: a janela fica vazia, pronta para você escolher um "
            "vídeo novo. Qualquer projeto pode ser aberto depois em “Abrir projeto…”.",
        )
        dicas.dica(
            self.botao_abrir,
            "Abre o projeto marcado (o mesmo que dar dois cliques nele, ou apertar Enter).",
        )

        para_abrir = next(
            (i for i, p in enumerate(self.projetos) if str(p.pasta) == ultimo.strip()), 0
        )
        self.selecionar(para_abrir)
        self.bind("<Return>", lambda _e: self.abrir())
        self.bind("<KP_Enter>", lambda _e: self.abrir())
        self.bind("<Escape>", lambda _e: self.sem_abrir())
        self.bind("<Up>", lambda _e: self.mover(-1))
        self.bind("<Down>", lambda _e: self.mover(1))
        self.protocol("WM_DELETE_WINDOW", self.sem_abrir)

    # --- uma linha ---------------------------------------------------------------------------
    def _montar_linha(self, posicao: int, projeto: ProjetoExistente, ultimo: str) -> ctk.CTkFrame:
        linha = ctk.CTkFrame(self.quadro, fg_color=_COR_DA_LINHA, corner_radius=8, height=56)
        linha.pack(fill="x", padx=2, pady=3)
        linha.columnconfigure(0, weight=1)
        eh_o_ultimo = str(projeto.pasta) == ultimo.strip()
        # o `height` pequeno é de propósito: um CTkLabel pede 28 px por padrão e a linha incharia
        titulo = ctk.CTkLabel(
            linha, text=projeto.nome, anchor="w", height=22,
            font=ctk.CTkFont(size=14, weight="bold"),
        )  # fmt: skip
        titulo.grid(row=0, column=0, sticky="ew", padx=(12, 6), pady=(7, 0))
        if eh_o_ultimo:
            ctk.CTkLabel(
                linha, text="aberto da última vez", text_color=_COR_DO_ULTIMO, height=22,
                font=ctk.CTkFont(size=11),
            ).grid(row=0, column=1, sticky="e", padx=(0, 12), pady=(7, 0))  # fmt: skip
        info = ctk.CTkLabel(
            linha,
            text=f"{formatar_data(projeto.data)} · {projeto.situacao}",
            anchor="w",
            height=18,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        info.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        detalhes = ctk.CTkLabel(
            linha,
            text=projeto.detalhes,
            anchor="w",
            height=16,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=11),
        )
        detalhes.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 7))
        explicacao = (
            f"{projeto.pasta}\nMexido pela última vez em {_quando(projeto.modificado)}.\n"
            "Um clique escolhe; dois cliques abrem."
        )
        for peca in (linha, titulo, info, detalhes):
            peca.bind("<Button-1>", lambda _e, i=posicao: self.selecionar(i), add="+")
            peca.bind("<Double-Button-1>", lambda _e, i=posicao: self._abrir_o(i), add="+")
            dicas.dica(peca, explicacao)
        return linha

    # --- escolher e abrir --------------------------------------------------------------------
    def selecionar(self, indice: int) -> None:
        if not self.linhas:
            return
        self.indice = min(max(indice, 0), len(self.linhas) - 1)
        for i, linha in enumerate(self.linhas):
            linha.configure(fg_color=_COR_SELECAO if i == self.indice else _COR_DA_LINHA)
        self._mostrar_a_selecionada()

    def mover(self, passo: int) -> None:
        self.selecionar(self.indice + passo)

    def _mostrar_a_selecionada(self) -> None:
        """Rola a lista até a linha marcada aparecer inteira (as setas andam além das seis)."""
        with contextlib.suppress(tkinter.TclError, TypeError, ZeroDivisionError):
            self.update_idletasks()
            tela = self.quadro._parent_canvas
            _, _, _, fundo = tela.bbox("all")  # a altura do conteúdo todo, em pixels
            linha = self.linhas[self.indice]
            inicio = linha.winfo_y() / fundo
            fim = (linha.winfo_y() + linha.winfo_height()) / fundo
            topo, base = tela.yview()
            if inicio < topo:
                tela.yview_moveto(inicio)
            elif fim > base:
                tela.yview_moveto(fim - (base - topo))

    def _abrir_o(self, indice: int) -> None:
        self.selecionar(indice)
        self.abrir()

    def abrir(self) -> None:
        if self.projetos:
            self.escolhido = self.projetos[self.indice].pasta
        self.destroy()

    def sem_abrir(self) -> None:
        self.escolhido = None
        self.destroy()


def escolher_projeto(
    janela, projetos: Sequence[ProjetoExistente], *, ultimo: str = ""
) -> Path | None:
    """Mostra a lista e espera. Devolve a pasta do projeto escolhido, ou None se a pessoa preferiu
    começar sem abrir nenhum (ou fechou a caixa)."""
    caixa = JanelaDeProjetos(janela, projetos, ultimo=ultimo)
    caixa.update_idletasks()
    x = janela.winfo_rootx() + (janela.winfo_width() - caixa.winfo_width()) // 2
    y = janela.winfo_rooty() + (janela.winfo_height() - caixa.winfo_height()) // 3
    caixa.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    caixa.after(150, caixa.focus)  # no Windows o foco imediato às vezes não pega
    caixa.grab_set()
    janela.wait_window(caixa)
    return caixa.escolhido
