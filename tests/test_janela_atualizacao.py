"""A janela principal com o aviso de atualização: consultar ao abrir, atualizar e reabrir.

Nenhum teste toca a internet: a consulta, o download e a reabertura são trocados por funções de
mentira. Fica num módulo à parte porque a janela principal é o root do Tk (a faixa usa outro).
"""

from __future__ import annotations

import contextlib
import time
import tkinter

import pytest

from spurgeon.core.atualizacao import (
    URL_DA_PAGINA,
    ErroDeAtualizacao,
    InfoAtualizacao,
    ResultadoDaAtualizacao,
)
from spurgeon.core.config import ConfigApp
from spurgeon.ui import app as app_mod
from spurgeon.ui import dialogos
from spurgeon.ui.painel_execucao import EstadoUI

pytestmark = pytest.mark.gui

INFO = InfoAtualizacao("9.0.0", "1.7.0")


def bombear(janela, condicao, limite_s: float = 20.0) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        janela.update()
        if condicao():
            return
        time.sleep(0.01)
    pytest.fail("a condição não ficou verdadeira a tempo")


# --- na janela principal -------------------------------------------------------------------


class Caixas:
    def __init__(self):
        self.mostradas: list[tuple[str, str]] = []
        self.confirma = True

    def mostrar(self, _janela, titulo, mensagem, *, botoes=("OK",), tipo="info"):
        self.mostradas.append((titulo, mensagem))
        return botoes[-1] if self.confirma else botoes[0]


@pytest.fixture
def abrir_janela(tmp_path, monkeypatch):
    """Cria janelas principais escondidas, com o que toca fora (rede, disco, reabrir) trocado."""
    caixas = Caixas()
    monkeypatch.setattr(dialogos, "mostrar", caixas.mostrar)
    monkeypatch.setattr(app_mod, "salvar_config", lambda *_a, **_k: None)
    chamadas = {"consultas": 0, "reabrir": [], "links": []}
    monkeypatch.setattr(
        app_mod, "reabrir_programa", lambda raiz, **kw: chamadas["reabrir"].append(kw)
    )
    monkeypatch.setattr(app_mod, "abrir_link", chamadas["links"].append)
    monkeypatch.setattr(app_mod, "motivo_de_nao_atualizar_sozinho", lambda _raiz: None)
    janelas = []

    def abrir(*, consulta, verificar: bool = True, **config) -> app_mod.JanelaPrincipal:
        def consultar():
            chamadas["consultas"] += 1
            if isinstance(consulta, Exception):
                raise consulta
            return consulta

        monkeypatch.setattr(app_mod, "consultar_atualizacao", consultar)
        cfg = ConfigApp(**config)
        cfg.pastas.entrada = tmp_path / "Downloads"
        cfg.pastas.projetos = tmp_path / "Projetos"
        cfg.pastas.recursos = tmp_path / "recursos"
        janela = None
        for _tentativa in range(4):  # logo depois de destruir uma janela, o Tk às vezes recusa
            try:
                janela = app_mod.JanelaPrincipal(cfg, verificar_atualizacao=verificar)
                break
            except tkinter.TclError:
                time.sleep(0.6)
        if janela is None:
            pytest.skip("sem ambiente gráfico")
        janela.withdraw()
        janelas.append(janela)
        return janela

    abrir.caixas, abrir.chamadas = caixas, chamadas
    yield abrir
    for janela in janelas:
        with contextlib.suppress(tkinter.TclError):  # a janela pode ter se fechado sozinha
            janela.executor.encerrar(espera_s=2)
            janela.destroy()


def test_ao_abrir_com_versao_nova_o_aviso_aparece_por_cima_das_abas(abrir_janela):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    aviso = janela.aviso_de_atualizacao
    assert aviso.piscando and aviso.winfo_manager() == "pack"
    assert aviso.master is janela._faixa_das_abas  # no topo da janela
    assert aviso.pack_info()["side"] == "top"  # o aviso em cima…
    assert janela._abas.master.pack_info()["side"] == "bottom"  # …e as abas embaixo dele


def _esperar_a_hora_da_consulta(janela) -> None:
    """Roda a janela por mais tempo do que ela espera para consultar o GitHub."""
    fim = time.monotonic() + app_mod._ATUALIZACAO_MS / 1000 + 0.8
    while time.monotonic() < fim:
        janela.update()
        time.sleep(0.01)


@pytest.mark.parametrize("consulta", [None, ErroDeAtualizacao("sem internet")])
def test_em_dia_ou_sem_internet_a_janela_abre_sem_aviso_e_sem_reclamar(abrir_janela, consulta):
    janela = abrir_janela(consulta=consulta)
    _esperar_a_hora_da_consulta(janela)
    assert abrir_janela.chamadas["consultas"] == 1  # perguntou, e a resposta não virou aviso
    assert janela.aviso_de_atualizacao.info is None
    assert janela.aviso_de_atualizacao.winfo_manager() == ""
    assert abrir_janela.caixas.mostradas == []  # offline é normal: nenhuma reclamação


@pytest.mark.parametrize(
    "como",
    [{"verificar": False}, {"atualizacao_ao_abrir": False}],
    ids=["janela_interna_ou_de_teste", "pessoa_desligou"],
)
def test_so_consulta_na_abertura_de_verdade_e_se_a_pessoa_nao_desligou(abrir_janela, como):
    janela = abrir_janela(consulta=INFO, **como)
    _esperar_a_hora_da_consulta(janela)
    assert abrir_janela.chamadas["consultas"] == 0
    assert janela.aviso_de_atualizacao.info is None


