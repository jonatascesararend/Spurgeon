"""Menos "piscar" na interface: `configure` que não muda nada não redesenha o widget.

No CustomTkinter, todo `configure` (de um botão, rótulo, caixa de marcar…) redesenha o widget
inteiro, mesmo quando o valor é o mesmo. A janela chama `configure` de muita coisa a cada segundo e
a cada vez que ganha o foco (habilitar e desabilitar botões, atualizar textos), e o resultado era
botões e rótulos piscando, e rolagem com rastros. Aqui o `configure` das classes de widget compara o
que foi pedido com o que o widget já tem (`cget`) e, se tudo é igual, não faz nada.

`instalar()` é idempotente e vale para o programa todo (a interface a chama ao ser importada).
"""

from __future__ import annotations

import functools
import tkinter

import customtkinter as ctk

_CLASSES = (
    ctk.CTkButton,
    ctk.CTkLabel,
    ctk.CTkCheckBox,
    ctk.CTkRadioButton,
    ctk.CTkSwitch,
    ctk.CTkEntry,
    ctk.CTkFrame,
    ctk.CTkProgressBar,
    ctk.CTkSlider,
    ctk.CTkSegmentedButton,
    ctk.CTkOptionMenu,
)
_MARCA = "_configure_economico"
_NAO_COMPARAR = frozenset({"require_redraw"})


def ja_esta_assim(widget, opcoes: dict) -> bool:
    """O widget já tem todos estes valores? (Sem poder conferir uma opção, diz que não.)"""
    for nome, valor in opcoes.items():
        if nome in _NAO_COMPARAR:
            continue
        try:
            atual = widget.cget(nome)
        except (ValueError, KeyError, AttributeError, TypeError, tkinter.TclError):
            return False
        if isinstance(atual, list):
            atual = tuple(atual)
        comparavel = tuple(valor) if isinstance(valor, list) else valor
        try:
            if atual != comparavel:
                return False
        except Exception:  # comparação esquisita (por exemplo, imagens): deixa mudar
            return False
    return True


def _envolver(original):
    @functools.wraps(original)
    def configure(self, *args, **opcoes):
        if opcoes and not args and not opcoes.get("require_redraw") and ja_esta_assim(self, opcoes):
            return None
        return original(self, *args, **opcoes)

    setattr(configure, _MARCA, True)
    return configure


def instalar() -> None:
    for classe in _CLASSES:
        atual = classe.__dict__.get("configure")
        if atual is None or getattr(atual, _MARCA, False):
            continue
        classe.configure = _envolver(atual)


class RotuloQueSome(ctk.CTkLabel):
    """Rótulo que não ocupa linha enquanto está sem texto.

    Um `CTkLabel` vazio ainda pede 28 px de altura: vários avisos que quase nunca aparecem deixavam
    vãos grandes no meio do painel de detalhes. Este some da grade (`grid_remove`, que guarda a
    posição) quando o texto é vazio e volta ao mesmo lugar quando ganha texto. Só vale para rótulos
    postos com `grid`.
    """

    def __init__(self, *args, **kwargs):
        self._some_por_vazio = False
        super().__init__(*args, **kwargs)

    def grid(self, **kwargs):
        self._some_por_vazio = False
        super().grid(**kwargs)
        self._acertar()

    def configure(self, *args, **kwargs):
        resultado = super().configure(*args, **kwargs)
        if "text" in kwargs:
            self._acertar()
        return resultado

    def _acertar(self) -> None:
        vazio = not str(self.cget("text")).strip()
        if vazio and self.winfo_manager() == "grid":
            self.grid_remove()
            self._some_por_vazio = True
        elif not vazio and self._some_por_vazio:
            self._some_por_vazio = False
            super().grid()
