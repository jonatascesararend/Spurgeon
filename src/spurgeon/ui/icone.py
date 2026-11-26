"""O ícone do programa (`recursos/icone_studio_spurgeon.ico`) na janela, nas caixas e na barra.

Sem isso o Windows mostra o ícone do Python (o programa roda no `pythonw.exe`) e o CustomTkinter
põe o dele nas janelas. Duas coisas resolvem:

* `iconbitmap` em cada janela. O CustomTkinter só põe o ícone dele 200 ms depois de criar a janela
  se ninguém chamou `iconbitmap` antes; chamar logo na criação faz o nosso ficar.
* A identidade do aplicativo (`AppUserModelID`): sem ela o Windows agrupa a janela na barra de
  tarefas sob o `pythonw.exe` e usa o ícone dele.

`instalar()` é idempotente e vale para todas as caixas (`CTkToplevel`); a janela principal chama
`aplicar` por conta própria.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import sys
import tkinter
from pathlib import Path

import customtkinter as ctk

from spurgeon.core.config import raiz_repositorio

NOME_DO_ICONE = "icone_studio_spurgeon.ico"
#: a identidade do programa para o Windows (agrupa a janela e dá o ícone certo na barra de tarefas)
ID_DO_APLICATIVO = "StudioSpurgeon.App"
_MARCA = "_icone_spurgeon"


def caminho_do_icone() -> Path | None:
    """O `.ico` em `recursos/`, ou None se ele não está lá (a janela fica com o ícone de sempre)."""
    arquivo = raiz_repositorio() / "recursos" / NOME_DO_ICONE
    return arquivo if arquivo.is_file() else None


def definir_identidade_do_app() -> bool:
    """Diz ao Windows que este processo é o Studio Spurgeon, não o Python.

    Tem que vir ANTES de a primeira janela existir. Só no Windows; qualquer falha é ignorada (o
    ícone é enfeite, não pode impedir o programa de abrir). Devolve se conseguiu.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        resultado = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ID_DO_APLICATIVO)
    except (AttributeError, OSError):
        return False
    return resultado == 0  # S_OK


def aplicar(janela) -> bool:
    """Põe o ícone do programa nesta janela (principal ou caixa). Devolve se pôs."""
    if not sys.platform.startswith("win"):
        return False
    arquivo = caminho_do_icone()
    if arquivo is None:
        return False
    try:
        janela.iconbitmap(str(arquivo))
    except tkinter.TclError:
        return False
    return True


def instalar() -> None:
    """Toda `CTkToplevel` (caixas de aviso, de pergunta, de escolha…) já nasce com o ícone."""
    original = ctk.CTkToplevel.__init__
    if getattr(original, _MARCA, False):
        return

    @functools.wraps(original)
    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        with contextlib.suppress(Exception):  # enfeite: nunca atrapalha abrir uma caixa
            aplicar(self)

    setattr(__init__, _MARCA, True)
    ctk.CTkToplevel.__init__ = __init__
