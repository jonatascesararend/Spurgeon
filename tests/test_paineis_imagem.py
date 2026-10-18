"""Painéis do corte, do zoom e do tratamento de imagem, com a janela de verdade."""

from __future__ import annotations

import queue
import tkinter
import types
from pathlib import Path

import pytest
from PIL import ImageStat

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.config import ConfigApp
from spurgeon.core.ffmpeg import sondar
from spurgeon.ui import dialogos
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.formulario import ValoresFormulario, calcular_habilitacoes
from spurgeon.ui.painel_aparar import PainelAparar
from spurgeon.ui.painel_tratamento import PainelTratamento
from spurgeon.ui.painel_zoom import PainelZoom
from spurgeon.ui.presets import RepositorioDePresets
from spurgeon.ui.quadros import FonteDeQuadros

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


class Ambiente:
    """Tarefas em segundo plano que entregam o resultado quando o teste manda (sem Tk em thread)."""

    def __init__(self, raiz, tmp_path):
        self.raiz = raiz
        self.fila: queue.Queue = queue.Queue()
        self.tarefas = Tarefas(self.fila.put)
        self.fonte = FonteDeQuadros(self.tarefas, pasta=tmp_path / "quadros")
        self.config = ConfigApp()
        self.salvou = 0
        self.presets = RepositorioDePresets(self.config, lambda _c: self._salvou())
        self.mudancas = 0

    def _salvou(self):
        self.salvou += 1

    def ao_mudar(self):
        self.mudancas += 1

    def bombear(self, condicao=lambda: True, limite_s=60.0):
        import time

        fim = time.monotonic() + limite_s
        while time.monotonic() < fim:
            while not self.fila.empty():
                self.fila.get()()
            self.raiz.update()
            if condicao():
                return
            time.sleep(0.02)
        raise AssertionError("o trabalho em segundo plano não terminou a tempo")

    def abrir_video(self, caminho: Path):
        self.fonte.definir_video(caminho, sondar(caminho))


@pytest.fixture
def amb(raiz, tmp_path, monkeypatch):
    return Ambiente(raiz, tmp_path)


@pytest.fixture
def caixas(monkeypatch):
    vistas = []
    monkeypatch.setattr(
        dialogos, "mostrar",
        lambda _j, titulo, msg, *, botoes=("OK",), tipo="info": vistas.append((titulo, msg))
        or botoes[-1],
    )  # fmt: skip
    return vistas


def quadro(raiz):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    return janela


# --- começo e fim -----------------------------------------------------------------------------


@pytest.fixture
def aparar(amb, raiz, video_teste):
    janela = quadro(raiz)
    abertas: list[Path] = []
    painel = PainelAparar(
        janela, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas,
        ao_mudar=amb.ao_mudar, abrir=abertas.append,
    )  # fmt: skip
    painel.pack()
    amb.abrir_video(video_teste)  # 20 s
    painel.abertas = abertas
    yield painel
    janela.destroy()


def test_campos_comecam_vazios_e_a_janela_e_o_video_todo(aparar):
    assert aparar.valores() == {"video_inicio": "", "video_fim": ""}
    assert aparar.janela() == (0.0, None)
    assert "Sem cortes" in aparar._resumo.cget("text")


def test_tempo_negativo_vira_o_tempo_certo_contando_do_fim(aparar, amb):
    aparar.var_inicio.set("0:03")
    aparar.var_fim.set("-0:05")
    aparar._confirmar("inicio")
    aparar._confirmar("fim")
    assert aparar.var_inicio.get() == "0:03,0" and aparar.var_fim.get() == "0:15,0"
    assert aparar.janela() == (3.0, 15.0)
    assert "12 s" in aparar._resumo.cget("text")
    assert "3 s do começo e 5 s do fim" in aparar._resumo.cget("text")
    assert amb.mudancas >= 2  # o formulário foi avisado


def test_zero_e_o_fim_do_video_voltam_a_ser_vazio(aparar):
    aparar.var_inicio.set("0:00")
    aparar.var_fim.set("0:20")  # a duração do vídeo
    aparar._confirmar("inicio")
    aparar._confirmar("fim")
    assert aparar.valores() == {"video_inicio": "", "video_fim": ""}


