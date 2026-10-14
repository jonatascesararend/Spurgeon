"""Painel "Português" da interface: funções puras e o painel de verdade (janela escondida)."""

from __future__ import annotations

import re
import tkinter

import pytest

from spurgeon.transcricao.verificador import Verificador
from spurgeon.transcricao.vocabulario import carregar_vocabulario
from spurgeon.ui.verificacao import PainelDePortugues, corpos_das_linhas, indice_do_tk

MARCA = re.compile(r"^\[(\d+:\d{2})\] ?")


def test_corpos_das_linhas_tiram_o_horario_e_dizem_o_tamanho_dele():
    corpos, cortes = corpos_das_linhas("[0:03] Olá mundo\n[1:10] outra\nsem horário", MARCA)
    assert corpos == ["Olá mundo", "outra", "sem horário"]
    assert cortes == [7, 7, 0]


def test_sem_prefixo_a_linha_inteira_e_o_corpo():
    assert corpos_das_linhas("a b\nc", None) == (["a b", "c"], [0, 0])


def test_indice_do_tk_conta_linhas_a_partir_de_um():
    assert indice_do_tk(0, 5) == "1.5"
    assert indice_do_tk(2, 0) == "3.0"


# --- painel de verdade ---------------------------------------------------------------------


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


TEXTO = (
    "[0:03] Ele disse isso. e depois saiu\n"
    "[0:09] Nós nós vamos chamá -las pelo nome\n"
    "[0:15] A blimbrado voltou para casa"
)


@pytest.fixture
def montado(raiz, dicionario_br, tmp_path):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    caixa = tkinter.Text(janela, undo=True)
    caixa.pack()
    caixa.insert("1.0", TEXTO)
    caixa.edit_reset()
    vocabulario = tmp_path / "vocabulario.txt"
    verificador = Verificador(["Spurgeon"], dicionario=dicionario_br)
    mudancas: list[int] = []
    painel = PainelDePortugues(
        janela,
        caixa,
        lambda: verificador,
        vocabulario=vocabulario,
        prefixo=MARCA,
        ao_corrigir=lambda: mudancas.append(1),
    )
    painel.pack()
    painel.verificar(sincrono=True)
    painel.caixa, painel.vocabulario, painel.verificador, painel.mudancas = (
        caixa, vocabulario, verificador, mudancas,
    )  # fmt: skip
    yield painel
    janela.destroy()


def pendentes(painel) -> list[tuple[str, str]]:
    return [(i.problema.tipo, i.problema.trecho) for i in painel._itens if i.estado == "pendente"]


@pytest.mark.gui
def test_os_avisos_aparecem_na_lista_e_sublinhados_no_texto(montado):
    tipos = {tipo for tipo, _ in pendentes(montado)}
    assert tipos == {"maiuscula", "repeticao", "pontuacao", "ortografia"}
    assert montado._lista.size() == len(pendentes(montado))
    sublinhado = montado.caixa.tag_ranges("pt_ortografia")
    assert montado.caixa.get(sublinhado[0], sublinhado[1]) == "blimbrado"  # sem o [horário]


@pytest.mark.gui
def test_aplicar_uma_sugestao_troca_so_o_trecho_do_aviso(montado):
    i = next(k for k, it in enumerate(montado._itens) if it.problema.tipo == "maiuscula")
    montado._selecionar(i)
    assert montado.aplicar_sugestao("E", ir_ao_proximo=False)
    linhas = montado.caixa.get("1.0", "end-1c").split("\n")
    assert linhas[0] == "[0:03] Ele disse isso. E depois saiu"
    assert ("maiuscula", "e") not in pendentes(montado)
    assert montado.mudancas  # a tela quer saber que o texto mudou


@pytest.mark.gui
def test_a_troca_acompanha_o_texto_mesmo_depois_de_outra_edicao_antes_dele(montado):
    montado.caixa.insert("1.0", "INSERIDO ")  # desloca todas as posições da primeira linha
    i = next(k for k, it in enumerate(montado._itens) if it.problema.tipo == "maiuscula")
    montado._selecionar(i)
    assert montado.aplicar_sugestao("E", ir_ao_proximo=False)
    assert montado.caixa.get("1.0", "end-1c").split("\n")[0] == (
        "INSERIDO [0:03] Ele disse isso. E depois saiu"
    )


@pytest.mark.gui
def test_trecho_alterado_pela_pessoa_nao_e_trocado_as_cegas(montado):
    i = next(k for k, it in enumerate(montado._itens) if it.problema.tipo == "ortografia")
    ini, fim = montado._faixa(montado._itens[i])
    montado.caixa.delete(ini, fim)
    montado.caixa.insert(ini, "outra")
    assert not montado._trocar(i, "casa")
    assert montado._itens[i].estado == "desatualizado"
    assert "outra" in montado.caixa.get("1.0", "end-1c")


@pytest.mark.gui
def test_corrigir_todos_do_tipo_aplica_a_primeira_sugestao_de_cada(montado):
    feitos = montado.corrigir_todos("pontuacao")
    assert feitos == 1
    assert "chamá-las" in montado.caixa.get("1.0", "end-1c")
    assert all(tipo != "pontuacao" for tipo, _ in pendentes(montado))


@pytest.mark.gui
def test_corrigir_todos_e_um_passo_so_no_desfazer(montado):
    antes = montado.caixa.get("1.0", "end-1c")
    montado.corrigir_todos("maiuscula")
    montado.corrigir_todos("repeticao")
    depois = montado.caixa.get("1.0", "end-1c")
    assert depois != antes
    montado.caixa.edit_undo()
    assert montado.caixa.get("1.0", "end-1c") != depois  # desfez o último lote


@pytest.mark.gui
def test_ignorar_some_com_todos_os_iguais_e_nao_volta_na_proxima_verificacao(montado):
    i = next(k for k, it in enumerate(montado._itens) if it.problema.tipo == "ortografia")
    montado._selecionar(i)
    montado.ignorar_atual()
    assert all(tipo != "ortografia" for tipo, _ in pendentes(montado))
    montado.verificar(sincrono=True)
    assert all(tipo != "ortografia" for tipo, _ in pendentes(montado))


@pytest.mark.gui
def test_guardar_no_vocabulario_grava_o_arquivo_e_para_de_avisar(montado):
    i = next(k for k, it in enumerate(montado._itens) if it.problema.tipo == "ortografia")
    montado._selecionar(i)
    montado.aceitar_atual()
    assert carregar_vocabulario(montado.vocabulario) == ["blimbrado"]
    montado.verificar(sincrono=True)
    assert all(tipo != "ortografia" for tipo, _ in pendentes(montado))


@pytest.mark.gui
def test_proximo_percorre_os_avisos_e_da_a_volta(montado):
    n = len(montado._visiveis)
    primeiro = montado._atual
    for _ in range(n):
        montado.proximo(1)
    assert montado._atual == primeiro
    montado.proximo(-1)
    assert montado._atual == montado._visiveis[-1]


@pytest.mark.gui
def test_limpar_tira_os_sublinhados_e_a_lista(montado):
    montado.limpar()
    assert montado._lista.size() == 0
    assert not montado.caixa.tag_ranges("pt_ortografia")
