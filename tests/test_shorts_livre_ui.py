"""A aba "Shorts livre" do Studio, a etiqueta de origem dos cortes e a prévia com legenda."""

from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from spurgeon.shorts.analise import separar_frases
from spurgeon.shorts.dados import (
    ID_BASE_DOS_MANUAIS,
    ORIGEM_AUTOMATICA,
    ORIGEM_MANUAL,
    Avaliacao,
    cor_da_categoria,
    rotulo_da_categoria,
)
from spurgeon.shorts.edicao import indices_incluidos
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.shorts.livre import ContextoDaIA, Trecho
from spurgeon.shorts.llm import MetadadosLLM
from spurgeon.shorts.titulos import Escolha
from spurgeon.transcricao.formato import Transcricao
from spurgeon.ui import shorts_livre
from spurgeon.ui.aba_studio import PAINEL_CORTES, PAINEL_LIVRE, AbaStudioShorts
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.legenda_de_categorias import CHAVE_FEITOS
from spurgeon.ui.pincel import FERRAMENTA_BORRACHA, FERRAMENTA_MARCA, FERRAMENTA_SETA
from spurgeon.ui.shorts_livre import BUSCA_PALAVRAS, PainelShortsLivre
from spurgeon.ui.studio_shorts import EditorDeCortes, rotulo_da_origem
from tests.test_marcas_do_sermao import _gravar_analise
from tests.test_studio_ferramentas import _sem_dica
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

    def melhorar_titulo(self, titulo, *_args, **_kwargs):
        return Escolha(titulo, None, 0)  # o título de mentira já serve

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
    assert texto.startswith("   0:00  ")  # a margem das marcas (3 espaços) e depois a hora
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
        pastas_do_app=Pastas(recursos=tmp_path / "recursos", projetos=tmp_path / "Projetos"),
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


# --- o texto "vivo": categorias detectadas e Shorts já feitos -----------------------------------


def _frase_do_corte(painel, corte):
    """O número da linha (1 a N) de uma frase que cai dentro do corte."""
    k = indices_incluidos(painel._palavras, corte)[30]
    return painel._posicoes[k][0]


@pytest.fixture
def painel_vivo(raiz, pastas, ambiente):
    """O painel com a análise da IA gravada no projeto e a lista de Shorts que existe."""
    palavras = Transcricao.carregar(pastas.transcricao).palavras
    frases = separar_frases(palavras)
    _gravar_analise(
        pastas,
        palavras,
        frases,
        [("exortacao", 8), ("profecia", 6), ("oracao", 9), ("promessa", 7)],
    )
    cortes = carregar_cortes(pastas.cortes_shorts)
    quadro = shorts_livre.ctk.CTkToplevel(raiz)
    quadro.geometry("1000x600")
    quadro.withdraw()
    criados: list = []

    def ao_criar(corte):
        criados.append(corte)
        cortes.append(corte)

    p = PainelShortsLivre(
        quadro, pastas, tarefas=ambiente.tarefas,
        contexto_da_ia=lambda: ContextoDaIA("modelo-de-teste", 2, False),
        cortes_existentes=lambda: cortes, ao_criar=ao_criar,
    )  # fmt: skip
    p.pack(fill="both", expand=True)
    p.criados, p.cortes = criados, cortes
    yield p
    quadro.destroy()


def _margem(painel, linha: int) -> str:
    return painel._caixa._textbox.get(f"{linha}.0", f"{linha}.2")


def test_cada_frase_detectada_ganha_a_barra_da_cor_da_categoria(painel_vivo):
    p = painel_vivo
    assert p._detectados and p._categoria_da_frase
    texto = p._caixa._textbox
    for posicao, detectado in p._categoria_da_frase.items():
        linha = posicao + 1
        assert _margem(p, linha)[0] == "▌"
        assert f"cat_{detectado.categoria}" in texto.tag_names(f"{linha}.0")
        cor = texto.tag_cget(f"cat_{detectado.categoria}", "foreground")
        assert cor == cor_da_categoria(detectado.categoria)
    sem_marca = next(n for n in range(1, len(p._frases) + 1) if n - 1 not in p._categoria_da_frase)
    assert _margem(p, sem_marca)[0] == " "


