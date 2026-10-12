"""Testes da janela principal, com a janela escondida e as etapas de demonstração."""

import time
import tkinter
from pathlib import Path

import pytest

from spurgeon.core.config import ConfigApp
from spurgeon.ui import app as app_mod
from spurgeon.ui import dialogos
from spurgeon.ui.formulario import ValoresFormulario
from spurgeon.ui.painel_execucao import EstadoUI

pytestmark = pytest.mark.gui

LIMITE_S = 60


class Dialogos:
    """Substitui as caixas modais (que bloqueariam o teste) e registra o que foi mostrado."""

    def __init__(self):
        self.mostradas: list[tuple[str, str]] = []
        self.resposta_confirmar = True

    def mostrar(self, _janela, titulo, mensagem, *, botoes=("OK",), tipo="info"):
        self.mostradas.append((titulo, mensagem))
        return botoes[-1] if self.resposta_confirmar else botoes[0]


@pytest.fixture(scope="module")
def janela(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("janela")
    config = ConfigApp()
    config.pastas.entrada = pasta / "Downloads"
    config.pastas.projetos = pasta / "Projetos"
    config.pastas.recursos = pasta / "recursos"
    try:
        w = app_mod.JanelaPrincipal(config)
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    w.withdraw()  # não piscar na tela de quem roda os testes
    yield w
    w.executor.encerrar(espera_s=5)
    w.destroy()


@pytest.fixture
def tela(janela, monkeypatch):
    """A janela limpa, sem efeitos colaterais no computador de quem testa."""
    caixas = Dialogos()
    monkeypatch.setattr(dialogos, "mostrar", caixas.mostrar)
    monkeypatch.setattr(app_mod, "abrir_pasta", lambda _p: None)
    monkeypatch.setattr(app_mod, "salvar_config", lambda *_a, **_k: None)
    monkeypatch.setenv("SPURGEON_DEMO", "1")
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.002")
    janela.executor.encerrar(espera_s=5)
    janela.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)
    janela._bloquear(False)
    padroes = ValoresFormulario()
    for nome, var in janela.opcoes.bools.items():
        var.set(getattr(padroes, nome))
    for nome, var in janela.opcoes.textos.items():
        var.set(getattr(padroes, nome))
    janela.opcoes._mudou()
    janela.caixas = caixas
    return janela


def bombear(janela, condicao, limite_s=LIMITE_S):
    """Roda a interface até `condicao()` ser verdadeira (os eventos chegam pelo `after`)."""
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        janela.update()
        if condicao():
            return
        time.sleep(0.03)
    pytest.fail(
        f"Tempo esgotado; estado={janela.execucao.estado}; caixas={janela.caixas.mostradas}"
    )


def escolher_video(janela, video):
    janela.entrada.definir_video(video)
    bombear(janela, lambda: janela._info_video is not None)


def test_abre_no_estado_inicial(tela):
    assert tela.execucao.estado == EstadoUI.OCIOSO
    assert tela.opcoes.valores() == ValoresFormulario()
    assert str(tela.execucao.botao_iniciar.cget("state")) == "normal"
    assert str(tela.execucao.botao_pausar.cget("state")) == "disabled"
    assert str(tela.execucao.botao_cancelar.cget("state")) == "disabled"


def test_legendas_dinamicas_so_ficam_ativas_com_shorts(tela):
    caixa = tela.opcoes.bools
    assert str(tela.opcoes._por_nome["legendas_dinamicas"].cget("state")) == "disabled"
    caixa["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["legendas_dinamicas"].cget("state")) == "normal"
    caixa["gerar_shorts"].set(False)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["legendas_dinamicas"].cget("state")) == "disabled"


def test_campo_de_pausa_so_vale_com_o_video_longo(tela):
    tela.opcoes.bools["gerar_video_longo"].set(False)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["limiar_pausa"].cget("state")) == "disabled"


def test_bloquear_trava_o_formulario(tela):
    tela._bloquear(True)
    assert str(tela.opcoes._por_nome["gerar_video_longo"].cget("state")) == "disabled"
    assert str(tela.entrada._controles[0].cget("state")) == "disabled"
    tela._bloquear(False)
    assert str(tela.opcoes._por_nome["gerar_video_longo"].cget("state")) == "normal"


@pytest.mark.ffmpeg
def test_escolher_video_preenche_nome_data_e_informacoes(tela, video_teste):
    escolher_video(tela, video_teste)
    assert tela.entrada.var_nome.get() == "teste"
    assert tela.entrada.var_data.get()
    assert "640×360" in tela.entrada.rotulo_info.cget("text")


def test_campo_invalido_mostra_o_problema_e_nao_inicia(tela, video_falso):
    tela.entrada.var_video.set(str(video_falso))
    tela.opcoes.textos["limiar_pausa"].set("abc")
    tela._iniciar()
    assert tela.execucao.estado == EstadoUI.OCIOSO
    titulos = [t for t, _ in tela.caixas.mostradas]
    assert titulos == ["Confira os campos"]
    assert "Tamanho mínimo da pausa" in tela.caixas.mostradas[0][1]


@pytest.mark.ffmpeg
def test_fluxo_completo_com_revisao(tela, video_teste):
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão de teste")
    tela._iniciar()

    bombear(tela, lambda: tela.execucao.estado == EstadoUI.REVISAO)
    assert tela._revisao["id"] == "demo_revisar"
    assert str(tela.execucao.botao_iniciar.cget("state")) == "disabled"
    assert tela._pastas is not None and (tela._pastas.raiz / "estado.json").exists()

    tela._aprovar_revisao()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CONCLUIDO)
    assert not tela._pastas.work.exists()  # intermediários apagados no sucesso
    assert tela.caixas.mostradas == []  # nenhum diálogo de erro no caminho feliz
    assert str(tela.opcoes._por_nome["gerar_video_longo"].cget("state")) == "normal"


@pytest.mark.ffmpeg
def test_processar_de_novo_um_projeto_concluido_pergunta_antes(tela, video_teste):
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão repetido")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CONCLUIDO)

    tela.caixas.resposta_confirmar = False  # o usuário escolhe "Cancelar"
    tela._iniciar()
    bombear(tela, lambda: any(t == "Projeto já concluído" for t, _ in tela.caixas.mostradas))
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.OCIOSO)


@pytest.mark.ffmpeg
def test_cancelar_deixa_o_projeto_retomavel(tela, video_teste, monkeypatch):
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.03")
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão cancelado")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.EXECUTANDO and tela._total > 0.1)

    tela._cancelar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CANCELADO)
    assert tela._pastas.work.exists()
    assert str(tela.execucao.botao_iniciar.cget("text")) == "Retomar processamento"


@pytest.mark.ffmpeg
def test_pausar_e_retomar(tela, video_teste, monkeypatch):
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.03")
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão pausado")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.EXECUTANDO and tela._total > 0.1)

    tela._pausar_retomar()
    assert tela.execucao.estado == EstadoUI.PAUSANDO
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.PAUSADO)
    assert str(tela.execucao.botao_pausar.cget("text")) == "Retomar"

    tela._pausar_retomar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CONCLUIDO)


@pytest.mark.ffmpeg
def test_video_ilegivel_mostra_erro_na_tela(tela, tmp_path):
    lixo = tmp_path / "lixo.mp4"
    lixo.write_bytes(b"isto nao e um video" * 100)
    tela.entrada.definir_video(Path(lixo))
    bombear(
        tela, lambda: "Não foi possível usar este vídeo" in tela.entrada.rotulo_info.cget("text")
    )
    assert tela._info_video is None
