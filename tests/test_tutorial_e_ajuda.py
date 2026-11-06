"""Dicas de ferramenta nos painéis, o "?" de cada etapa, o passo a passo, o rodapé da janela e a
reabertura do último projeto."""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from spurgeon.core.config import ConfigApp, carregar_config, salvar_config
from spurgeon.ui import ajuda, dicas
from spurgeon.ui import app as app_mod
from spurgeon.ui import tutorial as tutorial_mod
from spurgeon.ui.painel_execucao import EstadoUI
from spurgeon.ui.painel_opcoes import NOMES_DAS_PAGINAS
from spurgeon.ui.secao import SecaoRecolhivel
from spurgeon.ui.tutorial import (
    PASSOS,
    VISTA_LIVRE,
    VISTA_PROCESSAMENTO,
    VISTA_STUDIO,
    Destaque,
    Destino,
    OfertaDoTutorial,
    Passo,
    Tutorial,
    posicao_sem_cobrir,
)
from tests import test_janela as base

janela, tela, projeto_pronto = base.janela, base.tela, base.projeto_pronto  # a janela compartilhada
gui = pytest.mark.gui


# --- as fixtures --------------------------------------------------------------------------------
@pytest.fixture
def gravacoes(tela, monkeypatch):
    """Cada gravação do config.toml que a janela pediu (nada é escrito de verdade)."""
    gravadas: list[ConfigApp] = []
    monkeypatch.setattr(
        app_mod, "salvar_config", lambda config, *_a, **_k: gravadas.append(config.model_copy())
    )
    return gravadas


@pytest.fixture
def passo_a_passo(tela, gravacoes):
    """A janela no estado de quem abriu o programa pela primeira vez, e limpa depois."""
    tela.config_app.tutorial_ao_abrir = True
    tela._var_tutorial.set(True)
    tela.config_app.ultimo_projeto = ""
    tela._mostrar_pagina(0)
    yield tela
    if tela._oferta is not None:
        tela._oferta.escolher(False)
    if tela.tutorial is not None:
        tela.tutorial.pular()
        tela.tutorial = None
    tela._mostrar_pagina(0)
    tela.config_app.ultimo_projeto = ""


def _rodar_a_interface(janela, segundos: float) -> None:
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        janela.update()
        time.sleep(0.02)


def _como_lista(alvo) -> list:
    return list(alvo) if isinstance(alvo, list | tuple) else [alvo]


def _medidas_da_tira(tira) -> dict[str, int]:
    posto = tira.place_info()
    return {chave: int(posto[chave]) for chave in ("x", "y", "width", "height")}


def _dica_de(widget) -> str:
    objeto = getattr(widget, "_dica_spurgeon", None)
    return objeto.texto if objeto is not None else ""


def _com_dica(widgets, descricao: str) -> None:
    sem = [w for w in widgets if not _dica_de(w)]
    assert not sem, f"{descricao}: widgets sem dica de ferramenta: {sem}"


@gui
def test_por_padrao_nada_acontece_sozinho_na_abertura(janela):
    """Os testes (e qualquer `JanelaPrincipal(config)`) não têm oferta nem reabertura."""
    parametros = inspect.signature(app_mod.JanelaPrincipal.__init__).parameters
    assert parametros["oferecer_tutorial"].default is False
    assert parametros["reabrir_ultimo"].default is False
    assert janela.tutorial is None and janela._oferta is None
    assert janela._abertura_agendada is None
    assert janela.config_app.tutorial_ao_abrir is True and janela._var_tutorial.get() is True
    _rodar_a_interface(janela, 1.0)  # bem mais que os 600 ms da abertura de verdade
    assert janela.tutorial is None and janela._oferta is None and janela._pastas is None
    assert janela.config_app.ultimo_projeto == ""


# --- config -------------------------------------------------------------------------------------
def test_config_novos_campos_tem_padrao_e_viajam_no_arquivo(tmp_path):
    config = ConfigApp()
    assert config.ultimo_projeto == "" and config.tutorial_ao_abrir is True
    config.ultimo_projeto = str(tmp_path / "2026-10-04_Sermao")
    config.tutorial_ao_abrir = False
    arquivo = salvar_config(config, tmp_path / "config.toml")
    lido = carregar_config(arquivo)
    assert lido.ultimo_projeto == config.ultimo_projeto and lido.tutorial_ao_abrir is False


def test_config_antigo_sem_os_campos_novos_continua_valendo(tmp_path):
    arquivo = tmp_path / "config.toml"
    arquivo.write_text("versao = 2\n", encoding="utf-8")
    lido = carregar_config(arquivo)
    assert lido.ultimo_projeto == "" and lido.tutorial_ao_abrir is True


# --- os passos: o conteúdo ----------------------------------------------------------------------
def test_os_passos_tem_titulo_texto_e_vista_validos():
    assert len(PASSOS) >= 20
    for passo in PASSOS:
        assert passo.titulo.strip() == passo.titulo and len(passo.titulo) >= 4
        assert passo.texto.strip() == passo.texto and len(passo.texto) >= 80
        assert len(passo.texto) < 700  # o texto cabe na janelinha sem rolar
        assert passo.vista in (VISTA_PROCESSAMENTO, VISTA_STUDIO, VISTA_LIVRE)
        assert passo.alvo
        if passo.pagina is not None:
            assert passo.pagina in range(len(NOMES_DAS_PAGINAS))


