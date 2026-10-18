"""A janela principal com o painel "Ajustar o zoom e a legenda dos Shorts"."""

from __future__ import annotations

import pytest

from spurgeon.ui.formulario import ValoresFormulario, para_opcoes
from spurgeon.ui.painel_shorts import PainelShorts
from tests import test_janela as base

pytestmark = pytest.mark.gui

janela, tela = base.janela, base.tela  # as fixtures da janela compartilhada


def test_a_janela_tem_o_painel_e_a_legenda_saiu_das_opcoes_avancadas(tela):
    assert isinstance(tela.entrada.shorts, PainelShorts)
    assert "posicao_legenda" not in tela.opcoes.textos
    assert "posicao_legenda" in tela._valores().__dict__


def test_os_valores_do_painel_chegam_as_opcoes_do_processamento(tela):
    tela.entrada.shorts.zoom.set(140)
    tela.entrada.shorts.legenda.set(55)
    tela.entrada.shorts._moveu_zoom()
    opcoes = para_opcoes(tela._valores(), tela.config_app.padroes)
    assert opcoes.shorts_zoom_pct == 140.0
    assert opcoes.legenda_posicao_vertical == pytest.approx(0.55)
    assert tela._opcoes_da_tela().enquadramento_do_short.zoom_pct == 140.0


def test_o_painel_so_fica_ativo_com_shorts(tela):
    tela._atualizar_estimativa()
    assert str(tela.entrada.shorts._filhos[0].cget("state")) == "disabled"
    tela.opcoes.bools["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    assert str(tela.entrada.shorts._filhos[0].cget("state")) == "normal"
    tela._bloquear(True)
    assert str(tela.entrada.shorts._filhos[0].cget("state")) == "disabled"
    tela._bloquear(False)
    tela.opcoes.definir_modo("so_vinhetas")
    tela._atualizar_estimativa()
    assert str(tela.entrada.shorts._filhos[0].cget("state")) == "disabled"
    tela.opcoes.definir_modo("completo")
    tela._atualizar_estimativa()


def test_abrir_projeto_poe_o_zoom_e_a_legenda_do_projeto_na_tela(tela):
    tela.entrada.aplicar_valores(ValoresFormulario(shorts_zoom_pct=125.0, posicao_legenda="48"))
    assert tela.entrada.valores()["shorts_zoom_pct"] == 125.0
    assert tela.entrada.valores()["posicao_legenda"] == "48"
    tela.entrada.aplicar_valores(ValoresFormulario())