def test_fim_alem_do_video_e_limitado_a_duracao(aparar):
    aparar.var_fim.set("5:00")
    aparar._confirmar("fim")
    assert aparar.var_fim.get() == ""  # até o fim, mesmo


def test_tempo_invalido_mostra_o_erro_sem_mudar_o_campo(aparar):
    aparar.var_inicio.set("abc")
    aparar._confirmar("inicio")
    assert "Tempo inválido" in aparar._resumo.cget("text")
    assert aparar.var_inicio.get() == "abc"


def test_fim_antes_do_comeco_avisa(aparar):
    aparar.var_inicio.set("0:10")
    aparar.var_fim.set("0:05")
    aparar._confirmar("fim")
    assert "terminar depois" in aparar._resumo.cget("text")


def test_mostra_o_quadro_exato_do_comeco_e_do_fim(aparar, amb):
    aparar.var_inicio.set("0:03")
    aparar.var_fim.set("0:15")
    aparar._buscar_quadros()
    amb.bombear(
        lambda: all(
            aparar._miniaturas.get(p) not in (None, aparar._vazia) for p in ("inicio", "fim")
        )
    )
    quadros = list(amb.fonte._cache)
    assert 3.0 in quadros and any(14.7 < t < 15.0 for t in quadros)  # o último quadro antes do fim


def test_previa_de_15_segundos_do_comeco_abre_no_player(aparar, amb):
    aparar.var_inicio.set("0:02")
    aparar.gerar_previa("inicio")
    assert str(aparar._botoes[1].cget("state")) == "disabled"  # gerando
    amb.bombear(lambda: bool(aparar.abertas))
    arquivo = aparar.abertas[0]
    assert arquivo.name == "previa_inicio.mp4"
    assert sondar(arquivo).duracao_s == pytest.approx(15.0, abs=0.6)
    assert str(aparar._botoes[1].cget("state")) == "normal"


def test_previa_do_fim_sao_os_ultimos_15_segundos_antes_do_corte_final(aparar, amb):
    aparar.var_fim.set("0:18")
    aparar.gerar_previa("fim")
    amb.bombear(lambda: bool(aparar.abertas))
    assert sondar(aparar.abertas[0]).duracao_s == pytest.approx(15.0, abs=0.6)
    assert aparar.abertas[0].name == "previa_fim.mp4"


def test_previa_mais_curta_que_15s_usa_o_que_ha(aparar, amb):
    aparar.var_inicio.set("0:15")
    aparar.gerar_previa("inicio")
    amb.bombear(lambda: bool(aparar.abertas))
    assert sondar(aparar.abertas[0]).duracao_s == pytest.approx(5.0, abs=0.6)


def test_previa_sem_video_avisa(raiz, amb, caixas):
    janela = quadro(raiz)
    painel = PainelAparar(
        janela, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas, ao_mudar=amb.ao_mudar
    )
    painel.pack()
    painel.gerar_previa("inicio")
    assert caixas and caixas[0][0] == "Prévia"
    janela.destroy()


def test_previa_usa_o_zoom_e_a_cor_da_tela(raiz, amb, video_teste):
    janela = quadro(raiz)
    pedidos = []
    painel = PainelAparar(
        janela, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas, ao_mudar=amb.ao_mudar,
        estilo=lambda: pedidos.append(1)
        or (Enquadramento(zoom_pct=140), TratamentoImagem(brilho=10)),
        abrir=lambda _c: None,
    )  # fmt: skip
    painel.pack()
    amb.abrir_video(video_teste)
    painel.gerar_previa("inicio")
    amb.bombear(lambda: str(painel._botoes[1].cget("state")) == "normal")
    assert pedidos
    janela.destroy()


def test_aplicar_valores_de_um_projeto_aberto(aparar):
    from spurgeon.core.config import OpcoesProcessamento

    v = ValoresFormulario.de_opcoes(OpcoesProcessamento(video_inicio_s=7.0, video_fim_s=15.5))
    aparar.aplicar(v)
    assert aparar.valores() == {"video_inicio": "0:07,0", "video_fim": "0:15,5"}
    assert aparar.janela() == (7.0, 15.5)


