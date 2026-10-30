"""O aviso "Sugestões ou contribuições (Pix)" do canto superior direito da janela."""

from __future__ import annotations

import tkinter

import pytest

from spurgeon.ui import contato
from spurgeon.ui.contato import EMAIL_DE_CONTATO, LINK_DE_CONTATO, AvisoDeContato
from tests.ambiente_grafico import criar_raiz


def test_o_link_e_um_mailto_para_o_endereco():
    assert EMAIL_DE_CONTATO == "jonatascesararend@gmail.com"
    assert LINK_DE_CONTATO == "mailto:jonatascesararend@gmail.com"


def test_abrir_link_entrega_o_endereco_ao_programa_padrao(monkeypatch):
    abertos = []
    monkeypatch.setattr(contato.webbrowser, "open", abertos.append)
    contato.abrir_link(LINK_DE_CONTATO)
    assert abertos == [LINK_DE_CONTATO]


@pytest.fixture(scope="module")
def raiz():
    try:
        janela = criar_raiz()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


@pytest.mark.gui
def test_o_aviso_mostra_o_texto_e_o_email_e_tem_dica(raiz):
    aviso = AvisoDeContato(raiz)
    assert aviso.rotulo.cget("text") == "Sugestões ou contribuições (Pix): "
    assert aviso.link.cget("text") == EMAIL_DE_CONTATO
    for widget in (aviso.rotulo, aviso.link):
        assert "e-mail" in widget._dica_spurgeon.texto
    aviso.destroy()


@pytest.mark.gui
def test_clicar_no_email_abre_o_mailto(raiz):
    abertos = []
    aviso = AvisoDeContato(raiz, abrir=abertos.append)
    aviso.link.event_generate("<Button-1>")
    aviso._clicou()
    assert abertos[-1] == LINK_DE_CONTATO
    aviso.destroy()


@pytest.mark.gui
def test_botao_direito_copia_o_endereco_e_o_aviso_volta_ao_normal(raiz):
    aviso = AvisoDeContato(raiz)
    aviso._copiar()
    assert raiz.clipboard_get() == EMAIL_DE_CONTATO
    assert "copiado" in aviso.rotulo.cget("text")
    aviso._restaurar()
    assert aviso.rotulo.cget("text") == contato.TEXTO_DO_AVISO
    aviso.destroy()