def test_a_margem_nao_desloca_as_palavras_nem_a_hora(painel_vivo):
    p = painel_vivo
    for k in (0, 17, 133):
        assert palavra(p, k) == p._palavras[k].texto  # as posições valem depois de marcar
    primeira_linha = p._caixa._textbox.get("1.0", "1.end")
    assert primeira_linha[2:].strip().startswith("0:00")  # a margem de 3 caracteres e a hora


def test_os_shorts_que_ja_existem_aparecem_com_losango_e_sublinhado(painel_vivo):
    p = painel_vivo
    corte = p.cortes[0]
    linha = _frase_do_corte(p, corte)
    assert _margem(p, linha)[1] == "◆"
    texto = p._caixa._textbox
    assert f"feito_{corte.categoria}" in texto.tag_names(f"{linha}.1")
    assert texto.tag_ranges(f"sub_{corte.categoria}")
    fora = next(n for n in range(1, len(p._frases) + 1) if not p._shorts_da_frase(p._frases[n - 1]))
    assert _margem(p, fora)[1] == " "


def test_a_legenda_tem_um_chip_por_categoria_detectada_e_um_dos_shorts_feitos(painel_vivo):
    p = painel_vivo
    categorias = {d.categoria for d in p._categoria_da_frase.values()}
    assert {c for c in p._chips if c != CHAVE_FEITOS} == categorias
    assert CHAVE_FEITOS in p._chips and "Já virou Short" in p._chips[CHAVE_FEITOS].cget("text")
    for chave in categorias:
        chip = p._chips[chave]
        assert chip.cget("fg_color") == cor_da_categoria(chave)
        assert rotulo_da_categoria(chave) in chip.cget("text")
    assert p._legenda.winfo_manager() == "grid"


def test_clicar_na_legenda_leva_ao_proximo_trecho_da_categoria_e_da_a_volta(painel_vivo):
    p = painel_vivo
    chave = next(iter(p._grupos))
    linhas = p._grupos[chave]
    assert [p.ir_para(chave) for _ in range(len(linhas) + 1)] == [*linhas, linhas[0]]
    assert p.ir_para("categoria_que_nao_existe") is None
    assert p._info.cget("text").startswith(f"Frase {linhas[0]} ·")


def test_o_texto_da_frase_diz_a_categoria_a_nota_e_os_shorts_dela(painel_vivo):
    p = painel_vivo
    posicao, detectado = next(iter(p._categoria_da_frase.items()))
    texto = p.descrever_linha(posicao + 1)
    assert rotulo_da_categoria(detectado.categoria) in texto
    assert f"nota {detectado.nota} de 10" in texto
    corte = p.cortes[0]
    com_short = p.descrever_linha(_frase_do_corte(p, corte))
    assert "já é parte de um Short" in com_short and corte.titulo in com_short
    assert p.descrever_linha(0) == "" and p.descrever_linha(10**6) == ""


def test_sem_analise_o_texto_fica_limpo_mas_os_shorts_que_existem_aparecem(painel):
    assert painel._detectados == [] and painel._categoria_da_frase == {}
    assert _margem(painel, 1)[0] == " "
    assert set(painel._chips) == {CHAVE_FEITOS}


def test_sem_nada_a_mostrar_a_legenda_some(raiz, pastas, ambiente):
    quadro = shorts_livre.ctk.CTkToplevel(raiz)
    quadro.withdraw()
    p = PainelShortsLivre(
        quadro, pastas, tarefas=ambiente.tarefas, contexto_da_ia=lambda: None,
        cortes_existentes=list, ao_criar=lambda c: None,
    )  # fmt: skip
    assert p._chips == {} and p._legenda.winfo_manager() == ""
    quadro.destroy()


def test_atualizar_marcas_pega_um_short_novo_e_nao_mexe_na_escolha(painel_vivo):
    p = painel_vivo
    p.definir_trecho(200, 280)
    antes = len(p._feitos)
    novo = replace(p.cortes[0], id=ID_BASE_DOS_MANUAIS, titulo="Novo", categoria="esperanca")
    p.cortes.append(novo)
    p.atualizar_marcas()
    assert len(p._feitos) == antes + 1
    assert p.trecho() == Trecho(200, 280)
    assert p._caixa._textbox.tag_ranges("escolhido")  # o azul continua no lugar


# --- as ferramentas: seta, marca-texto e borracha ------------------------------------------------


def _palavras_da_frase(painel, numero: int) -> list[int]:
    frase = painel._frases[numero]
    return list(range(frase.primeira, frase.ultima + 1))


