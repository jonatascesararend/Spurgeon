"""A aba "Shorts livre" do Studio, a etiqueta de origem dos cortes e a prévia com legenda."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from spurgeon.shorts.dados import ORIGEM_AUTOMATICA, ORIGEM_MANUAL, Avaliacao
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.shorts.livre import ContextoDaIA, Trecho
from spurgeon.shorts.llm import MetadadosLLM
from spurgeon.transcricao.formato import Transcricao
from spurgeon.ui import shorts_livre
from spurgeon.ui.aba_studio import PAINEL_CORTES, PAINEL_LIVRE, AbaStudioShorts
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.shorts_livre import BUSCA_PALAVRAS, PainelShortsLivre
from spurgeon.ui.studio_shorts import EditorDeCortes, rotulo_da_origem
from tests.test_studio_shorts import caixas, pastas, raiz  # as fixtures do editor

pytestmark = pytest.mark.gui

__all__ = ["caixas", "pastas", "raiz"]


class ClienteDeMentira:
    def __init__(self):
        self.descarregado = False

    def avaliar(self, antes, trecho, depois, duracao):
        return Avaliacao("conclusao", 9, True, "", "Fecha a ideia com força.")

    def metadados(self, sermao, texto, categoria, duracao, titulos_usados=(), temperatura=0.1):
        return MetadadosLLM(
            titulo="Um título feito à mão",
            descricao="Resumo do trecho escolhido.",
            hashtags=["#shorts", "#graca"],
            slug="titulo-a-mao",
        )

    def descarregar(self):
        self.descarregado = True


@pytest.fixture
def ambiente(raiz):
    """Uma fila de "entrega na interface" e o `Tarefas` que a usa (como a janela principal)."""
    fila: list = []
    tarefas = Tarefas(fila.append)

    def esperar(condicao, segundos: float = 20.0) -> None:
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            raiz.update()
            while fila:
                fila.pop(0)()
            if condicao():
                return
            time.sleep(0.01)
        raise AssertionError("a condição não ficou verdadeira a tempo")

    return SimpleNamespace(tarefas=tarefas, esperar=esperar)


@pytest.fixture
def painel(raiz, pastas, ambiente):
    criados: list = []
    cortes = carregar_cortes(pastas.cortes_shorts)
    quadro = shorts_livre.ctk.CTkToplevel(raiz)
    quadro.geometry("1000x600")
    quadro.withdraw()
    p = PainelShortsLivre(
        quadro,
        pastas,
        tarefas=ambiente.tarefas,
        contexto_da_ia=lambda: ContextoDaIA("modelo-de-teste", 2, False),
        cortes_existentes=lambda: cortes,
        ao_criar=criados.append,
    )
    p.pack(fill="both", expand=True)
    p.criados = criados
    yield p
    quadro.destroy()


def palavra(painel, k: int) -> str:
    texto = painel._caixa._textbox
    return texto.get(painel._indice_da_palavra(k), painel._indice_da_palavra(k, fim=True))


# --- o texto e a marcação --------------------------------------------------------------------


def test_o_texto_inteiro_da_transcricao_aparece_com_a_hora_de_cada_frase(painel, pastas):
    esperado = Transcricao.carregar(pastas.transcricao).palavras
    assert len(painel._palavras) == len(esperado)
    texto = painel._caixa._textbox.get("1.0", "end")
    assert texto.count("\n") >= len(painel._frases)
    assert texto.startswith("0:00  ")
    for k in (0, 17, 133, len(esperado) - 1):
        assert palavra(painel, k) == esperado[k].texto


def test_a_palavra_sob_um_ponto_do_texto_e_a_certa(painel):
    for k in (0, 5, 40, 200):
        linha, de, ate = painel._posicoes[k]
        assert painel._palavra_no_indice(f"{linha}.{de}") == k
        assert painel._palavra_no_indice(f"{linha}.{(de + ate) // 2}") == k
    assert painel._palavra_no_indice("1.0") == 0  # em cima da hora, vale a primeira palavra


def test_marcar_comeco_e_fim_por_clique_mostra_a_duracao_e_libera_o_botao(painel):
    assert str(painel._botao_criar.cget("state")) == "disabled"
    assert "começo e o fim" in painel._rotulo_trecho.cget("text")
    painel._clique = 14
    painel._marcar_inicio()
    assert "o fim" in painel._rotulo_trecho.cget("text")
    painel._clique = 120
    painel._marcar_fim()
    assert painel.trecho() == Trecho(14, 120)
    assert "s ·" in painel._rotulo_trecho.cget(
        "text"
    ) and "107 palavras" in painel._rotulo_trecho.cget("text")
    assert str(painel._botao_criar.cget("state")) == "normal"
    assert painel._caixa._textbox.tag_ranges("escolhido")
    assert painel._palavras[14].texto in painel._rotulo_pontas.cget("text")


def test_trecho_curto_demais_avisa_e_nao_deixa_criar(painel):
    painel.definir_trecho(14, 20)
    assert "pelo menos" in painel._rotulo_trecho.cget("text")
    assert str(painel._botao_criar.cget("state")) == "disabled"


def test_arrastar_o_mouse_sobre_as_palavras_escolhe_o_trecho(painel):
    texto = painel._caixa._textbox
    texto.tag_add("sel", painel._indice_da_palavra(14), painel._indice_da_palavra(100, fim=True))
    painel._soltou_o_mouse(SimpleNamespace(x=1, y=1))
    assert painel.trecho() == Trecho(14, 100)
    assert not texto.tag_ranges("sel")  # a seleção do Tk some; o azul do trecho fica


def test_um_duplo_clique_numa_palavra_so_vale_como_clique(painel):
    texto = painel._caixa._textbox
    texto.tag_add("sel", painel._indice_da_palavra(30), painel._indice_da_palavra(30, fim=True))
    painel._soltou_o_mouse(SimpleNamespace(x=1, y=1))
    assert painel.trecho() is None and painel._clique == 30


def test_marcar_o_fim_antes_do_comeco_pede_o_comeco_de_novo(painel):
    painel._clique = 100
    painel._marcar_inicio()
    painel._clique = 50
    painel._marcar_fim()
    assert painel._primeira is None and painel._ultima == 50


def test_os_botoes_de_ajuste_andam_com_o_comeco_e_o_fim(painel):
    painel.definir_trecho(24, 120)
    painel._mover("inicio", -1, False)
    painel._mover("fim", 1, False)
    assert painel.trecho() == Trecho(23, 121)
    painel._mover("inicio", -1, True)  # +frase no começo: a frase anterior inteira
    assert painel.trecho().primeira == 12
    painel.limpar_trecho()
    assert painel.trecho() is None


# --- busca ------------------------------------------------------------------------------------


def test_buscar_palavra_destaca_a_frase_e_leva_o_trecho_ao_achado(painel):
    alvo = painel._palavras[40].texto.strip(".?!")
    painel._var_busca.set(alvo.upper())  # maiúsculas: a busca não liga para isso
    painel.buscar()
    assert painel._achados and painel._achado is painel._achados[0]
    assert painel._caixa._textbox.tag_ranges("achado")
    assert painel._caixa._textbox.tag_ranges("palavra_achada")
    assert str(painel._botao_usar_achado.cget("state")) == "normal"
    painel._usar_o_achado()
    achado = painel._achado
    assert painel.trecho() == Trecho(achado.primeira, achado.ultima)


def test_buscar_o_que_nao_existe_diz_que_nao_achou(painel):
    painel._var_busca.set("zzzzzzzzqx")
    painel.buscar()
    assert painel._achados == []
    assert "Nada encontrado" in painel._situacao_busca.cget("text")


def test_a_busca_por_tema_usa_o_modelo_de_vetores(painel, ambiente, monkeypatch):
    chamadas: list[str] = []

    def procurar_de_mentira(palavras, frases, consulta, nlp):
        chamadas.append(consulta)
        from spurgeon.shorts.busca import Achado

        f = frases[3]
        return [Achado(f.primeira, f.ultima, f.inicio, f.fim, 66.0, f.texto)]

    monkeypatch.setattr(shorts_livre, "carregar_modelo_de_vetores", lambda: object())
    monkeypatch.setattr(shorts_livre, "procurar_tema", procurar_de_mentira)
    painel._modo.set("Tema")
    painel._var_busca.set("perdão")
    painel.buscar()
    ambiente.esperar(lambda: bool(painel._achados))
    assert chamadas == ["perdão"] and painel._achado.pontos == 66.0
    assert painel._modo.get() != BUSCA_PALAVRAS


# --- criar o Short -----------------------------------------------------------------------------


def test_criar_faz_o_corte_manual_e_entrega_para_a_lista(painel, ambiente, monkeypatch):
    cliente = ClienteDeMentira()
    monkeypatch.setattr(shorts_livre, "preparar_cliente", lambda *a, **k: cliente)
    painel.definir_trecho(14, 130)
    painel.criar()
    assert painel._criando and str(painel._botao_criar.cget("state")) == "disabled"
    ambiente.esperar(lambda: bool(painel.criados))
    corte = painel.criados[0]
    assert corte.origem == ORIGEM_MANUAL and corte.id >= 1_000_000
    assert corte.titulo == "Um título feito à mão" and corte.categoria == "conclusao"
    assert corte.texto.split()[0] == painel._palavras[14].texto
    assert cliente.descarregado  # a memória da IA volta para a renderização
    assert not painel._criando and painel.trecho() is None
    assert "Short criado" in painel._situacao.cget("text")


def test_se_a_ia_falha_a_pessoa_e_avisada_e_pode_tentar_de_novo(
    painel, ambiente, caixas, monkeypatch
):
    from spurgeon.core.erros import ErroFerramenta

    def sem_ollama(*_a, **_k):
        raise ErroFerramenta("O Ollama não está respondendo.")

    monkeypatch.setattr(shorts_livre, "preparar_cliente", sem_ollama)
    painel.definir_trecho(14, 130)
    painel.criar()
    ambiente.esperar(lambda: not painel._criando)
    assert painel.criados == []
    assert any("Ollama" in m for _t, m in caixas)
    assert painel.trecho() == Trecho(14, 130)  # o trecho continua escolhido
    assert str(painel._botao_criar.cget("state")) == "normal"


def test_sem_opcoes_completas_nao_chama_a_ia(raiz, pastas, ambiente, caixas):
    quadro = shorts_livre.ctk.CTkToplevel(raiz)
    quadro.withdraw()
    p = PainelShortsLivre(
        quadro, pastas, tarefas=ambiente.tarefas, contexto_da_ia=lambda: None,
        cortes_existentes=list, ao_criar=lambda c: None,
    )  # fmt: skip
    p.definir_trecho(14, 130)
    p.criar()
    assert not p._criando and any("opções" in m for _t, m in caixas)
    quadro.destroy()


# --- a aba do Studio ---------------------------------------------------------------------------


@pytest.fixture
def aba(raiz, pastas, ambiente, tmp_path):
    from spurgeon.core.config import Pastas

    quadro = shorts_livre.ctk.CTkToplevel(raiz)
    quadro.geometry("1100x700")
    quadro.withdraw()
    a = AbaStudioShorts(
        quadro,
        ao_renderizar=lambda: None,
        pastas_do_app=Pastas(raiz=tmp_path),
        legenda_queimada=lambda: False,
        tarefas=ambiente.tarefas,
        contexto_da_ia=lambda: ContextoDaIA("modelo-de-teste", 2, False),
    )
    a.pack(fill="both", expand=True)
    a.mostrar_projeto(pastas)
    yield a
    quadro.destroy()


def test_a_aba_tem_o_seletor_e_o_shorts_livre(aba):
    assert aba._seletor.get() == PAINEL_CORTES
    assert aba._livre is not None and aba._editor is not None
    aba.mostrar_painel(PAINEL_LIVRE)
    assert aba._seletor.get() == PAINEL_LIVRE


def test_sem_projeto_o_seletor_some(aba):
    aba.mostrar_projeto(None)
    assert not aba._seletor.winfo_manager() and aba._livre is None


def test_o_corte_livre_entra_na_lista_marcado_gravado_e_aberto(aba, pastas, ambiente, monkeypatch):
    antes = len(aba.editor.cortes)
    monkeypatch.setattr(shorts_livre, "preparar_cliente", lambda *a, **k: ClienteDeMentira())
    aba.mostrar_painel(PAINEL_LIVRE)
    aba._livre.definir_trecho(14, 130)
    aba._livre.criar()
    ambiente.esperar(lambda: len(aba.editor.cortes) > antes)
    assert len(aba.editor.cortes) == antes + 1
    novo = aba.editor.cortes[-1]
    assert novo.origem == ORIGEM_MANUAL and novo.incluir
    gravados = carregar_cortes(pastas.cortes_shorts)
    assert [c.id for c in gravados if c.origem == ORIGEM_MANUAL] == [novo.id]  # já está salvo
    assert aba._seletor.get() == PAINEL_CORTES  # a pessoa é levada à lista
    assert aba.editor._atual == len(aba.editor.cortes) - 1
    assert "gerado manualmente" in aba.editor._rotulo_categoria.cget("text")


# --- etiqueta de origem e prévia com legenda ---------------------------------------------------


def test_cada_corte_diz_se_foi_gerado_automaticamente_ou_manualmente(raiz, pastas):
    editor = EditorDeCortes(raiz, pastas)
    try:
        cortes = editor.cortes
        assert all(c.origem == ORIGEM_AUTOMATICA for c in cortes)
        assert rotulo_da_origem(cortes[0]) == "gerado automaticamente"
        assert "gerado automaticamente" in editor._rotulo_categoria.cget("text")
        cortes[0].origem = ORIGEM_MANUAL
        assert rotulo_da_origem(cortes[0]) == "gerado manualmente"
        editor.selecionar(0)
        assert "gerado manualmente" in editor._rotulo_categoria.cget("text")
        textos = [
            w.cget("text")
            for linha in editor._linhas.values()
            for w in linha.winfo_children()
            if hasattr(w, "cget") and "gerado" in str(w.cget("text"))
        ]
        assert len(textos) == len(cortes)
    finally:
        editor.destroy()


def test_a_previa_do_corte_sempre_leva_a_legenda(raiz, pastas, ambiente, monkeypatch, tmp_path):
    from spurgeon.core.config import OpcoesProcessamento
    from spurgeon.render import previa
    from spurgeon.ui.studio_shorts import OrigemDaPrevia

    pedidos: list[dict] = []

    def previa_de_mentira(video, info, corte, palavras, destino, **kwargs):
        pedidos.append(kwargs)
        return previa.PreviaDoShort(destino, None, corte.duracao)

    monkeypatch.setattr(previa, "previa_do_corte", previa_de_mentira)
    origem = OrigemDaPrevia(tmp_path / "v.mp4", SimpleNamespace(), OpcoesProcessamento())
    editor = EditorDeCortes(
        raiz, pastas, tarefas=ambiente.tarefas, origem_da_previa=lambda: origem,
        legenda_queimada=lambda: False,
    )  # fmt: skip
    # o Tk do player precisa de um ambiente de verdade: só o pedido interessa aqui
    monkeypatch.setattr(editor._player, "carregar", lambda *_a, **_k: None)
    try:
        editor.selecionar(0)
        editor.gerar_previa()
        ambiente.esperar(lambda: bool(pedidos))
        assert pedidos[0]["com_legenda"] is True  # mesmo com as "Legendas dinâmicas" desligadas
        assert "sempre" in editor._dica_previa.cget("text")
    finally:
        editor.destroy()