def test_os_passos_percorrem_as_etapas_na_ordem_de_uso():
    titulos = [p.titulo for p in PASSOS]
    numeradas = [t for t in titulos if t[:1].isdigit() and t[1:3] == ". "]
    assert [t[0] for t in numeradas] == list("1234567")[: len(numeradas)]
    assert len(numeradas) == 7  # as sete etapas dos cartões
    ordem = [titulos.index(t) for t in numeradas]
    assert ordem == sorted(ordem)
    # depois do processamento vêm o Studio Shorts, o Shorts livre e, por fim, os arquivos
    primeiro_studio = next(i for i, p in enumerate(PASSOS) if p.vista == VISTA_STUDIO)
    assert primeiro_studio > ordem[-1]
    assert [p.vista for p in PASSOS if p.vista == VISTA_LIVRE] == [VISTA_LIVRE]
    assert "arquivos prontos" in titulos[-1]
    juntos = " ".join(p.texto for p in PASSOS).lower()
    for tema in ("marca-texto", "borracha", "desfazer", "refazer com ia", "renderizar"):
        assert tema in juntos
    assert "tutorial" not in juntos  # a pessoa lê "passo a passo", e nenhum nome interno


# --- o contorno (sem depender de uma janela visível) --------------------------------------------
class _Falso:
    """Um widget de mentira com a geometria que o `_medir` pergunta."""

    def __init__(self, rect, *, mestre=None, canvas=None, visivel=True):
        self.rect, self.master, self._parent_canvas, self.visivel = rect, mestre, canvas, visivel

    def winfo_exists(self):
        return 1

    def winfo_viewable(self):
        return int(self.visivel)

    def winfo_rootx(self):
        return self.rect[0]

    def winfo_rooty(self):
        return self.rect[1]

    def winfo_width(self):
        return self.rect[2] - self.rect[0]

    def winfo_height(self):
        return self.rect[3] - self.rect[1]


def test_medir_corta_o_que_a_area_rolavel_esconde_e_diz_quais_lados():
    area = _Falso((0, 100, 200, 300))
    quadro = _Falso((0, 0, 200, 900), canvas=area)  # o conteúdo rolável (maior que a área)
    alvo = _Falso((10, 50, 150, 700), mestre=quadro)  # começa acima e termina abaixo da área
    caixa, cortados = tutorial_mod._medir(alvo)
    assert caixa == (10, 100, 150, 300)
    assert cortados == (False, True, False, True)  # esquerda, topo, direita, base


def test_medir_devolve_none_para_o_que_nao_esta_a_vista():
    assert tutorial_mod._medir(_Falso((0, 0, 10, 10), visivel=False)) is None
    area = _Falso((0, 100, 200, 300))
    quadro = _Falso((0, 0, 200, 900), canvas=area)
    assert (
        tutorial_mod._medir(_Falso((10, 400, 50, 450), mestre=quadro)) is None
    )  # rolado para fora


def test_medir_usa_o_quadro_externo_dos_quadros_rolaveis():
    externo = _Falso((5, 5, 105, 205))
    rolavel = _Falso((0, 0, 100, 5000))
    rolavel._parent_frame = externo
    assert tutorial_mod._medir(rolavel)[0] == (5, 5, 105, 205)


def test_posicao_da_janelinha_foge_do_que_esta_sendo_explicado():
    janela_ = (0, 0, 1300, 700)
    tamanho = (400, 260)
    sem_alvo = posicao_sem_cobrir(janela_, tamanho, None, tela=(1920, 1080))
    assert sem_alvo == (1300 - 400 - 16, 700 - 260 - 16)  # inferior direito
    coluna_esquerda = (20, 100, 540, 600)
    assert posicao_sem_cobrir(janela_, tamanho, coluna_esquerda, tela=(1920, 1080)) == sem_alvo
    canto_direito = (900, 440, 1290, 690)  # onde ficaria: vai para o inferior esquerdo
    esquerda = posicao_sem_cobrir(janela_, tamanho, canto_direito, tela=(1920, 1080))
    assert esquerda == (16, 700 - 260 - 16)
    tudo = (0, 0, 1300, 700)  # nada escapa: escolhe o que cobre menos, e sempre devolve algo
    assert isinstance(posicao_sem_cobrir(janela_, tamanho, tudo), tuple)
    # numa tela pequena o resultado fica à vista
    x, y = posicao_sem_cobrir(janela_, tamanho, None, tela=(1000, 600))
    assert x + 400 <= 1000 and y + 260 <= 600


@gui
def test_o_contorno_pisca_duas_vezes_e_fica_fixo(tela, monkeypatch):
    monkeypatch.setattr(tutorial_mod, "_medir", lambda _a: ((100, 200, 300, 260), (False,) * 4))
    contorno = Destaque(tela, intervalo_ms=60_000)  # o relógio de verdade não atrapalha
    contorno.mostrar(object())
    try:
        assert contorno.ativo and len(contorno.tiras) == 4
        vistos = []
        for _ in range(10):
            vistos.append(contorno.tiras[0].winfo_manager() == "place")
            contorno._passo()
        assert vistos == [True, True, False, False, True, True, False, False, True, True]
        assert contorno.fixo
    finally:
        contorno.limpar()


@gui
def test_o_contorno_envolve_o_alvo_e_deixa_sem_tira_o_lado_cortado(tela, monkeypatch):
    medida = {"caixa": ((100, 200, 300, 260), (False,) * 4)}
    monkeypatch.setattr(tutorial_mod, "_medir", lambda _a: medida["caixa"])
    contorno = Destaque(tela, margem=3, espessura=3, intervalo_ms=60_000)
    contorno.mostrar(object(), piscar=False)
    try:
        cima, baixo, esquerda, direita = contorno.tiras
        x0 = 100 - tela.winfo_rootx() - 3
        y0 = 200 - tela.winfo_rooty() - 3
        info = {
            nome: _medidas_da_tira(tira)
            for nome, tira in (
                ("cima", cima),
                ("baixo", baixo),
                ("esq", esquerda),
                ("dir", direita),
            )
        }
        assert info["cima"] == {"x": x0, "y": y0, "width": 206, "height": 3}
        assert info["baixo"] == {"x": x0, "y": y0 + 66 - 3, "width": 206, "height": 3}
        assert info["esq"] == {"x": x0, "y": y0, "width": 3, "height": 66}
        assert info["dir"] == {"x": x0 + 206 - 3, "y": y0, "width": 3, "height": 66}
        # o alvo rolou: a parte de cima ficou fora da área, então não há tira em cima
        medida["caixa"] = ((100, 200, 300, 260), (False, True, False, False))
        contorno.atualizar()
        assert cima.winfo_manager() == "" and baixo.winfo_manager() == "place"
        # o alvo saiu da tela: nenhuma tira
        medida["caixa"] = None
        contorno.atualizar()
        assert all(t.winfo_manager() == "" for t in contorno.tiras)
    finally:
        contorno.limpar()