# --- zoom -------------------------------------------------------------------------------------


@pytest.fixture
def zoom(amb, raiz, video_teste):
    janela = quadro(raiz)
    painel = PainelZoom(
        janela, ValoresFormulario(), fonte=amb.fonte, presets=amb.presets, ao_mudar=amb.ao_mudar
    )
    painel.pack()
    amb.abrir_video(video_teste)
    amb.bombear(lambda: painel._imagem is not None)
    yield painel
    janela.destroy()


def test_zoom_comeca_sem_zoom_e_o_quadro_aparece(zoom):
    e = zoom.enquadramento()
    assert not e.ativo and zoom.valores()["zoom_pct"] == 100.0
    assert zoom._foto is not None and "Sem zoom" in zoom._info.cget("text")


def test_mexer_no_zoom_atualiza_valores_e_aviso_de_nitidez(zoom, amb):
    antes = amb.mudancas
    zoom.zoom.set(130)
    zoom._moveu_zoom()
    assert zoom.valores()["zoom_pct"] == 130.0 and zoom.zoom_texto.get() == "130"
    assert "130%" in zoom._info.cget("text") and "nitidez" not in zoom._info.cget("text")
    assert amb.mudancas > antes
    zoom.zoom.set(180)
    zoom._moveu_zoom()
    assert "perde bastante nitidez" in zoom._info.cget("text")


def test_digitar_o_zoom_em_porcentagem(zoom):
    zoom.zoom_texto.set("125%")
    zoom._digitou_zoom()
    assert zoom.valores()["zoom_pct"] == 125.0
    zoom.zoom_texto.set("999")
    zoom._digitou_zoom()
    assert zoom.valores()["zoom_pct"] == 200.0  # o máximo da tela
    zoom.zoom_texto.set("lixo")
    zoom._digitou_zoom()
    assert zoom.zoom_texto.get() == "200"


def test_clicar_no_quadro_centra_o_zoom_ali(zoom):
    zoom.zoom.set(150)
    zoom._moveu_zoom()
    largura, altura = zoom._imagem.size
    evento = types.SimpleNamespace(x=largura * 0.54, y=altura * 0.4)
    zoom._clicou(evento)
    valores = zoom.valores()
    assert valores["zoom_centro_x_pct"] == pytest.approx(54.0, abs=0.2)
    assert valores["zoom_centro_y_pct"] == pytest.approx(40.0, abs=0.2)


def test_a_area_cortada_aparece_sombreada():
    from PIL import Image

    from spurgeon.ui.composicao import sombrear_fora_do_zoom

    base = Image.new("RGB", (200, 100), (200, 200, 200))
    saida = sombrear_fora_do_zoom(base, Enquadramento(zoom_pct=200))
    assert saida.getpixel((100, 50)) == (200, 200, 200)  # dentro: intacto
    assert saida.getpixel((5, 5))[0] < 100  # fora: escuro
    assert sombrear_fora_do_zoom(base, Enquadramento()).getpixel((5, 5)) == (200, 200, 200)


def test_preset_de_zoom_salva_carrega_e_exclui(zoom, amb, monkeypatch, caixas):
    zoom.zoom.set(140)
    zoom.centro_x.set(54)
    zoom._moveu_zoom()
    monkeypatch.setattr(dialogos, "pedir_texto", lambda *_a, **_k: "Igreja A")
    zoom.salvar_preset()
    assert amb.presets.enquadramentos()["Igreja A"].zoom_pct == 140
    assert amb.salvou == 1  # gravado na configuração
    assert zoom.preset.get() == "Igreja A"

    zoom.zerar()
    assert zoom.valores()["zoom_pct"] == 100.0 and zoom.preset.get() == "(nenhum preset)"
    zoom._escolheu_preset("Igreja A")
    assert zoom.valores()["zoom_pct"] == 140.0 and zoom.valores()["zoom_centro_x_pct"] == 54.0

    zoom.preset.set("Igreja A")
    zoom.excluir_preset()
    assert amb.presets.enquadramentos() == {}


