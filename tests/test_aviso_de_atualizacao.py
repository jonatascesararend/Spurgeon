"""O aviso de atualização (faixa amarela que pisca) e o fluxo de atualizar, com a janela escondida.

Os testes da janela principal (outro root Tk) estão em `test_janela_atualizacao.py`.
"""

from __future__ import annotations

import time
import tkinter

import pytest

from spurgeon.core.atualizacao import InfoAtualizacao
from spurgeon.ui import aviso_de_atualizacao as aviso_mod
from spurgeon.ui.aviso_de_atualizacao import AvisoDeAtualizacao
from tests.ambiente_grafico import criar_raiz

pytestmark = pytest.mark.gui

INFO = InfoAtualizacao("9.0.0", "1.7.0")


def bombear(janela, condicao, limite_s: float = 20.0) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        janela.update()
        if condicao():
            return
        time.sleep(0.01)
    pytest.fail("a condição não ficou verdadeira a tempo")


# --- a faixa sozinha -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def raiz():
    try:
        janela = criar_raiz()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


@pytest.fixture
def faixa(raiz):
    chamadas = {"atualizar": [], "dispensar": 0}
    aviso = AvisoDeAtualizacao(
        raiz,
        ao_atualizar=chamadas["atualizar"].append,
        ao_dispensar=lambda: chamadas.__setitem__("dispensar", chamadas["dispensar"] + 1),
        intervalo_ms=15,
    )
    aviso.chamadas = chamadas
    yield aviso
    aviso.destroy()


def test_a_faixa_nasce_escondida_e_aparece_amarela_com_as_duas_versoes(faixa, raiz):
    assert faixa.winfo_manager() == "" and not faixa.piscando
    faixa.mostrar(INFO)
    assert faixa.winfo_manager() == "pack" and faixa.info == INFO
    texto = faixa.rotulo.cget("text")
    assert "Atualização disponível" in texto and "9.0.0" in texto and "1.7.0" in texto
    assert faixa.cget("fg_color") == aviso_mod._ACESO[0]  # amarelo vivo ao aparecer


def test_a_faixa_pisca_entre_o_amarelo_vivo_e_o_ambar(faixa, raiz):
    faixa.mostrar(INFO)
    vistas: set[str] = set()
    bombear(raiz, lambda: vistas.add(faixa.cget("fg_color")) or len(vistas) == 2)
    assert vistas == {aviso_mod._ACESO[0], aviso_mod._APAGADO[0]}
    assert faixa.piscando


def test_clicar_no_aviso_ou_no_botao_pede_a_atualizacao(faixa):
    faixa.mostrar(INFO)
    faixa.botao.invoke()
    faixa._clicou()  # o clique no texto
    assert faixa.chamadas["atualizar"] == [INFO, INFO]


def test_dispensar_esconde_para_de_piscar_e_avisa(faixa):
    faixa.mostrar(INFO)
    faixa.dispensar()
    assert faixa.winfo_manager() == "" and not faixa.piscando and faixa.info is None
    assert faixa._depois is None and faixa.chamadas["dispensar"] == 1
    faixa._clicou()  # sem versão, o clique não faz nada
    assert faixa.chamadas["atualizar"] == []


def test_durante_a_instalacao_a_faixa_fica_fixa_e_sem_botao(faixa, raiz):
    faixa.mostrar(INFO)
    faixa.mostrar_andamento("Baixando a atualização… 40%")
    assert not faixa.piscando and faixa.rotulo.cget("text") == "Baixando a atualização… 40%"
    assert str(faixa.botao.cget("state")) == "disabled"
    faixa.botao.invoke()
    faixa._clicou()
    assert faixa.chamadas["atualizar"] == []  # não dá para pedir duas vezes
    cor = faixa.cget("fg_color")
    for _ in range(10):  # e ela não pisca mais
        raiz.update()
        time.sleep(0.01)
    assert faixa.cget("fg_color") == cor == aviso_mod._ACESO[0]


def test_se_a_instalacao_falha_o_aviso_volta_piscando(faixa):
    faixa.mostrar(INFO)
    faixa.mostrar_andamento("Baixando…")
    faixa.voltar_ao_aviso()
    assert faixa.piscando and str(faixa.botao.cget("state")) == "normal"
    assert str(faixa.fechar.cget("state")) == "normal"
    assert "9.0.0" in faixa.rotulo.cget("text")


def test_cada_controle_da_faixa_tem_dica(faixa):
    for widget in (faixa.rotulo, faixa.botao, faixa.fechar):
        assert widget._dica_spurgeon.texto