@gui
def test_limpar_o_contorno_apaga_as_tiras_e_nao_mexe_em_widgets(tela):
    alvo = tela._botao_tutorial
    antes = (alvo.cget("border_width"), alvo.cget("fg_color"))
    contorno = Destaque(tela, intervalo_ms=60_000)
    contorno.mostrar(alvo)
    tiras = contorno.tiras
    assert contorno.ativo and all(t.winfo_exists() for t in tiras)
    contorno.limpar()
    assert not contorno.ativo and contorno.tiras == []
    assert not any(t.winfo_exists() for t in tiras)
    assert (alvo.cget("border_width"), alvo.cget("fg_color")) == antes
    contorno.mostrar(None)  # sem alvo, não há o que contornar
    assert not contorno.ativo


# --- dicas de ferramenta ------------------------------------------------------------------------
@gui
def test_todo_controle_do_painel_de_opcoes_tem_dica(tela):
    o = tela.opcoes
    _com_dica(o._todos, "painel de opções")
    _com_dica(
        [*o._seletor_modo._buttons_dict.values(), *o._seletor_fade._buttons_dict.values()],
        "seletores de modo e de fade",
    )
    _com_dica([o._botao_avancado], "opções avançadas")
    for rotulo, botao in o._seletor_modo._buttons_dict.items():
        assert len(_dica_de(botao)) > 40, rotulo


@gui
def test_todo_controle_dos_outros_paineis_tem_dica(tela):
    e = tela.entrada
    _com_dica(e._controles, "cartão do vídeo")
    _com_dica([*e.aparar._botoes, *e.aparar._rotulos_quadro.values()], "corte do vídeo")
    _com_dica([*e.zoom._filhos, e.zoom._tela], "zoom do vídeo")
    _com_dica([*e.shorts._filhos, e.shorts._tela, e.shorts._botao_youtube], "zoom dos Shorts")
    t, m = tela.opcoes.tratamento, tela.opcoes.marca
    _com_dica([*t._filhos, t._check_ativo, t._tela], "tratamento de imagem")
    _com_dica([*m._filhos, m._check_ativa, m._previa], "marca d'água")
    x = tela.execucao
    _com_dica(
        [
            *(x.botao_iniciar, x.botao_pausar, x.botao_cancelar, x.botao_pasta, x._log, x._barra),
            *(x.botao_abrir_revisao, x.botao_aprovar, x.botao_fechar_revisao),
        ],
        "painel de execução",
    )
    _com_dica([s.cabecalho for s in e.secoes.values()], "seções recolhíveis")


@gui
def test_botoes_das_paginas_e_das_abas_tem_dica(tela):
    for seletor in (tela._paginas, tela._abas):
        for rotulo, botao in seletor._buttons_dict.items():
            assert len(_dica_de(botao)) > 30, rotulo


@gui
def test_dica_da_aba_studio_muda_quando_ela_e_liberada(tela, projeto_pronto):
    botao = tela._abas._buttons_dict[app_mod.ABA_STUDIO]
    assert "Fica liberada" in _dica_de(botao)
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    assert "Revise os Shorts" in _dica_de(botao)


@gui
def test_dicas_de_pausar_e_iniciar_acompanham_o_estado(tela):
    x = tela.execucao
    assert "Suspende" in _dica_de(x.botao_pausar) and "ponto seguro" in _dica_de(x.botao_pausar)
    x.definir_estado(EstadoUI.PAUSADO)
    assert "Continua" in _dica_de(x.botao_pausar) and "Suspende" not in _dica_de(x.botao_pausar)
    assert "Confere o ambiente" in _dica_de(x.botao_iniciar)
    x.definir_estado(EstadoUI.CANCELADO, tem_projeto=True)
    assert "Continua o processamento" in _dica_de(x.botao_iniciar)
    x.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)


@gui
def test_dica_do_seletor_funciona_onde_o_bind_do_customtkinter_levanta_erro(tela):
    seletor = ctk.CTkSegmentedButton(tela, values=["A", "B", "C"])
    ajuda.dica_do_seletor(seletor, {"A": "Explica o A.", "B": "Explica o B."})
    assert _dica_de(seletor._buttons_dict["A"]) == "Explica o A."
    assert _dica_de(seletor._buttons_dict["B"]) == "Explica o B."
    assert _dica_de(seletor._buttons_dict["C"]) == ""  # sem texto, sem dica
    ajuda.dica_do_seletor(seletor, "Serve para todos.")
    assert _dica_de(seletor._buttons_dict["C"]) == "Serve para todos."
    seletor.destroy()


# --- o "?" de cada etapa ------------------------------------------------------------------------
def _cabecalhos(tela) -> dict[int, ajuda.CabecalhoDeEtapa]:
    o = tela.opcoes
    return {
        1: tela.entrada.cabecalho,
        2: o.cartoes["gerar"].cabecalho,
        3: o.cartoes["ajustes"].cabecalho,
        4: o.tratamento.cabecalho,
        5: o.marca.cabecalho,
        6: o.cartoes["revisao"].cabecalho,
        7: o.cartoes["processamento"].cabecalho,
    }


