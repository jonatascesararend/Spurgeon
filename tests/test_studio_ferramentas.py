"""Studio Shorts: marca-texto e borracha, desfazer e refazer, descartar, categorias e dicionário."""

from __future__ import annotations

import pytest

from spurgeon.core.config import Pastas
from spurgeon.shorts.dados import cor_da_categoria, rotulo_da_categoria
from spurgeon.shorts.edicao import indices_incluidos
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.ui import dialogos, studio_shorts
from spurgeon.ui.aba_studio import AbaStudioShorts
from spurgeon.ui.ferramentas_de_marca import FerramentasDeMarca
from spurgeon.ui.studio_shorts import EditorDeCortes
from spurgeon.ui.tabela_de_frases import FERRAMENTA_BORRACHA, FERRAMENTA_MARCA, FERRAMENTA_SETA
from tests import test_studio_shorts as base

caixas, editor, pastas, raiz = base.caixas, base.editor, base.pastas, base.raiz


def _pastas_do_app(tmp_path) -> Pastas:
    """As pastas do aplicativo dentro da pasta temporária (o `Pastas()` padrão aponta para o
    repositório de verdade, com o vocabulário da pessoa)."""
    return Pastas(
        entrada=tmp_path / "Downloads",
        projetos=tmp_path / "Projetos",
        recursos=tmp_path / "recursos",
        modelos=tmp_path / "modelos",
    )


def _aberto(ed):
    return ed._cortes[ed._atual]


def _incluidas(ed):
    return indices_incluidos(ed._palavras, _aberto(ed))


@pytest.fixture
def com_tirada(editor):
    """O primeiro corte aberto, com 4 palavras do meio tiradas pela borracha."""
    editor.selecionar(editor._ordem[0])
    editor.antes = _aberto(editor)
    editor.inicial = indices_incluidos(editor._palavras, editor.antes)
    editor.tiradas = editor.inicial[20:24]
    editor._pintou(editor.tiradas, FERRAMENTA_BORRACHA)
    return editor


# --- os três botões de ferramenta ----------------------------------------------------------


@pytest.mark.gui
def test_so_uma_ferramenta_fica_ligada_e_a_troca_avisa(raiz):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    trocas = []
    ferramentas = FerramentasDeMarca(janela, ao_trocar=trocas.append)
    ferramentas.pack()
    assert ferramentas.ferramenta == FERRAMENTA_SETA  # começa como sempre foi
    ferramentas.definir(FERRAMENTA_MARCA)
    ferramentas.definir(FERRAMENTA_MARCA)  # repetir não é troca
    ferramentas.definir(FERRAMENTA_BORRACHA)
    assert trocas == [FERRAMENTA_MARCA, FERRAMENTA_BORRACHA]
    ligado = [
        n
        for n, b in ferramentas._botoes.items()
        if b.cget("fg_color") not in (("gray70", "gray30"),)
    ]
    assert ligado == [FERRAMENTA_BORRACHA]
    ferramentas.definir(FERRAMENTA_SETA, avisar=False)
    assert trocas == [FERRAMENTA_MARCA, FERRAMENTA_BORRACHA]  # sem avisar, fica quieto
    with pytest.raises(ValueError):
        ferramentas.definir("pincel")
    ferramentas.habilitar(False)
    assert {str(b.cget("state")) for b in ferramentas._botoes.values()} == {"disabled"}
    janela.destroy()


@pytest.mark.gui
def test_a_tabela_do_studio_segue_a_ferramenta_escolhida(editor):
    editor.selecionar(editor._ordem[0])
    assert editor._ferramentas.ferramenta == FERRAMENTA_SETA
    editor._ferramentas.definir(FERRAMENTA_MARCA)
    assert editor._janela.ferramenta == FERRAMENTA_MARCA
    editor._ferramentas.definir(FERRAMENTA_BORRACHA)
    assert editor._janela.ferramenta == FERRAMENTA_BORRACHA


# --- a borracha e o marca-texto no corte ---------------------------------------------------


@pytest.mark.gui
def test_a_borracha_tira_palavras_do_meio_e_o_corte_continua_depois(com_tirada):
    ed = com_tirada
    novo = _aberto(ed)
    restantes = _incluidas(ed)
    assert novo.saltos and ed.mudou
    assert not set(ed.tiradas) & set(restantes)
    assert restantes[0] == ed.inicial[0] and restantes[-1] == ed.inicial[-1]  # as pontas ficam
    assert novo.duracao < ed.antes.duracao
    assert novo.inicio == pytest.approx(ed.antes.inicio)  # a janela é a mesma
    assert novo.fim == pytest.approx(ed.antes.fim)
    assert not any(ed._palavras[i].texto in novo.texto for i in ed.tiradas)


