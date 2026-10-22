"""A legenda de cores do texto: um chip por categoria detectada, mais o dos Shorts já feitos.

Serve ao Shorts livre e à tabela de frases do Studio. Cada chip leva a cor da categoria e, ao
clicar, avisa quem usa a legenda (`ao_clicar(chave)`) para levar o texto ao próximo trecho. Os
chips se arrumam um depois do outro e passam para a linha de baixo quando não cabem; a legenda
tem altura mínima (1 px) e só cresce com os chips, para não deixar vão quando está vazia.
"""

from __future__ import annotations

import tkinter
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import customtkinter as ctk

from spurgeon.shorts.dados import CATEGORIAS, cor_da_categoria, rotulo_da_categoria
from spurgeon.shorts.edicao import faixas_de
from spurgeon.ui.dicas import dica

CHAVE_FEITOS = "feitos"  # o "tipo" da legenda que leva aos Shorts que já existem
COR_DOS_FEITOS = "#c9a227"
LOSANGO = "◆"
BARRA = "▌"


def configurar_tags_de_categoria(texto: tkinter.Text) -> None:
    """Uma etiqueta de cor por categoria no texto: a barra da margem (`cat_…`), o losango dos Shorts
    que já existem (`feito_…`) e o sublinhado das palavras desses Shorts (`sub_…`)."""
    for chave in CATEGORIAS:
        cor = cor_da_categoria(chave)
        texto.tag_config(f"cat_{chave}", foreground=cor)
        texto.tag_config(f"feito_{chave}", foreground=cor)
        try:
            texto.tag_config(f"sub_{chave}", underline=True, underlinefg=cor)
        except tkinter.TclError:  # Tk antigo, sem cor própria no sublinhado
            texto.tag_config(f"sub_{chave}", underline=True)


@dataclass(frozen=True)
class ItemDaLegenda:
    chave: str
    texto: str
    cor: str
    cor_do_texto: str
    ajuda: str


def inicios_dos_grupos(linhas: Iterable[int]) -> list[int]:
    """`[3, 4, 5, 9, 10]` → `[3, 9]`: a primeira linha de cada sequência de linhas seguidas."""
    return [de for de, _ate in faixas_de(linhas)]


def itens_da_legenda(
    linhas_por_categoria: dict[str, Sequence[int]],
    linhas_dos_feitos: Sequence[int],
    quantos_feitos: int,
) -> tuple[list[ItemDaLegenda], dict[str, list[int]]]:
    """Os chips da legenda e, para cada um, as linhas onde abre cada trecho.

    `linhas_por_categoria` diz, para cada categoria detectada, as linhas (de 1 a N) do texto
    que a têm; `linhas_dos_feitos`, as que já estão em algum Short; `quantos_feitos`, quantos
    Shorts há. As linhas de abertura servem para ir de um trecho ao seguinte.
    """
    contagem = Counter({chave: len(linhas) for chave, linhas in linhas_por_categoria.items()})
    itens: list[ItemDaLegenda] = []
    grupos: dict[str, list[int]] = {}
    for chave in [c for c in CATEGORIAS if contagem[c]]:
        grupos[chave] = inicios_dos_grupos(sorted(linhas_por_categoria[chave]))
        rotulo = rotulo_da_categoria(chave)
        itens.append(
            ItemDaLegenda(
                chave,
                f"{rotulo} · {contagem[chave]}",
                cor_da_categoria(chave),
                "white",
                f"Frases em que a IA viu “{rotulo}”. Clique para ir ao próximo trecho assim.",
            )
        )
    if linhas_dos_feitos:
        grupos[CHAVE_FEITOS] = inicios_dos_grupos(sorted(linhas_dos_feitos))
        itens.append(
            ItemDaLegenda(
                CHAVE_FEITOS,
                f"{LOSANGO} Já virou Short · {quantos_feitos}",
                COR_DOS_FEITOS,
                "#101010",
                "As frases sublinhadas (e com o losango na margem) já estão em algum Short da "
                "lista. Clique para ir à próxima.",
            )
        )
    return itens, grupos


class LegendaDeCategorias(ctk.CTkFrame):
    def __init__(self, master, *, ao_clicar: Callable[[str], None]):
        super().__init__(master, fg_color="transparent", height=1)
        self._ao_clicar = ao_clicar
        self.chips: dict[str, ctk.CTkButton] = {}
        self._largura = 0
        self.bind("<Configure>", self._mudou_de_largura)

    def definir(self, itens: Sequence[ItemDaLegenda]) -> None:
        """Troca os chips pelos de `itens`."""
        for chip in self.chips.values():
            chip.destroy()
        self.chips = {}
        for item in itens:
            fonte = ctk.CTkFont(size=11, weight="bold")
            chip = ctk.CTkButton(
                self, text=item.texto, font=fonte, height=22, corner_radius=6, fg_color=item.cor,
                hover_color=item.cor, text_color=item.cor_do_texto,
                width=fonte.measure(item.texto) + 22,
                command=lambda chave=item.chave: self._ao_clicar(chave),
            )  # fmt: skip
            dica(chip, item.ajuda)
            self.chips[item.chave] = chip
        self._arrumar()

    def _mudou_de_largura(self, evento) -> None:
        if evento.width != self._largura:
            self._largura = evento.width
            self._arrumar()

    def _arrumar(self) -> None:
        """Põe os chips um depois do outro, passando para a linha de baixo quando não cabem."""
        limite = self._largura if self._largura > 1 else 10**6
        x = linha = coluna = 0
        for chip in self.chips.values():
            largura = chip.winfo_reqwidth() + 6
            if coluna and x + largura > limite:
                linha, coluna, x = linha + 1, 0, 0
            chip.grid(row=linha, column=coluna, padx=(0, 6), pady=2, sticky="w")
            x += largura
            coluna += 1