@gui
def test_cada_etapa_tem_o_interrogacao_com_a_explicacao(tela):
    cabecalhos = _cabecalhos(tela)
    assert sorted(cabecalhos) == [1, 2, 3, 4, 5, 6, 7]
    textos = set()
    for numero, cabecalho in cabecalhos.items():
        assert cabecalho.rotulo.cget("text").startswith(f"{numero}. ")
        icone = cabecalho.icone
        assert isinstance(icone, dicas.IconeDeAjuda) and icone.cget("text") == "?"
        assert len(icone.dica.texto) > 100, numero  # uma explicação de verdade, não um rótulo
        textos.add(icone.dica.texto)
    assert len(textos) == 7  # cada etapa explica a si mesma
    icone = tela.opcoes.icone_do_avancado
    assert icone.cget("text") == "?" and "não mexa" in icone.dica.texto


@gui
def test_o_interrogacao_fica_a_direita_na_mesma_linha_do_titulo(tela):
    for numero, cabecalho in _cabecalhos(tela).items():
        titulo, icone = cabecalho.rotulo.grid_info(), cabecalho.icone.grid_info()
        assert int(titulo["row"]) == int(icone["row"]) == 0, numero
        assert int(titulo["column"]) == 0 and int(icone["column"]) == 1, numero
        assert "e" in icone["sticky"] and cabecalho.grid_columnconfigure(0)["weight"] == 1
    tela._mostrar_pagina(0)
    tela.update_idletasks()
    cabecalho = tela.opcoes.cartoes["gerar"].cabecalho
    assert cabecalho.icone.winfo_x() > cabecalho.rotulo.winfo_x() + cabecalho.rotulo.winfo_width()


@gui
def test_as_secoes_recolhiveis_tem_o_interrogacao_por_cima_do_cabecalho(tela):
    secoes = tela.entrada.secoes
    assert set(secoes) == {"aparar", "zoom", "shorts"}
    for nome, secao in secoes.items():
        assert isinstance(secao.icone, dicas.IconeDeAjuda), nome
        assert len(secao.icone.dica.texto) > 100 and secao.icone.winfo_manager() == "place"
        # fica por cima do cabeçalho sem ser filho dele: clicar na bolinha não recolhe a seção
        assert secao.icone.master is secao
        assert secao.icone.place_info()["in"] is secao.cabecalho
    sem_ajuda = SecaoRecolhivel(tela, "Sem ajuda")
    assert sem_ajuda.icone is None  # sem `ajuda`, não há bolinha
    sem_ajuda.destroy()


# --- o passo a passo ----------------------------------------------------------------------------
@gui
def test_o_passo_a_passo_avanca_volta_e_conclui(passo_a_passo, monkeypatch):
    tela = passo_a_passo
    fechamentos: list[bool] = []
    monkeypatch.setattr(tela, "_tutorial_fechado", lambda concluido: fechamentos.append(concluido))
    tela.abrir_tutorial()
    tut = tela.tutorial
    total = len(PASSOS)
    assert tut.aberto and tut.indice == 0 and isinstance(tut, Tutorial)
    assert tut._rotulo_numero.cget("text") == f"Passo 1 de {total}"
    assert tut._rotulo_titulo.cget("text") == PASSOS[0].titulo
    assert str(tut.botao_anterior.cget("state")) == "disabled"
    assert tut.botao_proximo.cget("text") == "Próximo ▶" and tut.botao_pular.cget("text") == "Pular"
    tut.proximo()
    assert tut.indice == 1 and tut._rotulo_numero.cget("text") == f"Passo 2 de {total}"
    assert str(tut.botao_anterior.cget("state")) == "normal"
    tut.anterior()
    assert tut.indice == 0
    tut.anterior()  # no primeiro passo não volta mais
    assert tut.indice == 0
    tut.ir_ao_passo(total - 1)
    assert tut.botao_proximo.cget("text") == "Concluir"
    assert tut._rotulo_numero.cget("text") == f"Passo {total} de {total}"
    assert fechamentos == []
    tut.proximo()  # no último passo, "Próximo" é "Concluir"
    assert not tut.aberto and fechamentos == [True]
    tut.proximo()  # fechado: não faz nada nem avisa de novo
    assert fechamentos == [True]


@gui
def test_pular_fecha_sem_concluir_e_o_botao_do_rodape_reabre(passo_a_passo, monkeypatch):
    tela = passo_a_passo
    fechamentos: list[bool] = []
    monkeypatch.setattr(tela, "_tutorial_fechado", lambda concluido: fechamentos.append(concluido))
    tela.abrir_tutorial()
    tela.tutorial.proximo()
    tela.tutorial.botao_pular.invoke()
    assert not tela.tutorial.aberto and fechamentos == [False]
    tela._botao_tutorial.invoke()  # "Como usar" abre de novo, do começo
    assert tela.tutorial.aberto and tela.tutorial.indice == 0
    tela.tutorial.pular()


@gui
def test_abrir_o_tutorial_duas_vezes_nao_cria_duas_janelas(passo_a_passo):
    tela = passo_a_passo
    tela.abrir_tutorial()
    primeira = tela.tutorial.tela
    tela.tutorial.proximo()
    tela.abrir_tutorial()
    assert tela.tutorial.tela is primeira and tela.tutorial.indice == 1  # só vem para a frente


@gui
def test_o_destaque_acompanha_o_passo_e_e_apagado_ao_fechar(passo_a_passo):
    tela = passo_a_passo
    tela.abrir_tutorial()
    tut = tela.tutorial
    tut.ir_ao_passo(1)
    assert tut.destaque.ativo
    tiras = tut.destaque.tiras
    assert len(tiras) == 4 and all(t.winfo_exists() for t in tiras)
    tut.ir_ao_passo(2)  # o passo seguinte troca o contorno
    assert not any(t.winfo_exists() for t in tiras)
    assert tut.destaque.ativo and len(tut.destaque.tiras) == 4
    novas = tut.destaque.tiras
    tut.concluir()
    assert not tut.destaque.ativo and not any(t.winfo_exists() for t in novas)


