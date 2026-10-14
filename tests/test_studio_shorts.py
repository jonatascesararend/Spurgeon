"""Editor de cortes (aba Studio Shorts): tempos, ajuste por frase, legenda e estado de render."""

from __future__ import annotations

import contextlib
import re
import tkinter

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_NOVO,
    SITUACAO_PRONTO,
    detalhes_do_registro,
    ler_registro,
    situacao_do_short,
)
from spurgeon.shorts.edicao import indices_das_palavras
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.transcricao.formato import Transcricao
from spurgeon.ui import dialogos
from spurgeon.ui.studio_shorts import EditorDeCortes, trecho_da_legenda
from tests.test_shorts import cortes_do_sermao, gravar_transcricao, sermao_falso

# --- funções puras -------------------------------------------------------------------------


def corte_exemplo():
    return cortes_do_sermao(sermao_falso(frases=100))[0]


def test_registro_novo_vira_pronto_e_muda_quando_o_corte_muda():
    corte = corte_exemplo()
    entrada = {"arquivo": "short_01_x.mp4", "assinatura": "abc", **detalhes_do_registro(corte)}
    assert situacao_do_short(corte, None, False, com_legenda=False) == SITUACAO_NOVO
    assert situacao_do_short(corte, entrada, False, com_legenda=False) == SITUACAO_NOVO
    assert situacao_do_short(corte, entrada, True, com_legenda=False) == SITUACAO_PRONTO
    mexido = type(corte).de_dict({**corte.para_dict(), "fim": corte.fim - 1.0})
    assert situacao_do_short(mexido, entrada, True, com_legenda=False) == SITUACAO_ALTERADO


def test_legenda_corrigida_so_conta_quando_a_legenda_e_queimada():
    corte = corte_exemplo()
    entrada = {"arquivo": "a.mp4", "assinatura": "x", **detalhes_do_registro(corte)}
    corrigido = type(corte).de_dict({**corte.para_dict(), "legenda": "Texto corrigido"})
    assert situacao_do_short(corrigido, entrada, True, com_legenda=False) == SITUACAO_PRONTO
    assert situacao_do_short(corrigido, entrada, True, com_legenda=True) == SITUACAO_ALTERADO


def test_registro_antigo_sem_detalhes_vale_como_pronto():
    corte = corte_exemplo()
    antigo = {"arquivo": "a.mp4", "assinatura": "x"}
    assert situacao_do_short(corte, antigo, True, com_legenda=True) == SITUACAO_PRONTO


def test_trecho_da_legenda_corta_com_reticencias_e_prefere_a_legenda_corrigida():
    palavras = sermao_falso(frases=100)
    corte = corte_exemplo()
    longo = trecho_da_legenda(palavras, corte, limite=40)
    assert len(longo) <= 40 and longo.endswith("…")
    editado = type(corte).de_dict({**corte.para_dict(), "legenda": "Isto está corrigido."})
    assert trecho_da_legenda(palavras, editado) == "Isto está corrigido."
    assert trecho_da_legenda([], corte, limite=2000) == " ".join(corte.texto.split())


# --- editor de verdade ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def raiz():
    import customtkinter as ctk

    try:
        janela = ctk.CTk()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    palavras = sermao_falso(frases=100)
    gravar_transcricao(pastas.transcricao, palavras)
    cortes = cortes_do_sermao(palavras)
    for n, c in enumerate(cortes, start=1):
        c.titulo = f"Título {n}"
        c.descricao = f"Descrição {n}"
        c.hashtags = ["#shorts"]
        c.slug = f"titulo_{n}"
    gravar_cortes(pastas.cortes_shorts, cortes)
    return pastas


@pytest.fixture(autouse=True)
def caixas(monkeypatch):
    vistas: list[tuple[str, str]] = []

    def mostrar(_janela, titulo, mensagem, *, botoes=("OK",), tipo="info"):
        vistas.append((titulo, mensagem))
        return botoes[-1]

    monkeypatch.setattr(dialogos, "mostrar", mostrar)
    return vistas


@pytest.fixture
def editor(raiz, pastas):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(janela, pastas, legenda_queimada=lambda: True)
    ed.pack(fill="both", expand=True)
    ed.pastas = pastas
    yield ed
    janela.destroy()


def palavras_de(pastas):
    return Transcricao.carregar(pastas.transcricao).palavras


@pytest.mark.gui
def test_a_lista_mostra_o_trecho_da_legenda_de_cada_corte(editor):
    textos = []

    def coletar(widget):
        for filho in widget.winfo_children():
            if hasattr(filho, "cget"):
                with contextlib.suppress(tkinter.TclError, ValueError):
                    textos.append(filho.cget("text"))
            coletar(filho)

    coletar(editor._lista)
    primeiro = editor._cortes[0]
    trecho = " ".join(primeiro.texto.split())[:40]
    assert any(trecho in t for t in textos)


