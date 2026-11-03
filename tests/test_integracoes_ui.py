"""A aba Integrações: cartões das contas, salvar com segurança, conectar e desconectar."""

from __future__ import annotations

import queue
import threading
import time
import tkinter

import customtkinter as ctk
import pytest

from spurgeon.core.erros import Cancelado
from spurgeon.publicacao.agenda import criar_plataformas
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDePublicacao
from spurgeon.publicacao.tiktok import TikTok
from spurgeon.publicacao.youtube import YouTube
from spurgeon.ui import dialogos
from spurgeon.ui import integracoes as integ
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.integracoes import AbaIntegracoes
from tests.ambiente_grafico import criar_raiz
from tests.falso_de_rede import TransporteFalso

pytestmark = pytest.mark.gui


def bombear(janela, condicao, limite_s: float = 10.0) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        while True:  # o que as threads agendam é feito aqui, na thread da janela
            try:
                janela.fila.get_nowait()()
            except queue.Empty:
                break
        janela.update()
        if condicao():
            return
        time.sleep(0.01)
    pytest.fail("a condição não ficou verdadeira a tempo")


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
def cofre(tmp_path):
    return Cofre(tmp_path / "c.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1])


@pytest.fixture
def aba(raiz, cofre):
    fila: queue.Queue = queue.Queue()
    tarefas = Tarefas(fila.put)
    mudancas: list[int] = []
    tela = AbaIntegracoes(
        raiz,
        plataformas=criar_plataformas(cofre, TransporteFalso()),
        cofre=cofre,
        tarefas=tarefas,
        ao_mudar=lambda: mudancas.append(1),
    )
    tela.pack(fill="both", expand=True)
    tela.mudancas = mudancas  # type: ignore[attr-defined]
    tela.fila = fila  # type: ignore[attr-defined]
    raiz.update()
    yield tela
    tela.destroy()


def _dica_de(widget) -> str:
    objeto = getattr(widget, "_dica_spurgeon", None)
    return objeto.texto if objeto is not None else ""


def _todos(widget):
    for filho in widget.winfo_children():
        yield filho
        yield from _todos(filho)


def test_um_cartao_por_conta_e_a_meta_leva_instagram_e_facebook(aba):
    assert list(aba.cartoes) == ["youtube", "meta", "tiktok", "upload_post"]
    assert [p.chave for p in aba.cartoes["meta"].plataformas] == ["instagram", "facebook"]
    assert "○ Não conectado" in aba.cartoes["youtube"].situacao.cget("text")
    assert "fluxo assistido" in aba.cartoes["tiktok"].situacao.cget("text")  # já serve sem chaves


def test_todo_botao_e_campo_tem_dica_de_ferramenta(aba):
    sem = [
        w for w in _todos(aba)
        if isinstance(w, ctk.CTkButton | ctk.CTkEntry | ctk.CTkTextbox) and not _dica_de(w)
    ]  # fmt: skip
    assert not sem, f"sem dica: {sem}"
    assert len(_dica_de(aba.cartoes["youtube"].botao_conectar)) > 40


def test_salvar_guarda_o_segredo_criptografado_e_nao_o_mostra_de_volta(aba, cofre):
    cartao = aba.cartoes["youtube"]
    cartao.entradas["client_id"].insert(0, "  meu-id.apps.googleusercontent.com ")
    cartao.entradas["client_secret"].insert(0, "GOCSPX-segredo")
    assert cartao.entradas["client_secret"].cget("show") == "•"

    cartao.salvar()

    assert cofre.obter("youtube", "client_id") == "meu-id.apps.googleusercontent.com"
    assert cofre.obter("youtube", "client_secret") == "GOCSPX-segredo"
    assert "GOCSPX-segredo" not in cofre.caminho.read_text("utf-8")
    assert cartao.entradas["client_secret"].get() == ""  # o segredo não volta para a tela
    assert "guardado" in cartao.entradas["client_secret"].cget("placeholder_text")
    assert cartao.entradas["client_id"].get() == "meu-id.apps.googleusercontent.com"
    assert aba.mudancas == [1]


def test_salvar_com_o_segredo_em_branco_nao_apaga_o_que_ja_estava(aba, cofre):
    cofre.definir("youtube", "client_secret", "ANTIGO", segredo=True)
    cartao = aba.cartoes["youtube"]
    cartao.atualizar()
    cartao.salvar()
    assert cofre.obter("youtube", "client_secret") == "ANTIGO"
    cartao.entradas["client_secret"].insert(0, "NOVO")
    cartao.salvar()
    assert cofre.obter("youtube", "client_secret") == "NOVO"


def test_conectar_sem_preencher_diz_o_que_falta_sem_abrir_nada(aba, monkeypatch):
    chamadas: list[str] = []
    monkeypatch.setattr(YouTube, "conectar", lambda self, **k: chamadas.append("x") or "?")
    cartao = aba.cartoes["youtube"]
    cartao.conectar()
    assert "Falta preencher" in cartao.mensagem.cget("text")
    assert "ID do cliente" in cartao.mensagem.cget("text")
    assert chamadas == [] and not cartao.ocupado


def test_conectar_tiktok_sem_chaves_explica_que_o_assistido_ja_basta(aba):
    cartao = aba.cartoes["tiktok"]
    cartao.conectar()
    assert "fluxo assistido" in cartao.mensagem.cget("text") and not cartao.ocupado


def test_conectar_com_sucesso_atualiza_a_situacao(aba, cofre, monkeypatch):
    def conectou(self, **_k):
        self.cofre.definir("youtube", "refresh_token", "RT", segredo=True)
        self.cofre.definir("youtube", "conta_nome", "Igreja X")
        return "Igreja X"

    monkeypatch.setattr(YouTube, "conectar", conectou)
    cartao = aba.cartoes["youtube"]
    cartao.entradas["client_id"].insert(0, "id")
    cartao.entradas["client_secret"].insert(0, "seg")
    cartao.conectar()
    assert cartao.ocupado and "conectando" in cartao.situacao.cget("text")
    bombear(aba, lambda: not cartao.ocupado)
    assert cartao.mensagem.cget("text") == "Conectado: Igreja X."
    assert cartao.situacao.cget("text") == "● Conectado · Igreja X"
    assert str(cartao.botao_desconectar.cget("state")) == "normal"
    assert aba.mudancas  # a janela soube (para refazer as redes disponíveis no Studio)


def test_conectar_que_falha_mostra_o_motivo_em_vermelho(aba, monkeypatch):
    def falhou(self, **_k):
        raise ErroDePublicacao("O Google recusou o acesso.")

    monkeypatch.setattr(YouTube, "conectar", falhou)
    cartao = aba.cartoes["youtube"]
    cartao.entradas["client_id"].insert(0, "id")
    cartao.entradas["client_secret"].insert(0, "seg")
    cartao.conectar()
    bombear(aba, lambda: not cartao.ocupado)
    assert cartao.mensagem.cget("text") == "O Google recusou o acesso."
    assert cartao.mensagem.cget("text_color") == integ._COR_ERRO
    assert "Não conectado" in cartao.situacao.cget("text")


def test_cancelar_a_espera_do_navegador(aba, monkeypatch):
    chegou = threading.Event()

    def esperar(self, *, cancelado=None, **_k):
        chegou.set()
        while not cancelado():
            time.sleep(0.01)
        raise Cancelado("Conexão cancelada.")

    monkeypatch.setattr(YouTube, "conectar", esperar)
    cartao = aba.cartoes["youtube"]
    cartao.entradas["client_id"].insert(0, "id")
    cartao.entradas["client_secret"].insert(0, "seg")
    cartao.conectar()
    bombear(aba, chegou.is_set)
    assert cartao.botao_cancelar.winfo_manager() == "pack"
    assert str(cartao.botao_conectar.cget("state")) == "disabled"  # não conecta duas vezes
    cartao._cancelar()
    bombear(aba, lambda: not cartao.ocupado)
    assert cartao.mensagem.cget("text") == "Conexão cancelada."
    assert cartao.botao_cancelar.winfo_manager() == ""


def test_desconectar_apaga_tudo_da_conta_depois_de_confirmar(aba, cofre, monkeypatch):
    cofre.definir("meta", "app_id", "1")
    cofre.definir("meta", "page_token", "T", segredo=True)
    cofre.definir("meta", "page_id", "PG")
    cartao = aba.cartoes["meta"]
    cartao.atualizar()
    assert "Conectado" in cartao.situacao.cget("text")

    monkeypatch.setattr(dialogos, "confirmar", lambda *a, **k: False)
    cartao.desconectar()
    assert cofre.contas() == ["meta"]  # desistiu: nada mudou

    monkeypatch.setattr(dialogos, "confirmar", lambda *a, **k: True)
    cartao.desconectar()
    assert cofre.contas() == []
    assert cartao.entradas["app_id"].get() == ""
    assert "Não conectado" in cartao.situacao.cget("text")
    assert str(cartao.botao_desconectar.cget("state")) == "disabled"


def test_tiktok_conectado_pela_api_mostra_a_conta(aba, cofre):
    cofre.definir("tiktok", "refresh_token", "RT", segredo=True)
    cofre.definir("tiktok", "conta_nome", "Igreja no TikTok")
    aba.atualizar()
    assert aba.cartoes["tiktok"].situacao.cget("text") == "● Conectado · Igreja no TikTok"
    assert isinstance(aba.cartoes["tiktok"].plataformas[0], TikTok)


def test_registro_de_atividade_acrescenta_linhas(aba):
    aba.registrar("YouTube: Short “Deus é fiel” agendado para 05/11 18:00")
    aba.registrar("Instagram: publicado")
    texto = aba.atividade.get("1.0", "end")
    assert "YouTube: Short “Deus é fiel” agendado" in texto and texto.count("\n") >= 2
    assert str(aba.atividade.cget("state")) == "disabled"  # a pessoa não edita o registro
