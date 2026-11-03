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
from tests.ambiente_grafico import criar_raiz
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

    try:
        janela = criar_raiz()
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


def corrigir_a_primeira_palavra(editor):
    """Troca a primeira palavra da legenda do corte aberto e devolve a palavra nova."""
    original = editor._texto.get("1.0", "end-1c")
    primeira = original.split()[0]
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", original.replace(primeira, "corrigida", 1))
    editor._ao_editar_legenda()
    return "corrigida"


@pytest.mark.gui
def test_mexer_nas_pontas_mantem_a_correcao_das_palavras_que_ficam(editor):
    editor.selecionar(editor._ordem[0])
    nova = corrigir_a_primeira_palavra(editor)
    assert editor.mover("fim", -3)  # tira 3 palavras do fim: a correção, no começo, continua
    corte = editor._cortes[editor._atual]
    assert corte.legenda.startswith(nova)
    assert editor._rotulo_ajuste.cget("text") == ""
    assert editor._texto.get("1.0", "end-1c").split()[0] == nova
    assert editor._janela.texto_dentro().split()[0] == nova  # a tabela mostra a correção também
    assert editor.mover_frase("fim", 1)  # e acrescentar uma frase não a perde
    assert editor._cortes[editor._atual].legenda.startswith(nova)