@gui
def test_cada_passo_mostra_a_pagina_certa_e_acha_o_que_explicar(passo_a_passo):
    tela = passo_a_passo
    tela.abrir_tutorial()
    for indice, passo in enumerate(PASSOS):
        tela.tutorial.ir_ao_passo(indice)
        destino = tela._ir_para_passo(passo)
        assert isinstance(destino, Destino)
        assert destino.alvo is not None, passo.titulo
        if passo.vista == VISTA_PROCESSAMENTO:
            assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO
            if passo.pagina is not None:
                assert tela._paginas.get() == NOMES_DAS_PAGINAS[passo.pagina], passo.titulo
        else:  # sem projeto aberto a aba Studio não existe: o passo só mostra onde ela fica
            assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO
            assert "Shorts calculados" in destino.nota
    assert tela.tutorial.indice == len(PASSOS) - 1


@gui
def test_o_passo_a_passo_abre_as_secoes_que_explica_e_as_recolhe_no_fim(passo_a_passo):
    tela = passo_a_passo
    secoes = tela.entrada.secoes
    assert not any(s.aberta for s in secoes.values())
    tela.abrir_tutorial()
    for chave, secao in (("aparar", "aparar"), ("zoom", "zoom"), ("shorts_zoom", "shorts")):
        indice = next(i for i, p in enumerate(PASSOS) if p.alvo == chave)
        tela.tutorial.ir_ao_passo(indice)
        assert secoes[secao].aberta, chave
    tela.tutorial.concluir()
    assert not any(s.aberta for s in secoes.values())  # voltou ao que era


@gui
def test_ao_fechar_a_tela_volta_para_onde_estava(passo_a_passo):
    tela = passo_a_passo
    tela._mostrar_pagina(1)
    tela.abrir_tutorial()
    ultimo = len(PASSOS) - 1  # "Onde ficam os arquivos prontos": página 3
    tela.tutorial.ir_ao_passo(ultimo)
    assert tela._paginas.get() == NOMES_DAS_PAGINAS[2]
    tela.tutorial.pular()
    assert tela._paginas.get() == NOMES_DAS_PAGINAS[1]
    assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO
    assert tela.tutorial is None


@gui
def test_o_passo_que_esta_escondido_pelo_modo_avisa_em_vez_de_travar(passo_a_passo, monkeypatch):
    tela = passo_a_passo
    tela.opcoes.definir_modo("so_vinhetas")  # no modo só vinhetas o áudio some da tela
    monkeypatch.setattr(app_mod, "_a_vista", lambda _alvo: False)
    monkeypatch.setattr(tela, "winfo_viewable", lambda: 1)
    passo = next(p for p in PASSOS if p.alvo == "audio")
    destino = tela._ir_para_passo(passo)
    assert "escondida" in destino.nota and destino.alvo is not None
    tela.opcoes.definir_modo("completo")


@gui
def test_com_um_projeto_aberto_o_passo_a_passo_entra_no_studio(passo_a_passo, projeto_pronto):
    tela = passo_a_passo
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    editor = tela.studio.editor
    assert editor is not None
    tela._mostrar_aba(app_mod.ABA_PROCESSAMENTO)  # a pessoa estava na aba Processamento
    tela.abrir_tutorial()
    por_alvo = {p.alvo: p for p in PASSOS if p.vista != VISTA_PROCESSAMENTO}
    destino = tela._ir_para_passo(por_alvo["studio_lista"])
    assert tela._abas.get() == app_mod.ABA_STUDIO and destino.nota == ""
    assert destino.alvo is editor._lista
    assert editor._janela in _como_lista(tela._ir_para_passo(por_alvo["studio_tabela"]).alvo)
    rodape = _como_lista(tela._ir_para_passo(por_alvo["studio_rodape"]).alvo)
    assert tela.studio._botao_renderizar in rodape and tela.studio._botao_salvar in rodape
    texto = _como_lista(tela._ir_para_passo(por_alvo["studio_texto"]).alvo)
    assert editor._campo_titulo in texto and len(texto) == 3
    tela._ir_para_passo(por_alvo["studio_livre"])
    assert tela.studio._seletor.get() == "Shorts livre"
    tela._ir_para_passo(por_alvo["studio_lista"])
    assert tela.studio._seletor.get() == "Cortes"
    tela.tutorial.pular()
    assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO  # voltou para onde estava


@gui
def test_a_aba_studio_continua_no_studio_se_a_pessoa_estava_nela(passo_a_passo, projeto_pronto):
    tela = passo_a_passo
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    assert tela._abas.get() == app_mod.ABA_STUDIO
    tela.abrir_tutorial()
    tela.tutorial.ir_ao_passo(0)  # o primeiro passo é na aba Processamento
    assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO
    tela.tutorial.pular()
    assert tela._abas.get() == app_mod.ABA_STUDIO


@gui
def test_a_aba_studio_pode_dizer_ela_mesma_o_que_destacar(passo_a_passo, projeto_pronto):
    tela = passo_a_passo
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    escolhido = tela.studio.editor._campo_titulo
    tela.studio.alvos_do_tutorial = lambda: {"studio_lista": escolhido}
    try:
        passo = next(p for p in PASSOS if p.alvo == "studio_lista")
        assert tela._ir_para_passo(passo).alvo is escolhido
        outro = next(p for p in PASSOS if p.alvo == "studio_tabela")
        assert tela.studio.editor._janela in _como_lista(tela._ir_para_passo(outro).alvo)
        tela.studio.alvos_do_tutorial = lambda: 1 / 0  # uma aba com defeito não derruba o passo
        assert tela._ir_para_passo(passo).alvo is tela.studio.editor._lista
    finally:
        del tela.studio.alvos_do_tutorial