def test_a_ferramenta_comeca_na_seta_e_a_troca_chega_ao_pincel(painel):
    assert painel._ferramentas.ferramenta == FERRAMENTA_SETA
    assert painel._pincel.ferramenta == FERRAMENTA_SETA
    painel._ferramentas.definir(FERRAMENTA_MARCA)
    assert painel._pincel.ferramenta == FERRAMENTA_MARCA
    assert "pintá-las" in painel._rotulo_ferramenta.cget("text")
    painel._ferramentas.definir(FERRAMENTA_BORRACHA)
    assert painel._pincel.ferramenta == FERRAMENTA_BORRACHA
    assert "tirá-las" in painel._rotulo_ferramenta.cget("text")


def test_pintar_a_frase_tres_e_a_cinco_monta_um_corte_de_dois_pedacos(painel):
    painel._pintou(_palavras_da_frase(painel, 3), FERRAMENTA_MARCA)
    painel._pintou(_palavras_da_frase(painel, 5), FERRAMENTA_MARCA)
    trecho = painel.trecho()
    assert trecho.pedacos == [
        (painel._frases[3].primeira, painel._frases[3].ultima),
        (painel._frases[5].primeira, painel._frases[5].ultima),
    ]
    assert trecho.fora == frozenset(_palavras_da_frase(painel, 4))
    texto = painel._caixa._textbox
    assert len(texto.tag_ranges("escolhido")) == 4  # 2 pedaços, começo e fim de cada
    assert texto.tag_ranges("pulado")
    assert "2 pedaços" in painel._rotulo_trecho.cget("text")
    assert "Pula 12 palavras" in painel._rotulo_pontas.cget("text")


def test_a_borracha_tira_palavras_do_meio_e_o_marca_texto_as_devolve(painel):
    painel.definir_trecho(14, 130)
    tiradas = list(range(40, 52))
    painel._pintou(tiradas, FERRAMENTA_BORRACHA)
    assert painel.trecho() == Trecho(14, 130, frozenset(tiradas))
    assert "pedaços" in painel._rotulo_trecho.cget("text")
    painel._pintou(tiradas, FERRAMENTA_MARCA)
    assert painel.trecho() == Trecho(14, 130)
    assert "pedaços" not in painel._rotulo_trecho.cget("text")
    painel._pintou([14, 15], FERRAMENTA_BORRACHA)  # tirar da ponta só encurta o corte
    assert painel.trecho() == Trecho(16, 130)


def test_a_borracha_em_tudo_limpa_o_trecho(painel):
    painel.definir_trecho(14, 30)
    painel._pintou(list(range(14, 31)), FERRAMENTA_BORRACHA)
    assert painel.trecho() is None and painel._primeira is None
    assert str(painel._botao_criar.cget("state")) == "disabled"


def test_o_marca_texto_com_so_uma_ponta_marcada_a_inclui(painel):
    painel._clique = 50
    painel._marcar_inicio()
    painel._pintou(list(range(80, 120)), FERRAMENTA_MARCA)
    assert painel.trecho() == Trecho(50, 119, frozenset(range(51, 80)))


def test_a_seta_nao_pinta_e_um_painel_bloqueado_tambem_nao(painel):
    painel._pintou([20, 21, 22], FERRAMENTA_SETA)
    assert painel.trecho() is None
    painel.bloquear("Processando…")
    painel._pintou(list(range(20, 60)), FERRAMENTA_MARCA)
    assert painel.trecho() is None
    painel.bloquear(None)


def test_o_comeco_e_o_fim_por_clique_respeitam_o_que_ja_foi_pulado(painel):
    painel._pintou(list(range(10, 40)), FERRAMENTA_MARCA)
    painel._pintou(list(range(60, 100)), FERRAMENTA_MARCA)
    painel._pintou(list(range(70, 75)), FERRAMENTA_BORRACHA)
    buracos = frozenset(range(40, 60)) | frozenset(range(70, 75))
    assert painel.trecho() == Trecho(10, 99, buracos)
    painel._clique = 90
    painel._marcar_fim()  # o fim sobe para a palavra 90: os buracos de antes continuam
    assert painel.trecho() == Trecho(10, 90, buracos)
    painel._clique = 65
    painel._marcar_inicio()  # o começo passa do primeiro buraco: ele deixa de existir
    assert painel.trecho() == Trecho(65, 90, frozenset(range(70, 75)))