@pytest.mark.gui
def test_o_campo_de_tempo_mostra_o_inicio_e_o_fim_do_corte(editor):
    editor.selecionar(editor._ordem[0])
    corte = editor._cortes[editor._atual]
    assert editor._var_inicio.get() != "" and editor._var_fim.get() != ""
    assert "," in editor._var_inicio.get()
    assert corte.fim > corte.inicio


@pytest.mark.gui
def test_digitar_o_tempo_leva_o_fim_ao_limite_de_palavra_mais_perto(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    antes = editor._cortes[i]
    _primeira, ultima = indices_das_palavras(palavras, antes)
    alvo = palavras[ultima - 12].fim  # uma frase inteira (12 palavras) a menos
    editor._var_fim.set(f"{int(alvo // 60)}:{alvo % 60:04.1f}".replace(".", ","))
    assert editor.aplicar_tempo("fim")
    depois = editor._cortes[i]
    assert indices_das_palavras(palavras, depois)[1] == ultima - 12
    assert depois.editado and depois.fim < antes.fim


@pytest.mark.gui
def test_tempo_digitado_invalido_avisa_e_restaura_o_campo(editor):
    editor.selecionar(editor._ordem[0])
    original = editor._var_inicio.get()
    editor._var_inicio.set("abc")
    assert editor.aplicar_tempo("inicio") is False
    assert "Tempo inválido" in editor._rotulo_ajuste.cget("text")
    assert editor._var_inicio.get() == original


@pytest.mark.gui
def test_acrescentar_uma_frase_pelo_inicio_inclui_a_frase_anterior(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    assert editor.mover_frase("inicio", -1)
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira - 12, ultima)
    assert editor.mover_frase("inicio", 1)
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira, ultima)


@pytest.mark.gui
def test_aviso_de_inicio_no_meio_da_frase(editor):
    editor.selecionar(editor._ordem[0])
    assert editor._rotulo_avisos.cget("text") == ""
    assert editor.mover("inicio", 4)
    assert "começa no meio de uma frase" in editor._rotulo_avisos.cget("text").lower()


@pytest.mark.gui
def test_corrigir_a_legenda_guarda_no_corte_e_no_arquivo(editor, pastas):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    original = editor._texto.get("1.0", "end-1c")
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", original.replace("verdade", "verdadeiro", 1))
    editor._ao_editar_legenda()
    assert "legenda editada" in editor._rotulo_editada.cget("text")
    assert editor.salvar()
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[editor._cortes[i].id]
    assert gravado.legenda.startswith("verdadeiro")
    assert gravado.legenda != original
    assert editor.mudou is False


@pytest.mark.gui
def test_legenda_igual_a_da_transcricao_nao_conta_como_editada(editor):
    editor.selecionar(editor._ordem[0])
    texto = editor._texto.get("1.0", "end-1c")
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", texto + "  ")  # só espaço a mais
    editor._ao_editar_legenda()
    editor._guardar_detalhe()
    assert editor._cortes[editor._atual].legenda == ""
    assert editor._rotulo_editada.cget("text") == ""


@pytest.mark.gui
def test_desfazer_correcoes_volta_ao_texto_da_transcricao(editor):
    editor.selecionar(editor._ordem[0])
    original = editor._texto.get("1.0", "end-1c")
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", "Texto totalmente diferente.")
    editor._guardar_detalhe()
    assert editor._cortes[editor._atual].legenda
    editor.desfazer_legenda()
    assert editor._cortes[editor._atual].legenda == ""
    assert editor._texto.get("1.0", "end-1c") == original


@pytest.mark.gui
def test_mexer_no_inicio_descarta_a_legenda_corrigida_e_avisa(editor):
    editor.selecionar(editor._ordem[0])
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", "Legenda corrigida pela pessoa.")
    assert editor.mover("fim", -3)
    corte = editor._cortes[editor._atual]
    assert corte.legenda == ""
    assert "correções da legenda foram descartadas" in editor._rotulo_ajuste.cget("text")


