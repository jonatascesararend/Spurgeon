"""Caixas de diálogo no tema escuro do aplicativo (as nativas do Tk ficam brancas)."""

from __future__ import annotations

from collections.abc import Sequence

import customtkinter as ctk

_COR_ERRO = "#e05555"
_COR_AVISO = "#e0a030"


def mostrar(
    janela: ctk.CTk,
    titulo: str,
    mensagem: str,
    *,
    botoes: Sequence[str] = ("OK",),
    tipo: str = "info",
) -> str:
    """Mostra uma caixa modal e devolve o rótulo do botão clicado.

    Fechar a janela no X vale o primeiro botão, que deve ser o seguro (o "não").
    """
    escolhido = {"valor": botoes[0]}
    caixa = ctk.CTkToplevel(janela)
    caixa.title(titulo)
    caixa.resizable(False, False)
    caixa.transient(janela)

    cor = {"erro": _COR_ERRO, "aviso": _COR_AVISO}.get(tipo)
    cabecalho = ctk.CTkLabel(
        caixa, text=titulo, font=ctk.CTkFont(size=16, weight="bold"), text_color=cor, anchor="w"
    )
    cabecalho.pack(fill="x", padx=20, pady=(18, 6))
    ctk.CTkLabel(caixa, text=mensagem, wraplength=460, justify="left", anchor="w").pack(
        fill="x", padx=20, pady=(0, 14)
    )

    linha = ctk.CTkFrame(caixa, fg_color="transparent")
    linha.pack(fill="x", padx=20, pady=(0, 16))

    def fechar(rotulo: str) -> None:
        escolhido["valor"] = rotulo
        caixa.destroy()

    for i, rotulo in enumerate(botoes):
        principal = i == len(botoes) - 1
        ctk.CTkButton(
            linha,
            text=rotulo,
            width=110,
            command=lambda r=rotulo: fechar(r),
            fg_color=None if principal else ("gray70", "gray30"),
        ).pack(side="right", padx=(8, 0))

    caixa.protocol("WM_DELETE_WINDOW", caixa.destroy)
    caixa.update_idletasks()
    x = janela.winfo_rootx() + (janela.winfo_width() - caixa.winfo_width()) // 2
    y = janela.winfo_rooty() + (janela.winfo_height() - caixa.winfo_height()) // 3
    caixa.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    caixa.after(150, caixa.focus)  # no Windows o foco imediato às vezes não pega
    caixa.grab_set()
    janela.wait_window(caixa)
    return escolhido["valor"]


def erro(janela: ctk.CTk, titulo: str, mensagem: str) -> None:
    mostrar(janela, titulo, mensagem, tipo="erro")


def aviso(janela: ctk.CTk, titulo: str, mensagem: str) -> None:
    mostrar(janela, titulo, mensagem, tipo="aviso")


def confirmar(janela: ctk.CTk, titulo: str, mensagem: str, *, sim: str, nao: str) -> bool:
    return mostrar(janela, titulo, mensagem, botoes=(nao, sim)) == sim