@pytest.mark.gui
def test_a_correcao_de_uma_palavra_que_sai_do_corte_e_descartada_e_avisa(editor):
    editor.selecionar(editor._ordem[0])
    corrigir_a_primeira_palavra(editor)
    assert editor.mover("inicio", 3)  # a palavra corrigida saiu do começo do corte
    corte = editor._cortes[editor._atual]
    assert corte.legenda == ""
    assert "eram de palavras que saíram do corte" in editor._rotulo_ajuste.cget("text")


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
def test_tempo_que_passa_do_limite_e_aceito_com_aviso(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    corte = editor._cortes[i]
    editor._var_fim.set(f"{int((corte.fim + 200) // 60)}:{(corte.fim + 200) % 60:04.1f}")
    assert editor.aplicar_tempo("fim") is True
    assert editor._cortes[i].fim > corte.fim and editor._cortes[i].duracao > 180.0
    assert "não pode ser um Short" in editor._rotulo_limite.cget("text")
    assert str(editor._rotulo_tempo.cget("text_color")) == "#e05555"


@pytest.mark.gui
def test_o_aviso_do_limite_some_ao_voltar_para_ele(editor):
    editor.selecionar(editor._ordem[0])
    for _ in range(500):
        editor.mover("fim", 1)
    assert editor._rotulo_limite.cget("text")
    while editor._cortes[editor._atual].duracao > 180.0:
        assert editor.mover("fim", -1)
    assert editor._rotulo_limite.cget("text") == ""


@pytest.mark.gui
def test_salvar_exige_ate_o_limite_mas_o_trabalho_em_andamento_pode_ser_salvo(editor, pastas):
    editor.selecionar(editor._ordem[0])
    for _ in range(500):
        editor.mover("fim", 1)
    assert len(editor.cortes_acima_do_limite()) == 1
    assert editor.salvar() is False
    assert editor.salvar(exigir_ate_o_limite=False) is True
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[editor._cortes[0].id]
    assert gravado.duracao > 180.0


@pytest.mark.gui
def test_uma_caixa_so_mostra_o_corte_inteiro_e_o_que_ha_antes_e_depois(editor):
    editor.selecionar(editor._ordem[0])
    corte = editor._cortes[editor._atual]
    primeira, ultima = indices_das_palavras(editor._palavras, corte)
    texto = editor._janela.get("1.0", "end-1c")
    assert "COMEÇA AQUI" not in texto and "TERMINA AQUI" not in texto  # só cinza e branco
    linhas = editor._janela.linhas
    tipos = [linha.tipo for linha in linhas]
    primeira_dentro = tipos.index("dentro")
    ultima_dentro = len(tipos) - 1 - tipos[::-1].index("dentro")
    antes = linhas[:primeira_dentro]
    dentro = linhas[primeira_dentro : ultima_dentro + 1]
    depois = linhas[ultima_dentro + 1 :]
    assert {linha.tipo for linha in antes} == {linha.tipo for linha in depois} == {"fora"}
    assert {linha.tipo for linha in dentro} == {"dentro"}

    def lidas(bloco):  # as palavras de um grupo de linhas da tabela
        return " ".join(linha.texto for linha in bloco).split()

    assert lidas(antes)[-1] == editor._palavras[primeira - 1].texto
    assert lidas(dentro)[0] == editor._palavras[primeira].texto
    assert lidas(dentro)[-1] == editor._palavras[ultima].texto
    assert lidas(depois)[0] == editor._palavras[ultima + 1].texto
    assert len(lidas(antes)) > 100 and len(lidas(depois)) > 100
    assert len(lidas(dentro)) == ultima - primeira + 1  # o corte inteiro, não só as pontas
    caixa = editor._janela._textbox
    assert caixa.tag_ranges("fora") and caixa.tag_ranges("dentro") and caixa.tag_ranges("hora")


@pytest.mark.gui
def test_os_botoes_do_inicio_e_do_fim_tem_a_ordem_pedida(editor):
    def rotulos(ponta):
        return [b.cget("text") for b in editor._botoes_das_pontas[ponta]]

    assert rotulos("inicio") == ["◀ +palavra", "◀ +frase", "−frase ▶", "−palavra ▶"]
    assert rotulos("fim") == ["◀ −palavra", "◀ −frase", "+frase ▶", "+palavra ▶"]


@pytest.mark.gui
def test_cada_botao_do_inicio_e_do_fim_move_para_o_lado_certo(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    inicio, fim = editor._botoes_das_pontas["inicio"], editor._botoes_das_pontas["fim"]
    inicio[3].invoke()  # −palavra: o começo anda para a direita
    assert indices_das_palavras(palavras, editor._cortes[i])[0] == primeira + 1
    inicio[0].invoke()  # +palavra: volta
    assert indices_das_palavras(palavras, editor._cortes[i])[0] == primeira
    fim[0].invoke()  # −palavra: o fim anda para a esquerda
    assert indices_das_palavras(palavras, editor._cortes[i])[1] == ultima - 1
    fim[3].invoke()  # +palavra: volta
    assert indices_das_palavras(palavras, editor._cortes[i])[1] == ultima
    fim[2].invoke()  # +frase: o fim vai ao fim de uma frase mais adiante
    assert indices_das_palavras(palavras, editor._cortes[i])[1] > ultima
    inicio[1].invoke()  # +frase: o começo vai ao começo de uma frase mais atrás
    assert indices_das_palavras(palavras, editor._cortes[i])[0] < primeira
    inicio[2].invoke()  # −frase: o começo volta para a frente
    assert indices_das_palavras(palavras, editor._cortes[i])[0] == primeira


@pytest.mark.gui
def test_as_frases_ficam_uma_por_linha_na_tabela_com_a_hora_ao_lado(editor):
    editor.selecionar(editor._ordem[0])
    texto = editor._janela.get("1.0", "end-1c")
    linhas = [linha for linha in texto.split("\n") if linha.strip()]
    assert len(linhas) > 10
    for linha in linhas[:-1]:
        if "AQUI" in linha:
            continue
        hora, frase = linha.split("\t")
        assert re.fullmatch(r"(\d+:)?\d{1,2}:\d{2}", hora.lstrip(" ▌◆"))  # antes dela, a margem
        assert frase.rstrip().endswith((".", "?", "!"))


def clique_na_palavra(editor, indice, *, x=1, y=1):
    """Simula o duplo clique sobre a palavra `indice` da tabela."""
    tabela = editor._janela
    linha, coluna = (int(n) for n in tabela.posicao_da_palavra(indice).split("."))
    # a janela de teste está escondida (sem medidas de tela): a posição sob o clique é dada direto
    original = tabela._posicao
    tabela._posicao = lambda ref: (linha, coluna + 1) if str(ref).startswith("@") else original(ref)
    try:
        return tabela._duplo_clique(type("Evento", (), {"x": x, "y": y})())
    finally:
        del tabela._posicao


@pytest.mark.gui
def test_duplo_clique_no_cinza_antes_do_corte_move_o_inicio_para_ela(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    assert clique_na_palavra(editor, primeira - 12) == "break"
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira - 12, ultima)


@pytest.mark.gui
def test_duplo_clique_no_cinza_depois_do_corte_termina_o_corte_nela(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    clique_na_palavra(editor, ultima + 12)
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira, ultima + 12)


@pytest.mark.gui
def test_duplo_clique_dentro_do_corte_so_seleciona_a_palavra_para_editar(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    assert clique_na_palavra(editor, primeira + 5) is None  # o gesto normal do Tk vale
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira, ultima)


@pytest.mark.gui
def test_o_menu_de_uma_palavra_comeca_ou_termina_o_corte_nela(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    palavras = editor._palavras
    primeira, ultima = indices_das_palavras(palavras, editor._cortes[i])
    editor._janela._ao_menu(primeira + 5, "inicio")  # o que o botão direito escolhe
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira + 5, ultima)
    editor._janela._ao_menu(ultima - 5, "fim")
    assert indices_das_palavras(palavras, editor._cortes[i]) == (primeira + 5, ultima - 5)


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


# --- prévia do Short -----------------------------------------------------------------------


class TarefasNaHora:
    """Roda a tarefa na hora, na mesma thread (sem fila): o teste vê o resultado em seguida."""

    def __init__(self):
        self.pedidos = []

    def rodar(self, funcao, ao_terminar=None, ao_falhar=None, *, chave=None):
        self.pedidos.append(chave)
        try:
            resultado = funcao()
        except Exception as erro:
            if ao_falhar:
                ao_falhar(erro)
            return
        if ao_terminar:
            ao_terminar(resultado)


@pytest.fixture
def editor_com_previa(raiz, pastas, tmp_path, monkeypatch):
    import customtkinter as ctk

    from spurgeon.core.config import OpcoesProcessamento
    from spurgeon.core.ffmpeg import InfoMidia
    from spurgeon.render import previa
    from spurgeon.ui.studio_shorts import OrigemDaPrevia

    chamadas = []

    def falsa(video, info, corte, palavras, destino, **kwargs):
        chamadas.append((corte.inicio, corte.fim, corte.legenda, kwargs))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x")
        return previa.PreviaDoShort(destino, None, corte.duracao)

    monkeypatch.setattr(previa, "previa_do_corte", falsa)
    info = InfoMidia(caminho=tmp_path / "v.mp4", duracao_s=100.0, tem_video=True, tem_audio=True)
    origem = OrigemDaPrevia(tmp_path / "v.mp4", info, OpcoesProcessamento())
    estado = {"origem": origem}
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    carregados = []
    ed = EditorDeCortes(
        janela,
        pastas,
        legenda_queimada=lambda: True,
        tarefas=TarefasNaHora(),
        origem_da_previa=lambda: estado["origem"],
    )
    ed.pack(fill="both", expand=True)
    ed._pasta_das_previas = tmp_path / "previas"
    monkeypatch.setattr(ed._player, "carregar", lambda p: carregados.append(p))
    ed.chamadas, ed.estado, ed.carregados = chamadas, estado, carregados
    yield ed
    janela.destroy()


@pytest.mark.gui
def test_gerar_previa_renderiza_o_corte_atual_com_as_opcoes_da_tela(editor_com_previa):
    ed = editor_com_previa
    ed.selecionar(ed._ordem[0])
    corte = ed._cortes[ed._atual]
    assert str(ed._botao_previa.cget("text")) == "Gerar prévia"
    ed.gerar_previa()
    assert len(ed.chamadas) == 1
    inicio, fim, _legenda, extras = ed.chamadas[0]
    assert (inicio, fim) == (corte.inicio, corte.fim)
    assert extras["com_legenda"] is True and extras["posicao_legenda"] == 0.62
    assert ed._gerando is None and len(ed.carregados) == 1
    assert str(ed._botao_previa.cget("text")) == "Gerar de novo"
    assert ed._player.winfo_manager() == "grid"


@pytest.mark.gui
def test_a_previa_deixa_de_valer_quando_o_corte_muda_e_volta_ao_voltar(editor_com_previa):
    ed = editor_com_previa
    ed.selecionar(ed._ordem[0])
    ed.gerar_previa()
    assert ed._player.winfo_manager() == "grid"
    ed.mover("fim", -1)
    assert ed._player.winfo_manager() == ""  # a prévia era do corte antigo
    assert "versão anterior" in ed._situacao_previa.cget("text")
    ed.mover("fim", 1)
    assert ed._player.winfo_manager() == "grid"  # o corte voltou ao que a prévia mostra


@pytest.mark.gui
def test_a_previa_acompanha_a_legenda_corrigida_e_a_aparencia(editor_com_previa):
    ed = editor_com_previa
    ed.selecionar(ed._ordem[0])
    ed.gerar_previa()
    ed.estado["origem"].opcoes.shorts_zoom_pct = 130.0
    ed.selecionar(ed._ordem[0])
    assert ed._player.winfo_manager() == ""  # o zoom do Short mudou depois da prévia


@pytest.mark.gui
def test_cada_corte_guarda_a_sua_previa(editor_com_previa):
    ed = editor_com_previa
    primeiro, segundo = ed._ordem[0], ed._ordem[1]
    ed.selecionar(primeiro)
    ed.gerar_previa()
    ed.selecionar(segundo)
    assert ed._player.winfo_manager() == ""
    assert str(ed._botao_previa.cget("text")) == "Gerar prévia"
    ed.selecionar(primeiro)
    assert ed._player.winfo_manager() == "grid"


@pytest.mark.gui
def test_sem_video_a_previa_avisa_em_vez_de_gerar(editor_com_previa, caixas):
    ed = editor_com_previa
    ed.estado["origem"] = None
    ed.selecionar(ed._ordem[0])
    ed.gerar_previa()
    assert not ed.chamadas and caixas and caixas[-1][0] == "Prévia do Short"


@pytest.mark.gui
def test_sem_tarefas_o_botao_da_previa_fica_desligado(editor):
    editor.selecionar(editor._ordem[0])
    assert str(editor._botao_previa.cget("state")) == "disabled"
    editor.gerar_previa()  # não faz nada, nem quebra


# --- já postado no YouTube -----------------------------------------------------------------


def _renderizar_o_primeiro(editor, pastas):
    """Põe o 1º corte como já renderizado (arquivo e registro) e devolve o corte."""
    corte = editor._cortes[0]
    pastas.shorts.mkdir(parents=True, exist_ok=True)
    (pastas.shorts / "short_01_x.mp4").write_bytes(b"x")
    (pastas.shorts / "short_01_x.txt").write_text("publicar", encoding="utf-8")
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
    editor.selecionar(0)
    return corte


@pytest.mark.gui
def test_marcar_postado_grava_na_hora_move_o_arquivo_e_aparece_na_lista(editor, pastas):
    corte = _renderizar_o_primeiro(editor, pastas)
    assert not editor._var_postado.get() and editor._rotulo_postado.cget("text") == ""
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    # gravado no arquivo sem precisar salvar, e o resto do que estava em edição não foi junto
    gravados = carregar_cortes(pastas.cortes_shorts)
    assert gravados[0].postado and gravados[0].postado_em and not gravados[1].postado
    assert not editor.mudou
    # o arquivo foi para postados/, o registro acompanhou e o corte continua "renderizado"
    assert (pastas.shorts / "postados" / "short_01_x.mp4").exists()
    assert (
        (pastas.shorts / "postados" / "short_01_x.txt").read_text("utf-8").startswith("JÁ POSTADO")
    )
    assert (
        ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]
        == "postados/short_01_x.mp4"
    )
    assert editor.situacao(corte) == SITUACAO_PRONTO
    # a tela diz
    assert "postados" in editor._rotulo_postado.cget("text")
    assert "postado no YouTube" in editor._rotulos_da_linha[0]["info"].cget("text")
    assert "1 já postado" in editor._resumo.cget("text")


@pytest.mark.gui
def test_desmarcar_postado_traz_o_arquivo_de_volta(editor, pastas):
    corte = _renderizar_o_primeiro(editor, pastas)
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    editor._var_postado.set(False)
    editor._ao_marcar_postado()
    assert (pastas.shorts / "short_01_x.mp4").exists()
    assert not (pastas.shorts / "postados" / "short_01_x.mp4").exists()
    assert not carregar_cortes(pastas.cortes_shorts)[0].postado
    assert editor.situacao(corte) == SITUACAO_PRONTO
    assert editor._rotulo_postado.cget("text") == ""
    assert "já postado" not in editor._resumo.cget("text")


@pytest.mark.gui
def test_postado_sem_arquivo_renderizado_so_grava_a_marca(editor, pastas):
    editor.selecionar(0)
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert carregar_cortes(pastas.cortes_shorts)[0].postado
    assert not (pastas.shorts / "postados").exists()
    assert "Ainda não há arquivo" in editor._rotulo_postado.cget("text")


@pytest.mark.gui
def test_corte_novo_que_ainda_nao_foi_salvo_leva_a_marca_no_proximo_salvar(editor, pastas):
    novo = type(editor._cortes[0]).de_dict({**editor._cortes[0].para_dict(), "id": 777_000})
    editor.adicionar_corte(novo)
    editor._mudou = False
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert editor.mudou  # não está no arquivo: espera o "Salvar"
    assert editor.salvar(exigir_ate_o_limite=False)
    assert next(c for c in carregar_cortes(pastas.cortes_shorts) if c.id == 777_000).postado


@pytest.mark.gui
def test_salvar_move_os_arquivos_dos_cortes_marcados_como_postados(editor, pastas):
    corte = _renderizar_o_primeiro(editor, pastas)
    corte.postado = True  # marcado em memória (como numa carga antiga) e salvo pelo botão
    editor._houve_mudanca()
    assert editor.salvar(exigir_ate_o_limite=False)
    assert (pastas.shorts / "postados" / "short_01_x.mp4").exists()
    assert editor.situacao(corte) == SITUACAO_PRONTO


@pytest.mark.gui
def test_arquivo_preso_avisa_na_tela_e_a_marca_fica_gravada(editor, pastas, monkeypatch):
    from spurgeon.render import shorts as render_shorts

    _renderizar_o_primeiro(editor, pastas)

    def preso(origem, destino):
        raise PermissionError(13, "usado por outro processo")

    monkeypatch.setattr(render_shorts, "_mover_par", preso)
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert carregar_cortes(pastas.cortes_shorts)[0].postado
    assert (pastas.shorts / "short_01_x.mp4").exists()
    assert "Não consegui mover" in editor._rotulo_postado.cget("text")


def test_texto_do_postado_conforme_o_estado_do_corte():
    from spurgeon.ui.studio_shorts import texto_do_postado

    comum = corte_exemplo()
    assert texto_do_postado(comum, tem_arquivo=True, alterado=False) == ""
    postado = type(comum).de_dict(
        {**comum.para_dict(), "postado": True, "postado_em": "2026-11-02"}
    )
    com_arquivo = texto_do_postado(postado, tem_arquivo=True, alterado=False)
    assert "02/11/2026" in com_arquivo and "shorts_gerados/postados" in com_arquivo
    assert "Ainda não há arquivo" in texto_do_postado(postado, tem_arquivo=False, alterado=False)
    assert "O corte mudou depois" in texto_do_postado(postado, tem_arquivo=True, alterado=True)
    sem_data = type(comum).de_dict({**comum.para_dict(), "postado": True})
    assert "em " not in texto_do_postado(sem_data, tem_arquivo=True, alterado=False).split(".")[0]


def test_aviso_do_titulo_so_fala_de_repetido_parecido_ou_generico():
    from spurgeon.ui.studio_shorts import aviso_do_titulo

    outros = ["Acaz queimou os próprios filhos", "Jotão não entrou no templo"]
    assert aviso_do_titulo("Quando um rei ímpio governa o povo sofre", outros, "") == ""
    assert aviso_do_titulo("   ", outros, "") == ""
    assert "é igual ao título “Jotão não entrou no templo”" in aviso_do_titulo(
        "jotão não entrou no templo!", outros, ""
    )
    assert "parecido demais" in aviso_do_titulo("Acaz queimou seus filhos no fogo", outros, "")
    assert "genérico" in aviso_do_titulo("Deus punirá o pecado", outros, "")


@pytest.mark.gui
def test_o_aviso_do_titulo_aparece_ao_repetir_o_de_outro_short_marcado(editor):
    editor.selecionar(0)
    outro = editor._cortes[1].titulo
    assert editor._aviso_do_titulo.cget("text") == ""
    editor._var_titulo.set(outro)
    assert f"é igual ao título “{outro}”" in editor._aviso_do_titulo.cget("text")
    editor._cortes[1].incluir = False  # um Short desmarcado não conta
    editor._atualizar_aviso_do_titulo()
    assert editor._aviso_do_titulo.cget("text") == ""
    editor._cortes[1].incluir = True
    editor._var_titulo.set("Quando um rei ímpio governa o povo sofre")
    assert editor._aviso_do_titulo.cget("text") == ""
