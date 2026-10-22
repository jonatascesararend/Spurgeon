"""Ajuda dos testes de janela: abrir a janela-raiz do Tk com algumas tentativas.

Raramente, a primeira janela de um módulo de testes falhava logo depois de a do módulo anterior
ser destruída, e os testes do módulo inteiro eram pulados como "sem ambiente gráfico". Aqui a
criação tenta de novo (com uma pausa) antes de desistir, e o erro verdadeiro vai para um arquivo
na pasta temporária, para a próxima vez que acontecer dar para saber a causa.
"""

from __future__ import annotations

import tempfile
import time
import tkinter
import traceback
from pathlib import Path

ARQUIVO_DO_ERRO = Path(tempfile.gettempdir()) / "spurgeon_erro_da_janela_de_teste.txt"


def criar_raiz(tentativas: int = 4, espera_s: float = 0.6):
    """Uma `customtkinter.CTk()` nova; levanta `tkinter.TclError` se nenhuma tentativa der certo."""
    import customtkinter as ctk

    ultimo: tkinter.TclError | None = None
    for numero in range(1, tentativas + 1):
        try:
            return ctk.CTk()
        except tkinter.TclError as erro:
            ultimo = erro
            with ARQUIVO_DO_ERRO.open("a", encoding="utf-8") as arquivo:
                arquivo.write(f"--- tentativa {numero} de {tentativas}\n{traceback.format_exc()}\n")
            time.sleep(espera_s)
    assert ultimo is not None
    raise ultimo
