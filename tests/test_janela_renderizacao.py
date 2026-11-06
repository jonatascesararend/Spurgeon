"""A janela principal: o Studio Shorts durante a renderização e o "Fechar o programa ao terminar".

A janela é a compartilhada de `test_janela`; nada aqui fecha a janela de verdade (o `destroy` é
trocado por uma função que só anota).
"""

from __future__ import annotations

import logging

import pytest

from spurgeon.ui import app as app_mod
from spurgeon.ui.painel_execucao import EstadoUI
from tests import test_janela as base

pytestmark = pytest.mark.gui

janela, tela, projeto_pronto = base.janela, base.tela, base.projeto_pronto

ETAPAS_DE_RENDER = (
    "renderizar_video_longo",
    "adicionar_vinhetas",
    "renderizar_shorts",
    "finalizar",
)
ETAPAS_ANTES = (None, "extrair_audio", "transcrever", "avaliar_shorts", "selecionar_shorts")


def _rodando(tela, etapa: str | None) -> None:
    """O trabalhador está na etapa `etapa` (a janela só vê o estado e o último evento)."""
    tela.execucao.definir_estado(EstadoUI.EXECUTANDO)
    tela._etapa_atual = etapa
    tela._atualizar_aba_studio()


def _editor_a_vista(tela) -> bool:
    return tela.studio.editor._corpo.winfo_manager() == "grid"


# --- o Studio fica à mão na renderização ---------------------------------------------------
@pytest.mark.parametrize("etapa", ETAPAS_DE_RENDER)
def test_studio_fica_a_mao_nas_etapas_de_renderizacao(tela, projeto_pronto, etapa):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, etapa)
    assert tela.studio._motivo_bloqueio is None and _editor_a_vista(tela)
    assert tela.studio.processando and tela.studio.editor._processando
    assert str(tela.studio._botao_renderizar.cget("state")) == "disabled"
    assert "Renderização em andamento" in tela.studio._estado.cget("text")
    assert str(tela.studio._botao_salvar.cget("state")) == "disabled"  # nada para salvar ainda


@pytest.mark.parametrize("etapa", ETAPAS_ANTES)
def test_studio_continua_travado_ate_a_renderizacao_comecar(tela, projeto_pronto, etapa):
    """Antes dela os cortes ainda podem ser reescritos (`selecionar_shorts`): o editor some."""
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, etapa)
    assert "em andamento" in tela.studio._motivo_bloqueio and not _editor_a_vista(tela)
    assert tela.studio.editor._processando  # e, escondido, também não mexe nos arquivos


def test_pausado_na_renderizacao_o_studio_continua_a_mao(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    tela.execucao.definir_estado(EstadoUI.PAUSADO)
    tela._atualizar_aba_studio()
    assert tela.studio._motivo_bloqueio is None and tela.studio.processando


def test_o_editor_e_relido_quando_o_studio_se_solta(tela, projeto_pronto):
    """Enquanto esteve escondido, a seleção dos cortes pode ter reescrito o arquivo."""
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "selecionar_shorts")
    escondido = tela.studio.editor
    _rodando(tela, "renderizar_shorts")
    assert tela.studio.editor is not escondido and tela.studio.editor._processando
    assert _editor_a_vista(tela)