def test_a_caixa_do_rodape_liga_e_desliga_a_consulta_e_esconde_o_aviso(abrir_janela):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    assert janela.config_app.atualizacao_ao_abrir is True
    assert janela._check_atualizacao._dica_spurgeon.texto
    janela._var_atualizacao.set(False)
    janela._mudou_atualizacao_ao_abrir()
    assert janela.config_app.atualizacao_ao_abrir is False
    assert janela.aviso_de_atualizacao.info is None


def test_clicar_baixa_instala_avisa_e_reabre_o_programa(abrir_janela, monkeypatch):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    pedidos = []

    def baixar(raiz, info, *, ao_progresso=None, abrir=None):
        pedidos.append(info)
        ao_progresso(-1.0)  # o GitHub não diz o tamanho do ZIP
        ao_progresso(0.5)
        ao_progresso(1.0)
        return ResultadoDaAtualizacao("9.0.0", 7, 2, precisa_instalar=False)

    monkeypatch.setattr(app_mod, "baixar_e_atualizar", baixar)
    janela.aviso_de_atualizacao.botao.invoke()
    perguntas = [m for m in abrir_janela.caixas.mostradas if m[0] == "Atualizar o programa"]
    assert "1.7.0 para a 9.0.0" in perguntas[0][1] and "não são mexidos" in perguntas[0][1]
    textos = []
    original = janela.aviso_de_atualizacao.mostrar_andamento
    monkeypatch.setattr(
        janela.aviso_de_atualizacao, "mostrar_andamento", lambda t: (textos.append(t), original(t))
    )
    bombear(janela, lambda: janela._encerrando or bool(abrir_janela.chamadas["reabrir"]))
    assert pedidos == [INFO]
    assert any(
        "Atualização concluída" in m[0] and "9.0.0" in m[1] for m in abrir_janela.caixas.mostradas
    )
    assert abrir_janela.chamadas["reabrir"][0]["instalar"] is False
    assert any("50%" in t for t in textos) and "Instalando a atualização…" in textos
    assert "Baixando a atualização…" in textos  # sem porcentagem quando o tamanho não se sabe
    assert janela._encerrando  # o programa fechou para o novo abrir


def test_se_as_bibliotecas_mudaram_o_aviso_diz_que_o_instalador_vai_rodar(
    abrir_janela, monkeypatch
):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    monkeypatch.setattr(
        app_mod, "baixar_e_atualizar",
        lambda *a, **k: ResultadoDaAtualizacao("9.0.0", 7, 2, precisa_instalar=True),
    )  # fmt: skip
    janela.aviso_de_atualizacao.botao.invoke()
    bombear(janela, lambda: bool(abrir_janela.chamadas["reabrir"]))
    assert abrir_janela.chamadas["reabrir"][0]["instalar"] is True
    assert any("instalador vai rodar" in m[1] for m in abrir_janela.caixas.mostradas)


def test_dizer_depois_nao_baixa_nada(abrir_janela, monkeypatch):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    monkeypatch.setattr(
        app_mod, "baixar_e_atualizar", lambda *a, **k: pytest.fail("não devia baixar")
    )
    abrir_janela.caixas.confirma = False  # a pessoa responde "Depois"
    janela.aviso_de_atualizacao.botao.invoke()
    assert janela.aviso_de_atualizacao.piscando  # o aviso continua lá
    assert abrir_janela.chamadas["reabrir"] == []


def test_falha_na_atualizacao_mostra_o_erro_e_o_aviso_volta(abrir_janela, monkeypatch):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)

    def cai(*_a, **_k):
        raise ErroDeAtualizacao("Não foi possível baixar a atualização (sem rede).")

    monkeypatch.setattr(app_mod, "baixar_e_atualizar", cai)
    janela.aviso_de_atualizacao.botao.invoke()
    bombear(
        janela,
        lambda: any(
            m[0] == "Atualizar o programa" and "sem rede" in m[1]
            for m in abrir_janela.caixas.mostradas
        ),
    )
    bombear(janela, lambda: janela.aviso_de_atualizacao.piscando)
    assert str(janela.aviso_de_atualizacao.botao.cget("state")) == "normal"
    assert abrir_janela.chamadas["reabrir"] == [] and not janela._encerrando


def test_nao_atualiza_com_processamento_em_andamento(abrir_janela, monkeypatch):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    monkeypatch.setattr(
        app_mod, "baixar_e_atualizar", lambda *a, **k: pytest.fail("não devia baixar")
    )
    janela.execucao.definir_estado(EstadoUI.EXECUTANDO, tem_projeto=True)
    janela.aviso_de_atualizacao.botao.invoke()
    assert any("Espere o processamento" in m[1] for m in abrir_janela.caixas.mostradas)
    janela.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)


def test_clone_do_git_oferece_a_pagina_do_projeto_em_vez_de_atualizar(abrir_janela, monkeypatch):
    janela = abrir_janela(consulta=INFO)
    bombear(janela, lambda: janela.aviso_de_atualizacao.info is not None)
    monkeypatch.setattr(
        app_mod, "motivo_de_nao_atualizar_sozinho", lambda _r: "Pasta com git pull."
    )
    monkeypatch.setattr(
        app_mod, "baixar_e_atualizar", lambda *a, **k: pytest.fail("não devia baixar")
    )
    janela.aviso_de_atualizacao.botao.invoke()
    assert any("Pasta com git pull." in m[1] for m in abrir_janela.caixas.mostradas)
    assert abrir_janela.chamadas["links"] == [URL_DA_PAGINA]
