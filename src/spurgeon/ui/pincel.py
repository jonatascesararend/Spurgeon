"""O pincel do marca-texto e da borracha: o arrasto do mouse sobre as palavras de um texto do Tk.

Serve à tabela de frases do Studio e ao texto do Shorts livre. Quem usa diz como achar a palavra
sob o mouse (`palavra_em`), onde cada palavra está no widget (`onde`) e, opcionalmente, quais
palavras a ferramenta pode mexer (`vale`: o marca-texto só acende palavras que ainda não estão no
Short; a borracha, só as que estão). O pincel pinta as palavras enquanto o botão está apertado e,
ao soltar, entrega a lista a `ao_pintar(indices, ferramenta)`. Com a seta (o gesto normal) ele não
faz nada: o widget segue como sempre.

O cursor é sempre o de texto (o "I"): as três ferramentas agem sobre o texto. Com o marca-texto ou
a borracha, a palavra sob o mouse acende antes do clique (amarelo ou vermelho): é o aviso do que
o arrasto vai pintar ou apagar. O arrasto é um elástico do ponto em que o botão foi apertado até a
palavra sob o mouse: voltar o mouse desfaz o que ele tinha pintado a mais.
"""

from __future__ import annotations

import contextlib
import tkinter
from collections.abc import Callable
from tkinter import TclError

FERRAMENTA_SETA = "seta"
FERRAMENTA_MARCA = "marca"
FERRAMENTA_BORRACHA = "borracha"
CURSOR = "xterm"  # o cursor de texto, igual nas três ferramentas
_PINTURA = {FERRAMENTA_MARCA: "pintando", FERRAMENTA_BORRACHA: "apagando"}
_PREVIA = {FERRAMENTA_MARCA: "previa_marca", FERRAMENTA_BORRACHA: "previa_borracha"}


