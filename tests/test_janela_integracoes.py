"""A janela principal com a aba Integrações, a fila de postagens e o aviso ao sair."""

from __future__ import annotations

import pytest

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.agenda import RegistroDePostagens
from spurgeon.publicacao.modelo import ENVIADO, ERRO, NA_FILA, Resultado
from spurgeon.ui import app as app_mod
from spurgeon.ui import dialogos
from spurgeon.ui.app import ABA_INTEGRACOES, ABA_PROCESSAMENTO, ABA_STUDIO
from tests.test_janela_atualizacao import abrir_janela, bombear  # noqa: F401 - fixture

pytestmark = pytest.mark.gui


def _dica_de(widget) -> str:
    objeto = getattr(widget, "_dica_spurgeon", None)
    return objeto.texto if objeto is not None else ""


def test_a_janela_tem_tres_abas_e_a_de_integracoes_toma_a_janela_toda(abrir_janela):  # noqa: F811
    tela = abrir_janela(consulta=None, verificar=False)
    assert list(tela._abas._buttons_dict) == [ABA_PROCESSAMENTO, ABA_STUDIO, ABA_INTEGRACOES]
    assert len(_dica_de(tela._abas._buttons_dict[ABA_INTEGRACOES])) > 60

    tela._mostrar_aba(ABA_INTEGRACOES)
    info = tela.integracoes.grid_info()
    assert (int(info["row"]), int(info["column"]), int(info["columnspan"])) == (1, 0, 2)
    assert tela._coluna.winfo_manager() == "" and tela._direita.winfo_manager() == ""
    assert tela.studio.winfo_manager() == ""
    assert tela._abas.winfo_manager() == "pack"  # a faixa das abas continua no lugar

    tela._mostrar_aba(ABA_PROCESSAMENTO)
    assert tela.integracoes.winfo_manager() == ""
    assert tela._coluna.winfo_manager() == "grid" and tela._direita.winfo_manager() == "grid"

    tela._mostrar_aba(ABA_STUDIO)
    assert tela.studio.winfo_manager() == "grid" and tela.integracoes.winfo_manager() == ""
    tela._mostrar_aba(ABA_INTEGRACOES)  # de uma aba cheia para a outra
    assert tela.studio.winfo_manager() == "" and tela.integracoes.winfo_manager() == "grid"


def test_as_redes_e_o_cofre_da_janela(abrir_janela):  # noqa: F811
    tela = abrir_janela(consulta=None, verificar=False)
    assert list(tela._plataformas) == ["youtube", "instagram", "facebook", "tiktok", "upload_post"]
    assert tela.integracoes.plataformas is tela._plataformas
    assert tela.cofre.caminho.name == "integracoes.json"
    assert "config_do_usuario" in str(tela.cofre.caminho)  # o conftest isola o do usuário
    assert not tela.cofre.caminho.exists()  # abrir a janela não escreve nada


def test_a_fila_nao_anda_em_janela_de_teste(abrir_janela):  # noqa: F811
    tela = abrir_janela(consulta=None, verificar=False)
    assert tela._fila_agendada is None and not tela._fila_rodando


def test_a_fila_anda_depois_de_abrir_e_a_cada_minuto(abrir_janela, monkeypatch):  # noqa: F811
    chamadas: list = []

    def falso(plataformas, pasta, *, cancelado=None, ao_mudar=None):
        chamadas.append(pasta)
        ao_mudar(PastasProjeto(pasta / "projeto_um"), 3, Resultado("instagram", ENVIADO, "M1"))
        return 1

    monkeypatch.setattr(app_mod, "rodar_fila", falso)
    monkeypatch.setattr(app_mod, "_FILA_PRIMEIRA_MS", 50)
    monkeypatch.setattr(app_mod, "_FILA_MS", 150)
    tela = abrir_janela(consulta=None, verificar=True)
    bombear(tela, lambda: len(chamadas) >= 2)  # andou, e agendou a próxima volta
    assert chamadas[0] == tela.config_app.pastas.projetos
    atividade = tela.integracoes.atividade.get("1.0", "end")
    assert "projeto_um, Short 3: Instagram Reels publicado" in atividade