@gui
def test_um_passo_com_defeito_nao_trava_o_passo_a_passo(tela):
    def quebra(_passo):
        raise RuntimeError("falha de propósito")

    tut = Tutorial(tela, [Passo("Um", "Texto " * 20), Passo("Dois", "Texto " * 20)], ir_para=quebra)
    try:
        tut.iniciar()
        assert tut.aberto and tut.indice == 0 and not tut.destaque.ativo
        tut.proximo()
        assert tut.indice == 1
    finally:
        tut.fechar()
    assert not tut.aberto


@gui
def test_as_setas_e_o_esc_estao_ligados_ao_passo_a_passo(passo_a_passo):
    tela = passo_a_passo
    tela.abrir_tutorial()
    janela_ = tela.tutorial.tela
    assert all(janela_.bind(tecla) for tecla in ("<Right>", "<Left>", "<Escape>"))
    tela.tutorial.pular()


# --- a oferta da primeira abertura --------------------------------------------------------------
@gui
def test_a_oferta_tem_a_pergunta_os_dois_botoes_e_a_caixinha(passo_a_passo):
    tela = passo_a_passo
    respostas: list[tuple[bool, bool]] = []
    oferta = OfertaDoTutorial(tela, ao_responder=lambda *a: respostas.append(a))
    try:
        textos = []

        def varrer(pai):
            for filho in pai.winfo_children():
                if isinstance(filho, ctk.CTkLabel | ctk.CTkButton | ctk.CTkCheckBox):
                    textos.append(filho.cget("text"))
                varrer(filho)

        varrer(oferta)
        assert "Quer ver o passo a passo para aprender a usar o programa?" in textos
        assert {"Ver o passo a passo", "Agora não", "Não mostrar novamente"} <= set(textos)
        assert oferta.var_nao_mostrar.get() is False  # a caixinha começa desmarcada
    finally:
        oferta.escolher(False)
    assert respostas == [(False, False)]
    oferta.escolher(True)  # responder duas vezes não vale
    assert respostas == [(False, False)]


@gui
@pytest.mark.parametrize(
    ("ver", "caixinha"), [(True, False), (True, True), (False, False), (False, True)]
)
def test_as_respostas_da_oferta(passo_a_passo, gravacoes, ver, caixinha):
    tela = passo_a_passo
    tela._oferecer_tutorial()
    oferta = tela._oferta
    assert oferta is not None
    tela._oferecer_tutorial()  # com uma pergunta aberta, não abre outra
    assert tela._oferta is oferta
    oferta.var_nao_mostrar.set(caixinha)
    (oferta.botao_ver if ver else oferta.botao_agora_nao).invoke()
    assert tela._oferta is None
    assert (tela.tutorial is not None and tela.tutorial.aberto) is ver
    if caixinha:  # "Não mostrar novamente" vale também para "Ver o passo a passo"
        assert tela.config_app.tutorial_ao_abrir is False and tela._var_tutorial.get() is False
        assert [g.tutorial_ao_abrir for g in gravacoes] == [False]
    else:  # sem a caixinha, nada é gravado e a pergunta volta na próxima abertura
        assert tela.config_app.tutorial_ao_abrir is True and tela._var_tutorial.get() is True
        assert gravacoes == []


@gui
def test_fechar_a_oferta_no_x_vale_agora_nao_e_respeita_a_caixinha(passo_a_passo, gravacoes):
    tela = passo_a_passo
    tela._oferecer_tutorial()
    oferta = tela._oferta
    oferta.var_nao_mostrar.set(True)
    tratador = oferta.protocol("WM_DELETE_WINDOW")  # o que o X da janela chama
    assert tratador
    oferta.tk.call(tratador)
    assert tela._oferta is None and tela.tutorial is None
    assert tela.config_app.tutorial_ao_abrir is False
    assert [g.tutorial_ao_abrir for g in gravacoes] == [False]


@gui
def test_fechar_a_oferta_no_x_sem_a_caixinha_nao_grava_nada(passo_a_passo, gravacoes):
    tela = passo_a_passo
    tela._oferecer_tutorial()
    tela._oferta.tk.call(tela._oferta.protocol("WM_DELETE_WINDOW"))
    assert tela._oferta is None and tela.tutorial is None
    assert tela.config_app.tutorial_ao_abrir is True and gravacoes == []


@gui
def test_o_botao_como_usar_responde_a_pergunta_aberta_e_abre_o_passo_a_passo(passo_a_passo):
    tela = passo_a_passo
    tela._oferecer_tutorial()
    tela._botao_tutorial.invoke()
    assert tela._oferta is None and tela.tutorial is not None and tela.tutorial.aberto


@gui
def test_nao_oferece_com_o_passo_a_passo_aberto(passo_a_passo):
    tela = passo_a_passo
    tela.abrir_tutorial()
    tela._oferecer_tutorial()
    assert tela._oferta is None


