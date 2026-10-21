"""Studio Shorts: a tabela de frases (edição no lugar), os ajustes da prévia e refazer com a IA."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import InfoMidia
from spurgeon.render import previa
from spurgeon.shorts.analise import separar_frases
from spurgeon.shorts.edicao import (
    LinhaDaTabela,
    indices_das_palavras,
    legenda_preservada,
    linhas_da_tabela,
    mover_limite,
    palavras_com_a_legenda,
)
from spurgeon.shorts.livre import ContextoDaIA
from spurgeon.shorts.llm import CAMPOS_REFAZIVEIS, ClienteLLM
from spurgeon.ui import studio_shorts
from spurgeon.ui.studio_shorts import ORDEM_HORARIO, EditorDeCortes, OrigemDaPrevia
from spurgeon.ui.tabela_de_frases import TabelaDeFrases
from tests import test_studio_shorts as base
from tests.test_shorts import OllamaFalso, cortes_do_sermao, sermao_falso

caixas, editor, pastas, raiz = base.caixas, base.editor, base.pastas, base.raiz
TarefasNaHora = base.TarefasNaHora

# --- as linhas da tabela (sem janela) -------------------------------------------------------


def _corte_e_palavras():
    palavras = sermao_falso(frases=100)
    return palavras, cortes_do_sermao(palavras)[0]


def test_a_tabela_tem_os_dois_marcadores_e_o_corte_inteiro_uma_frase_por_linha():
    palavras, corte = _corte_e_palavras()
    primeira, ultima = indices_das_palavras(palavras, corte)
    frases = separar_frases(palavras)
    linhas = linhas_da_tabela(palavras, corte, frases, fora=30)
    tipos = [linha.tipo for linha in linhas]
    inicio, fim = tipos.index("inicio"), tipos.index("fim")
    assert tipos.count("inicio") == tipos.count("fim") == 1 and inicio < fim
    assert set(tipos[:inicio]) == set(tipos[fim + 1 :]) == {"fora"}
    assert set(tipos[inicio + 1 : fim]) == {"dentro"}
    dentro = [t for linha in linhas[inicio + 1 : fim] for t in linha.textos]
    assert dentro == [p.texto for p in palavras[primeira : ultima + 1]]
    assert len([p for linha in linhas[:inicio] for p in linha.textos]) == min(30, primeira)
    frase_de = {i: n for n, f in enumerate(frases) for i in range(f.primeira, f.ultima + 1)}
    for linha in linhas:
        if not linha.e_marcador:
            assert len({frase_de[i] for i in linha.indices}) == 1  # nunca mistura duas frases
    horas = [linha.tempo for linha in linhas if not linha.e_marcador]
    assert horas == sorted(horas)


def test_a_frase_que_o_corte_atravessa_vira_duas_linhas_uma_de_cada_lado():
    palavras, corte = _corte_e_palavras()
    no_meio = mover_limite(palavras, corte, "inicio", 4)
    linhas = linhas_da_tabela(palavras, no_meio, separar_frases(palavras), fora=30)
    k = [linha.tipo for linha in linhas].index("inicio")
    antes, depois = linhas[k - 1], linhas[k + 1]
    assert (antes.tipo, depois.tipo) == ("fora", "dentro")
    assert antes.indices[-1] + 1 == depois.indices[0]  # a mesma frase, cortada no marcador
    assert len(antes.textos) == 4


def test_com_a_legenda_corrigida_as_linhas_do_corte_saem_do_texto_novo():
    palavras, corte = _corte_e_palavras()
    frases = separar_frases(palavras)
    dentro = " ".join(p.texto for p in palavras_com_a_legenda(palavras, corte))
    editado = dentro.replace(dentro.split()[0], "corrigida", 1)
    linhas = linhas_da_tabela(palavras, corte, frases, fora=10, texto_dentro=editado)
    do_corte = [linha for linha in linhas if linha.tipo == "dentro"]
    assert do_corte[0].textos[0] == "corrigida" and not any(x.indices for x in do_corte)
    assert all(linha.texto.endswith((".", "?")) for linha in do_corte)
    assert " ".join(linha.texto for linha in do_corte) == editado
    # o mesmo texto da transcrição não conta como edição: as linhas guardam as posições
    iguais = linhas_da_tabela(palavras, corte, frases, fora=10, texto_dentro=dentro)
    assert all(linha.indices for linha in iguais if linha.tipo == "dentro")
    com_legenda_do_corte = replace(corte, legenda=editado)
    de_novo = linhas_da_tabela(palavras, com_legenda_do_corte, frases, fora=10)
    assert [x.textos for x in de_novo if x.tipo == "dentro"] == [x.textos for x in do_corte]


# --- a legenda corrigida acompanha as pontas -------------------------------------------------


def _com_legenda(palavras, corte, depois_de: int | None = None):
    """O corte com a legenda corrigida: `corrigida` no lugar da palavra `depois_de` (do corte)."""
    primeira, ultima = indices_das_palavras(palavras, corte)
    textos = [p.texto for p in palavras[primeira : ultima + 1]]
    textos[depois_de or 0] = "corrigida"
    return replace(corte, legenda=" ".join(textos))


def test_legenda_preservada_sem_correcao_e_vazia():
    palavras, corte = _corte_e_palavras()
    assert legenda_preservada(palavras, corte, mover_limite(palavras, corte, "fim", 5)) == ""


def test_a_correcao_das_palavras_que_ficam_continua_quando_as_pontas_mexem():
    palavras, corte = _corte_e_palavras()
    corrigido = _com_legenda(palavras, corte, depois_de=20)
    maior = mover_limite(palavras, mover_limite(palavras, corrigido, "fim", 7), "inicio", -5)
    nova = legenda_preservada(palavras, corrigido, maior)
    primeira, ultima = indices_das_palavras(palavras, maior)
    p0, _ = indices_das_palavras(palavras, corrigido)
    esperado = [p.texto for p in palavras[primeira : ultima + 1]]
    esperado[p0 - primeira + 20] = "corrigida"
    assert nova.split() == esperado  # as palavras que entraram vêm da transcrição
    menor = mover_limite(palavras, corrigido, "fim", -7)
    assert "corrigida" in legenda_preservada(palavras, corrigido, menor)


def test_a_correcao_de_uma_palavra_que_sai_do_corte_se_perde():
    palavras, corte = _corte_e_palavras()
    corrigido = _com_legenda(palavras, corte, depois_de=2)
    menos = mover_limite(palavras, corrigido, "inicio", 5)
    assert legenda_preservada(palavras, corrigido, menos) == ""


# --- a tabela como widget ---------------------------------------------------------------------

_LINHAS = [
    LinhaDaTabela("fora", 10.0, ("Antes", "um."), (0, 1)),
    LinhaDaTabela("inicio"),
    LinhaDaTabela("dentro", 20.0, ("Dentro", "um."), (2, 3)),
    LinhaDaTabela("dentro", 25.0, ("Dentro", "dois."), (4, 5)),
    LinhaDaTabela("fim"),
    LinhaDaTabela("fora", 30.0, ("Depois.",), (6,)),
]
# linhas do widget: 1 fora, 2 marcador, 3 e 4 dentro, 5 marcador, 6 fora; o texto começa na coluna
# 5 (`0:10`, `0:20`, `0:25` e `0:30` têm 4 caracteres, mais o TAB)
_COLUNA = 5


@pytest.fixture
def tabela(raiz):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    eventos = SimpleNamespace(edicoes=0, duplos=[], menus=[])

    def editou():
        eventos.edicoes += 1

    t = TabelaDeFrases(
        janela,
        height=200,
        ao_editar=editou,
        ao_duplo_clique=eventos.duplos.append,
        ao_menu=lambda indice, ponta: eventos.menus.append((indice, ponta)),
    )
    t.pack(fill="both", expand=True)
    t.mostrar(_LINHAS, centralizar="inicio")
    t.eventos = eventos
    yield t
    janela.destroy()


def _linha(tabela, numero: int) -> str:
    return tabela._textbox.get(f"{numero}.0", f"{numero}.end")


@pytest.mark.gui
def test_a_tabela_mostra_a_hora_e_a_frase_em_cada_linha(tabela):
    assert _linha(tabela, 1) == "0:10\tAntes um."
    assert _linha(tabela, 2) == "▶ COMEÇA AQUI ▶"
    assert _linha(tabela, 3) == "0:20\tDentro um."
    assert _linha(tabela, 5) == "◀ TERMINA AQUI ◀"
    assert tabela.texto_dentro() == "Dentro um. Dentro dois."
    caixa = tabela._textbox
    assert caixa.tag_ranges("hora") and caixa.tag_ranges("fora") and caixa.tag_ranges("dentro")


@pytest.mark.gui
def test_pode_escrever_dentro_do_corte_e_o_texto_novo_fica_em_destaque(tabela):
    caixa = tabela._textbox
    caixa.insert(f"3.{_COLUNA}", "Novo ")
    assert _linha(tabela, 3) == "0:20\tNovo Dentro um."
    assert "dentro" in caixa.tag_names(f"3.{_COLUNA}")
    caixa.insert("4.end", " fim")  # no fim da linha também
    assert _linha(tabela, 4) == "0:25\tDentro dois. fim"


@pytest.mark.gui
def test_nao_deixa_escrever_na_hora_no_que_e_de_fora_nem_nos_marcadores(tabela):
    caixa = tabela._textbox
    antes = caixa.get("1.0", "end-1c")
    for onde in ("3.1", "3.0", "1.7", "1.end", "2.3", "5.2", "6.3"):
        caixa.insert(onde, "XX")
    assert caixa.get("1.0", "end-1c") == antes


@pytest.mark.gui
def test_apagar_so_leva_o_texto_do_corte_e_a_estrutura_fica(tabela):
    caixa = tabela._textbox
    fim = caixa.index("end-1c")
    caixa.delete(f"3.{_COLUNA + 3}", "4.8")  # do meio de uma linha ao meio da outra
    assert _linha(tabela, 3) == "0:20\tDen"
    assert _linha(tabela, 4).startswith("0:25\t")  # a hora e a quebra de linha continuam
    assert caixa.index("end-1c") == fim  # nenhuma linha sumiu nem se juntou
    caixa.delete("3.0", "3.4")  # a hora não sai
    caixa.delete("1.0", "1.end")  # nem o texto de fora
    caixa.delete("2.0", "2.end")  # nem o marcador
    caixa.delete("3.end", "4.0")  # nem a quebra de linha entre duas frases
    assert _linha(tabela, 3) == "0:20\tDen" and _linha(tabela, 1) == "0:10\tAntes um."
    assert _linha(tabela, 2) == "▶ COMEÇA AQUI ▶"


@pytest.mark.gui
def test_enter_nao_parte_a_frase_e_colar_com_quebras_vira_espacos(tabela):
    caixa = tabela._textbox
    fim = caixa.index("end-1c")
    caixa.insert("3.end", "\n")
    assert caixa.index("end-1c") == fim
    caixa.insert("3.end", " a\nb")
    assert _linha(tabela, 3) == "0:20\tDentro um. a b"


@pytest.mark.gui
def test_o_aviso_de_edicao_so_vem_quando_o_texto_mudou(tabela):
    tabela._conferir_edicao()
    assert tabela.eventos.edicoes == 0
    tabela._textbox.insert(f"3.{_COLUNA}", "x")
    tabela._conferir_edicao()
    tabela._conferir_edicao()
    assert tabela.eventos.edicoes == 1
    tabela.mostrar(_LINHAS)  # mostrar de novo não é edição
    tabela._conferir_edicao()
    assert tabela.eventos.edicoes == 1 and tabela.texto_dentro() == "Dentro um. Dentro dois."


def _clicar(tabela, indice, *, duplo=True):
    linha, coluna = (int(n) for n in tabela.posicao_da_palavra(indice).split("."))
    original = tabela._posicao
    tabela._posicao = lambda ref: (linha, coluna + 1) if str(ref).startswith("@") else original(ref)
    try:
        evento = SimpleNamespace(x=1, y=1)
        return tabela._duplo_clique(evento) if duplo else tabela.palavra_em(1, 1)
    finally:
        del tabela._posicao


@pytest.mark.gui
def test_a_palavra_sob_o_mouse_e_achada_pela_posicao_na_transcricao(tabela):
    assert tabela.posicao_da_palavra(1) == f"1.{_COLUNA + 6}"  # "um." depois de "Antes "
    assert _clicar(tabela, 1, duplo=False) == 1
    assert _clicar(tabela, 3, duplo=False) == 3  # dentro do corte também, enquanto não for editada
    tabela._textbox.insert(f"3.{_COLUNA}", "x")  # a linha mudou: já não dá para dizer
    assert _clicar(tabela, 3, duplo=False) is None
    assert _clicar(tabela, 1, duplo=False) == 1  # as outras seguem valendo


@pytest.mark.gui
def test_duplo_clique_so_age_no_cinza(tabela):
    assert _clicar(tabela, 6) == "break" and tabela.eventos.duplos == [6]
    assert _clicar(tabela, 2) is None and tabela.eventos.duplos == [6]  # dentro: gesto normal


@pytest.mark.gui
def test_mostrar_sem_centralizar_nao_quebra_com_a_tabela_vazia(raiz):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    t = TabelaDeFrases(
        janela, height=100, ao_editar=lambda: None, ao_duplo_clique=print, ao_menu=print
    )
    t.pack()
    t.mostrar(_LINHAS)  # a primeira vez, sem âncora
    t.mostrar([])
    t.mostrar_aviso("(transcrição indisponível)")
    assert t.texto_dentro() == "" and t.linhas == []
    janela.destroy()


# --- editar a legenda: tabela e caixa se acompanham -----------------------------------------


def _primeira_linha_do_corte(tabela) -> int:
    return next(n for n, linha in enumerate(tabela.linhas, start=1) if linha.tipo == "dentro")


@pytest.mark.gui
def test_o_que_se_escreve_na_tabela_vira_a_legenda_do_corte(editor):
    editor.selecionar(editor._ordem[0])
    tabela = editor._janela
    n = _primeira_linha_do_corte(tabela)
    tabela._textbox.insert(f"{n}.{tabela._colunas[n - 1]}", "Ó ")
    tabela._conferir_edicao()  # o que o <KeyRelease> faz
    assert editor._tabela_pendente and editor.mudou
    editor._guardar_detalhe()  # salvar, trocar de corte, renderizar… passam por aqui
    corte = editor._cortes[editor._atual]
    assert corte.legenda.startswith("Ó ")
    assert editor._texto.get("1.0", "end-1c").startswith("Ó ")  # a caixa recebeu o texto
    assert "legenda editada" in editor._rotulo_editada.cget("text")
    assert not editor._tabela_pendente


@pytest.mark.gui
def test_a_edicao_na_tabela_chega_a_caixa_depois_de_uma_pausa(editor, raiz):
    editor.selecionar(editor._ordem[0])
    tabela = editor._janela
    n = _primeira_linha_do_corte(tabela)
    tabela._textbox.insert(f"{n}.{tabela._colunas[n - 1]}", "Ó ")
    tabela._conferir_edicao()
    assert not editor._texto.get("1.0", "end-1c").startswith("Ó ")  # ainda não: espera a pausa
    editor._levar_a_tabela_para_a_legenda()  # o que o `after` chama
    assert editor._texto.get("1.0", "end-1c").startswith("Ó ")


@pytest.mark.gui
def test_o_que_se_escreve_na_caixa_da_legenda_aparece_na_tabela(editor):
    editor.selecionar(editor._ordem[0])
    palavras_do_corte = editor._texto.get("1.0", "end-1c").split()
    corrigida = " ".join(["corrigida", *palavras_do_corte[1:]])
    editor._texto.delete("1.0", "end")
    editor._texto.insert("1.0", corrigida)
    editor._ao_editar_legenda()
    editor._levar_a_legenda_para_a_tabela()  # o que o `after` chama
    assert editor._janela.texto_dentro().split()[0] == "corrigida"
    assert editor._janela.texto_dentro() == " ".join(corrigida.split())


@pytest.mark.gui
def test_desfazer_as_correcoes_tambem_limpa_a_tabela(editor):
    editor.selecionar(editor._ordem[0])
    original = editor._janela.texto_dentro()
    tabela = editor._janela
    n = _primeira_linha_do_corte(tabela)
    tabela._textbox.insert(f"{n}.{tabela._colunas[n - 1]}", "Ó ")
    tabela._conferir_edicao()
    editor._guardar_detalhe()
    assert editor._janela.texto_dentro() != original
    editor.desfazer_legenda()
    assert editor._janela.texto_dentro() == original
    assert editor._cortes[editor._atual].legenda == ""


@pytest.mark.gui
def test_trocar_de_corte_guarda_o_que_estava_na_tabela(editor):
    editor.selecionar(editor._ordem[0])
    primeiro = editor._atual
    tabela = editor._janela
    n = _primeira_linha_do_corte(tabela)
    tabela._textbox.insert(f"{n}.{tabela._colunas[n - 1]}", "Ó ")
    tabela._conferir_edicao()
    editor.selecionar(editor._ordem[1])
    assert editor._cortes[primeiro].legenda.startswith("Ó ")
    assert not editor._janela.texto_dentro().startswith("Ó ")


# --- mexer nas pontas sem refazer o painel -----------------------------------------------------


@pytest.mark.gui
def test_mexer_nas_pontas_atualiza_a_linha_da_lista_sem_refazer_a_lista(editor):
    editor.selecionar(editor._ordem[0])
    i = editor._atual
    linhas = dict(editor._linhas)
    info = editor._rotulos_da_linha[i]["info"]
    antes = info.cget("text")
    assert editor.mover_frase("fim", 1) and editor.mover("inicio", -2)
    assert all(editor._linhas[k] is quadro for k, quadro in linhas.items())  # os mesmos widgets
    assert editor._rotulos_da_linha[i]["info"] is info and info.cget("text") != antes
    assert "editado" in info.cget("text")


@pytest.mark.gui
def test_mexer_nas_pontas_na_ordem_por_horario_reposiciona_sem_recriar(editor):
    editor._seletor_ordem.set(ORDEM_HORARIO)
    editor._reordenar()
    editor.selecionar(editor._ordem[0])
    linhas = dict(editor._linhas)
    primeiro = editor._atual
    editor._cortes[primeiro] = replace(editor._cortes[primeiro], inicio=1e6, fim=1e6 + 30)
    editor._reposicionar()  # o primeiro passou a ser o último
    assert editor._ordem[-1] == primeiro
    assert all(editor._linhas[k] is quadro for k, quadro in linhas.items())
    posicoes = {i: int(editor._linhas[i].grid_info()["row"]) for i in editor._ordem}
    assert [posicoes[i] for i in editor._ordem] == list(range(len(editor._ordem)))


# --- ajustes da prévia: altura da legenda (de todos) e corte das pausas (de cada Short) -------


@pytest.fixture
def editor_com_ajustes(raiz, pastas, tmp_path, monkeypatch):
    import customtkinter as ctk

    chamadas = []

    def falsa(video, info, corte, palavras, destino, **kwargs):
        chamadas.append(kwargs)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x")
        return previa.PreviaDoShort(destino, None, corte.duracao)

    monkeypatch.setattr(previa, "previa_do_corte", falsa)
    info = InfoMidia(caminho=tmp_path / "v.mp4", duracao_s=100.0, tem_video=True, tem_audio=True)
    estado = {"origem": OrigemDaPrevia(tmp_path / "v.mp4", info, OpcoesProcessamento())}
    gravados = []

    def definir(legenda=None):
        gravados.append(legenda)
        if legenda is not None:
            estado["origem"].opcoes.legenda_posicao_vertical = legenda

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(
        janela,
        pastas,
        legenda_queimada=lambda: True,
        tarefas=TarefasNaHora(),
        origem_da_previa=lambda: estado["origem"],
        definir_ajustes=definir,
    )
    ed.pack(fill="both", expand=True)
    ed._pasta_das_previas = tmp_path / "previas"
    ed.chamadas, ed.estado, ed.gravados = chamadas, estado, gravados
    monkeypatch.setattr(ed._player, "carregar", lambda p: None)
    yield ed
    janela.destroy()


@pytest.mark.gui
def test_subir_e_descer_a_legenda_muda_a_opcao_e_invalida_a_previa(editor_com_ajustes):
    ed = editor_com_ajustes
    ed.selecionar(ed._ordem[0])
    ed.gerar_previa()
    assert ed._player.winfo_manager() == "grid"
    assert "62%" in ed._rotulo_legenda_altura.cget("text")
    assert ed.mover_legenda(-1)  # para cima: a altura é contada de cima, então diminui
    assert ed.estado["origem"].opcoes.legenda_posicao_vertical == pytest.approx(0.60)
    assert ed.gravados[-1] == pytest.approx(0.60)
    assert ed._player.winfo_manager() == ""  # a prévia era da legenda em outra altura
    assert "versão anterior" in ed._situacao_previa.cget("text")
    assert "60%" in ed._rotulo_legenda_altura.cget("text")
    assert ed.mover_legenda(1) and ed.mover_legenda(1)
    assert ed.estado["origem"].opcoes.legenda_posicao_vertical == pytest.approx(0.64)
    ed.gerar_previa()
    assert ed.chamadas[-1]["posicao_legenda"] == pytest.approx(0.64)


@pytest.mark.gui
def test_a_legenda_nao_passa_dos_limites(editor_com_ajustes):
    ed = editor_com_ajustes
    ed.selecionar(ed._ordem[0])
    ed.estado["origem"].opcoes.legenda_posicao_vertical = 0.31
    assert ed.mover_legenda(-1) and ed.estado["origem"].opcoes.legenda_posicao_vertical == 0.3
    assert ed.mover_legenda(-1) is False  # já está no topo
    ed.estado["origem"].opcoes.legenda_posicao_vertical = 0.89
    assert ed.mover_legenda(1) and ed.estado["origem"].opcoes.legenda_posicao_vertical == 0.9
    assert ed.mover_legenda(1) is False


@pytest.mark.gui
def test_cortar_pausas_e_so_do_short_aberto_e_vai_para_a_previa(editor_com_ajustes):
    ed = editor_com_ajustes
    primeiro, segundo = ed._ordem[0], ed._ordem[1]
    ed.selecionar(primeiro)
    corte = ed._cortes[primeiro]
    assert not ed._var_cortar_pausas.get() and ed._var_pausa.get() == "1,0"
    assert corte.pausa_s == 0.0 and "mantém todas as pausas" in ed._rotulo_pausas.cget("text")
    ed._var_cortar_pausas.set(True)
    ed._var_pausa.set("0,5")  # as frases do sermão de teste têm 0,6 s entre uma e outra
    ed._mudou_o_corte_de_pausas()
    assert corte.pausa_s == 0.5 and ed.mudou
    assert ed._cortes[segundo].pausa_s == 0.0  # os outros Shorts não mudam
    texto = ed._rotulo_pausas.cget("text")
    assert "0,5 s ou mais ficam com 0,25 s" in texto and "s a menos" in texto
    ed.gerar_previa()  # a prévia já sabe pelo próprio corte: nenhum parâmetro de pausas
    assert "cortar_pausas_s" not in ed.chamadas[-1]
    ed._var_pausa.set("abc")  # não é número: volta ao que valia
    ed._mudou_o_corte_de_pausas()
    assert corte.pausa_s == 0.5 and ed._var_pausa.get() == "0,5"
    for digitado, vale in (("50", 10.0), ("0,05", 0.1), ("0", 0.1), ("2.5", 2.5)):
        ed._var_pausa.set(digitado)
        ed._mudou_o_corte_de_pausas()
        assert corte.pausa_s == vale, digitado  # o mínimo é 0,1 s
    assert "2,5 s ou mais ficam com 0,4 s" in ed._rotulo_pausas.cget("text")
    assert "nada é cortado" in ed._rotulo_pausas.cget("text")  # as pausas do teste têm 0,6 s
    ed._var_cortar_pausas.set(False)
    ed._mudou_o_corte_de_pausas()
    assert corte.pausa_s == 0.0 and ed._var_pausa.get() == "2,5"  # o tamanho fica para depois
    ed.selecionar(segundo)  # outro Short: a caixa mostra o dele
    assert not ed._var_cortar_pausas.get()


@pytest.mark.gui
def test_a_pausa_do_short_vai_para_o_arquivo_e_o_registro_acusa_a_mudanca(
    editor_com_ajustes, pastas
):
    from spurgeon.shorts.etapas import carregar_cortes

    ed = editor_com_ajustes
    ed.selecionar(ed._ordem[0])
    id_ = ed._cortes[ed._atual].id
    ed._var_cortar_pausas.set(True)
    ed._var_pausa.set("0,1")
    ed._mudou_o_corte_de_pausas()
    assert ed.salvar()
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[id_]
    assert gravado.pausa_s == 0.1


@pytest.mark.gui
def test_aplicar_a_pausa_em_todos_copia_para_os_outros_shorts(editor_com_ajustes, caixas):
    ed = editor_com_ajustes
    ed.selecionar(ed._ordem[0])
    ed._var_cortar_pausas.set(True)
    ed._var_pausa.set("2,0")
    ed._mudou_o_corte_de_pausas()
    outros = len(ed._cortes) - 1
    assert ed.aplicar_a_pausa_em_todos() == outros
    assert {c.pausa_s for c in ed._cortes} == {2.0}
    assert caixas[-1][0] == "Aplicar a todos os Shorts"
    assert ed.aplicar_a_pausa_em_todos() == 0  # já são todos iguais


@pytest.mark.gui
def test_a_previa_deixa_de_valer_quando_as_pausas_mudam(editor_com_ajustes):
    ed = editor_com_ajustes
    ed.selecionar(ed._ordem[0])
    ed.gerar_previa()
    assert ed._player.winfo_manager() == "grid"
    ed._var_cortar_pausas.set(True)
    ed._mudou_o_corte_de_pausas()
    assert ed._player.winfo_manager() == ""
    assert "versão anterior" in ed._situacao_previa.cget("text")


@pytest.mark.gui
def test_sem_video_a_legenda_fica_desligada_mas_as_pausas_do_short_continuam(
    editor_com_ajustes, editor
):
    ed = editor_com_ajustes
    ed.estado["origem"] = None
    ed.selecionar(ed._ordem[0])
    assert str(ed._botoes_da_legenda[0].cget("state")) == "disabled"
    assert ed.mover_legenda(1) is False
    assert str(ed._caixa_pausas.cget("state")) == "normal"  # as pausas são do Short, não do vídeo
    assert (
        editor._quadro_da_legenda.winfo_manager() == ""
    )  # a aprovação não tem a altura da legenda
    assert str(editor._caixa_pausas.cget("state")) == "normal"


# --- refazer título, descrição e hashtags com a IA --------------------------------------------


class ClienteDaIAFalso:
    def __init__(self):
        self.pedidos = []

    def refazer(self, campo, sermao, trecho, categoria, duracao_s, titulos_usados=()):
        self.pedidos.append(
            SimpleNamespace(campo=campo, sermao=sermao, trecho=trecho, titulos=list(titulos_usados))
        )
        return {
            "titulo": "Um título novo?",
            "descricao": "Uma descrição nova.",
            "hashtags": ["#shorts", "#novo"],
        }[campo]


class TarefasParadas:
    """Guarda a tarefa sem rodar: o teste decide quando ela termina."""

    def __init__(self):
        self.fila = []

    def rodar(self, funcao, ao_terminar=None, ao_falhar=None, *, chave=None):
        self.fila.append((funcao, ao_terminar, ao_falhar))

    def terminar(self):
        funcao, ao_terminar, ao_falhar = self.fila.pop(0)
        try:
            resultado = funcao()
        except Exception as erro:
            ao_falhar(erro)
        else:
            ao_terminar(resultado)


@pytest.fixture
def editor_com_ia(raiz, pastas, monkeypatch):
    import customtkinter as ctk

    cliente = ClienteDaIAFalso()
    preparos = []

    def preparar(modelo, **kwargs):
        preparos.append((modelo, kwargs))
        return cliente

    monkeypatch.setattr(studio_shorts, "preparar_cliente", preparar)
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    tarefas = TarefasParadas()
    contexto = {"ia": ContextoDaIA("qwen3:8b", 4, False)}
    ed = EditorDeCortes(
        janela, pastas, tarefas=tarefas, contexto_da_ia=lambda: contexto["ia"],
        legenda_queimada=lambda: True,
    )  # fmt: skip
    ed.pack(fill="both", expand=True)
    ed.cliente, ed.tarefas, ed.preparos, ed.contexto = cliente, tarefas, preparos, contexto
    yield ed
    janela.destroy()


@pytest.mark.gui
def test_refazer_o_titulo_usa_o_texto_do_short_e_nao_o_do_trecho_original(editor_com_ia):
    ed = editor_com_ia
    ed.selecionar(ed._ordem[0])
    corte = ed._cortes[ed._atual]
    ed._var_titulo.set("Título velho")
    original = ed._texto.get("1.0", "end-1c")
    ed._texto.delete("1.0", "end")
    ed._texto.insert("1.0", original.replace(original.split()[0], "reescrita", 1))
    ed._ao_editar_legenda()
    assert ed.refazer_com_ia("titulo") is True
    assert ed._refazendo == (corte.id, "titulo")  # um pedido em andamento
    assert str(ed._botoes_da_ia["titulo"].cget("text")) == "Criando…"
    assert all(str(b.cget("state")) == "disabled" for b in ed._botoes_da_ia.values())
    assert ed.refazer_com_ia("descricao") is False  # um por vez: a IA pesa na CPU
    ed.tarefas.terminar()
    pedido = ed.cliente.pedidos[0]
    assert pedido.campo == "titulo" and pedido.trecho.startswith("reescrita")
    assert "Título velho" in pedido.titulos  # o novo tem de ser diferente do de agora
    assert pedido.sermao == "proj"
    assert ed._var_titulo.get() == "Um título novo?"
    assert ed._refazendo is None and ed.mudou
    assert corte.titulo == "Um título novo?" and corte.slug == ""  # o nome do arquivo acompanha
    assert "refeito" in ed._situacao_da_ia["titulo"].cget("text")
    assert all(str(b.cget("state")) == "normal" for b in ed._botoes_da_ia.values())
    assert ed.preparos[0][0] == "qwen3:8b"
    assert ed.preparos[0][1]["manter_na_memoria"] == "3m"  # o Ollama solta o modelo depois


@pytest.mark.gui
def test_refazer_a_descricao_e_as_hashtags_mexe_so_no_campo_pedido(editor_com_ia):
    ed = editor_com_ia
    ed.selecionar(ed._ordem[0])
    titulo = ed._var_titulo.get()
    assert ed.refazer_com_ia("descricao")
    ed.tarefas.terminar()
    assert ed._campo_descricao.get("1.0", "end-1c") == "Uma descrição nova."
    assert ed._var_titulo.get() == titulo and ed._cortes[ed._atual].slug != ""
    assert ed.refazer_com_ia("hashtags")
    ed.tarefas.terminar()
    assert ed._var_hashtags.get() == "#shorts #novo"
    corte = ed._cortes[ed._atual]
    assert corte.descricao == "Uma descrição nova." and corte.hashtags == ["#shorts", "#novo"]


@pytest.mark.gui
def test_o_que_a_ia_criou_so_vai_para_o_arquivo_ao_salvar(editor_com_ia, pastas):
    from spurgeon.shorts.etapas import carregar_cortes

    ed = editor_com_ia
    ed.selecionar(ed._ordem[0])
    id_ = ed._cortes[ed._atual].id
    ed.refazer_com_ia("titulo")
    ed.tarefas.terminar()
    assert {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[id_].titulo != "Um título novo?"
    assert ed.salvar()
    gravado = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}[id_]
    assert gravado.titulo == "Um título novo?" and gravado.slug  # o nome vem do título novo


@pytest.mark.gui
def test_a_ia_que_termina_em_outro_corte_nao_mexe_no_que_esta_aberto(editor_com_ia):
    ed = editor_com_ia
    primeiro, segundo = ed._ordem[0], ed._ordem[1]
    ed.selecionar(primeiro)
    assert ed.refazer_com_ia("titulo")
    ed.selecionar(segundo)  # a pessoa foi olhar outro corte enquanto a IA pensava
    titulo_do_segundo = ed._var_titulo.get()
    assert str(ed._botoes_da_ia["titulo"].cget("text")) != "Criando…"
    ed.tarefas.terminar()
    assert ed._var_titulo.get() == titulo_do_segundo
    assert ed._cortes[primeiro].titulo == "Um título novo?"
    assert ed._cortes[segundo].titulo == titulo_do_segundo
    assert ed.mudou


@pytest.mark.gui
def test_se_a_ia_falha_avisa_e_libera_os_botoes(editor_com_ia, caixas, monkeypatch):
    ed = editor_com_ia

    def falha(*_a, **_k):
        raise ErroFerramenta("O Ollama não está respondendo.")

    monkeypatch.setattr(studio_shorts, "preparar_cliente", falha)
    ed.selecionar(ed._ordem[0])
    titulo = ed._var_titulo.get()
    assert ed.refazer_com_ia("hashtags")
    ed.tarefas.terminar()
    assert ed._refazendo is None and ed._var_titulo.get() == titulo
    assert caixas[-1][0] == "Refazer com IA" and "Ollama" in caixas[-1][1]
    assert all(str(b.cget("state")) == "normal" for b in ed._botoes_da_ia.values())


@pytest.mark.gui
def test_sem_as_opcoes_da_ia_completas_avisa_em_vez_de_pedir(editor_com_ia, caixas):
    ed = editor_com_ia
    ed.contexto["ia"] = None
    ed.selecionar(ed._ordem[0])
    assert ed.refazer_com_ia("titulo") is False and ed._refazendo is None
    assert caixas and caixas[-1][0] == "Refazer com IA" and not ed.tarefas.fila


@pytest.mark.gui
def test_os_botoes_da_ia_somem_onde_nao_ha_ia(editor):
    editor.selecionar(editor._ordem[0])
    assert all(botao.winfo_manager() == "" for botao in editor._botoes_da_ia.values())
    assert editor.refazer_com_ia("titulo") is False
    assert set(editor._botoes_da_ia) == set(CAMPOS_REFAZIVEIS)


# --- o cliente da IA: um pedido por campo -----------------------------------------------------


def _cliente_llm(falso, **kwargs):
    return ClienteLLM("qwen3:8b", cliente=falso, **kwargs)


@pytest.mark.parametrize(
    ("campo", "resposta", "esperado"),
    [
        ("titulo", {"titulo": "  Por que confiar? "}, "Por que confiar?"),
        ("descricao", {"descricao": " Um resumo. "}, "Um resumo."),
        ("hashtags", {"hashtags": ["shorts", "#fé", "fé", "  "]}, ["#shorts", "#fé"]),
    ],
)
def test_refazer_devolve_so_o_campo_pedido(campo, resposta, esperado):
    falso = OllamaFalso([resposta])
    valor = _cliente_llm(falso).refazer(campo, "Miquéias 2", "o texto do Short", "outra", 42.0)
    assert valor == esperado
    pedido = falso.chamadas[0]
    assert "o texto do Short" in pedido["messages"][1]["content"]
    assert list(pedido["format"]["properties"]) == [campo]  # a IA só devolve o que se pediu


def test_refazer_o_titulo_pede_um_diferente_dos_ja_usados_e_so_ele():
    falso = OllamaFalso([{"titulo": "Outro título"}, {"descricao": "Resumo."}])
    cliente = _cliente_llm(falso)
    cliente.refazer("titulo", "S", "texto", "outra", 40.0, ["Título A", "Título B"])
    prompt = falso.chamadas[0]["messages"][1]["content"]
    assert "Título A" in prompt and "Título B" in prompt and "diferente" in prompt
    cliente.refazer("descricao", "S", "texto", "outra", 40.0, ["Título A"])
    assert "Título A" not in falso.chamadas[1]["messages"][1]["content"]


def test_refazer_campo_que_nao_existe_e_erro_e_a_memoria_do_modelo_e_configuravel():
    falso = OllamaFalso([{"titulo": "Um título qualquer"}])
    cliente = _cliente_llm(falso, manter_na_memoria="3m")
    with pytest.raises(ValueError, match="campo"):
        cliente.refazer("slug", "S", "texto", "outra", 40.0)
    cliente.refazer("titulo", "S", "texto", "outra", 40.0)
    assert falso.chamadas[0]["keep_alive"] == "3m"
    outro = OllamaFalso([{"titulo": "Um título qualquer"}])
    _cliente_llm(outro).refazer("titulo", "S", "texto", "outra", 40.0)
    assert outro.chamadas[0]["keep_alive"] == "30m"  # o padrão de antes não mudou


# --- textos de segundos e o fim de vida do editor ---------------------------------------------


@pytest.mark.parametrize(
    ("valor", "texto"), [(1.0, "1,0"), (0.5, "0,5"), (0.25, "0,25"), (10.0, "10,0"), (2.5, "2,5")]
)
def test_os_segundos_aparecem_como_a_pessoa_escreve(valor, texto):
    assert studio_shorts._texto_de_segundos(valor) == texto
    assert studio_shorts.ler_segundos(texto) == valor
    assert studio_shorts.ler_segundos(texto.replace(",", ".")) == valor
    with pytest.raises(ValueError, match="could not convert"):
        studio_shorts.ler_segundos("abc")


@pytest.mark.gui
def test_destruir_o_editor_com_edicao_pendente_nao_deixa_erro_para_tras(raiz, pastas):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(janela, pastas, legenda_queimada=lambda: True)
    ed.pack(fill="both", expand=True)
    ed.selecionar(ed._ordem[0])
    tabela = ed._janela
    n = _primeira_linha_do_corte(tabela)
    tabela._textbox.insert(f"{n}.{tabela._colunas[n - 1]}", "x")
    tabela._conferir_edicao()
    ed._ao_editar_legenda()
    assert ed._sincronizacao is not None and ed._acompanhar_a_tabela is not None
    erros = []
    raiz.report_callback_exception = lambda *args: erros.append(args)
    ed.destroy()
    janela.update()
    raiz.after(500)
    janela.update()
    janela.destroy()
    assert erros == []
