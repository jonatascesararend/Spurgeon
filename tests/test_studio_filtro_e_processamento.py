"""Studio Shorts: o selo e o filtro na lista e o editor durante a renderização."""

from __future__ import annotations

from dataclasses import replace

import customtkinter as ctk
import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.render.shorts import detalhes_do_registro, ler_registro, planejar_shorts
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.ui.studio_shorts import (
    FILTRO_AGENDADOS,
    FILTRO_POSTADOS,
    FILTRO_SEM_POSTAR,
    FILTRO_TODOS,
    EditorDeCortes,
)
from tests import test_studio_agendamento as base
from tests.test_studio_agendamento import _no_arquivo, _renderizado

caixas, pastas = base.caixas, base.pastas  # as fixtures compartilhadas
raiz, relogio, atividade, editor = base.raiz, base.relogio, base.atividade, base.editor

pytestmark = pytest.mark.gui


def _postar(editor, i: int, quando: str = "2026-11-02T09:15") -> None:
    editor._cortes[i].postado, editor._cortes[i].postado_em = True, quando


def _agendar(editor, i: int, quando: str = "2026-11-10T19:00") -> None:
    editor._cortes[i].agendado_para = quando


def _filtrar(editor, filtro: str) -> None:
    editor._seletor_filtro.set(filtro)
    editor._trocar_filtro()


def _selo(editor, i: int):
    return editor._rotulos_da_linha[i]["selo"]


def _tres(editor) -> tuple[int, int, int]:
    """Um corte postado, um agendado e um livre (os índices em `editor._cortes`)."""
    assert len(editor._cortes) >= 3
    _postar(editor, 0)
    _agendar(editor, 1)
    editor._reordenar()
    return 0, 1, 2


# --- o selo e o filtro na lista ------------------------------------------------------------
def test_a_lista_mostra_o_selo_so_nas_linhas_postadas_e_agendadas(editor):
    postado, agendado, livre = _tres(editor)
    assert _selo(editor, postado).cget("text") == "✔ Postado 02/11 às 09:15"
    assert _selo(editor, postado).winfo_manager()
    assert _selo(editor, agendado).cget("text") == "📅 Agendado 10/11 às 19:00"
    assert not _selo(editor, livre).winfo_manager()  # sem selo a linha não ocupa espaço
    assert "✔ postado" not in editor._rotulos_da_linha[postado]["info"].cget("text")


def test_filtros_mostram_so_o_que_pedem_e_o_resumo_diz_quantos(editor):
    postado, agendado, livre = _tres(editor)
    total = len(editor._cortes)
    _filtrar(editor, FILTRO_POSTADOS)
    assert editor._ordem == [postado]
    assert f"mostrando 1 de {total}" in editor._resumo.cget("text")
    _filtrar(editor, FILTRO_AGENDADOS)
    assert editor._ordem == [agendado]
    _filtrar(editor, FILTRO_SEM_POSTAR)
    assert postado not in editor._ordem and agendado not in editor._ordem
    assert livre in editor._ordem and len(editor._ordem) == total - 2
    _filtrar(editor, FILTRO_TODOS)
    assert len(editor._ordem) == total and "mostrando" not in editor._resumo.cget("text")


def test_mudar_o_filtro_abre_o_primeiro_da_lista_se_o_aberto_saiu(editor):
    postado, _agendado, livre = _tres(editor)
    editor.selecionar(livre)
    _filtrar(editor, FILTRO_POSTADOS)
    assert editor._atual == postado


def test_filtro_sem_nenhum_corte_avisa_e_nada_quebra(editor):
    _filtrar(editor, FILTRO_POSTADOS)  # ninguém foi postado ainda
    assert editor._ordem == [] and editor._linhas == {}
    textos = [f.cget("text") for f in editor._lista.winfo_children() if isinstance(f, ctk.CTkLabel)]
    assert "Nenhum Short neste filtro." in textos
    assert f"mostrando 0 de {len(editor._cortes)}" in editor._resumo.cget("text")
    marcados = [c.incluir for c in editor._cortes]
    editor._andar(1)
    editor._andar(-1)
    editor.marcar_todos(False)  # nada visível: não mexe em nada
    assert [c.incluir for c in editor._cortes] == marcados
    _filtrar(editor, FILTRO_TODOS)
    assert len(editor._ordem) == len(editor._cortes) and editor._linhas


def test_andar_com_o_aberto_fora_do_filtro_vai_para_a_ponta_da_lista(editor):
    postado, _agendado, livre = _tres(editor)
    _filtrar(editor, FILTRO_POSTADOS)
    editor.selecionar(livre)  # aberto, mas o filtro não o mostra
    editor._andar(1)
    assert editor._atual == postado


def test_marcar_todos_so_vale_para_o_que_o_filtro_mostra(editor):
    postado, agendado, livre = _tres(editor)
    for corte in editor._cortes:
        corte.incluir = True
    _filtrar(editor, FILTRO_POSTADOS)
    editor.marcar_todos(False)
    assert not editor._cortes[postado].incluir
    assert editor._cortes[agendado].incluir and editor._cortes[livre].incluir


def test_marcar_postado_com_filtro_mantem_a_linha_ate_a_lista_ser_refeita(editor, pastas):
    _filtrar(editor, FILTRO_SEM_POSTAR)
    aberto = editor._atual
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert aberto in editor._ordem  # não some debaixo do mouse
    assert _selo(editor, aberto).cget("text") == "✔ Postado 03/11 às 12:00"
    assert _no_arquivo(pastas, editor._cortes[aberto].id).postado_em == "2026-11-03T12:00"
    _filtrar(editor, FILTRO_SEM_POSTAR)  # refeita: agora ele não é mais "sem postar"
    assert aberto not in editor._ordem