@pytest.mark.gui
def test_o_estado_de_render_aparece_na_lista_e_nos_pendentes(editor, pastas):
    corte = editor._cortes[0]
    pastas.shorts.mkdir(parents=True, exist_ok=True)
    (pastas.shorts / "short_01_x.mp4").write_bytes(b"x")
    escrever_json_atomico(
        pastas.shorts_renderizados,
        {
            str(corte.id): {
                "arquivo": "short_01_x.mp4",
                "assinatura": "a",
                **detalhes_do_registro(corte),
            }
        },
    )
    editor.recarregar_registro()
    assert editor.situacao(corte) == SITUACAO_PRONTO
    assert corte not in editor.pendentes()
    assert any(c is not corte and c.incluir for c in editor.pendentes())
    assert "por renderizar" in editor._resumo.cget("text")
    # mexer no fim do corte o torna pendente de novo
    editor.selecionar(editor._cortes.index(corte))
    assert editor.mover("fim", -3)
    assert editor.situacao(editor._cortes[0]) == SITUACAO_ALTERADO
    assert editor._cortes[0] in editor.pendentes()
    assert ler_registro(pastas.shorts_renderizados)  # o registro em si não foi tocado


@pytest.mark.gui
def test_bloquear_esconde_o_editor_e_mostra_o_motivo(editor):
    editor.bloquear("O processamento está em andamento.")
    assert editor._aviso_bloqueio.winfo_manager() == "grid"
    assert editor._corpo.winfo_manager() == ""
    editor.bloquear(None)
    assert editor._corpo.winfo_manager() == "grid"
    assert editor._aviso_bloqueio.winfo_manager() == ""


@pytest.mark.gui
def test_sem_cortes_o_editor_abre_vazio_sem_erro(raiz, tmp_path):
    import customtkinter as ctk

    vazio = PastasProjeto(tmp_path / "vazio")
    vazio.criar()
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    try:
        ed = EditorDeCortes(janela, vazio)
        assert ed._cortes == [] and ed._atual is None
        assert ed.pendentes() == []
    finally:
        janela.destroy()