# --- o rodapé -----------------------------------------------------------------------------------
@gui
def test_o_rodape_tem_o_hardware_a_esquerda_e_os_controles_a_direita(tela):
    rodape = tela._faixa_do_rodape
    assert int(rodape.grid_info()["columnspan"]) == 2  # a linha ocupa a janela toda
    colunas = {
        "rotulo": int(tela._rodape.grid_info()["column"]),
        "atualizacao": int(tela._check_atualizacao.grid_info()["column"]),
        "fechar": int(tela._check_fechar.grid_info()["column"]),
        "caixa": int(tela._check_tutorial.grid_info()["column"]),
        "botao": int(tela._botao_tutorial.grid_info()["column"]),
    }
    assert colunas == {"rotulo": 0, "atualizacao": 1, "fechar": 2, "caixa": 3, "botao": 4}
    assert tela._check_atualizacao.cget("text") == "Procurar atualizações ao abrir"
    assert len(_dica_de(tela._check_atualizacao)) > 40
    assert tela._check_fechar.cget("text") == "Fechar o programa ao terminar"
    assert len(_dica_de(tela._check_fechar)) > 40
    assert "e" in tela._botao_tutorial.grid_info()["sticky"]
    assert tela._check_tutorial.cget("text") == "Mostrar o passo a passo ao abrir"
    assert tela._botao_tutorial.cget("text") == "Como usar"
    assert len(_dica_de(tela._check_tutorial)) > 40 and len(_dica_de(tela._botao_tutorial)) > 40
    # o texto do hardware continua sendo escrito no mesmo rótulo
    perfil = SimpleNamespace(
        cpu_nome="CPU X", threads_logicos=8, ram_total_gb=16.0, gpus=[], cuda_utilizavel=False
    )
    recomendado = SimpleNamespace(whisper_modelo=tela.opcoes._recomendado or "large-v3-turbo")
    tela._aplicar_hardware(perfil, recomendado, False)
    assert "CPU X" in tela._rodape.cget("text")


@gui
def test_a_caixa_do_rodape_grava_a_preferencia_no_config(passo_a_passo, gravacoes):
    tela = passo_a_passo
    assert tela._var_tutorial.get() is True  # acompanha o config
    tela._check_tutorial.toggle()
    assert tela.config_app.tutorial_ao_abrir is False
    assert [g.tutorial_ao_abrir for g in gravacoes] == [False]
    tela._check_tutorial.toggle()
    assert tela.config_app.tutorial_ao_abrir is True
    assert [g.tutorial_ao_abrir for g in gravacoes] == [False, True]


@gui
def test_a_caixa_do_rodape_nao_quebra_se_o_config_nao_puder_ser_gravado(passo_a_passo, monkeypatch):
    tela = passo_a_passo

    def recusa(*_a, **_k):
        raise OSError("disco cheio")

    monkeypatch.setattr(app_mod, "salvar_config", recusa)
    tela._check_tutorial.toggle()
    assert tela.config_app.tutorial_ao_abrir is False  # vale nesta sessão
    assert "Não foi possível salvar a preferência do passo a passo" in tela.execucao._log.get(
        "1.0", "end"
    )


# --- o último projeto ---------------------------------------------------------------------------
@gui
def test_abrir_um_projeto_guarda_a_pasta_como_o_ultimo(passo_a_passo, projeto_pronto, gravacoes):
    tela = passo_a_passo
    raiz = projeto_pronto.pastas.raiz
    tela._abrir_projeto(raiz)
    assert tela.config_app.ultimo_projeto == str(raiz)
    assert gravacoes and gravacoes[-1].ultimo_projeto == str(raiz)
    quantas = len(gravacoes)
    tela._abrir_projeto(raiz)  # o mesmo projeto de novo: não grava à toa
    assert len(gravacoes) == quantas


@gui
def test_uma_pasta_que_nao_e_projeto_nao_vira_o_ultimo(passo_a_passo, tmp_path, gravacoes):
    tela = passo_a_passo
    vazia = tmp_path / "2026-10-04_Sem_estado"
    vazia.mkdir()
    tela._abrir_projeto(vazia)
    assert tela.config_app.ultimo_projeto == "" and gravacoes == []


@gui
def test_iniciar_um_processamento_guarda_o_projeto_como_o_ultimo(
    passo_a_passo, tmp_path, gravacoes
):
    tela = passo_a_passo
    pasta = tmp_path / "Projetos" / "2026-10-04_Novo"
    tela._lembrar_preferencias(tela.config_app.model_copy(deep=True), pasta)
    assert tela.config_app.ultimo_projeto == str(pasta)
    assert gravacoes[-1].ultimo_projeto == str(pasta)
    tela._lembrar_preferencias(tela.config_app.model_copy(deep=True))  # sem pasta: não muda
    assert tela.config_app.ultimo_projeto == str(pasta)


@gui
@pytest.mark.ffmpeg
def test_o_projeto_criado_ao_iniciar_vira_o_ultimo(passo_a_passo, video_teste, monkeypatch):
    tela = passo_a_passo
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.03")
    base.escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão do último projeto")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    base.bombear(tela, lambda: tela._pastas is not None)
    assert tela.config_app.ultimo_projeto == str(tela._pastas.raiz)
    base.bombear(tela, lambda: tela.execucao.estado == EstadoUI.EXECUTANDO and tela._total > 0.1)
    tela._cancelar()
    base.bombear(tela, lambda: tela.execucao.estado == EstadoUI.CANCELADO)


@gui
def test_reabrir_o_ultimo_projeto_preenche_a_tela(passo_a_passo, projeto_pronto, video_falso):
    tela = passo_a_passo
    tela.config_app.ultimo_projeto = str(projeto_pronto.pastas.raiz)
    assert tela._reabrir_ultimo_projeto() is True
    assert tela._pastas is not None and tela._pastas.raiz == projeto_pronto.pastas.raiz
    assert tela.entrada.var_nome.get() == "Sermão Teste"
    assert tela.entrada.var_video.get() == str(video_falso)
    assert tela._abas.get() == app_mod.ABA_STUDIO  # o projeto tem cortes: abre no Studio
    assert tela.caixas.mostradas == []


@gui
def test_o_ultimo_projeto_que_sumiu_e_esquecido_em_silencio(passo_a_passo, tmp_path, gravacoes):
    tela = passo_a_passo
    tela.config_app.ultimo_projeto = str(tmp_path / "Projetos" / "2026-10-04_Apagado")
    assert tela._reabrir_ultimo_projeto() is False
    assert tela.config_app.ultimo_projeto == ""
    assert gravacoes and gravacoes[-1].ultimo_projeto == ""
    assert tela._pastas is None and tela.caixas.mostradas == []  # nenhuma caixa de erro


