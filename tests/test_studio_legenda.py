"""Studio Shorts: a legenda acompanha o que se acrescenta e se tira, e o texto corre junto."""

from __future__ import annotations

from dataclasses import replace

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_PRONTO,
    detalhes_do_registro,
)
from spurgeon.shorts.edicao import (
    comparar_cortes,
    contexto_da_legenda,
    indices_das_palavras,
    mover_por_frase,
)
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.transcricao.formato import Transcricao
from spurgeon.ui.studio_shorts import EditorDeCortes, resumir_palavras
from tests import test_studio_shorts as base

# as fixtures e os auxiliares do outro arquivo de testes do editor
from tests.test_shorts import cortes_do_sermao, sermao_falso

# reaproveita as fixtures do outro arquivo (um import direto seria "redefinido" pelos parâmetros)
caixas, editor, pastas, raiz = base.caixas, base.editor, base.pastas, base.raiz


def tags_novas(editor) -> list[str]:
    caixa = editor._texto._textbox
    faixas = caixa.tag_ranges("novo")
    return [caixa.get(faixas[k], faixas[k + 1]) for k in range(0, len(faixas), 2)]


# --- funções puras --------------------------------------------------------------------------


def test_comparar_cortes_diz_o_que_entrou_e_o_que_saiu():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    mais_fim = mover_por_frase(palavras, corte, "fim", +1)
    m = comparar_cortes(palavras, corte, mais_fim)
    nova_ultima = indices_das_palavras(palavras, mais_fim)[1]
    novas = tuple(p.texto for p in palavras[ultima + 1 : nova_ultima + 1])
    assert m.fim_acrescentou == novas and not m.inicio_acrescentou
    assert m.ponta == "fim" and m.houve
    menos_fim = mover_por_frase(palavras, mais_fim, "fim", -1)
    assert comparar_cortes(palavras, mais_fim, menos_fim).fim_tirou == novas

    mais_inicio = mover_por_frase(palavras, corte, "inicio", -1)
    m = comparar_cortes(palavras, corte, mais_inicio)
    novo_inicio = indices_das_palavras(palavras, mais_inicio)[0]
    assert m.inicio_acrescentou == tuple(p.texto for p in palavras[novo_inicio:primeira])
    assert m.ponta == "inicio"
    menos_inicio = mover_por_frase(palavras, corte, "inicio", +1)
    assert comparar_cortes(palavras, corte, menos_inicio).inicio_tirou
    sem_mudanca = comparar_cortes(palavras, corte, corte)
    assert not sem_mudanca.houve and sem_mudanca.ponta is None


def test_comparar_cortes_fora_da_transcricao_nao_quebra():
    palavras = sermao_falso(frases=10)
    corte = cortes_do_sermao(sermao_falso(frases=100))[0]
    fora = replace(corte, inicio=9999.0, fim=10030.0)
    assert not comparar_cortes(palavras, fora, fora).houve


def test_contexto_da_legenda_traz_o_que_vem_antes_e_depois_do_corte():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    antes, depois = contexto_da_legenda(palavras, corte, quantas=5)
    assert antes.split() == [p.texto for p in palavras[primeira - 5 : primeira]]
    assert depois.split() == [p.texto for p in palavras[ultima + 1 : ultima + 6]]
    comeco = replace(corte, inicio=palavras[0].inicio, fim=palavras[0].inicio + 30)
    assert contexto_da_legenda(palavras, comeco)[0] == ""  # nada antes do começo do sermão
    assert contexto_da_legenda(palavras, replace(corte, inicio=9999.0, fim=10030.0)) == ("", "")


def test_resumir_palavras_encurta_com_reticencias_no_meio():
    assert resumir_palavras(["Deus", "é", "fiel."]) == "Deus é fiel."
    longo = resumir_palavras([f"palavra{i}" for i in range(100)], limite=60)
    assert len(longo) <= 62 and " … " in longo and longo.startswith("palavra0")
    assert longo.endswith("palavra99")


# --- no editor de verdade -------------------------------------------------------------------


@pytest.mark.gui
def test_acrescentar_uma_frase_no_fim_destaca_as_palavras_novas_na_legenda(editor):
    editor.selecionar(editor._ordem[0])
    palavras = editor._palavras
    antes = indices_das_palavras(palavras, editor._cortes[editor._atual])
    assert editor.mover_frase("fim", 1)
    depois = indices_das_palavras(palavras, editor._cortes[editor._atual])
    novas = [p.texto for p in palavras[antes[1] + 1 : depois[1] + 1]]
    assert tags_novas(editor) == novas  # as palavras da frase nova, e só elas
    rotulo = editor._rotulo_mudanca.cget("text")
    assert "acrescentou no fim" in rotulo and novas[0] in rotulo
    # a legenda mostra o texto novo no fim, como ficaria no vídeo
    assert editor._texto.get("1.0", "end-1c").split()[-len(novas) :] == novas


@pytest.mark.gui
def test_acrescentar_uma_frase_no_comeco_destaca_o_comeco_da_legenda(editor):
    editor.selecionar(editor._ordem[0])
    palavras = editor._palavras
    antes = indices_das_palavras(palavras, editor._cortes[editor._atual])
    assert editor.mover_frase("inicio", -1)
    depois = indices_das_palavras(palavras, editor._cortes[editor._atual])
    novas = [p.texto for p in palavras[depois[0] : antes[0]]]
    assert tags_novas(editor) == novas
    assert editor._texto.get("1.0", "end-1c").split()[: len(novas)] == novas
    assert "acrescentou no começo" in editor._rotulo_mudanca.cget("text")