@pytest.mark.gui
def test_tempo_que_passa_dos_60_segundos_e_aceito_com_aviso(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    corte = editor._cortes[i]
    editor._var_fim.set(f"{int((corte.fim + 30) // 60)}:{(corte.fim + 30) % 60:04.1f}")
    assert editor.aplicar_tempo("fim") is True
    assert editor._cortes[i].fim > corte.fim and editor._cortes[i].duracao > 60.0
    assert "não pode ser um Short" in editor._rotulo_limite.cget("text")
    assert str(editor._rotulo_tempo.cget("text_color")) == "#e05555"


@pytest.mark.gui
def test_o_aviso_dos_60_segundos_some_ao_voltar_para_o_limite(editor):
    editor.selecionar(editor._ordem[0])
    for _ in range(80):
        editor.mover("fim", 1)
    assert editor._rotulo_limite.cget("text")
    while editor._cortes[editor._atual].duracao > 60.0:
        assert editor.mover("fim", -1)
    assert editor._rotulo_limite.cget("text") == ""


@pytest.mark.gui
def test_salvar_exige_ate_60s_mas_o_trabalho_em_andamento_pode_ser_salvo(editor, pastas):
    editor.selecionar(editor._ordem[0])
    for _ in range(80):
        editor.mover("fim", 1)
    assert len(editor.cortes_acima_do_limite()) == 1
    assert editor.salvar() is False
    assert editor.salvar(exigir_ate_60s=False) is True
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[editor._cortes[0].id]
    assert gravado.duracao > 60.0


@pytest.mark.gui
def test_a_janela_do_inicio_deixa_rolar_muito_texto_antes_e_depois_da_marca(editor):
    editor.selecionar(editor._ordem[0])
    corte = editor._cortes[editor._atual]
    primeira, _ultima = indices_das_palavras(editor._palavras, corte)
    texto = editor._janelas["inicio"].get("1.0", "end-1c")
    assert "▶ COMEÇA AQUI ▶" in texto
    antes, depois = texto.split("▶ COMEÇA AQUI ▶")
    assert editor._palavras[primeira - 1].texto in antes
    assert editor._palavras[primeira].texto in depois
    assert len(antes.split()) > 100  # bastante contexto para rolar, além do que cabe na tela
    caixa = editor._janelas["inicio"]._textbox
    fora = caixa.tag_ranges("fora")
    assert fora and caixa.tag_ranges("dentro")


@pytest.mark.gui
def test_a_janela_do_fim_mostra_o_que_vem_depois(editor):
    editor.selecionar(editor._ordem[0])
    corte = editor._cortes[editor._atual]
    _primeira, ultima = indices_das_palavras(editor._palavras, corte)
    texto = editor._janelas["fim"].get("1.0", "end-1c")
    assert "◀ TERMINA AQUI ◀" in texto
    antes, depois = texto.split("◀ TERMINA AQUI ◀")
    assert editor._palavras[ultima].texto in antes
    assert editor._palavras[ultima + 1].texto in depois
    assert len(depois.split()) > 100


@pytest.mark.gui
def test_as_frases_ficam_uma_por_linha_na_janela(editor):
    editor.selecionar(editor._ordem[0])
    texto = editor._janelas["fim"].get("1.0", "end-1c")
    linhas = [linha for linha in texto.split("\n") if linha.strip()]
    assert len(linhas) > 10
    assert all(linha.rstrip().endswith((".", "?", "!")) or "AQUI" in linha for linha in linhas[:-1])


def clique_na_palavra(editor, ponta, indice):
    """Simula o duplo clique sobre a palavra `indice` da janela da ponta."""
    caixa = editor._janelas[ponta]._textbox
    inicio = str(caixa.tag_ranges(f"pal_{indice}")[0])
    # a janela de teste está escondida (sem medidas de tela): o índice sob o clique é dado direto
    original = caixa.index
    caixa.index = lambda ref: inicio if str(ref).startswith("@") else original(ref)
    try:
        return editor._duplo_clique(ponta, type("Evento", (), {"x": 1, "y": 1})())
    finally:
        del caixa.index


@pytest.mark.gui
def test_duplo_clique_numa_palavra_da_janela_move_o_inicio_para_ela(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    assert clique_na_palavra(editor, "inicio", primeira - 12) == "break"
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira - 12, ultima)


@pytest.mark.gui
def test_duplo_clique_numa_palavra_da_janela_do_fim_termina_o_corte_nela(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    clique_na_palavra(editor, "fim", ultima - 12)
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira, ultima - 12)


@pytest.mark.gui
def test_a_legenda_aparece_dividida_em_blocos_de_ate_duas_linhas(editor):
    editor.selecionar(editor._ordem[0])
    texto = editor._texto.get("1.0", "end-1c")
    blocos = texto.split("\n\n")
    assert len(blocos) > 3
    for bloco in blocos:
        linhas = bloco.split("\n")
        assert 1 <= len(linhas) <= 2
        assert all(len(linha) <= 24 + 12 for linha in linhas)  # a linha da legenda é curta


@pytest.mark.gui
def test_a_legenda_do_editor_e_a_que_o_video_vai_mostrar(editor):
    from spurgeon.render.legenda import montar_eventos, palavras_do_short
    from spurgeon.shorts.edicao import palavras_com_a_legenda

    editor.selecionar(editor._ordem[0])
    corte = editor._cortes[editor._atual]
    faladas = palavras_do_short(
        palavras_com_a_legenda(editor._palavras, corte), corte.inicio, corte.fim
    )
    na_tela = []
    for _inicio, _fim, texto in montar_eventos(faladas):
        limpo = re.sub(r"\{[^}]*\}", "", texto).replace("\\N", "\n")
        if not na_tela or na_tela[-1] != limpo:
            na_tela.append(limpo)
    assert editor._texto.get("1.0", "end-1c").split("\n\n") == na_tela


@pytest.mark.gui
def test_editar_a_legenda_e_sair_do_campo_refaz_as_quebras_sem_mexer_nas_palavras(editor):
    editor.selecionar(editor._ordem[0])
    caixa = editor._texto._textbox
    # junta tudo numa linha só e acrescenta uma palavra grande no começo
    palavras = editor._texto.get("1.0", "end-1c").split()
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", "Extraordinariamente " + " ".join(palavras))
    editor._ao_editar_legenda()
    editor._reorganizar_legenda()
    texto = caixa.get("1.0", "end-1c")
    assert texto.split() == ["Extraordinariamente", *palavras]  # as palavras são as mesmas
    assert "\n\n" in texto and texto.startswith("Extraordinariamente")
    assert "legenda editada" in editor._rotulo_editada.cget("text")
    assert all(len(b.split("\n")) <= 2 for b in texto.split("\n\n"))


@pytest.mark.gui
def test_a_reorganizacao_nao_apaga_as_marcas_das_palavras(editor):
    editor.selecionar(editor._ordem[0])
    caixa = editor._texto._textbox
    caixa.tag_add("marca_teste", "1.0", "1.5")
    editor._texto.insert("end", " ")  # só mexe nos espaços
    editor._reorganizar_legenda()
    assert caixa.tag_ranges("marca_teste")


@pytest.mark.gui
def test_legenda_sem_mudar_palavras_nao_conta_como_editada_depois_de_reorganizar(editor):
    editor.selecionar(editor._ordem[0])
    texto = editor._texto.get("1.0", "end-1c")
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", texto.replace("\n\n", " ").replace("\n", " "))
    editor._reorganizar_legenda()
    editor._guardar_detalhe()
    assert editor._cortes[editor._atual].legenda == ""
    assert editor._texto.get("1.0", "end-1c") == texto