def test_os_botoes_de_palavra_pulam_o_que_esta_de_fora(painel):
    painel.definir_palavras([*range(20, 30), *range(33, 90)])
    painel._mover("fim", -1, False)
    assert painel.trecho().ultima == 88
    painel._mover("inicio", 1, False)
    assert painel.trecho().primeira == 21 and painel.trecho().fora == frozenset({30, 31, 32})


def test_arrastar_o_marca_texto_e_um_elastico_e_entrega_as_palavras_ao_soltar(painel):
    pintadas: list = []
    painel._pincel._ao_pintar = lambda indices, ferramenta: pintadas.append((indices, ferramenta))
    painel._ferramentas.definir(FERRAMENTA_MARCA)
    sob = {
        (1, 0): 20,
        (2, 0): 24,
        (3, 0): 22,
    }  # o mouse "pula" palavras e volta (y=0: a janela está escondida)
    painel._pincel._palavra_em = lambda x, y: sob.get((x, y))
    assert painel._pincel._comecou(SimpleNamespace(x=1, y=0)) == "break"
    assert painel._caixa._textbox.tag_ranges("pintando")
    painel._pincel._arrastou(SimpleNamespace(x=2, y=0))
    assert len(painel._caixa._textbox.tag_ranges("pintando")) == 2 * 5  # 20 a 24, uma a uma
    painel._pincel._arrastou(SimpleNamespace(x=3, y=0))  # voltou: a 23 e a 24 saem da conta
    assert painel._pincel._soltou(SimpleNamespace(x=3, y=0)) == "break"
    assert pintadas == [(list(range(20, 23)), FERRAMENTA_MARCA)]
    assert not painel._caixa._textbox.tag_ranges("pintando")  # a tinta some depois de entregar


def test_o_marca_texto_so_acende_o_que_ainda_nao_esta_no_trecho_e_a_borracha_o_que_esta(painel):
    painel.definir_trecho(14, 130)
    assert painel._pode_pintar(5, FERRAMENTA_MARCA) and not painel._pode_pintar(
        5, FERRAMENTA_BORRACHA
    )
    assert painel._pode_pintar(50, FERRAMENTA_BORRACHA) and not painel._pode_pintar(
        50, FERRAMENTA_MARCA
    )
    painel._pintou(list(range(40, 52)), FERRAMENTA_BORRACHA)  # um pedaço tirado do meio
    assert painel._pode_pintar(45, FERRAMENTA_MARCA)  # pode voltar a entrar
    assert not painel._pode_pintar(45, FERRAMENTA_BORRACHA)  # e não há o que apagar


def test_sem_trecho_a_borracha_nao_acende_nada_e_o_marca_texto_acende_tudo(painel):
    assert painel.trecho() is None
    assert painel._pode_pintar(30, FERRAMENTA_MARCA) and not painel._pode_pintar(
        30, FERRAMENTA_BORRACHA
    )


def test_o_aviso_sob_o_mouse_no_texto_do_shorts_livre(painel):
    texto = painel._caixa._textbox
    painel.definir_trecho(14, 130)
    painel._ferramentas.definir(FERRAMENTA_MARCA)
    painel._pincel._palavra_em = lambda x, y: {(1, 0): 5, (2, 0): 50}.get((x, y))
    painel._pincel._passou(SimpleNamespace(x=1, y=0))  # uma palavra de fora do trecho
    assert texto.tag_ranges("previa_marca")
    painel._pincel._passou(SimpleNamespace(x=2, y=0))  # uma que já está nele
    assert not texto.tag_ranges("previa_marca")
    painel._ferramentas.definir(FERRAMENTA_BORRACHA)
    painel._pincel._passou(SimpleNamespace(x=2, y=0))
    assert texto.tag_ranges("previa_borracha") and not texto.tag_ranges("previa_marca")
    assert str(texto.cget("cursor")) == "xterm"


def test_a_seta_deixa_o_mouse_como_sempre(painel):
    assert painel._pincel._comecou(SimpleNamespace(x=1, y=1)) is None
    assert painel._pincel._soltou(SimpleNamespace(x=1, y=1)) is None


def test_na_margem_ou_na_hora_o_arrasto_nao_acha_palavra(painel, monkeypatch):
    texto = painel._caixa._textbox
    coluna = painel._posicoes[0][1]
    for onde, esperado in (("1.1", None), ("1.5", None), (f"1.{coluna + 2}", 0)):
        monkeypatch.setattr(texto, "index", lambda ref, onde=onde: onde, raising=False)
        assert painel._palavra_sob(1, 1) == esperado, onde