class Pincel:
    def __init__(
        self,
        caixa: tkinter.Text,
        *,
        palavra_em: Callable[[int, int], int | None],
        onde: Callable[[int], tuple[int, int, int] | None],
        ao_pintar: Callable[[list[int], str], None] | None,
        vale: Callable[[int, str], bool] | None = None,
    ):
        """`palavra_em(x, y)`: a palavra (número) sob o ponto, ou None. `onde(palavra)`: `(linha,
        coluna, tamanho)` dela no widget, ou None se ela não está na tela. `vale(palavra,
        ferramenta)`: a ferramenta pode agir sobre esta palavra? (sem ele, vale em todas)."""
        self._caixa = caixa
        self._palavra_em = palavra_em
        self._onde = onde
        self._ao_pintar = ao_pintar
        self._vale_na_palavra = vale
        self._ferramenta = FERRAMENTA_SETA
        self._pintados: set[int] = set()  # as palavras que o arrasto em andamento está pintando
        self._ponto_de_partida: int | None = None
        self._pintando = False
        self._previa: int | None = None  # a palavra que está acesa sob o mouse
        caixa.tag_config("pintando", background="#c9a227", foreground="#101010")
        caixa.tag_config("apagando", background="#8c3b3b", foreground="#ffffff")
        caixa.tag_config("previa_marca", background="#5e5017", foreground="#ffffff")
        caixa.tag_config("previa_borracha", background="#5a2d2d", foreground="#ffffff")
        for tag in (*_PREVIA.values(), *_PINTURA.values()):
            caixa.tag_raise(tag)
        caixa.bind("<ButtonPress-1>", self._comecou)
        caixa.bind("<B1-Motion>", self._arrastou)
        caixa.bind("<ButtonRelease-1>", self._soltou)
        caixa.bind("<Motion>", self._passou, add="+")
        caixa.bind("<Leave>", lambda _e: self._apagar_a_previa(), add="+")
        with contextlib.suppress(TclError):
            caixa.configure(cursor=CURSOR)

    @property
    def ferramenta(self) -> str:
        return self._ferramenta

    @property
    def pintando(self) -> bool:
        return self._pintando

    def definir(self, nome: str) -> None:
        """`seta` (o gesto normal), `marca` (marca-texto) ou `borracha`."""
        if nome not in (FERRAMENTA_SETA, *_PINTURA):
            raise ValueError(f"ferramenta desconhecida: {nome}")
        self._ferramenta = nome
        self.terminar(entregar=False)
        self._apagar_a_previa()
        with contextlib.suppress(TclError):
            self._caixa.configure(cursor=CURSOR)

    def _vale(self) -> bool:
        return self._ferramenta != FERRAMENTA_SETA and self._ao_pintar is not None

    def _pode(self, indice: int) -> bool:
        if self._onde(indice) is None:
            return False
        return self._vale_na_palavra is None or self._vale_na_palavra(indice, self._ferramenta)

    def _palavra_para_pintar(self, x: int, y: int) -> int | None:
        indice = self._palavra_em(x, y)
        return indice if indice is not None and self._pode(indice) else None

    # --- o aviso do que vai ser pintado ---------------------------------------------------------
    def _passou(self, evento) -> None:
        if self._pintando or not self._vale():
            self._apagar_a_previa()
            return
        indice = self._palavra_para_pintar(evento.x, evento.y)
        if indice == self._previa:
            return
        self._apagar_a_previa()
        if indice is not None:
            linha, coluna, tamanho = self._onde(indice)
            self._caixa.tag_add(
                _PREVIA[self._ferramenta], f"{linha}.{coluna}", f"{linha}.{coluna + tamanho}"
            )
            self._previa = indice

    def _apagar_a_previa(self) -> None:
        self._previa = None
        with contextlib.suppress(TclError):
            for tag in _PREVIA.values():
                self._caixa.tag_remove(tag, "1.0", "end")

    # --- o arrasto ------------------------------------------------------------------------------
    def _comecou(self, evento) -> str | None:
        if not self._vale():
            return None
        self._apagar_a_previa()
        self._pintados, self._ponto_de_partida, self._pintando = set(), None, True
        self._pintar_ate(self._palavra_em(evento.x, evento.y))
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
        self._pintar_ate(self._palavra_em(evento.x, max(0, min(evento.y, altura - 1))))
        return "break"

    def _soltou(self, _evento) -> str | None:
        if not self._pintando:
            return None
        self.terminar(entregar=True)
        return "break"

    def _pintar_ate(self, indice: int | None) -> None:
        """Pinta de onde o botão foi apertado até esta palavra (e tira o que passou da conta)."""
        if indice is None:
            return
        if self._ponto_de_partida is None:
            self._ponto_de_partida = indice
        de = self._ponto_de_partida
        novos = {i for i in range(min(de, indice), max(de, indice) + 1) if self._pode(i)}
        tag = _PINTURA[self._ferramenta]
        for i in self._pintados - novos:
            self._marcar(i, tag, ligar=False)
        for i in novos - self._pintados:
            self._marcar(i, tag, ligar=True)
        self._pintados = novos

    def _marcar(self, indice: int, tag: str, *, ligar: bool) -> None:
        linha, coluna, tamanho = self._onde(indice)
        operacao = self._caixa.tag_add if ligar else self._caixa.tag_remove
        operacao(tag, f"{linha}.{coluna}", f"{linha}.{coluna + tamanho}")

    def terminar(self, *, entregar: bool) -> None:
        """Acaba o arrasto: tira a tinta e, se `entregar`, passa as palavras a `ao_pintar`."""
        if not self._pintando:
            return
        self._pintando = False
        pintados, self._pintados, self._ponto_de_partida = sorted(self._pintados), set(), None
        with contextlib.suppress(TclError):
            for tag in _PINTURA.values():
                self._caixa.tag_remove(tag, "1.0", "end")
        if entregar and pintados and self._ao_pintar is not None:
            self._ao_pintar(pintados, self._ferramenta)
