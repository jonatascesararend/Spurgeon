"""A janela principal com o painel "Ajustar o zoom e a legenda dos Shorts"."""

from __future__ import annotations

import pytest

from spurgeon.ui import app as app_mod
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


# --- o Studio Shorts como aba que ocupa a janela toda ---------------------------------------


def test_as_abas_ficam_no_alto_da_janela_sobre_as_duas_colunas(tela):
    faixa = tela._abas.master.grid_info()
    assert int(faixa["row"]) == 0 and int(faixa["columnspan"]) == 2
    assert int(tela._coluna.grid_info()["row"]) == 1 and int(tela._direita.grid_info()["row"]) == 1


def test_o_studio_shorts_toma_a_janela_inteira_e_o_processamento_volta(tela):
    assert tela._coluna.winfo_manager() == "grid" and tela._direita.winfo_manager() == "grid"
    assert tela.studio.winfo_manager() == ""
    tela._abas.set(app_mod.ABA_STUDIO)
    tela._trocar_aba(app_mod.ABA_STUDIO)
    assert tela._coluna.winfo_manager() == "" and tela._direita.winfo_manager() == ""
    info = tela.studio.grid_info()
    assert (int(info["column"]), int(info["columnspan"])) == (0, 2)
    assert tela._abas.winfo_manager() == "pack"  # a faixa das abas continua no lugar
    tela._abas.set(app_mod.ABA_PROCESSAMENTO)
    tela._trocar_aba(app_mod.ABA_PROCESSAMENTO)
    assert tela.studio.winfo_manager() == ""
    assert tela._coluna.winfo_manager() == "grid" and tela._direita.winfo_manager() == "grid"


# --- as opções que o Studio acerta olhando a prévia ----------------------------------------


def test_o_studio_acerta_a_altura_da_legenda_nos_campos_da_tela(tela):
    tela.opcoes.bools["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    tela._definir_ajustes_do_short(legenda=0.58)
    assert tela.entrada.shorts.posicao_da_legenda() == 58
    assert tela._opcoes_da_tela().legenda_posicao_vertical == pytest.approx(0.58)
    assert tela.origem_da_previa() is None  # sem vídeo escolhido não há prévia, mas nada quebra


def test_o_corte_das_pausas_do_short_nao_e_mais_uma_opcao_da_tela(tela):
    """Agora é de cada Short (no Studio): não há caixa global nem campo na coluna esquerda."""
    assert "shorts_cortar_pausas" not in tela.opcoes._por_nome
    assert "shorts_pausa" not in tela.opcoes._por_nome
    opcoes = tela._opcoes_da_tela()
    assert not hasattr(opcoes, "shorts_cortar_pausas") and not hasattr(opcoes, "shorts_pausa_s")
    tela.opcoes.aplicar_valores(ValoresFormulario())