def test_salvar_preset_cancelado_nao_grava(zoom, amb, monkeypatch):
    monkeypatch.setattr(dialogos, "pedir_texto", lambda *_a, **_k: None)
    zoom.salvar_preset()
    assert amb.presets.enquadramentos() == {} and amb.salvou == 0


def test_mexer_no_zoom_tira_o_preset_selecionado(zoom, amb):
    amb.config.presets_enquadramento["X"] = Enquadramento(zoom_pct=120)
    zoom._escolheu_preset("X")
    zoom.preset.set("X")
    zoom.zoom.set(150)
    zoom._moveu_zoom()
    assert zoom.preset.get() == "(nenhum preset)"


def test_zoom_nos_shorts_e_desligavel(zoom):
    assert zoom.valores()["zoom_nos_shorts"] is True
    zoom.nos_shorts.set(False)
    assert zoom.valores()["zoom_nos_shorts"] is False


def test_zoom_aplica_os_valores_de_um_projeto(zoom):
    from spurgeon.core.config import OpcoesProcessamento

    v = ValoresFormulario.de_opcoes(
        OpcoesProcessamento(zoom_pct=133, zoom_centro_x_pct=60, zoom_nos_shorts=False)
    )
    zoom.aplicar(v)
    assert zoom.valores() == {
        "zoom_pct": 133.0, "zoom_centro_x_pct": 60.0, "zoom_centro_y_pct": 50.0,
        "zoom_nos_shorts": False,
    }  # fmt: skip


def test_zoom_sem_video_mostra_o_aviso(amb, raiz):
    janela = quadro(raiz)
    painel = PainelZoom(
        janela, ValoresFormulario(), fonte=amb.fonte, presets=amb.presets, ao_mudar=amb.ao_mudar
    )
    painel.pack()
    painel._carregar_quadro()
    assert "Escolha um vídeo" in painel._aviso.cget("text")
    janela.destroy()


# --- tratamento de imagem ---------------------------------------------------------------------


@pytest.fixture
def tratamento(amb, raiz, video_teste):
    janela = quadro(raiz)
    painel = PainelTratamento(
        janela, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas, presets=amb.presets,
        ao_mudar=amb.ao_mudar,
    )  # fmt: skip
    painel.pack()
    amb.abrir_video(video_teste)
    amb.bombear(lambda: painel._original is not None)
    yield painel
    janela.destroy()


def esperar_previa(amb, painel):
    painel._gerar_previa()
    amb.bombear(lambda: painel._tratado is not None)


def test_tratamento_comeca_desligado_e_neutro(tratamento):
    assert tratamento.valores()["tratamento_ativo"] is False
    assert tratamento.tratamento().neutro
    assert tratamento._foto is not None  # o original já aparece


def test_mexer_num_ajuste_liga_o_tratamento_e_gera_o_quadro_tratado(tratamento, amb):
    tratamento.ajustes["brilho"].set(60)
    tratamento._moveu("brilho")
    assert tratamento.ativo.get() is True
    assert tratamento._rotulos_valor["brilho"].cget("text") == "+60"
    assert tratamento.valores()["tratamento_brilho"] == 60.0
    esperar_previa(amb, tratamento)
    assert tratamento._tratado is not tratamento._original
    claro = ImageStat.Stat(tratamento._tratado.convert("L")).mean[0]
    escuro = ImageStat.Stat(tratamento._original.convert("L")).mean[0]
    assert claro > escuro  # o brilho se vê no quadro tratado


def test_dividir_o_quadro_arrastando_a_linha(tratamento):
    largura = tratamento._original.width
    tratamento._arrastar(types.SimpleNamespace(x=largura * 0.25, y=10))
    assert tratamento._divisao == pytest.approx(0.25)
    tratamento._arrastar(types.SimpleNamespace(x=-50, y=10))
    assert tratamento._divisao == 0.0
    tratamento._arrastar(types.SimpleNamespace(x=largura * 3, y=10))
    assert tratamento._divisao == 1.0


