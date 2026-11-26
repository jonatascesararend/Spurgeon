"""Studio Shorts e a fila do PC: programar no YouTube (renderiza antes, se precisar), as marcas da
fila na lista, o reagendamento ao mudar o horário e o que a fila grava no disco aparecendo na tela.
Nada abre o Chrome nem renderiza: a fila é só o arquivo, e o despachante não roda aqui."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from spurgeon.canal.postar import ResultadoDaPostagem
from spurgeon.remoto import postagem
from spurgeon.shorts import fila
from spurgeon.shorts.etapas import gravar_campos_do_corte
from spurgeon.ui import dialogos
from tests import test_studio_agendamento as agenda
from tests import test_studio_shorts as base
from tests.test_studio_agendamento import _no_arquivo, _renderizado

pytestmark = pytest.mark.gui

caixas, pastas = base.caixas, base.pastas  # as fixtures compartilhadas
editor, raiz, relogio, atividade = agenda.editor, agenda.raiz, agenda.relogio, agenda.atividade


def _marcar_horario(editor, data: str, hora: str) -> None:
    linha = editor._linha_agendamento
    linha._var_marcado.set(True)
    linha._ao_marcar()
    linha._var_data.set(data)
    linha._var_hora.set(hora)


def _marca_da_linha(editor, i: int) -> str:
    rotulo = editor._rotulos_da_linha[i]["fila"]
    return rotulo.cget("text") if rotulo.winfo_manager() else ""


def test_programar_um_short_nao_renderizado_entra_na_fila(editor, pastas, caixas, atividade):
    acordou = []
    editor._publicacao.acordar_fila = lambda: acordou.append(1)
    i = editor._atual
    corte = editor._cortes[i]
    _marcar_horario(editor, "10/11/2026", "09:30")
    assert editor._linha_agendamento.botao_youtube.cget("text") == "⚙ Renderizar e programar"
    assert editor.programar_no_youtube()
    item = fila.FilaDoProjeto(pastas).do_corte(corte.id)
    assert item.acao == fila.POSTAR and item.quando == "2026-11-10T09:30"
    assert any("renderiza antes" in m for _t, m in caixas)  # a confirmação disse
    assert acordou and any("fila do YouTube" in a for a in atividade)
    assert _marca_da_linha(editor, i) == "⏳ na fila para renderizar"
    assert "Vai ser programado no YouTube para 10/11 às 09:30" in (
        editor._linha_agendamento.situacao_da_fila.cget("text")
    )
    editor.tirar_da_fila()
    assert fila.FilaDoProjeto(pastas).do_corte(corte.id) is None
    assert _marca_da_linha(editor, i) == ""


def test_programar_um_renderizado_vai_direto_para_a_fila_do_youtube(editor, pastas, caixas):
    i = editor._atual
    _renderizado(editor, pastas, editor._cortes[i])
    _marcar_horario(editor, "10/11/2026", "09:30")
    editor.ver_a_fila_agora()
    assert editor._linha_agendamento.botao_youtube.cget("text") == "📤 Programar no YouTube"
    assert editor.programar_no_youtube()
    assert _marca_da_linha(editor, i) == "🕒 na fila de programação"


def test_a_renderizacao_de_fora_marca_o_short_da_vez(editor, pastas):
    i = editor._atual
    corte = editor._cortes[i]
    outros = [c.id for c in editor._cortes if c.id != corte.id and c.incluir]
    fila.marcar_render(pastas, [corte.id, *outros], atual=corte.id, pid=os.getpid())
    editor.ver_a_fila_agora()
    assert _marca_da_linha(editor, i) == "⚙ renderizando agora"
    if outros:
        j = next(k for k, c in enumerate(editor._cortes) if c.id == outros[0])
        assert _marca_da_linha(editor, j) == "⏳ na fila para renderizar"
    assert editor._processando  # a renderização de fora não deixa mover arquivos
    fila.limpar_render(pastas)
    editor.ver_a_fila_agora()
    assert _marca_da_linha(editor, i) == "" and not editor._processando


def _programado_no_youtube(pastas, id_do_corte: int, quando: datetime) -> None:
    feito = ResultadoDaPostagem("abc12345678", "https://youtube.com/shorts/abc12345678", "",
                                quando.strftime(fila.FORMATO))  # fmt: skip
    postagem.registrar_sucesso(pastas, id_do_corte, feito, quando, mover_arquivos=False)


def test_mudar_o_horario_de_um_programado_pergunta_e_reagenda(editor, pastas, caixas):
    corte = editor._cortes[editor._atual]
    _programado_no_youtube(pastas, corte.id, datetime(2026, 11, 4, 18, 0))
    editor._sincronizar_publicacao_do_disco()
    assert corte.agendado_para == "2026-11-04T18:00"  # o que a fila gravou chegou à tela
    editor._linha_agendamento._var_hora.set("20:15")
    assert editor._reagendamento_pendente is not None  # pergunta quando parar de digitar
    editor._perguntar_se_reagenda(corte.id)
    assert caixas[-1][0] == "Reagendar no YouTube?" and "20:15" in caixas[-1][1]
    item = fila.FilaDoProjeto(pastas).do_corte(corte.id)
    assert item.acao == fila.REAGENDAR and item.quando == "2026-11-04T20:15"


def test_manter_o_horario_do_youtube_volta_o_daqui(editor, pastas, monkeypatch):
    corte = editor._cortes[editor._atual]
    _programado_no_youtube(pastas, corte.id, datetime(2026, 11, 4, 18, 0))
    editor._sincronizar_publicacao_do_disco()
    monkeypatch.setattr(dialogos, "confirmar", lambda *_a, **_k: False)
    editor._linha_agendamento._var_hora.set("20:15")
    editor._perguntar_se_reagenda(corte.id)
    assert fila.FilaDoProjeto(pastas).do_corte(corte.id) is None
    assert corte.agendado_para == "2026-11-04T18:00"
    assert _no_arquivo(pastas, corte.id).agendado_para == "2026-11-04T18:00"


def test_salvar_nao_desfaz_o_que_a_fila_gravou(editor, pastas):
    i = editor._atual
    corte = editor._cortes[i]
    editor._var_titulo.set("Outro título para salvar")
    gravar_campos_do_corte(pastas.cortes_shorts, corte.id, agendado_para="2026-11-09T19:00")
    assert editor.salvar(exigir_ate_o_limite=False)
    salvo = _no_arquivo(pastas, corte.id)
    assert salvo.agendado_para == "2026-11-09T19:00" and salvo.titulo == "Outro título para salvar"