@pytest.mark.gui
def test_o_marca_texto_traz_de_volta_o_que_a_borracha_tirou(com_tirada):
    ed = com_tirada
    ed._pintou(ed.tiradas, FERRAMENTA_MARCA)
    assert _incluidas(ed) == ed.inicial and _aberto(ed).saltos == []


@pytest.mark.gui
def test_o_marca_texto_depois_do_fim_acrescenta_palavras_ao_corte(editor):
    editor.selecionar(editor._ordem[0])
    inicial = _incluidas(editor)
    editor._pintou(list(range(inicial[-1] + 1, inicial[-1] + 6)), FERRAMENTA_MARCA)
    assert _incluidas(editor)[-1] == inicial[-1] + 5
    assert editor.mudou and _aberto(editor).saltos == []


@pytest.mark.gui
def test_a_ferramenta_seta_nao_pinta_nada(editor):
    editor.selecionar(editor._ordem[0])
    antes = _aberto(editor)
    editor._pintou(_incluidas(editor)[5:8], FERRAMENTA_SETA)
    assert _aberto(editor) == antes and not editor.mudou


@pytest.mark.gui
def test_a_borracha_nao_apaga_o_corte_inteiro_sem_aviso(editor):
    """Tirar todas as palavras deixaria um Short vazio: o corte não pode ficar sem nada."""
    editor.selecionar(editor._ordem[0])
    todas = _incluidas(editor)
    editor._pintou(todas, FERRAMENTA_BORRACHA)
    assert _incluidas(editor)  # sobrou pelo menos uma palavra


# --- desfazer e refazer ---------------------------------------------------------------------


@pytest.mark.gui
def test_sem_mudar_nada_nao_ha_o_que_desfazer(editor):
    editor.selecionar(editor._ordem[0])
    assert not editor.pode_desfazer and not editor.pode_refazer
    assert {str(b.cget("state")) for b in editor._botoes_do_historico["desfazer"]} == {"disabled"}
    assert {str(b.cget("state")) for b in editor._botoes_do_historico["refazer"]} == {"disabled"}
    assert editor.desfazer() is False and editor.refazer() is False


@pytest.mark.gui
def test_desfazer_e_refazer_andam_na_historia_do_corte(com_tirada):
    ed = com_tirada
    assert ed.pode_desfazer and not ed.pode_refazer
    assert {str(b.cget("state")) for b in ed._botoes_do_historico["desfazer"]} == {"normal"}
    assert ed.desfazer()
    assert _incluidas(ed) == ed.inicial and _aberto(ed).saltos == []
    assert ed.pode_refazer and not ed.pode_desfazer
    assert {str(b.cget("state")) for b in ed._botoes_do_historico["refazer"]} == {"normal"}
    assert {str(b.cget("state")) for b in ed._botoes_do_historico["desfazer"]} == {"disabled"}
    assert ed.refazer()
    assert not set(ed.tiradas) & set(_incluidas(ed))
    assert ed.pode_desfazer and not ed.pode_refazer


@pytest.mark.gui
def test_uma_mudanca_nova_depois_de_desfazer_apaga_o_que_havia_para_refazer(com_tirada):
    ed = com_tirada
    ed.desfazer()
    assert ed.pode_refazer
    ed.mover("fim", -2)
    assert not ed.pode_refazer


@pytest.mark.gui
def test_desfazer_serve_para_as_pontas_e_para_a_legenda(editor):
    editor.selecionar(editor._ordem[0])
    antes = _aberto(editor)
    assert editor.mover("fim", -3)
    assert editor.desfazer()
    assert (_aberto(editor).inicio, _aberto(editor).fim) == (antes.inicio, antes.fim)
    texto = editor._texto.get("1.0", "end-1c")
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", "Texto totalmente diferente.")
    editor._guardar_detalhe()
    assert _aberto(editor).legenda
    editor._ajustar(lambda c: c)  # qualquer ajuste guarda o que está digitado na história
    assert editor.mover("fim", -1)
    assert editor.desfazer() and editor.desfazer()
    assert _aberto(editor).legenda == "" and editor._texto.get("1.0", "end-1c") == texto


