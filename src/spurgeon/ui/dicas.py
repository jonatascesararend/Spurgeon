"""Dicas de ferramenta: o balãozinho que explica um botão quando o mouse fica em cima dele.

`dica(widget, "texto")` põe a dica em qualquer widget; `IconeDeAjuda` é a bolinha "?" que se põe ao
lado do título de uma etapa. O balão aparece depois de uma pausa (para não piscar enquanto o mouse
passa), some ao sair, ao clicar ou quando o widget é destruído, e acompanha a escala da tela.
O texto pode ser uma função (lida só na hora de mostrar), para dicas que mudam com o estado.
"""

from __future__ import annotations

import contextlib
import tkinter
from collections.abc import Callable

import customtkinter as ctk

ATRASO_PADRAO_MS = 450
_SUMIR_DEPOIS_MS = 90  # sair de um filho do botão e entrar em outro não pode piscar a dica
_FUNDO = "#1c1c1c"
_BORDA = "#6a6a6a"
_TEXTO = "#ececec"
_LARGURA_MAXIMA = 340

Texto = str | Callable[[], str]


class Dica:
    """A dica de um widget. Várias dicas do mesmo widget são trocadas, não somadas."""

    def __init__(self, widget, texto: Texto, *, atraso_ms: int = ATRASO_PADRAO_MS):
        self._widget = widget
        self._texto = texto
        self._atraso_ms = atraso_ms
        self._pendente: str | None = None  # o `after` que vai mostrar o balão
        self._sumir: str | None = None  # o `after` que vai esconder o balão
        self._balao: tkinter.Toplevel | None = None
        for evento, comando in (
            ("<Enter>", self._entrou),
            ("<Leave>", self._saiu),
            ("<ButtonPress>", self._clicou),
            ("<Destroy>", self._destruiu),
        ):
            widget.bind(evento, comando, add="+")

    # --- o texto ------------------------------------------------------------------------------
    @property
    def texto(self) -> str:
        valor = self._texto() if callable(self._texto) else self._texto
        return " ".join(str(valor).split()) if valor else ""

    def definir_texto(self, texto: Texto) -> None:
        self._texto = texto

    # --- eventos ------------------------------------------------------------------------------
    def _entrou(self, _evento=None) -> None:
        self._cancelar(sumir=True)
        if self._balao is not None or self._pendente is not None:
            return
        with contextlib.suppress(tkinter.TclError):
            self._pendente = self._widget.after(self._atraso_ms, self.mostrar)

    def _saiu(self, _evento=None) -> None:
        self._cancelar(pendente=True)
        if self._balao is not None and self._sumir is None:
            with contextlib.suppress(tkinter.TclError):
                self._sumir = self._widget.after(_SUMIR_DEPOIS_MS, self.esconder)

    def _clicou(self, _evento=None) -> None:
        self.esconder()

    def _destruiu(self, _evento=None) -> None:
        self.esconder()  # nos widgets do CustomTkinter o evento vem de um filho: serve igual

    def _cancelar(self, *, pendente: bool = False, sumir: bool = False) -> None:
        if pendente and self._pendente is not None:
            with contextlib.suppress(tkinter.TclError):
                self._widget.after_cancel(self._pendente)
            self._pendente = None
        if sumir and self._sumir is not None:
            with contextlib.suppress(tkinter.TclError):
                self._widget.after_cancel(self._sumir)
            self._sumir = None

    # --- o balão ------------------------------------------------------------------------------
    @property
    def visivel(self) -> bool:
        return self._balao is not None

    def mostrar(self) -> None:
        """Mostra o balão perto do mouse (já, sem esperar o atraso)."""
        self._pendente = None
        texto = self.texto
        if not texto or self._balao is not None:
            return
        try:
            if not self._widget.winfo_exists():
                return
            balao = tkinter.Toplevel(self._widget)
            balao.withdraw()
            balao.overrideredirect(True)
            with contextlib.suppress(tkinter.TclError):
                balao.attributes("-topmost", True)
            moldura = tkinter.Frame(balao, background=_BORDA, padx=1, pady=1)
            moldura.pack()
            rotulo = tkinter.Label(
                moldura, text=texto, justify="left", wraplength=_LARGURA_MAXIMA,
                background=_FUNDO, foreground=_TEXTO, padx=9, pady=6,
                font=("Segoe UI", 10),
            )  # fmt: skip
            rotulo.pack()
            balao.update_idletasks()
            x = self._widget.winfo_pointerx() + 14
            y = self._widget.winfo_pointery() + 20
            largura, altura = balao.winfo_reqwidth(), balao.winfo_reqheight()
            x = max(0, min(x, self._widget.winfo_screenwidth() - largura - 4))
            if y + altura > self._widget.winfo_screenheight() - 4:  # sem espaço embaixo: vai acima
                y = max(0, self._widget.winfo_pointery() - altura - 10)
            balao.geometry(f"+{x}+{y}")
            balao.deiconify()
            self._balao = balao
        except tkinter.TclError:
            self._balao = None

    def esconder(self) -> None:
        self._cancelar(pendente=True, sumir=True)
        balao, self._balao = self._balao, None
        if balao is not None:
            with contextlib.suppress(tkinter.TclError):
                balao.destroy()


def dica(widget, texto: Texto, *, atraso_ms: int = ATRASO_PADRAO_MS) -> Dica:
    """Põe a dica `texto` em `widget` (e devolve o objeto, para trocar o texto depois)."""
    anterior = getattr(widget, "_dica_spurgeon", None)
    if anterior is not None:
        anterior.definir_texto(texto)
        return anterior
    nova = Dica(widget, texto, atraso_ms=atraso_ms)
    widget._dica_spurgeon = nova
    return nova


class IconeDeAjuda(ctk.CTkLabel):
    """A bolinha "?" que explica, ao passar o mouse, o que é a etapa ao lado dela."""

    def __init__(self, master, texto: Texto, *, tamanho: int = 20, **opcoes):
        opcoes.setdefault("fg_color", ("gray70", "gray32"))
        opcoes.setdefault("text_color", ("gray10", "gray92"))
        super().__init__(
            master, text="?", width=tamanho, height=tamanho, corner_radius=tamanho // 2,
            font=ctk.CTkFont(size=max(10, tamanho - 8), weight="bold"), **opcoes,
        )  # fmt: skip
        with contextlib.suppress(tkinter.TclError):
            self.configure(cursor="question_arrow")
        self.dica = dica(self, texto, atraso_ms=150)  # quem procura ajuda não quer esperar