def test_corte_novo_aparece_mesmo_com_o_filtro_ligado(editor):
    _tres(editor)
    _filtrar(editor, FILTRO_POSTADOS)
    novo = replace(editor._cortes[0], id=900, postado=False, postado_em="", titulo="Um Short novo")
    editor.adicionar_corte(novo)
    indice = len(editor._cortes) - 1
    assert indice in editor._ordem and editor._atual == indice


def test_ordenar_continua_valendo_dentro_do_filtro(editor):
    _tres(editor)
    _postar(editor, 2)
    editor._reordenar()
    _filtrar(editor, FILTRO_POSTADOS)
    editor._seletor_ordem.set("Horário")
    editor._reordenar()
    inicios = [editor._cortes[i].inicio for i in editor._ordem]
    assert len(inicios) == 2 and inicios == sorted(inicios)


def test_mudar_as_pontas_com_filtro_nao_refaz_a_lista_nem_quebra(editor):
    postado, *_ = _tres(editor)
    _filtrar(editor, FILTRO_POSTADOS)
    editor.selecionar(postado)
    antes = dict(editor._linhas)
    editor.mover("fim", 1)
    assert editor._linhas == antes and editor._ordem == [postado]


# --- o editor durante a renderização -------------------------------------------------------
def _no_registro(pastas, corte: Corte) -> str:
    return ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]


def test_marcar_postado_durante_o_processamento_grava_na_hora_e_adia_o_arquivo(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor.definir_processamento(True)
    editor._var_postado.set(True)
    editor._ao_marcar_postado()

    assert _no_arquivo(pastas, corte.id).postado  # a marca vale na hora
    assert _no_registro(pastas, corte) == antigo  # o registro e o arquivo não se mexeram
    assert (pastas.shorts / antigo).exists()
    assert "quando o processamento terminar" in editor._rotulo_postado.cget("text")
    assert _selo(editor, editor._atual).cget("text").startswith("✔ Postado")

    editor.definir_processamento(False)  # acabou: o arquivo vai para postados/
    novo = _no_registro(pastas, corte)
    assert novo == f"postados/{antigo}" and (pastas.shorts / novo).exists()
    assert not (pastas.shorts / antigo).exists()
    assert "quando o processamento terminar" not in editor._rotulo_postado.cget("text")


def test_agendar_durante_o_processamento_adia_o_arquivo_e_avisa_sob_o_horario(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor.definir_processamento(True)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()

    assert _no_arquivo(pastas, corte.id).agendado_para == "2026-11-04T18:00"
    assert _no_registro(pastas, corte) == antigo
    assert "quando o processamento terminar" in editor._linha_agendamento.resumo.cget("text")

    editor.definir_processamento(False)
    assert _no_registro(pastas, corte) == f"agendados/{antigo}"


def test_salvar_com_titulo_novo_durante_o_processamento_so_renomeia_no_fim(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor.definir_processamento(True)
    editor._var_titulo.set("Deus governa os dias do homem")
    assert editor.salvar(exigir_ate_o_limite=False)

    assert _no_arquivo(pastas, corte.id).titulo == "Deus governa os dias do homem"
    assert _no_registro(pastas, corte) == antigo and (pastas.shorts / antigo).exists()

    editor.definir_processamento(False)
    novo = _no_registro(pastas, corte)
    assert novo != antigo and (pastas.shorts / novo).exists()
    assert not (pastas.shorts / antigo).exists()
    texto = (pastas.shorts / novo).with_suffix(".txt").read_text("utf-8")
    assert "Título: Deus governa os dias do homem" in texto


def test_o_fim_do_processamento_so_mexe_no_que_estava_salvo(editor, pastas):
    """O título digitado e ainda não salvo não renomeia o arquivo nem é gravado ao terminar."""
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor.definir_processamento(True)
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    editor._var_titulo.set("Título que ainda está sendo digitado")
    editor.definir_processamento(False)
    assert _no_registro(pastas, corte) == f"postados/{antigo}"  # o postado, sim
    assert _no_arquivo(pastas, corte.id).titulo != "Título que ainda está sendo digitado"
    assert editor._var_titulo.get() == "Título que ainda está sendo digitado"  # a tela segue
    assert editor.mudou


def test_sem_processamento_tudo_continua_na_hora(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert _no_registro(pastas, corte) == f"postados/{antigo}"
    assert "quando o processamento terminar" not in editor._rotulo_postado.cget("text")


def test_editor_criado_durante_o_processamento_nao_move_arquivos_na_carga(raiz, pastas):
    """A aba recria o editor quando o Studio se solta (a renderização começou): a carga também
    aplicaria as pastas dos marcados, e isso tem que esperar."""
    cortes = carregar_cortes(pastas.cortes_shorts)
    corte = next(c for c in cortes if c.incluir)
    video = planejar_shorts([corte])[0].video  # renderizado ANTES de ser marcado: fora de postados/
    arquivo = pastas.shorts / video
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_bytes(b"video")
    escrever_json_atomico(
        pastas.shorts_renderizados,
        {str(corte.id): {"arquivo": video, "assinatura": "a", **detalhes_do_registro(corte)}},
    )
    corte.postado, corte.postado_em = True, "2026-11-02T09:15"
    gravar_cortes(pastas.cortes_shorts, cortes)

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    try:
        durante = EditorDeCortes(janela, pastas, legenda_queimada=lambda: True, processando=True)
        assert _no_registro(pastas, corte) == video  # ficou onde estava
        durante.definir_processamento(False)
        assert _no_registro(pastas, corte).startswith("postados/")
        durante.destroy()
    finally:
        janela.destroy()