@pytest.mark.gui
def test_tirar_uma_frase_mostra_o_que_saiu_sem_destacar_nada(editor):
    editor.selecionar(editor._ordem[0])
    palavras = editor._palavras
    antes = indices_das_palavras(palavras, editor._cortes[editor._atual])
    assert editor.mover_frase("fim", -1)
    depois = indices_das_palavras(palavras, editor._cortes[editor._atual])
    tiradas = [p.texto for p in palavras[depois[1] + 1 : antes[1] + 1]]
    rotulo = editor._rotulo_mudanca.cget("text")
    assert "tirou do fim" in rotulo and tiradas[0] in rotulo
    assert tags_novas(editor) == []
    assert tiradas[0] not in editor._texto.get("1.0", "end-1c").split()  # saiu da legenda


@pytest.mark.gui
def test_a_legenda_rola_ate_o_ponto_que_mudou(editor, raiz):
    editor.selecionar(editor._ordem[0])
    for _ in range(25):  # uma legenda comprida: o fim fica fora da vista
        if not editor.mover_frase("fim", 1):
            break
    raiz.update()
    caixa = editor._texto._textbox
    assert caixa.yview()[1] == pytest.approx(1.0, abs=0.01)  # no fim, onde a frase entrou
    assert editor.mover_frase("inicio", -1)
    raiz.update()
    assert caixa.yview()[0] == pytest.approx(0.0, abs=0.01)  # no começo, onde a outra entrou


@pytest.mark.gui
def test_o_texto_de_fora_corre_junto_nas_duas_pontas(editor):
    editor.selecionar(editor._ordem[0])
    palavras = editor._palavras
    corte = editor._cortes[editor._atual]
    primeira, ultima = indices_das_palavras(palavras, corte)
    depois = editor._contexto_depois.cget("text")
    assert palavras[primeira - 1].texto in editor._contexto_antes.cget("text")
    assert palavras[ultima + 1].texto in depois
    assert editor.mover_frase("fim", 1)
    nova_ultima = indices_das_palavras(palavras, editor._cortes[editor._atual])[1]
    assert palavras[nova_ultima + 1].texto in editor._contexto_depois.cget("text")
    assert editor._contexto_depois.cget("text") != depois  # o que há para a frente avançou


@pytest.mark.gui
def test_trocar_de_corte_limpa_o_destaque(editor):
    editor.selecionar(editor._ordem[0])
    assert editor.mover_frase("fim", 1)
    assert editor._rotulo_mudanca.cget("text")
    editor.selecionar(editor._ordem[1])
    assert editor._rotulo_mudanca.cget("text") == "" and tags_novas(editor) == []


@pytest.mark.gui
def test_desfazer_a_legenda_nao_reacende_o_destaque_antigo(editor):
    editor.selecionar(editor._ordem[0])
    assert editor.mover_frase("fim", 1)
    editor.desfazer_legenda()
    assert tags_novas(editor) == [] and editor._rotulo_mudanca.cget("text") == ""


@pytest.mark.gui
def test_ajuste_pelo_tempo_digitado_tambem_atualiza_a_legenda(editor):
    editor.selecionar(editor._ordem[0])
    palavras = editor._palavras
    _primeira, ultima = indices_das_palavras(palavras, editor._cortes[editor._atual])
    alvo = palavras[ultima + 12].fim
    editor._var_fim.set(f"{int(alvo // 60)}:{alvo % 60:04.1f}".replace(".", ","))
    assert editor.aplicar_tempo("fim")
    assert "acrescentou no fim" in editor._rotulo_mudanca.cget("text")
    assert len(tags_novas(editor)) == 12


@pytest.mark.gui
def test_editor_so_usa_a_fala_da_janela_do_video(raiz, pastas):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    todas = Transcricao.carregar(pastas.transcricao).palavras
    meio = todas[len(todas) // 2].inicio
    ed = EditorDeCortes(janela, pastas, janela_do_video=lambda: (meio, None))
    assert ed._palavras and ed._palavras[0].inicio >= meio and len(ed._palavras) < len(todas)
    janela.destroy()


@pytest.mark.gui
def test_o_selo_de_renderizado_some_quando_a_aparencia_mudou(raiz, pastas):
    import customtkinter as ctk

    cortes = carregar_cortes(pastas.cortes_shorts)
    pastas.shorts.mkdir(parents=True, exist_ok=True)
    registro = {}
    for c in cortes:
        (pastas.shorts / f"s{c.id}.mp4").write_bytes(b"x")
        registro[str(c.id)] = {
            "arquivo": f"s{c.id}.mp4",
            "assinatura": "a",
            **detalhes_do_registro(c, "visual-1"),
        }
    escrever_json_atomico(pastas.shorts_renderizados, registro)
    visual = {"v": "visual-1"}
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(janela, pastas, aparencia=lambda: visual["v"])
    assert all(ed.situacao(c) == SITUACAO_PRONTO for c in ed._cortes)
    visual["v"] = "visual-2"  # a pessoa mexeu no zoom
    assert all(ed.situacao(c) == SITUACAO_ALTERADO for c in ed._cortes)
    assert len(ed.pendentes()) == sum(1 for c in ed._cortes if c.incluir)
    janela.destroy()
