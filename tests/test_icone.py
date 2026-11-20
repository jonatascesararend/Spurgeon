"""O ícone do programa (`recursos/studio_spurgeon.ico`) na janela, nas caixas e na barra."""

from __future__ import annotations

import ctypes
import sys
import tkinter
from pathlib import Path

import customtkinter as ctk
import pytest

from spurgeon.ui import icone
from tests import test_janela as base

janela = base.janela  # a janela compartilhada dos testes da interface
gui = pytest.mark.gui
windows = pytest.mark.skipif(sys.platform != "win32", reason="o ícone da janela é coisa do Windows")


def test_o_icone_do_repositorio_existe_e_e_o_do_studio_spurgeon():
    arquivo = icone.caminho_do_icone()
    assert arquivo is not None and arquivo.name == "studio_spurgeon.ico"
    assert arquivo.read_bytes()[:4] == b"\x00\x00\x01\x00"  # cabeçalho de um .ico de verdade


def test_sem_o_arquivo_a_janela_fica_com_o_icone_de_sempre(monkeypatch, tmp_path):
    monkeypatch.setattr(icone, "raiz_repositorio", lambda: tmp_path)
    assert icone.caminho_do_icone() is None
    assert icone.aplicar(object()) is False  # nem tenta (e não dá erro)


@windows
def test_a_identidade_do_aplicativo_nao_e_a_do_python():
    assert icone.definir_identidade_do_app() is True
    ponteiro = ctypes.c_wchar_p()
    resultado = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(
        ctypes.byref(ponteiro)
    )
    assert resultado == 0 and ponteiro.value == icone.ID_DO_APLICATIVO


def test_aplicar_poe_o_ico_do_studio_spurgeon_na_janela():
    class Janela:
        def __init__(self):
            self.chamadas: list[str] = []

        def iconbitmap(self, caminho):
            self.chamadas.append(caminho)

    janela_falsa = Janela()
    assert icone.aplicar(janela_falsa) is (sys.platform == "win32")
    if sys.platform == "win32":
        assert [Path(c).name for c in janela_falsa.chamadas] == ["studio_spurgeon.ico"]


def test_aplicar_nao_quebra_se_o_tk_recusar_o_arquivo():
    class Recusa:
        def iconbitmap(self, _caminho):
            raise tkinter.TclError("bitmap not defined")

    assert icone.aplicar(Recusa()) is False


# O Tk não devolve o caminho do ícone depois (o `wm iconbitmap` volta vazio no Windows, e o do
# CustomTkinter nem devolve), então a prova é a bandeira: com ela ligada o CustomTkinter NÃO põe o
# ícone dele 200 ms depois de criar a janela, e o nosso fica.
@gui
@windows
def test_a_janela_principal_ficou_com_o_icone_do_programa(janela):
    assert janela._iconbitmap_method_called


@gui
@windows
def test_as_caixas_ja_nascem_com_o_icone_do_programa(janela):
    caixa = ctk.CTkToplevel(janela)
    try:
        assert caixa._iconbitmap_method_called
    finally:
        caixa.destroy()


def test_instalar_nao_embrulha_duas_vezes():
    icone.instalar()
    antes = ctk.CTkToplevel.__init__
    icone.instalar()
    assert ctk.CTkToplevel.__init__ is antes