@pytest.mark.gui
def test_cada_short_tem_a_sua_historia(com_tirada):
    ed = com_tirada
    primeiro = ed._ordem[0]
    ed.selecionar(ed._ordem[1])
    assert not ed.pode_desfazer  # a história é do outro
    assert {str(b.cget("state")) for b in ed._botoes_do_historico["desfazer"]} == {"disabled"}
    ed.selecionar(primeiro)
    assert ed.pode_desfazer  # e a do primeiro continua guardada
    assert ed.desfazer() and _incluidas(ed) == ed.inicial


@pytest.mark.gui
def test_os_atalhos_ctrl_z_e_ctrl_y_agem_no_corte_aberto(com_tirada):
    ed = com_tirada
    ed.focus_get = lambda: None  # sem campo com o foco, o atalho vale para o corte
    assert ed._atalho_desfazer() == "break"
    assert _aberto(ed).saltos == []
    assert ed._atalho_refazer() == "break"
    assert _aberto(ed).saltos
    # na tabela e na legenda o desfazer do texto é desligado: o Ctrl+Z também é o do corte
    ed._janela._textbox.event_generate("<<Undo>>")
    ed.update()
    assert _aberto(ed).saltos == [] and ed.pode_refazer


@pytest.mark.gui
def test_o_ctrl_z_num_campo_de_uma_linha_nao_mexe_na_historia_do_corte(com_tirada, raiz):
    ed = com_tirada
    ed.focus_get = lambda: ed._campo_titulo._entry  # digitando o título: o Ctrl+Z é do campo
    assert ed._atalho_desfazer() is None
    assert _aberto(ed).saltos  # nada foi desfeito
    ed.focus_get = lambda: ed._janela._textbox  # na tabela, sim
    assert ed._atalho_desfazer() == "break"
    assert _aberto(ed).saltos == []
    ed.focus_get = lambda: raiz  # de fora do editor
    assert ed._atalho_desfazer() is None


@pytest.mark.gui
def test_desfazer_esta_bloqueado_enquanto_o_editor_esta_bloqueado(com_tirada):
    ed = com_tirada
    ed.bloquear("Processando…")
    assert ed.desfazer() is False
    ed.bloquear(None)
    assert ed.desfazer() is True


# --- descartar alterações -------------------------------------------------------------------


@pytest.mark.gui
def test_descartar_volta_ao_que_esta_gravado_e_esquece_a_historia(com_tirada, pastas):
    ed = com_tirada
    id_ = _aberto(ed).id
    assert ed.mudou
    ed.descartar_alteracoes()
    assert not ed.mudou and _aberto(ed).id == id_  # continua no mesmo Short
    assert _aberto(ed).saltos == [] and _incluidas(ed) == ed.inicial
    assert not ed.pode_desfazer and not ed.pode_refazer
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[id_]
    assert gravado.saltos == []  # nada foi escrito no arquivo


@pytest.mark.gui
def test_salvar_grava_os_saltos_e_depois_descartar_nao_tira_mais_nada(com_tirada, pastas):
    ed = com_tirada
    id_ = _aberto(ed).id
    assert ed.salvar(exigir_ate_o_limite=False)
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[id_]
    assert gravado.saltos == _aberto(ed).saltos != []
    ed.descartar_alteracoes()
    assert _aberto(ed).saltos == gravado.saltos


def _aba(raiz, pastas, tmp_path):
    import customtkinter as ctk

    quadro = ctk.CTkToplevel(raiz)
    quadro.geometry("1100x700")
    quadro.withdraw()
    aba = AbaStudioShorts(
        quadro,
        ao_renderizar=lambda: None,
        pastas_do_app=_pastas_do_app(tmp_path),
        legenda_queimada=lambda: False,
    )
    aba.pack(fill="both", expand=True)
    aba.mostrar_projeto(pastas)
    return quadro, aba


@pytest.mark.gui
def test_o_botao_descartar_da_aba_so_vale_com_alteracoes_e_pergunta_antes(
    raiz, pastas, tmp_path, caixas, monkeypatch
):
    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        aba.editor.selecionar(aba.editor._ordem[0])
        assert str(aba._botao_descartar.cget("state")) == "disabled"
        assert aba.descartar() is False  # nada a descartar: nem pergunta
        assert caixas == []
        aba.editor._pintou(_incluidas(aba.editor)[20:24], FERRAMENTA_BORRACHA)
        assert str(aba._botao_descartar.cget("state")) == "normal"
        assert str(aba._botao_salvar.cget("state")) == "normal"
        confirmar = dialogos.confirmar
        monkeypatch.setattr(dialogos, "confirmar", lambda *a, **k: False)
        assert aba.descartar() is False and aba.mudou  # "Continuar editando"
        monkeypatch.setattr(dialogos, "confirmar", confirmar)
        assert aba.descartar() is True  # a caixa de teste escolhe o botão principal
        assert caixas and caixas[-1][0] == "Descartar alterações"
        assert not aba.mudou
        assert str(aba._botao_descartar.cget("state")) == "disabled"
        assert str(aba._botao_salvar.cget("state")) == "disabled"
    finally:
        quadro.destroy()


