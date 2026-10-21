"""O pincel do marca-texto e da borracha: o arrasto do mouse sobre as palavras de um texto do Tk.

Serve à tabela de frases do Studio e ao texto do Shorts livre. Quem usa diz como achar a palavra
sob o mouse (`palavra_em`) e onde cada palavra está no widget (`onde`); o pincel pinta as palavras
tocadas enquanto o botão está apertado e, ao soltar, entrega a lista a `ao_pintar(indices,
ferramenta)`. Com a seta (o gesto normal) ele não faz nada: o widget segue como sempre.
"""

from __future__ import annotations

import contextlib
import tkinter
from collections.abc import Callable
from tkinter import TclError

FERRAMENTA_SETA = "seta"
FERRAMENTA_MARCA = "marca"
FERRAMENTA_BORRACHA = "borracha"
CURSOR = {FERRAMENTA_SETA: "", FERRAMENTA_MARCA: "pencil", FERRAMENTA_BORRACHA: "circle"}
_PINTURA = {FERRAMENTA_MARCA: "pintando", FERRAMENTA_BORRACHA: "apagando"}


class Pincel:
    def __init__(
        self,
        caixa: tkinter.Text,
        *,
        palavra_em: Callable[[int, int], int | None],
        onde: Callable[[int], tuple[int, int, int] | None],
        ao_pintar: Callable[[list[int], str], None] | None,
    ):
        """`palavra_em(x, y)`: a palavra (número) sob o ponto, ou None. `onde(palavra)`: `(linha,
        coluna, tamanho)` dela no widget, ou None se ela não está na tela."""
        self._caixa = caixa
        self._palavra_em = palavra_em
        self._onde = onde
        self._ao_pintar = ao_pintar
        self._ferramenta = FERRAMENTA_SETA
        self._pintados: list[int] = []  # as palavras que o arrasto em andamento já tocou
        self._ultimo: int | None = None
        self._pintando = False
        caixa.tag_config("pintando", background="#c9a227", foreground="#101010")
        caixa.tag_config("apagando", background="#8c3b3b", foreground="#ffffff")
        caixa.tag_raise("pintando")
        caixa.tag_raise("apagando")
        caixa.bind("<ButtonPress-1>", self._comecou)
        caixa.bind("<B1-Motion>", self._arrastou)
        caixa.bind("<ButtonRelease-1>", self._soltou)

    @property
    def ferramenta(self) -> str:
        return self._ferramenta

    @property
    def pintando(self) -> bool:
        return self._pintando

    def definir(self, nome: str) -> None:
        """`seta` (o gesto normal), `marca` (marca-texto) ou `borracha`."""
        if nome not in CURSOR:
            raise ValueError(f"ferramenta desconhecida: {nome}")
        self._ferramenta = nome
        self.terminar(entregar=False)
        with contextlib.suppress(TclError):
            self._caixa.configure(cursor=CURSOR[nome])

    def _vale(self) -> bool:
        return self._ferramenta != FERRAMENTA_SETA and self._ao_pintar is not None

    def _palavra_para_pintar(self, x: int, y: int) -> int | None:
        indice = self._palavra_em(x, y)
        return indice if indice is not None and self._onde(indice) is not None else None

    def _comecou(self, evento) -> str | None:
        if not self._vale():
            return None
        self._pintados, self._ultimo, self._pintando = [], None, True
        self._pintar_ate(self._palavra_para_pintar(evento.x, evento.y))
        with contextlib.suppress(TclError):
            self._caixa.focus_set()
        return "break"

    def _arrastou(self, evento) -> str | None:
        if not self._pintando:
            return None
        altura = self._caixa.winfo_height()
        if evento.y < 0:
            self._caixa.yview_scroll(-1, "units")
        elif evento.y > altura:
            self._caixa.yview_scroll(1, "units")
        self._pintar_ate(self._palavra_para_pintar(evento.x, max(0, min(evento.y, altura - 1))))
        return "break"

    def _soltou(self, _evento) -> str | None:
        if not self._pintando:
            return None
        self.terminar(entregar=True)
        return "break"

    def _pintar_ate(self, indice: int | None) -> None:
        """Soma ao arrasto as palavras entre a última tocada e esta (o mouse pula palavras)."""
        if indice is None:
            return
        de = self._ultimo if self._ultimo is not None else indice
        novas = range(min(de, indice), max(de, indice) + 1)
        self._ultimo = indice
        tag = _PINTURA[self._ferramenta]
        for i in novas:
            onde = self._onde(i)
            if onde is None or i in self._pintados:
                continue
            self._pintados.append(i)
            linha, coluna, tamanho = onde
            self._caixa.tag_add(tag, f"{linha}.{coluna}", f"{linha}.{coluna + tamanho}")

    def terminar(self, *, entregar: bool) -> None:
        """Acaba o arrasto: tira a tinta e, se `entregar`, passa as palavras a `ao_pintar`."""
        if not self._pintando:
            return
        self._pintando = False
        pintados, self._pintados, self._ultimo = sorted(self._pintados), [], None
        with contextlib.suppress(TclError):
            for tag in _PINTURA.values():
                self._caixa.tag_remove(tag, "1.0", "end")
        if entregar and pintados and self._ao_pintar is not None:
            self._ao_pintar(pintados, self._ferramenta)