def test_ao_terminar_o_studio_volta_ao_normal(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    tela.execucao.definir_estado(EstadoUI.CONCLUIDO)
    tela._atualizar_aba_studio()
    assert not tela.studio.processando and not tela.studio.editor._processando
    assert str(tela.studio._botao_renderizar.cget("state")) == "normal"


def test_fim_do_processamento_nao_perde_alteracoes_nao_salvas(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    editor = tela.studio.editor
    editor.alternar_atual()  # uma alteração que ainda não foi salva
    assert editor.mudou
    tela._tratar_evento({"tipo": "fim", "resultado": "concluido"})
    assert tela.studio.editor is editor and editor.mudou
    assert not tela.studio.processando and not editor._processando


def test_fim_do_processamento_sem_alteracoes_relê_o_projeto(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    editor = tela.studio.editor
    tela._tratar_evento({"tipo": "fim", "resultado": "concluido"})
    assert tela.studio.editor is not editor and not tela.studio.editor.mudou


def test_renderizar_durante_a_renderizacao_so_avisa(tela, projeto_pronto, monkeypatch):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    chamadas = []
    monkeypatch.setattr(tela, "_iniciar", lambda ajustes=None: chamadas.append(ajustes))
    tela._renderizar_do_studio()
    assert chamadas == []
    assert any(
        t == "Renderizar os Shorts" and "em andamento" in m for t, m in tela.caixas.mostradas
    )


def test_a_aba_do_studio_nao_e_travada_pelo_processamento_na_renderizacao(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    assert tela._abas._buttons_dict[app_mod.ABA_STUDIO].cget("state") == "normal"


# --- fechar o programa ao terminar ---------------------------------------------------------
@pytest.fixture
def fecha(tela, monkeypatch):
    """O que o programa faria ao fechar (o `destroy` de verdade não roda) e o que ele disse."""
    chamadas: dict[str, list] = {"destroy": [], "log": []}
    monkeypatch.setattr(tela, "destroy", lambda: chamadas["destroy"].append(True))
    monkeypatch.setattr(app_mod, "projetos_com_fila", lambda _p: [])
    monkeypatch.setattr(
        tela.execucao, "adicionar_log", lambda t, nivel=logging.INFO: chamadas["log"].append(t)
    )
    yield chamadas
    tela._var_fechar.set(False)
    tela._cancelar_fechamento()
    tela._fila_rodando = False


def _terminou(tela, resultado: str = "concluido") -> None:
    tela._tratar_evento({"tipo": "fim", "resultado": resultado, "erro": "falhou"})


def test_a_caixa_fica_ao_lado_da_atualizacao_e_nao_vai_para_o_config(tela):
    assert tela._check_fechar.cget("text") == "Fechar o programa ao terminar"
    assert tela._var_fechar.get() is False  # sempre desmarcada ao abrir
    assert int(tela._check_atualizacao.grid_info()["column"]) == 1
    assert int(tela._check_fechar.grid_info()["column"]) == 2
    assert not any("fechar" in nome for nome in tela.config_app.model_dump())


def test_desmarcada_o_programa_fica_aberto(tela, projeto_pronto, fecha):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela)
    assert tela._fechamento_agendado is None and fecha["destroy"] == []


def test_marcada_agenda_o_fechamento_e_fecha_depois_da_espera(tela, projeto_pronto, fecha):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela)
    assert tela._fechamento_agendado is not None
    assert any("se fecha sozinho" in t for t in fecha["log"])
    assert fecha["destroy"] == []  # ainda na espera
    tela._fechar_ao_terminar()  # o `after` de verdade só dispara em 10 s
    assert fecha["destroy"] == [True]


@pytest.mark.parametrize("resultado", ["erro", "cancelado", "aguardando_revisao"])
def test_so_fecha_quando_terminou_bem(tela, projeto_pronto, fecha, resultado):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela, resultado)
    assert tela._fechamento_agendado is None and fecha["destroy"] == []


def test_desmarcar_na_espera_cancela_o_fechamento(tela, projeto_pronto, fecha):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela)
    assert tela._fechamento_agendado is not None
    tela._var_fechar.set(False)
    tela._mudou_fechar_ao_terminar()
    assert tela._fechamento_agendado is None
    tela._fechar_ao_terminar()
    assert fecha["destroy"] == []


def test_nao_fecha_com_alteracoes_nao_salvas_no_studio(tela, projeto_pronto, fecha):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    tela.studio.editor.alternar_atual()
    _terminou(tela)
    tela._fechar_ao_terminar()
    assert fecha["destroy"] == []
    assert any("alterações não salvas" in t for t in fecha["log"])


def test_nao_fecha_com_postagens_na_fila_do_spurgeon(tela, projeto_pronto, fecha, monkeypatch):
    monkeypatch.setattr(app_mod, "projetos_com_fila", lambda _p: [object()])
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela)
    tela._fechar_ao_terminar()
    assert fecha["destroy"] == [] and any("fila do Spurgeon" in t for t in fecha["log"])


def test_espera_a_postagem_que_esta_saindo(tela, projeto_pronto, fecha):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela)
    tela._fila_rodando = True
    tela._fechar_ao_terminar()
    assert fecha["destroy"] == [] and tela._fechamento_agendado is not None  # tenta de novo
    tela._fila_rodando = False
    tela._fechar_ao_terminar()
    assert fecha["destroy"] == [True]


def test_nao_fecha_se_comecou_outro_processamento(tela, projeto_pronto, fecha):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._var_fechar.set(True)
    _rodando(tela, "renderizar_shorts")
    _terminou(tela)
    tela.execucao.definir_estado(EstadoUI.EXECUTANDO)  # a pessoa já iniciou outro
    tela._fechar_ao_terminar()
    assert fecha["destroy"] == [] and any("outro processamento" in t for t in fecha["log"])