# --- categorias com cor ---------------------------------------------------------------------


@pytest.mark.gui
def test_o_corte_mostra_a_categoria_com_a_cor_dela_na_lista_e_no_detalhe(editor):
    i = editor._ordem[0]
    editor._cortes[i].categoria = "apontando_cristo"
    editor._atualizar_linha(i)
    editor.selecionar(i)
    chip_da_lista = editor._rotulos_da_linha[i]["chip"]
    for chip in (chip_da_lista, editor._chip_categoria):
        assert chip.cget("text") == rotulo_da_categoria("apontando_cristo") == "Apontando Cristo"
        assert chip.cget("fg_color") == cor_da_categoria("apontando_cristo")
    outro = editor._ordem[1]
    editor._cortes[outro].categoria = "profecia"
    editor.selecionar(outro)
    assert editor._chip_categoria.cget("fg_color") == cor_da_categoria("profecia")
    assert editor._chip_categoria.cget("fg_color") != cor_da_categoria("apontando_cristo")


# --- o dicionário personalizado -------------------------------------------------------------


@pytest.fixture
def editor_com_dicionario(raiz, pastas, tmp_path):
    import customtkinter as ctk

    do_app = _pastas_do_app(tmp_path)
    do_app.recursos.mkdir(parents=True, exist_ok=True)
    do_app.vocabulario.write_text("# termos\nPropiciação\nsantificação\nTerminoErrado\n", "utf-8")
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(janela, pastas, pastas_do_app=do_app)
    ed.pack(fill="both", expand=True)
    ed.do_app = do_app
    yield ed
    if ed._dicionario is not None and ed._dicionario.winfo_exists():
        ed._dicionario.destroy()
    janela.destroy()


@pytest.mark.gui
def test_o_atalho_abre_o_dicionario_com_os_termos_e_so_uma_janela(editor_com_dicionario):
    ed = editor_com_dicionario
    dicionario = ed.abrir_dicionario()
    assert dicionario.termos == ["Propiciação", "santificação", "TerminoErrado"]
    assert ed.abrir_dicionario() is dicionario  # abrir de novo só traz para a frente


@pytest.mark.gui
def test_tirar_um_termo_errado_grava_o_arquivo_e_avisa_o_verificador(
    editor_com_dicionario, monkeypatch
):
    ed = editor_com_dicionario
    avisos = []
    monkeypatch.setattr(studio_shorts, "atualizar_vocabulario_compartilhado", avisos.append)
    dicionario = ed.abrir_dicionario()
    dicionario._lista.selection_set(dicionario._visiveis.index("TerminoErrado"))
    assert dicionario.remover_selecionados() == 1
    texto = ed.do_app.vocabulario.read_text("utf-8")
    assert "TerminoErrado" not in texto and "# termos" in texto and "Propiciação" in texto
    assert dicionario.termos == ["Propiciação", "santificação"]
    assert len(avisos) == 1  # o verificador foi avisado uma vez


@pytest.mark.gui
def test_acrescentar_e_procurar_no_dicionario(editor_com_dicionario):
    ed = editor_com_dicionario
    dicionario = ed.abrir_dicionario()
    dicionario._var_novo.set("  Supralapsarianismo ")
    assert dicionario.adicionar() and "Supralapsarianismo" in dicionario.termos
    dicionario._var_novo.set("supralapsarianismo")
    assert dicionario.adicionar() is False  # maiúsculas não contam: já está lá
    dicionario._var_busca.set("ção")
    assert dicionario._visiveis == ["Propiciação", "santificação"]
    dicionario._var_busca.set("")
    assert len(dicionario._visiveis) == len(dicionario.termos) == 4


# --- o painel de português não ocupa espaço à toa -------------------------------------------