def test_antes_e_depois_mostram_cada_lado():
    from PIL import Image

    from spurgeon.ui.composicao import compor_antes_depois

    original = Image.new("RGB", (100, 50), (10, 10, 10))
    tratado = Image.new("RGB", (100, 50), (200, 200, 200))
    montado = compor_antes_depois(original, tratado, 0.5)
    assert montado.getpixel((10, 5)) == (10, 10, 10)  # esquerda: original
    assert montado.getpixel((90, 5)) == (200, 200, 200)  # direita: tratado
    todo_tratado = compor_antes_depois(original, tratado, 0.0)
    assert todo_tratado.getpixel((10, 5)) == (200, 200, 200)
    todo_original = compor_antes_depois(original, tratado, 1.0)
    assert todo_original.getpixel((90, 5)) == (10, 10, 10)


def test_sugestao_automatica_preenche_os_ajustes_a_partir_do_video(tratamento, amb):
    tratamento.sugerir()
    amb.bombear(
        lambda: (
            "Sugestão" in tratamento._aviso.cget("text")
            or "equilibrada" in tratamento._aviso.cget("text")
        )
    )
    sugerido = tratamento.tratamento()
    assert tratamento.ativo.get() is (not sugerido.neutro)
    # o vídeo de teste (testsrc2) tem de tudo um pouco: algum ajuste sai do zero
    assert not sugerido.neutro


def test_sugestao_sem_video_avisa(amb, raiz, caixas):
    janela = quadro(raiz)
    painel = PainelTratamento(
        janela, ValoresFormulario(), fonte=amb.fonte, tarefas=amb.tarefas, presets=amb.presets,
        ao_mudar=amb.ao_mudar,
    )  # fmt: skip
    painel.pack()
    painel.sugerir()
    assert caixas and caixas[0][0] == "Sugestão automática"
    janela.destroy()


def test_zerar_volta_tudo_a_zero(tratamento):
    tratamento.ajustes["contraste"].set(40)
    tratamento._moveu("contraste")
    tratamento.zerar()
    assert tratamento.tratamento().neutro
    assert tratamento._rotulos_valor["contraste"].cget("text") == "0"


def test_preset_de_tratamento_salva_carrega_e_exclui(tratamento, amb, monkeypatch, caixas):
    tratamento.ajustes["saturacao"].set(25)
    tratamento.ajustes["sombras"].set(-15)
    tratamento._moveu("saturacao")
    monkeypatch.setattr(dialogos, "pedir_texto", lambda *_a, **_k: "Estilo da igreja")
    tratamento.salvar_preset()
    guardado = amb.presets.tratamentos()["Estilo da igreja"]
    assert guardado.saturacao == 25 and guardado.sombras == -15
    assert tratamento.preset.get() == "Estilo da igreja"
    assert "Estilo da igreja" in tratamento._menu_preset.cget("values")

    tratamento.zerar()
    tratamento._escolheu_preset("Estilo da igreja")
    assert tratamento.tratamento() == guardado and tratamento.ativo.get() is True

    tratamento.preset.set("Estilo da igreja")
    tratamento.excluir_preset()
    assert amb.presets.tratamentos() == {}


def test_preset_vazio_desliga_o_tratamento_ao_escolher(tratamento, amb):
    amb.config.presets_tratamento["Nada"] = TratamentoImagem()
    tratamento._atualizar_presets()
    tratamento.ativo.set(True)
    tratamento._escolheu_preset("Nada")
    assert tratamento.ativo.get() is False


def test_outro_quadro_troca_o_exemplo(tratamento, amb):
    primeiro = tratamento._quadro.instante_s
    tratamento.outro_quadro()
    amb.bombear(
        lambda: tratamento._quadro is not None and tratamento._quadro.instante_s != primeiro
    )
    assert tratamento._quadro.instante_s != primeiro


def test_tratamento_aplica_os_valores_de_um_projeto(tratamento):
    from spurgeon.core.config import OpcoesProcessamento

    v = ValoresFormulario.de_opcoes(
        OpcoesProcessamento(
            tratamento_ativo=True, tratamento_brilho=12, tratamento_nos_shorts=False
        )
    )
    tratamento.aplicar(v)
    valores = tratamento.valores()
    assert valores["tratamento_ativo"] and valores["tratamento_brilho"] == 12.0
    assert valores["tratamento_nos_shorts"] is False


