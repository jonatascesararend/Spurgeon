"""Peças de interface que explicam: o cabeçalho de uma etapa com o "?" e dicas em seletores.

`CabecalhoDeEtapa` é a linha do título de um cartão: o nome à esquerda e a bolinha "?" (com a
explicação da etapa) à direita. `dica_do_seletor` põe a dica de ferramenta em cada segmento de um
`CTkSegmentedButton`, que não aceita `bind` (ele levanta NotImplementedError); as dicas dos outros
widgets são postas direto com `spurgeon.ui.dicas.dica`.
"""

from __future__ import annotations

from collections.abc import Mapping

import customtkinter as ctk

from spurgeon.ui import dicas


class CabecalhoDeEtapa(ctk.CTkFrame):
    """Título do cartão à esquerda e o "?" (ajuda da etapa) à direita, na mesma linha."""

    def __init__(self, master, titulo: str, ajuda: dicas.Texto = "", *, tamanho: int = 15):
        super().__init__(master, fg_color="transparent")
        self.columnconfigure(0, weight=1)
        self.rotulo = ctk.CTkLabel(
            self, text=titulo, anchor="w", font=ctk.CTkFont(size=tamanho, weight="bold")
        )
        self.rotulo.grid(row=0, column=0, sticky="w")
        self.icone: dicas.IconeDeAjuda | None = None
        if ajuda:
            self.icone = dicas.IconeDeAjuda(self, ajuda)
            self.icone.grid(row=0, column=1, sticky="e", padx=(8, 0))


def dica_do_seletor(seletor: ctk.CTkSegmentedButton, textos: str | Mapping[str, str]) -> None:
    """Dica em cada segmento do seletor: um texto só para todos, ou um por valor (rótulo → texto).

    Refaça a chamada se os valores do seletor forem trocados (os segmentos são recriados).
    """
    for valor, botao in seletor._buttons_dict.items():
        texto = textos.get(valor, "") if isinstance(textos, Mapping) else textos
        if texto:
            dicas.dica(botao, texto)