@pytest.mark.gui
def test_o_painel_de_portugues_sem_avisos_e_pequeno_e_cresce_com_eles(editor):
    editor.selecionar(editor._ordem[0])
    editor._alternar_portugues()
    painel = editor._portugues
    painel.limpar()
    editor.update_idletasks()
    vazio = painel.winfo_reqheight()
    assert vazio < 100  # só o título, o botão e a situação
    assert painel._moldura.winfo_manager() == "" and painel._detalhe.winfo_manager() == ""
    assert painel._rodape.winfo_manager() == ""
    assert painel._lista.cget("height") <= 8  # com avisos, a lista tem uma linha por aviso
    editor._alternar_portugues()  # esconde
    assert editor._portugues.winfo_manager() == ""


@pytest.mark.gui
def test_os_avisos_vazios_do_detalhe_nao_deixam_vao_em_branco(editor):
    editor.selecionar(editor._ordem[0])
    editor.update_idletasks()
    for nome in ("_rotulo_limite", "_rotulo_ajuste", "_rotulo_avisos", "_rotulo_alerta"):
        rotulo = getattr(editor, nome)
        assert not rotulo.cget("text") and rotulo.winfo_manager() == "", nome
    editor._rotulo_ajuste.configure(text="algo mudou")
    assert editor._rotulo_ajuste.winfo_manager() == "grid"
    editor._rotulo_ajuste.configure(text="")
    assert editor._rotulo_ajuste.winfo_manager() == ""


# --- dicas de ferramenta em todos os botões --------------------------------------------------

_INTERATIVOS = ("CTkButton", "CTkCheckBox", "CTkSwitch", "CTkEntry", "CTkOptionMenu", "CTkComboBox")


def _sem_dica(raiz_dos_widgets) -> list[str]:
    """Os controles do Studio (a partir de `raiz_dos_widgets`) que não explicam o que fazem."""
    faltam = []
    pilha = [raiz_dos_widgets]
    while pilha:
        w = pilha.pop()
        pilha.extend(w.winfo_children())
        if type(w).__name__ in _INTERATIVOS and getattr(w, "_dica_spurgeon", None) is None:
            rotulo = w.cget("text") if type(w).__name__ != "CTkEntry" else str(w)
            faltam.append(f"{type(w).__name__}({rotulo!r})")
    return faltam


@pytest.mark.gui
def test_todo_botao_do_studio_tem_dica_de_ferramenta(editor):
    editor.selecionar(editor._ordem[0])
    editor._alternar_portugues()
    faltam = _sem_dica(editor)
    assert not faltam, ", ".join(faltam)
    for botao in editor._seletor_ordem._buttons_dict.values():
        assert getattr(botao, "_dica_spurgeon", None) is not None


@pytest.mark.gui
def test_todo_botao_da_aba_e_da_janela_do_dicionario_tem_dica(raiz, pastas, tmp_path):
    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        faltam = _sem_dica(aba)
        assert not faltam, ", ".join(faltam)
        dicionario = aba.editor.abrir_dicionario()
        assert [f for f in _sem_dica(dicionario) if "Fechar" not in f] == []
        for botao in aba._seletor._buttons_dict.values():
            assert getattr(botao, "_dica_spurgeon", None) is not None
        dicionario.destroy()
    finally:
        quadro.destroy()


# --- a tabela do Studio: vista parada, duplo clique que seleciona, ferramentas ------------------