def test_tratamento_se_apaga_no_modo_so_vinhetas(tratamento):
    h = calcular_habilitacoes(
        ValoresFormulario(modo="so_vinhetas", introducao=True),
        introducao_existe=True, vinheta_existe=True,
    )  # fmt: skip
    tratamento.aplicar_habilitacoes(h, livre=True)
    assert str(tratamento._check_ativo.cget("state")) == "disabled"
    h = calcular_habilitacoes(ValoresFormulario(), introducao_existe=True, vinheta_existe=True)
    tratamento.aplicar_habilitacoes(h, livre=True)
    assert str(tratamento._check_ativo.cget("state")) == "normal"


# --- a fonte de quadros e as tarefas ----------------------------------------------------------


def test_so_o_pedido_mais_novo_de_cada_chave_entrega_o_resultado():
    fila: queue.Queue = queue.Queue()
    tarefas = Tarefas(fila.put)
    recebidos = []
    for n in range(3):
        tarefas.rodar(lambda n=n: n, recebidos.append, chave="slider")
    import time

    time.sleep(0.5)
    while not fila.empty():
        fila.get()()
    assert recebidos == [2]


def test_tarefa_que_falha_entrega_o_erro():
    fila: queue.Queue = queue.Queue()
    tarefas = Tarefas(fila.put)
    erros = []

    def falha():
        raise ValueError("boom")

    tarefas.rodar(falha, None, erros.append)
    import time

    time.sleep(0.3)
    while not fila.empty():
        fila.get()()
    assert len(erros) == 1 and "boom" in str(erros[0])


def test_quadros_de_exemplo_ficam_dentro_da_janela_escolhida(amb, video_teste):
    amb.abrir_video(video_teste)  # 20 s
    amb.fonte.definir_janela(4.0, 16.0)
    assert amb.fonte.janela_util() == (4.0, 16.0)
    instantes = [amb.fonte.instante_de_exemplo(i) for i in range(5)]
    assert all(4.0 <= t <= 16.0 for t in instantes) and instantes[0] == 7.0
    amb.fonte.definir_janela(0.0, None)
    assert amb.fonte.janela_util() == (0.0, 20.0)


def test_trocar_de_video_esquece_os_quadros_e_avisa_os_paineis(amb, video_teste):
    avisos = []
    amb.fonte.ao_mudar_o_video(lambda: avisos.append(1))
    amb.abrir_video(video_teste)
    assert amb.fonte.pronta and len(avisos) == 1
    amb.fonte.definir_video(None, None)
    assert not amb.fonte.pronta and len(avisos) == 2 and amb.fonte._cache == {}


def test_quadro_que_nao_existe_entrega_erro(amb, video_teste):
    amb.abrir_video(video_teste)
    erros = []
    amb.fonte.quadro(999.0, lambda _q: None, erros.append)
    amb.bombear(lambda: bool(erros))
    assert erros


def test_varios_paineis_pedindo_o_mesmo_quadro_ao_mesmo_tempo_nao_se_atrapalham(amb, video_teste):
    amb.abrir_video(video_teste)
    resultados, erros = [], []
    for chave in ("zoom", "tratamento", "aparar"):  # os três pedem o mesmo quadro ao abrir o vídeo
        amb.fonte.quadro(5.0, resultados.append, erros.append, chave=chave)
    amb.bombear(lambda: len(resultados) + len(erros) == 3)
    assert not erros and len(resultados) == 3
    assert (
        resultados[0].arquivo.exists() and len({id(q) for q in resultados}) == 1
    )  # o mesmo quadro


def test_trocar_de_video_durante_a_extracao_nao_guarda_o_quadro_velho(amb, video_teste):
    amb.abrir_video(video_teste)
    amb.fonte.quadro(5.0, lambda _q: None)
    amb.fonte.definir_video(None, None)  # a pessoa escolheu outro arquivo no meio da extração
    amb.bombear(lambda: amb.fila.empty() and True, limite_s=5)
    import time

    time.sleep(1.5)  # deixa a thread terminar
    while not amb.fila.empty():
        amb.fila.get()()
    assert amb.fonte._cache == {}