def test_a_fila_mostra_o_erro_e_o_aviso_na_atividade(abrir_janela, monkeypatch):  # noqa: F811
    def falso(plataformas, pasta, *, cancelado=None, ao_mudar=None):
        ao_mudar(
            PastasProjeto(pasta / "p"), 1,
            Resultado("instagram", ERRO, detalhe="O Instagram recusou o vídeo."),
        )  # fmt: skip
        ao_mudar(
            PastasProjeto(pasta / "p"), 2,
            Resultado("instagram", ENVIADO, "M", aviso="Publicado com atraso."),
        )  # fmt: skip
        return 2

    monkeypatch.setattr(app_mod, "rodar_fila", falso)
    monkeypatch.setattr(app_mod, "_FILA_PRIMEIRA_MS", 50)
    tela = abrir_janela(consulta=None, verificar=True)
    bombear(tela, lambda: "Publicado com atraso" in tela.integracoes.atividade.get("1.0", "end"))
    atividade = tela.integracoes.atividade.get("1.0", "end")
    assert "Short 1: Instagram Reels com erro. O Instagram recusou o vídeo." in atividade
    assert "Short 2: Instagram Reels publicado. ⚠ Publicado com atraso." in atividade


def test_falha_na_fila_vai_para_a_atividade_e_a_fila_continua(abrir_janela, monkeypatch):  # noqa: F811
    voltas: list[int] = []

    def quebrado(plataformas, pasta, *, cancelado=None, ao_mudar=None):
        voltas.append(1)
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(app_mod, "rodar_fila", quebrado)
    monkeypatch.setattr(app_mod, "_FILA_PRIMEIRA_MS", 50)
    monkeypatch.setattr(app_mod, "_FILA_MS", 150)
    tela = abrir_janela(consulta=None, verificar=True)
    bombear(tela, lambda: len(voltas) >= 2)  # uma falha não derruba a fila
    assert "Fila de postagens: disco cheio" in tela.integracoes.atividade.get("1.0", "end")
    assert not tela._fila_rodando or len(voltas) >= 2


def test_sair_com_postagens_na_fila_pergunta(abrir_janela, monkeypatch):  # noqa: F811
    tela = abrir_janela(consulta=None, verificar=False)
    arquivo = tela.config_app.pastas.projetos / "p1" / "_work" / "postagens.json"
    RegistroDePostagens(arquivo).gravar(
        1, Resultado("instagram", NA_FILA, quando="2026-11-05T10:00:00-03:00")
    )
    perguntas: list[str] = []
    resposta = {"sair": False}

    def confirmar(janela, titulo, mensagem, *, sim, nao):
        perguntas.append(mensagem)
        return resposta["sair"]

    monkeypatch.setattr(dialogos, "confirmar", confirmar)
    destruidas: list[int] = []
    monkeypatch.setattr(tela, "destroy", lambda: destruidas.append(1))
    try:
        tela._ao_fechar()
        assert "fila do Spurgeon em 1 projeto" in perguntas[0] and destruidas == []
        resposta["sair"] = True
        tela._ao_fechar()
        assert destruidas == [1]
    finally:
        tela.__dict__.pop("destroy", None)  # o `destroy` de verdade volta para a limpeza do teste


def test_sair_sem_fila_nao_pergunta_nada(abrir_janela, monkeypatch):  # noqa: F811
    tela = abrir_janela(consulta=None, verificar=False)
    monkeypatch.setattr(
        dialogos, "confirmar", lambda *a, **k: pytest.fail("não devia perguntar nada")
    )
    destruidas: list[int] = []
    monkeypatch.setattr(tela, "destroy", lambda: destruidas.append(1))
    try:
        tela._ao_fechar()
        assert destruidas == [1]
    finally:
        tela.__dict__.pop("destroy", None)