@gui
def test_o_ultimo_projeto_que_nao_e_mais_um_projeto_tambem_e_esquecido(passo_a_passo, tmp_path):
    tela = passo_a_passo
    pasta = tmp_path / "2026-10-04_Sem_estado"
    pasta.mkdir()  # existe, mas sem o estado.json
    tela.config_app.ultimo_projeto = str(pasta)
    assert tela._reabrir_ultimo_projeto() is False
    assert tela.config_app.ultimo_projeto == "" and tela.caixas.mostradas == []
    estragada = tmp_path / "2026-10-05_Estragado"
    estragada.mkdir()
    (estragada / "estado.json").write_text("{isto não é json", encoding="utf-8")
    tela.config_app.ultimo_projeto = str(estragada)
    assert tela._reabrir_ultimo_projeto() is False
    assert tela.config_app.ultimo_projeto == "" and tela.caixas.mostradas == []


@gui
def test_nao_reabre_se_ha_processamento_em_andamento(passo_a_passo, projeto_pronto, gravacoes):
    tela = passo_a_passo
    tela.config_app.ultimo_projeto = str(projeto_pronto.pastas.raiz)
    tela.execucao.definir_estado(EstadoUI.EXECUTANDO)
    try:
        assert tela._reabrir_ultimo_projeto() is False
        assert tela._pastas is None  # nada foi aberto
        assert tela.config_app.ultimo_projeto == str(projeto_pronto.pastas.raiz)  # e nada se perde
        assert gravacoes == [] and tela.caixas.mostradas == []
    finally:
        tela.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)


@gui
def test_sem_ultimo_projeto_nao_ha_o_que_reabrir(passo_a_passo, gravacoes):
    tela = passo_a_passo
    assert tela._reabrir_ultimo_projeto() is False and gravacoes == []


# --- a abertura do programa ---------------------------------------------------------------------
@gui
def test_agendar_a_abertura_so_agenda_quando_pedido(passo_a_passo):
    tela = passo_a_passo
    assert tela._agendar_abertura(False, False) is None and tela._abertura_agendada is None
    for oferecer, reabrir in ((True, False), (False, True), (True, True)):
        agendado = tela._agendar_abertura(oferecer, reabrir)
        assert agendado is not None and tela._abertura_agendada == agendado
        tela.after_cancel(agendado)
        tela._abertura_agendada = None


@gui
def test_na_abertura_primeiro_reabre_e_so_depois_oferece(passo_a_passo, monkeypatch):
    tela = passo_a_passo
    chamadas: list[str] = []
    monkeypatch.setattr(tela, "_reabrir_ultimo_projeto", lambda: chamadas.append("reabrir"))
    monkeypatch.setattr(tela, "_oferecer_tutorial", lambda: chamadas.append("oferecer"))
    monkeypatch.setattr(app_mod, "_ENTRE_REABRIR_E_OFERTAR_MS", 20)
    tela._ao_abrir_o_programa(oferecer=True, reabrir=True)
    assert chamadas == ["reabrir"]  # a pergunta vem depois, não junto
    base.bombear(tela, lambda: chamadas == ["reabrir", "oferecer"], limite_s=5)


@gui
def test_na_abertura_sem_a_preferencia_nao_oferece(passo_a_passo, monkeypatch):
    tela = passo_a_passo
    chamadas: list[str] = []
    monkeypatch.setattr(tela, "_reabrir_ultimo_projeto", lambda: chamadas.append("reabrir"))
    monkeypatch.setattr(tela, "_oferecer_tutorial", lambda: chamadas.append("oferecer"))
    tela.config_app.tutorial_ao_abrir = False  # a pessoa marcou "Não mostrar novamente"
    tela._ao_abrir_o_programa(oferecer=True, reabrir=True)
    _rodar_a_interface(tela, 0.6)
    assert chamadas == ["reabrir"]
    tela._ao_abrir_o_programa(oferecer=False, reabrir=False)
    assert chamadas == ["reabrir"]  # sem pedido, nem reabre


@gui
def test_na_abertura_de_verdade_reabre_o_projeto_e_so_depois_a_pergunta(
    passo_a_passo, projeto_pronto, monkeypatch
):
    tela = passo_a_passo
    tela.config_app.ultimo_projeto = str(projeto_pronto.pastas.raiz)
    monkeypatch.setattr(app_mod, "_ENTRE_REABRIR_E_OFERTAR_MS", 20)
    tela._ao_abrir_o_programa(oferecer=True, reabrir=True)
    assert tela._pastas is not None and tela._pastas.raiz == projeto_pronto.pastas.raiz
    assert tela._oferta is None  # a pergunta ainda não apareceu por cima do projeto abrindo
    base.bombear(tela, lambda: tela._oferta is not None, limite_s=5)
    tela._oferta.escolher(False)


@gui
def test_executar_abre_a_janela_com_a_oferta_e_a_reabertura(monkeypatch):
    chamadas: list[dict] = []

    class Falsa:
        def __init__(self, *args, **kwargs):
            chamadas.append({"args": args, **kwargs})

        def mainloop(self):
            chamadas.append({"mainloop": True})

    monkeypatch.setattr(app_mod, "JanelaPrincipal", Falsa)
    assert app_mod.executar() == 0
    assert chamadas[0] == {
        "args": (),
        "oferecer_tutorial": True,
        "reabrir_ultimo": True,
        "verificar_atualizacao": True,  # só a abertura de verdade consulta o GitHub
    }
    assert chamadas[1] == {"mainloop": True}


def test_o_caminho_do_ultimo_projeto_nao_e_resolvido_com_arquivos_de_verdade(tmp_path):
    """O config guarda o texto do caminho; a janela o converte com `Path` na hora de abrir."""
    config = ConfigApp(ultimo_projeto=str(tmp_path / "x"))
    assert Path(config.ultimo_projeto) == tmp_path / "x"
