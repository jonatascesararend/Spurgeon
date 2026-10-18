"""Painel "Ajustar o zoom e a legenda dos Shorts": prévia do corte vertical e altura da legenda."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.ui.formulario import ValoresFormulario
from spurgeon.ui.painel_shorts import PainelShorts
from tests import test_paineis_imagem as imagens

pytestmark = pytest.mark.gui

raiz, amb, quadro = imagens.raiz, imagens.amb, imagens.quadro  # as fixtures dos painéis
FONTES = Path(__file__).resolve().parent.parent / "recursos" / "fontes"


def esperar_previa(amb, painel, antes=None):
    """Pede a prévia e espera chegar uma diferente de `antes` (a chave do que estava na tela)."""
    painel._gerar_previa()
    amb.bombear(lambda: painel._chave is not None and painel._chave != antes)


@pytest.fixture
def shorts(amb, raiz, video_teste):
    janela_do_painel = quadro(raiz)
    painel = PainelShorts(
        janela_do_painel, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas,
        opcoes_atuais=lambda: OpcoesProcessamento(), ao_mudar=amb.ao_mudar, pasta_fontes=FONTES,
    )  # fmt: skip
    painel.pack()
    amb.abrir_video(video_teste)
    amb.bombear(lambda: painel._quadro is not None)
    esperar_previa(amb, painel)
    yield painel
    janela_do_painel.destroy()


def test_comeca_sem_zoom_com_a_legenda_a_62_e_a_previa_aparece(shorts):
    assert shorts.valores() == {
        "shorts_zoom_pct": 100.0, "shorts_zoom_centro_x_pct": 50.0,
        "shorts_zoom_centro_y_pct": 50.0, "posicao_legenda": "62",
    }  # fmt: skip
    assert shorts._foto is not None
    assert (shorts._imagem.width, shorts._imagem.height) == (234, 416)  # 9:16
    assert "Sem zoom extra" in shorts._info.cget("text")
    assert shorts._rotulo_legenda.cget("text") == "62%"


def test_mexer_no_zoom_atualiza_valores_aviso_e_pede_nova_previa(shorts, amb):
    antes, chave = amb.mudancas, shorts._chave
    shorts.zoom.set(120)
    shorts._moveu_zoom()
    assert shorts.valores()["shorts_zoom_pct"] == 120.0 and shorts.zoom_texto.get() == "120"
    assert "120%" in shorts._info.cget("text") and "nitidez" not in shorts._info.cget("text")
    assert amb.mudancas > antes
    shorts.zoom.set(160)
    shorts._moveu_zoom()
    assert "perde bastante nitidez" in shorts._info.cget("text")
    esperar_previa(amb, shorts, chave)
    assert shorts._chave != chave


def test_digitar_o_zoom_em_porcentagem(shorts):
    shorts.zoom_texto.set("135%")
    shorts._digitou_zoom()
    assert shorts.valores()["shorts_zoom_pct"] == 135.0
    shorts.zoom_texto.set("999")
    shorts._digitou_zoom()
    assert shorts.valores()["shorts_zoom_pct"] == 200.0  # o máximo da tela
    shorts.zoom_texto.set("lixo")
    shorts._digitou_zoom()
    assert shorts.zoom_texto.get() == "200"


def test_a_posicao_do_zoom_entra_nos_valores(shorts):
    shorts.zoom.set(150)
    shorts.centro_x.set(30)
    shorts.centro_y.set(70)
    shorts._moveu_posicao()
    e = shorts.zoom_do_short()
    assert (e.zoom_pct, e.centro_x_pct, e.centro_y_pct) == (150.0, 30.0, 70.0)


def test_mexer_na_legenda_muda_o_valor_e_a_previa(shorts, amb):
    chave = shorts._chave
    shorts.legenda.set(45)
    shorts._moveu_legenda()
    assert shorts.valores()["posicao_legenda"] == "45"
    assert shorts._rotulo_legenda.cget("text") == "45%"
    esperar_previa(amb, shorts, chave)
    assert shorts._chave != chave


def test_clicar_no_quadro_poe_a_legenda_naquela_altura(shorts):
    shorts._clicou(types.SimpleNamespace(x=100, y=416 * 0.75))
    assert shorts.valores()["posicao_legenda"] == "75"
    shorts._clicou(types.SimpleNamespace(x=100, y=2))  # no topo: nunca passa do limite
    assert shorts.valores()["posicao_legenda"] == "30"
    shorts._clicou(types.SimpleNamespace(x=100, y=900))
    assert shorts.valores()["posicao_legenda"] == "90"


def test_sem_zoom_e_legenda_padrao_voltam_ao_inicio(shorts):
    shorts.zoom.set(170)
    shorts.centro_x.set(10)
    shorts._moveu_zoom()
    shorts.legenda.set(40)
    shorts.zerar_zoom()
    shorts.legenda_padrao()
    v = shorts.valores()
    assert v["shorts_zoom_pct"] == 100.0 and v["shorts_zoom_centro_x_pct"] == 50.0
    assert v["posicao_legenda"] == "62"
    assert shorts.zoom_texto.get() == "100"


def test_nada_que_apareca_mudou_nao_gera_outra_previa(shorts, amb):
    antes = shorts._sequencia
    shorts._gerar_previa()
    shorts.atualizar_previa()
    shorts._gerar_previa()
    assert shorts._sequencia == antes


def test_a_previa_acompanha_o_zoom_cor_e_recorte_da_janela(shorts, amb):
    atuais = OpcoesProcessamento(zoom_pct=140, recorte_deslocamento_x_pct=-10)
    shorts._opcoes_atuais = lambda: atuais
    chave = shorts._chave
    shorts.atualizar_previa()
    esperar_previa(amb, shorts, chave)
    assert shorts._chave != chave
    atuais = atuais.model_copy(update={"zoom_nos_shorts": False})
    chave = shorts._chave
    esperar_previa(amb, shorts, chave)  # sem o zoom do vídeo nos Shorts, outra imagem
    assert shorts._chave != chave


def test_campo_incompleto_na_janela_mantem_a_previa_anterior(shorts, amb):
    imagem = shorts._imagem
    shorts._opcoes_atuais = lambda: None
    shorts.zoom.set(150)
    shorts._moveu_zoom()
    shorts._gerar_previa()
    assert shorts._imagem is imagem


def test_sem_video_avisa_e_nao_gera_nada(amb, raiz):
    janela_do_painel = quadro(raiz)
    painel = PainelShorts(
        janela_do_painel, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas,
        opcoes_atuais=lambda: OpcoesProcessamento(), ao_mudar=amb.ao_mudar,
    )  # fmt: skip
    painel.pack()
    painel._carregar_quadro()
    assert "Escolha um vídeo" in painel._aviso.cget("text")
    painel._gerar_previa()
    assert painel._imagem is None and painel._foto is None
    janela_do_painel.destroy()


def test_aplicar_os_valores_de_um_projeto_aberto(shorts):
    shorts.aplicar(
        ValoresFormulario(
            shorts_zoom_pct=130.0,
            shorts_zoom_centro_x_pct=40.0,
            shorts_zoom_centro_y_pct=55.0,
            posicao_legenda="70",
        )
    )
    assert shorts.valores() == {
        "shorts_zoom_pct": 130.0, "shorts_zoom_centro_x_pct": 40.0,
        "shorts_zoom_centro_y_pct": 55.0, "posicao_legenda": "70",
    }  # fmt: skip
    assert shorts.zoom_texto.get() == "130"


def test_habilitar_liga_e_desliga_todos_os_controles(shorts):
    shorts.habilitar(False)
    assert all(str(c.cget("state")) == "disabled" for c in shorts._filhos)
    shorts.habilitar(True)
    assert all(str(c.cget("state")) == "normal" for c in shorts._filhos)


def test_os_elementos_do_youtube_vem_ligados_e_o_botao_alterna(shorts):
    assert shorts.mostrar_youtube.get() is True
    assert shorts._botao_youtube.cget("text") == "Esconder elementos do YouTube"
    assert "botões da direita" in shorts._conflito.cget("text")  # 62% cruza a coluna da direita

    shorts._alternar_youtube()
    assert shorts.mostrar_youtube.get() is False
    assert shorts._botao_youtube.cget("text") == "Mostrar elementos do YouTube"
    assert shorts._conflito.cget("text") == ""
    assert shorts._foto is not None  # a prévia continua na tela, só sem os elementos

    shorts._alternar_youtube()
    assert shorts.mostrar_youtube.get() is True
    assert shorts._conflito.cget("text") != ""


def test_a_conclusao_muda_com_a_altura_da_legenda(shorts):
    shorts.legenda.set(35)
    shorts._moveu_legenda()
    assert "livre dos elementos" in shorts._conflito.cget("text")
    shorts.legenda.set(90)
    shorts._moveu_legenda()
    assert "canal" in shorts._conflito.cget("text")
    shorts.legenda_padrao()
