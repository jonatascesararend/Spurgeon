"""A janela do PC e o controle remoto (Remoto.md, R8 e S10).

O celular abre o projeto, o PC salva o que tinha em edição e trava o Studio; a revisão aprovada pelo
celular solta a janela; um processamento por vez. A janela é a compartilhada de `test_janela`.
"""

from __future__ import annotations

import pytest

from spurgeon.core.estado import EstadoEtapa, StatusEtapa, carregar_estado, salvar_estado
from spurgeon.remoto import sessao
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.ui import app as app_mod
from spurgeon.ui.painel_execucao import EstadoUI
from tests import test_janela as base

pytestmark = pytest.mark.gui

janela, tela, projeto_pronto = base.janela, base.tela, base.projeto_pronto


@pytest.fixture(autouse=True)
def celular_sai_no_fim(tela, monkeypatch):
    """Nenhum trabalhador "rodando" no PC de quem testa e, no fim, o celular sai do projeto (a
    janela é compartilhada com outros testes)."""
    monkeypatch.setattr(app_mod, "trabalhadores_rodando", lambda: [])
    tela._celular_com_o_projeto = False
    yield
    if tela._pastas is not None:
        sessao.soltar(tela._pastas)
    tela._celular_com_o_projeto = False
    tela._atualizar_aba_studio()


def test_celular_abre_o_projeto_o_pc_salva_e_trava(tela, projeto_pronto):
    pastas = projeto_pronto.pastas
    tela._abrir_projeto(pastas.raiz)
    editor = tela.studio.editor
    marcado_antes = editor._cortes[editor._atual].incluir
    editor.alternar_atual()  # uma alteração que ainda não foi salva
    assert tela.studio.mudou

    id_ = sessao.tomar(pastas)
    tela._conferir_o_celular()
    assert sessao.recibo(pastas) == id_
    assert not tela.studio.mudou  # salvou sem perguntar
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}
    assert gravado[editor._cortes[editor._atual].id].incluir is not marcado_antes
    tela._atualizar_aba_studio()
    assert "celular" in tela.studio._motivo_bloqueio
    assert sessao.janela_com_o_projeto(pastas)  # o servidor sabe que a janela está nele

    sessao.soltar(pastas)
    tela._conferir_o_celular()
    tela._atualizar_aba_studio()
    assert not tela._celular_com_o_projeto and tela.studio._motivo_bloqueio is None


def test_sem_alteracoes_so_da_o_recibo(tela, projeto_pronto, monkeypatch):
    pastas = projeto_pronto.pastas
    tela._abrir_projeto(pastas.raiz)
    salvou = []
    monkeypatch.setattr(tela.studio, "salvar_em_silencio", lambda: salvou.append(True))
    id_ = sessao.tomar(pastas)
    tela._conferir_o_celular()
    assert sessao.recibo(pastas) == id_ and salvou == []


def test_revisao_aprovada_pelo_celular_solta_a_janela(tela, projeto_pronto):
    pastas = projeto_pronto.pastas
    tela._abrir_projeto(pastas.raiz)
    estado = carregar_estado(pastas.raiz)
    estado.etapas.append(EstadoEtapa(id="aprovar_cortes", status=StatusEtapa.AGUARDANDO_REVISAO))
    salvar_estado(pastas.raiz, estado)
    tela._revisao = {"id": "aprovar_cortes", "nome": "Aprovação dos cortes"}
    tela.execucao.definir_estado(EstadoUI.REVISAO, tem_projeto=True)
    tela._conferir_o_celular()
    assert tela.execucao.estado == EstadoUI.REVISAO  # ainda esperando

    estado.etapas[-1].status = StatusEtapa.EM_ANDAMENTO  # o celular aprovou e o PC seguiu
    salvar_estado(pastas.raiz, estado)
    tela._conferir_o_celular()
    assert tela.execucao.estado == EstadoUI.OCIOSO and tela._revisao is None


def test_um_processamento_por_vez_no_pc(tela, projeto_pronto, monkeypatch):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    monkeypatch.setattr(app_mod, "trabalhadores_rodando", lambda: [4321])
    assert "celular" in tela._impedimento_do_controle_remoto()
    tela._iniciar_execucao()
    assert not tela.executor.ativo

    monkeypatch.setattr(app_mod, "trabalhadores_rodando", lambda: [])
    sessao.tomar(projeto_pronto.pastas)
    assert "editando este projeto" in tela._impedimento_do_controle_remoto()
    sessao.soltar(projeto_pronto.pastas)
    assert tela._impedimento_do_controle_remoto() == ""