@pytest.mark.gui
def test_pintar_ou_apagar_no_meio_nao_leva_a_vista_da_tabela_para_o_comeco(editor):
    editor.selecionar(editor._ordem[0])
    tabela = editor._janela
    inc = _incluidas(editor)
    alvo = inc[len(inc) // 2]
    linha = tabela._onde[alvo][0]
    tabela._textbox.yview(f"{linha}.0")  # a vista está no meio do Short
    antes = tabela._tempo_do_alto()
    editor._pintou(inc[len(inc) // 2 : len(inc) // 2 + 3], FERRAMENTA_BORRACHA)
    assert tabela._tempo_do_alto() == pytest.approx(antes, abs=6.0)  # a mesma frase (ou a vizinha)
    primeira = tabela._marcas["inicio"]
    assert tabela._textbox.compare(f"{linha}.0", ">", primeira)  # e não foi parar no começo


@pytest.mark.gui
def test_o_duplo_clique_numa_palavra_cinza_leva_a_ponta_e_deixa_a_palavra_selecionada(editor):
    editor.selecionar(editor._ordem[0])
    primeira = _incluidas(editor)[0]
    alvo = primeira - 14
    editor._levar_ponta_a(alvo)
    assert _incluidas(editor)[0] == alvo  # o começo veio até a palavra
    caixa = editor._janela._textbox
    assert caixa.get("sel.first", "sel.last") == editor._palavras[alvo].texto


@pytest.mark.gui
def test_a_tabela_nao_tem_marcadores_de_comeco_e_fim_e_a_seta_e_a_ferramenta_inicial(editor):
    editor.selecionar(editor._ordem[0])
    texto = editor._janela._textbox.get("1.0", "end")
    assert "COMEÇA AQUI" not in texto and "TERMINA AQUI" not in texto
    assert editor._janela.ferramenta == FERRAMENTA_SETA
    assert str(editor._janela._textbox.cget("cursor")) == "xterm"


@pytest.mark.gui
def test_escolher_marca_texto_ou_borracha_traz_a_tabela_para_a_vista_sem_erro(editor):
    editor.selecionar(editor._ordem[0])
    editor._ferramentas.definir(FERRAMENTA_MARCA)
    editor._trazer_a_tabela_para_a_vista()  # com a janela escondida não há o que rolar: não quebra
    editor._ferramentas.definir(FERRAMENTA_BORRACHA)
    editor.update()
    assert str(editor._janela._textbox.cget("cursor")) == "xterm"
    editor._ferramentas.definir(FERRAMENTA_SETA)
    assert (
        str(editor._janela._textbox.cget("cursor")) == "xterm"
    )  # a seta também é o cursor de texto


@pytest.mark.gui
def test_os_tres_botoes_de_ferramenta_tem_icone_e_dica(raiz):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    f = FerramentasDeMarca(janela)
    f.pack()
    textos = {nome: b.cget("text") for nome, b in f._botoes.items()}
    assert textos[FERRAMENTA_SETA].endswith("Seta") and textos[FERRAMENTA_MARCA].endswith(
        "Marca-texto"
    )
    assert "⌶" in textos[FERRAMENTA_MARCA]  # o ícone do cursor de texto, não um lápis
    for botao in f._botoes.values():
        assert getattr(botao, "_dica_spurgeon", None) is not None
        assert len(botao._dica_spurgeon.texto) > 60
    janela.destroy()


# --- a aba avisa a janela a cada mudança: a legenda de antes não pode cobrir a do corte novo ------


def _palavras_na_tabela(editor, tipo: str) -> list[str]:
    return [t for linha in editor._janela.linhas if linha.tipo == tipo for t in linha.textos]


def _frase_antes_do_corte(editor) -> list[int]:
    primeira = _incluidas(editor)[0]
    frase = next(f for f in editor._frases_do_sermao() if f.ultima == primeira - 1)
    return list(range(frase.primeira, frase.ultima + 1))


def _confere_que_a_tabela_mostra_todo_o_short(editor):
    """Toda palavra do Short aparece em branco na tabela, e o que foi cortado, riscado."""
    incluidas = _incluidas(editor)
    corte = _aberto(editor)
    assert len(_palavras_na_tabela(editor, "dentro")) == len(incluidas)
    cortadas = (incluidas[-1] - incluidas[0] + 1) - len(incluidas)
    assert len(_palavras_na_tabela(editor, "cortado")) == cortadas
    return corte


@pytest.mark.gui
def test_pintar_uma_frase_antes_do_corte_a_poe_na_tabela_mesmo_com_a_aba_ligada(
    raiz, pastas, tmp_path
):
    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        ed = aba.editor
        ed.selecionar(ed._ordem[0])
        frase = _frase_antes_do_corte(ed)
        antes = len(_incluidas(ed))
        ed._ferramentas.definir(FERRAMENTA_MARCA)
        ed._pintou(frase, FERRAMENTA_MARCA)
        assert len(_incluidas(ed)) == antes + len(frase)
        _confere_que_a_tabela_mostra_todo_o_short(ed)
        assert ed._palavras[frase[0]].texto in _palavras_na_tabela(ed, "dentro")  # não sumiu
        assert not _aberto(ed).legenda  # nada foi corrigido: a legenda é a da transcrição
        assert ed._palavras[frase[0]].texto in ed._texto.get("1.0", "end-1c").split()
    finally:
        quadro.destroy()


@pytest.mark.gui
def test_com_a_legenda_corrigida_pintar_uma_frase_tambem_a_poe_na_tabela_e_na_legenda(
    raiz, pastas, tmp_path
):
    from tests.test_studio_shorts import corrigir_a_primeira_palavra

    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        ed = aba.editor
        ed.selecionar(ed._ordem[0])
        corrigir_a_primeira_palavra(ed)  # a legenda do Short passa a ter uma correção
        ed._guardar_detalhe()
        assert _aberto(ed).legenda
        frase = _frase_antes_do_corte(ed)
        ed._pintou(frase, FERRAMENTA_MARCA)
        _confere_que_a_tabela_mostra_todo_o_short(ed)
        corte = _aberto(ed)
        assert corte.legenda and ed._palavras[frase[0]].texto in corte.legenda.split()
        assert "corrigida" in corte.legenda.split()  # a correção das palavras que ficam continua
        assert "corrigida" in _palavras_na_tabela(ed, "dentro")
        assert ed._palavras[frase[0]].texto in _palavras_na_tabela(ed, "dentro")
    finally:
        quadro.destroy()


@pytest.mark.gui
def test_apagar_do_meio_com_a_aba_ligada_risca_o_que_saiu_e_o_resto_continua(
    raiz, pastas, tmp_path
):
    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        ed = aba.editor
        ed.selecionar(ed._ordem[0])
        inc = _incluidas(ed)
        tiradas = inc[20:30]
        ed._pintou(tiradas, FERRAMENTA_BORRACHA)
        _confere_que_a_tabela_mostra_todo_o_short(ed)
        assert _palavras_na_tabela(ed, "cortado") == [ed._palavras[i].texto for i in tiradas]
        assert not _aberto(ed).legenda  # a legenda não ganhou as palavras que saíram
        assert ed._palavras[tiradas[0]].texto not in ed._texto.get("1.0", "end-1c").split()
    finally:
        quadro.destroy()


@pytest.mark.gui
def test_desfazer_e_refazer_com_a_aba_ligada_tambem_acertam_a_tabela(raiz, pastas, tmp_path):
    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        ed = aba.editor
        ed.selecionar(ed._ordem[0])
        inicial = _incluidas(ed)
        ed._pintou(_frase_antes_do_corte(ed), FERRAMENTA_MARCA)
        ed._pintou(inicial[40:44], FERRAMENTA_BORRACHA)
        com_tudo = _incluidas(ed)
        _confere_que_a_tabela_mostra_todo_o_short(ed)
        assert ed.desfazer() and ed.desfazer()
        assert _incluidas(ed) == inicial
        _confere_que_a_tabela_mostra_todo_o_short(ed)
        assert ed.refazer() and ed.refazer()
        assert _incluidas(ed) == com_tudo
        _confere_que_a_tabela_mostra_todo_o_short(ed)
    finally:
        quadro.destroy()


@pytest.mark.gui
def test_o_botao_de_palavra_com_a_aba_ligada_tambem_nao_perde_a_palavra_que_entrou(
    raiz, pastas, tmp_path
):
    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        ed = aba.editor
        ed.selecionar(ed._ordem[0])
        primeira = _incluidas(ed)[0]
        assert ed.mover("inicio", -3)
        assert _incluidas(ed)[0] == primeira - 3
        _confere_que_a_tabela_mostra_todo_o_short(ed)
        assert ed._palavras[primeira - 3].texto in ed._texto.get("1.0", "end-1c").split()
        assert not _aberto(ed).legenda
    finally:
        quadro.destroy()


@pytest.mark.gui
def test_guardar_o_detalhe_nao_copia_a_caixa_enquanto_ela_mostra_o_corte_de_antes(editor):
    editor.selecionar(editor._ordem[0])
    corte = _aberto(editor)
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", "texto de antes que não deve cobrir nada")
    editor._tela_desatualizada = True
    editor._guardar_detalhe()
    assert _aberto(editor).legenda == corte.legenda  # nada foi copiado
    editor._tela_desatualizada = False
    editor._guardar_detalhe()
    assert _aberto(editor).legenda == "texto de antes que não deve cobrir nada"


# --- a margem e a legenda de categorias na tabela do Studio -------------------------------------


@pytest.fixture
def editor_com_analise(raiz, pastas):
    """O editor de um projeto que tem a análise da IA gravada (categorias dos trechos avaliados)."""
    import customtkinter as ctk

    from spurgeon.shorts.analise import separar_frases
    from tests.test_marcas_do_sermao import _gravar_analise

    palavras = _palavras_do_projeto(pastas)
    _gravar_analise(
        pastas,
        palavras,
        separar_frases(palavras),
        [("exortacao", 8), ("profecia", 6), ("oracao", 9), ("promessa", 7), ("esperanca", 8)],
    )
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(janela, pastas, legenda_queimada=lambda: True)
    ed.pack(fill="both", expand=True)
    yield ed
    janela.destroy()


def _palavras_do_projeto(pastas):
    from spurgeon.transcricao.formato import Transcricao

    return Transcricao.carregar(pastas.transcricao).palavras


def _margens_das_linhas(ed) -> list[str]:
    caixa = ed._janela._textbox
    return [caixa.get(f"{n}.0", f"{n}.2") for n in range(1, len(ed._janela.linhas) + 1)]


@pytest.mark.gui
def test_a_tabela_do_studio_mostra_a_barra_da_categoria_que_a_ia_viu(editor_com_analise):
    ed = editor_com_analise
    ed.selecionar(ed._ordem[0])
    margens = _margens_das_linhas(ed)
    assert any(m.startswith("▌") for m in margens)  # alguma frase tem categoria detectada
    caixa = ed._janela._textbox
    n = next(i for i, m in enumerate(margens, start=1) if m.startswith("▌"))
    cores = [t for t in caixa.tag_names(f"{n}.0") if t.startswith("cat_")]
    assert len(cores) == 1  # a barra tem a cor de uma categoria só


@pytest.mark.gui
def test_a_legenda_da_tabela_tem_os_chips_das_categorias_que_aparecem_nela(editor_com_analise):
    ed = editor_com_analise
    ed.selecionar(ed._ordem[0])
    chips = ed._legenda_da_tabela.chips
    assert chips and ed._legenda_da_tabela.winfo_manager() == "grid"
    for chave, chip in chips.items():
        if chave != "feitos":
            assert chip.cget("fg_color") == cor_da_categoria(chave)
            assert rotulo_da_categoria(chave) in chip.cget("text")
        assert getattr(chip, "_dica_spurgeon", None) is not None
    categorias_nas_linhas = {c for m in _margens_das_linhas(ed) for c in [m[0]] if m[0] == "▌"}
    assert categorias_nas_linhas  # as barras e os chips contam a mesma história


@pytest.mark.gui
def test_clicar_num_chip_leva_a_tabela_ao_proximo_trecho_da_categoria(editor_com_analise):
    ed = editor_com_analise
    ed.selecionar(ed._ordem[0])
    chave = next(c for c in ed._grupos_da_tabela if c != "feitos")
    linhas = ed._grupos_da_tabela[chave]
    vistas = [ed._ir_para_na_tabela(chave) for _ in range(len(linhas) + 1)]
    assert vistas == [*linhas, linhas[0]]  # anda de trecho em trecho e dá a volta
    assert ed._ir_para_na_tabela("nao_existe") is None
    ed._janela.ir_para_linha(linhas[0])
    assert int(ed._janela._textbox.index("@0,0").split(".")[0]) <= linhas[0]


@pytest.mark.gui
def test_o_losango_marca_so_o_que_ja_virou_outro_short_e_nunca_o_proprio(editor_com_analise):
    ed = editor_com_analise
    ed.selecionar(ed._ordem[0])
    atual = _aberto(ed)
    margens = _margens_das_linhas(ed)
    linhas = ed._janela.linhas
    for margem, linha in zip(margens, linhas, strict=True):
        if linha.tipo == "dentro":
            assert margem[1] == " "  # o próprio Short não ganha losango nas suas frases
    outro = ed._cortes[ed._ordem[1]]
    ed.selecionar(ed._ordem[1])
    assert outro.id != atual.id
    assert all(len(m) == 2 for m in _margens_das_linhas(ed))


@pytest.mark.gui
def test_sem_nada_a_mostrar_a_legenda_da_tabela_some(editor):
    editor.selecionar(editor._ordem[0])  # o projeto deste teste não tem análise gravada
    if not editor._legenda_da_tabela.chips:
        assert editor._legenda_da_tabela.winfo_manager() == ""


@pytest.mark.gui
def test_a_borracha_funciona_num_short_com_a_legenda_corrigida(raiz, pastas, tmp_path):
    from tests.test_studio_shorts import corrigir_a_primeira_palavra

    quadro, aba = _aba(raiz, pastas, tmp_path)
    try:
        ed = aba.editor
        ed.selecionar(ed._ordem[0])
        corrigir_a_primeira_palavra(ed)
        ed._guardar_detalhe()
        assert _aberto(ed).legenda
        alvo = _incluidas(ed)[30]
        assert ed._janela._pode_pintar(alvo, FERRAMENTA_BORRACHA)  # a palavra tem posição
        ed._pintou(_incluidas(ed)[30:34], FERRAMENTA_BORRACHA)
        assert len(_palavras_na_tabela(ed, "cortado")) == 4
        assert "corrigida" in _palavras_na_tabela(ed, "dentro")  # e a correção continua
    finally:
        quadro.destroy()