# --- criar um Short de pedaços separados ---------------------------------------------------------


def test_criar_com_pedaco_de_fora_faz_um_corte_com_salto(painel, ambiente, monkeypatch):
    monkeypatch.setattr(shorts_livre, "preparar_cliente", lambda *a, **k: ClienteDeMentira())
    quais = [3, 4, 5, 6, 8, 9, 10, 11]  # 8 frases de ~4 s, e a sétima (a de número 7) fica de fora
    painel.definir_palavras([i for q in quais for i in _palavras_da_frase(painel, q)])
    assert "2 pedaços" in painel._rotulo_trecho.cget("text")
    painel.criar()
    ambiente.esperar(lambda: bool(painel.criados))
    corte = painel.criados[0]
    assert len(corte.saltos) == 1 and corte.origem == ORIGEM_MANUAL
    de, ate = corte.saltos[0]
    assert painel._frases[6].fim <= de < ate <= painel._frases[8].inicio
    assert painel._palavras[painel._frases[7].primeira].texto not in corte.texto.split()


def test_depois_de_criar_o_short_novo_ja_aparece_marcado(painel_vivo, ambiente, monkeypatch):
    p = painel_vivo
    monkeypatch.setattr(shorts_livre, "preparar_cliente", lambda *a, **k: ClienteDeMentira())
    antes = len(p._feitos)
    p.definir_trecho(14, 130)
    p.criar()
    ambiente.esperar(lambda: bool(p.criados))
    assert len(p._feitos) == antes + 1
    assert p.trecho() is None  # a escolha foi limpa
    assert "Um título feito à mão" in p.descrever_linha(p._posicoes[20][0])


# --- as dicas de ferramenta ----------------------------------------------------------------------


def test_todo_controle_do_shorts_livre_tem_dica(painel_vivo):
    p = painel_vivo
    p._var_busca.set(p._palavras[40].texto.strip(".?!"))
    p.buscar()  # os botões dos achados nascem na busca
    faltam = _sem_dica(p)
    assert not faltam, ", ".join(faltam)
    for botao in p._modo._buttons_dict.values():
        assert getattr(botao, "_dica_spurgeon", None) is not None
    for chip in p._chips.values():
        assert getattr(chip, "_dica_spurgeon", None) is not None


# --- passar o mouse no texto não pode mexer na grade (o painel da direita "piscava") ------------


def test_o_texto_da_frase_sob_o_mouse_nao_muda_o_tamanho_de_nada(painel, monkeypatch):
    painel.update_idletasks()
    antes = (painel._corpo.winfo_reqwidth(), painel._corpo.winfo_reqheight())
    altura_do_quadro = painel._quadro_info.winfo_reqheight()
    longo = "Frase 3 · 0:12 · já é parte de Shorts: " + "“Um título bem comprido” " * 20
    for texto in ("", "Frase 3 · 0:12", longo):
        monkeypatch.setattr(painel, "descrever_linha", lambda _n, texto=texto: texto)
        painel._mostrar_a_dica(3)
        painel.update_idletasks()
        assert (painel._corpo.winfo_reqwidth(), painel._corpo.winfo_reqheight()) == antes
        assert painel._quadro_info.winfo_reqheight() == altura_do_quadro  # duas linhas reservadas
    assert len(painel._info.cget("text")) <= 180 and painel._info.cget("text").endswith("…")
    assert painel._quadro_info.winfo_reqwidth() <= 10  # a largura vem da coluna, não do texto


def test_a_cor_da_barra_da_margem_nao_some_dentro_do_trecho_escolhido(painel_vivo):
    p = painel_vivo
    texto = p._caixa._textbox
    assert texto.tag_cget("escolhido", "foreground") == ""  # só o fundo azul, sem cor de letra
    posicao, detectado = next((n, d) for n, d in p._categoria_da_frase.items() if n >= 6)
    primeira = p._frases[posicao].primeira
    p.definir_trecho(primeira - 50, primeira + 80)  # a frase fica no meio do trecho
    assert "escolhido" in texto.tag_names(f"{posicao + 1}.0")  # a margem está dentro do azul
    assert f"cat_{detectado.categoria}" in texto.tag_names(f"{posicao + 1}.0")
    assert texto.tag_cget(f"cat_{detectado.categoria}", "foreground") == cor_da_categoria(
        detectado.categoria
    )
