"""O `configure` econômico (menos piscar) e as dicas de ferramenta."""

from __future__ import annotations

import tkinter

import pytest

from spurgeon.ui import dicas, estabilidade

pytestmark = pytest.mark.gui


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


def _contar(monkeypatch, objeto, nome):
    """Conta as chamadas de `objeto.nome` (o redesenho de verdade, no CustomTkinter)."""
    chamadas = []
    original = getattr(objeto, nome)

    def contando(*args, **kwargs):
        chamadas.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(objeto, nome, contando)
    return chamadas


def test_configure_igual_nao_redesenha_e_diferente_redesenha(raiz, monkeypatch):
    import customtkinter as ctk

    botao = ctk.CTkButton(raiz, text="a", state="normal")
    desenhos = _contar(monkeypatch, ctk.CTkButton, "_draw")
    textos = _contar(monkeypatch, botao._text_label, "configure")
    botao.configure(text="a", state="normal")  # nada mudou
    assert desenhos == [] and textos == []
    botao.configure(text="b")
    assert textos and botao.cget("text") == "b"
    botao.configure(state="disabled", text="b")  # uma coisa muda: vale o pedido inteiro
    assert desenhos and botao.cget("state") == "disabled"


def test_configure_com_comando_novo_sempre_vale(raiz):
    import customtkinter as ctk

    visto = []
    botao = ctk.CTkButton(raiz, text="a", command=lambda: visto.append(1))
    botao.configure(command=lambda: visto.append(2))  # outra função: tem de ser trocada
    botao.invoke()
    assert visto == [2]


def test_configure_do_rotulo_e_da_caixa_de_marcar_tambem_economiza(raiz, monkeypatch):
    import customtkinter as ctk

    rotulo = ctk.CTkLabel(raiz, text="x", text_color="gray60")
    internos = _contar(monkeypatch, rotulo._label, "configure")
    rotulo.configure(text="x", text_color="gray60")
    assert internos == []
    rotulo.configure(text="y")
    assert internos and rotulo.cget("text") == "y"
    caixa = ctk.CTkCheckBox(raiz, text="c")
    desenhos = _contar(monkeypatch, ctk.CTkCheckBox, "_draw")
    caixa.configure(state="normal", text="c")
    assert desenhos == []
    caixa.configure(state="disabled")
    assert desenhos


def test_opcao_que_o_widget_nao_sabe_ler_nao_e_engolida(raiz):
    import customtkinter as ctk

    botao = ctk.CTkButton(raiz, text="a")
    assert estabilidade.ja_esta_assim(botao, {"text": "a"}) is True
    assert estabilidade.ja_esta_assim(botao, {"text": "b"}) is False
    assert estabilidade.ja_esta_assim(botao, {"opcao_que_nao_existe": 1}) is False


def test_instalar_e_idempotente(raiz):
    import customtkinter as ctk

    estabilidade.instalar()
    estabilidade.instalar()
    funcao = ctk.CTkButton.configure
    assert getattr(funcao, "_configure_economico", False)
    assert not getattr(getattr(funcao, "__wrapped__", None), "_configure_economico", False)


# --- as dicas ----------------------------------------------------------------------------------


def test_a_dica_aparece_e_some(raiz):
    import customtkinter as ctk

    botao = ctk.CTkButton(raiz, text="Salvar")
    botao.pack()
    d = dicas.dica(botao, "Grava as   alterações\nno projeto.")
    assert d.texto == "Grava as alterações no projeto." and not d.visivel
    d.mostrar()
    assert d.visivel
    d.mostrar()  # já está à vista: não cria um segundo balão
    d.esconder()
    assert not d.visivel
    d.esconder()  # esconder de novo não quebra


def test_dica_com_texto_que_muda_e_dica_vazia(raiz):
    import customtkinter as ctk

    botao = ctk.CTkButton(raiz, text="x")
    estado = {"ligado": False}
    d = dicas.dica(botao, lambda: "Desliga" if estado["ligado"] else "Liga")
    assert d.texto == "Liga"
    estado["ligado"] = True
    assert d.texto == "Desliga"
    assert dicas.dica(botao, "Outro texto") is d and d.texto == "Outro texto"  # troca, não soma
    d.definir_texto("")
    d.mostrar()
    assert not d.visivel  # sem texto, nada de balão vazio


def test_o_balao_some_quando_o_widget_e_destruido(raiz):
    import customtkinter as ctk

    quadro = ctk.CTkFrame(raiz)
    botao = ctk.CTkButton(quadro, text="x")
    botao.pack()
    d = dicas.dica(botao, "Texto")
    d.mostrar()
    assert d.visivel
    quadro.destroy()
    raiz.update()
    assert not d.visivel


def test_icone_de_ajuda_leva_a_dica(raiz):
    icone = dicas.IconeDeAjuda(raiz, "O que esta etapa faz.")
    assert icone.cget("text") == "?" and icone.dica.texto == "O que esta etapa faz."
    icone.dica.mostrar()
    assert icone.dica.visivel
    icone.dica.esconder()
