"""Rolagem suave, painel de português compacto, prévia do Short em miniatura e mini player."""

from __future__ import annotations

import contextlib
import time
import tkinter

import pytest

from spurgeon.core.config import raiz_repositorio
from spurgeon.core.ffmpeg import sondar
from spurgeon.render import previa
from spurgeon.render.previa import PreviaDoShort
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import janela_do_corte
from spurgeon.transcricao.formato import Palavra
from tests.test_shorts import cortes_do_sermao, sermao_falso

# --- janela do corte (pura) ----------------------------------------------------------------


def test_a_janela_do_corte_tem_o_corte_inteiro_e_o_contexto_dos_dois_lados():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    janela = janela_do_corte(palavras, corte, fora=30)
    dentro = [p for p in janela.palavras if p.dentro]
    assert [p.indice for p in dentro] == list(range(janela.primeira, janela.ultima + 1))
    assert janela.palavras[0].indice == janela.primeira - 30
    assert janela.palavras[-1].indice == janela.ultima + 30


def test_a_janela_do_corte_nao_sai_do_sermao():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    janela = janela_do_corte(palavras, corte, fora=10**6)
    assert janela.palavras[0].indice == 0
    assert janela.palavras[-1].indice == len(palavras) - 1


# --- janela ------------------------------------------------------------------------------


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


def bombear(raiz, segundos: float) -> None:
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        raiz.update()
        time.sleep(0.01)


@pytest.fixture
def toplevel(raiz):
    import customtkinter as ctk

    janela = ctk.CTkToplevel(raiz)
    janela.geometry("420x320")
    janela.withdraw()
    yield janela
    janela.destroy()


# --- rolagem -------------------------------------------------------------------------------


@pytest.fixture
def quadro(toplevel, raiz):
    import customtkinter as ctk

    from spurgeon.ui.rolagem import QuadroRolavel

    quadro = QuadroRolavel(toplevel, width=380, height=260)
    quadro.pack(fill="both", expand=True)
    for n in range(80):
        ctk.CTkLabel(quadro, text=f"linha {n}").pack(anchor="w")
    bombear(raiz, 0.3)
    return quadro


@pytest.mark.gui
def test_um_giro_da_roda_anda_mais_que_o_padrao_e_para_sozinho(quadro, raiz):
    canvas = quadro._parent_canvas
    assert canvas.yview() != (0.0, 1.0)
    altura = float(canvas.cget("scrollregion").split()[3])
    quadro.rolar_giro(1)
    bombear(raiz, 0.9)
    andou = canvas.yview()[0] * altura
    assert 60 <= andou <= 100  # 80 px por giro (o padrão do CustomTkinter é 20)
    parou = canvas.yview()[0]
    bombear(raiz, 0.2)
    assert canvas.yview()[0] == parou  # a animação terminou: nada mais anda


@pytest.mark.gui
def test_a_rolagem_acumula_giros_e_nao_passa_do_fim_nem_do_comeco(quadro, raiz):
    canvas = quadro._parent_canvas
    for _ in range(40):
        quadro.rolar_giro(1)
    bombear(raiz, 2.5)
    assert canvas.yview()[1] == pytest.approx(1.0, abs=0.001)
    for _ in range(60):
        quadro.rolar_giro(-1)
    bombear(raiz, 3.0)
    assert canvas.yview()[0] == pytest.approx(0.0, abs=0.001)


@pytest.mark.gui
def test_nenhum_passo_da_rolagem_passa_do_maximo(quadro, raiz, monkeypatch):
    from spurgeon.ui import rolagem

    canvas = quadro._parent_canvas
    passos: list[int] = []
    original = canvas.yview_scroll
    monkeypatch.setattr(
        canvas,
        "yview_scroll",
        lambda n, unidade: (passos.append(abs(int(n))), original(n, unidade)),
    )
    for _ in range(10):
        quadro.rolar_giro(1)
    bombear(raiz, 1.5)
    assert passos and max(passos) <= rolagem.PASSO_MAXIMO_PX


@pytest.mark.gui
def test_a_roda_do_mouse_sobre_o_quadro_usa_a_rolagem_suave(quadro, raiz):
    evento = type("Evento", (), {"widget": quadro._parent_canvas, "delta": -120, "num": 0})()
    quadro._mouse_wheel_all(evento)
    bombear(raiz, 0.8)
    assert quadro._parent_canvas.yview()[0] > 0


# --- painel de português compacto -----------------------------------------------------------


@pytest.mark.gui
def test_o_painel_compacto_so_ocupa_o_que_mostra(toplevel, raiz, dicionario_br):
    from spurgeon.transcricao.verificador import Verificador
    from spurgeon.ui.verificacao import PainelDePortugues

    caixa = tkinter.Text(toplevel)
    caixa.pack()
    caixa.insert("1.0", "O pregador falou blimbrado hoje. Também falou sobre sobre a fé.")
    painel = PainelDePortugues(
        toplevel,
        caixa,
        lambda: Verificador(["Spurgeon"], dicionario=dicionario_br),
        compacto=True,
    )
    painel.pack(fill="x")
    raiz.update()
    assert painel._moldura.winfo_manager() == ""  # antes de verificar não há lista
    painel.verificar(sincrono=True)
    raiz.update()
    pendentes = [i for i in painel._itens if i.estado == "pendente"]
    assert len(pendentes) >= 2
    assert painel._moldura.winfo_manager() == "grid"
    assert int(painel._lista.cget("height")) == min(len(pendentes), 8)
    for _ in range(len(pendentes) + 2):
        if painel._atual is None:
            break
        painel.ignorar_atual()
    raiz.update()
    assert not [i for i in painel._itens if i.estado == "pendente"]
    assert painel._moldura.winfo_manager() == ""  # sem aviso, a lista e a navegação somem
    assert painel._rodape.winfo_manager() == ""


@pytest.mark.gui
def test_o_painel_normal_continua_com_a_lista_de_8_linhas(toplevel, raiz, dicionario_br):
    from spurgeon.transcricao.verificador import Verificador
    from spurgeon.ui.verificacao import PainelDePortugues

    caixa = tkinter.Text(toplevel)
    caixa.insert("1.0", "Tudo certo aqui.")
    painel = PainelDePortugues(
        toplevel, caixa, lambda: Verificador(["Spurgeon"], dicionario=dicionario_br)
    )
    painel.pack(fill="both", expand=True)
    painel.verificar(sincrono=True)
    assert painel._moldura.winfo_manager() == "grid"
    assert int(painel._lista.cget("height")) == 8


# --- prévia do Short -----------------------------------------------------------------------


def corte_e_palavras():
    palavras = [
        Palavra(texto, 2.2 + k * 0.4, 2.5 + k * 0.4)
        for k, texto in enumerate(
            ["A", "graça", "de", "Deus", "nos", "basta", "em", "todo", "momento", "da", "vida."]
        )
    ]
    corte = Corte(
        id=7, inicio=2.0, fim=6.0, texto="x", categoria="aplicacao", nota=80.0, detalhe={}
    )
    return corte, palavras


@pytest.mark.ffmpeg
def test_a_previa_do_corte_sai_pequena_vertical_e_com_som(tmp_path, video_teste):
    info = sondar(video_teste)
    corte, palavras = corte_e_palavras()
    progresso: list[float] = []
    pronta = previa.previa_do_corte(
        video_teste, info, corte, palavras, tmp_path / "p" / "previa_7.mp4",
        deslocamento_pct=0.0, posicao_legenda=0.62, com_legenda=True,
        pasta_fontes=raiz_repositorio() / "recursos" / "fontes", ao_progresso=progresso.append,
    )  # fmt: skip
    saida = sondar(pronta.video)
    assert (saida.largura, saida.altura) == (270, 480)
    assert saida.duracao_s == pytest.approx(4.0, abs=0.4)
    assert pronta.audio is not None and sondar(pronta.audio).duracao_s == pytest.approx(
        4.0, abs=0.2
    )
    assert pronta.duracao_s == pytest.approx(4.0)
    assert not list((tmp_path / "p").glob("*.parcial")) and not list((tmp_path / "p").glob("*.ass"))
    assert progresso and progresso[-1] == pytest.approx(1.0, abs=0.05)


@pytest.mark.ffmpeg
def test_cancelar_a_previa_nao_deixa_arquivo_pela_metade(tmp_path, video_teste, monkeypatch):
    from spurgeon.core.erros import Cancelado

    def interrompido(args, **kwargs):
        for arquivo in (a for a in args if str(a).endswith(".parcial")):
            arquivo.write_bytes(b"pela metade")
        raise Cancelado()

    monkeypatch.setattr(previa, "executar_ffmpeg", interrompido)
    info = sondar(video_teste)
    corte, palavras = corte_e_palavras()
    destino = tmp_path / "p" / "previa.mp4"
    with pytest.raises(Cancelado):
        previa.previa_do_corte(
            video_teste, info, corte, palavras, destino,
            deslocamento_pct=0.0, posicao_legenda=0.62, com_legenda=False,
        )  # fmt: skip
    assert list((tmp_path / "p").iterdir()) == []


# --- mini player ---------------------------------------------------------------------------


@pytest.fixture
def previa_pronta(tmp_path, video_teste) -> PreviaDoShort:
    info = sondar(video_teste)
    corte, palavras = corte_e_palavras()
    return previa.previa_do_corte(
        video_teste, info, corte, palavras, tmp_path / "mini" / "previa.mp4",
        deslocamento_pct=0.0, posicao_legenda=0.62, com_legenda=False,
    )  # fmt: skip


@pytest.fixture
def player(toplevel, raiz, monkeypatch):
    from spurgeon.ui import miniplayer

    monkeypatch.setattr(miniplayer, "winsound", None)  # os testes não fazem barulho
    jogador = miniplayer.MiniPlayer(toplevel)
    jogador.pack()
    yield jogador
    jogador.parar()


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_o_player_toca_a_imagem_acompanhando_o_relogio(player, previa_pronta, raiz, monkeypatch):
    quadros: list[int] = []
    original = player._pintar
    monkeypatch.setattr(player, "_pintar", lambda dados: (quadros.append(1), original(dados)))
    player.carregar(previa_pronta)
    bombear(raiz, 0.6)
    assert quadros  # o primeiro quadro aparece parado
    antes = len(quadros)
    player.tocar()
    bombear(raiz, 1.4)
    assert player.tocando
    assert len(quadros) - antes >= 4
    assert 0.5 < player._barra.get() <= 2.5  # o relógio andou cerca de 1,4 s
    player.pausar()
    parou_em = player.posicao
    bombear(raiz, 0.4)
    assert not player.tocando and player.posicao == parou_em


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_o_player_salta_para_outro_instante_e_volta_ao_fim(player, previa_pronta, raiz):
    player.carregar(previa_pronta)
    player.ir_para(3.0)
    bombear(raiz, 0.6)
    assert player.posicao == pytest.approx(3.0)
    player.tocar()
    bombear(raiz, 2.2)  # 3,0 + 2,2 passa dos 4 s: chega ao fim
    assert not player.tocando
    assert player.posicao == 0.0


@pytest.mark.gui
@pytest.mark.ffmpeg
def test_limpar_para_tudo_e_desabilita_o_botao(player, previa_pronta, raiz):
    player.carregar(previa_pronta)
    player.tocar()
    bombear(raiz, 0.3)
    player.limpar()
    assert not player.tocando and player.previa is None
    assert player._fluxo is None
    assert str(player._botao.cget("state")) == "disabled"
    with contextlib.suppress(tkinter.TclError):
        raiz.update()


@pytest.mark.gui
def test_arrastar_a_barra_chega_ao_destino_em_passos_curtos(quadro, raiz, monkeypatch):
    from spurgeon.ui import rolagem

    canvas = quadro._parent_canvas
    altura = float(canvas.cget("scrollregion").split()[3])
    saltos: list[float] = []
    original = canvas.yview_moveto
    monkeypatch.setattr(
        canvas,
        "yview_moveto",
        lambda f: (saltos.append(abs(float(f) - canvas.yview()[0]) * altura), original(f)),
    )
    quadro._arrastar("moveto", "0.9")  # a barra pede o fim de uma vez
    bombear(raiz, 1.5)
    assert canvas.yview()[0] == pytest.approx(0.9, abs=0.01)  # chegou lá
    assert len(saltos) > 3 and max(saltos) <= rolagem.PASSO_DO_ARRASTO_PX + 2  # sem salto grande
    quadro._arrastar("moveto", "0.0")
    bombear(raiz, 1.5)
    assert canvas.yview()[0] == pytest.approx(0.0, abs=0.001)


@pytest.mark.gui
def test_o_arrasto_segue_so_o_ultimo_pedido_da_barra(quadro, raiz):
    canvas = quadro._parent_canvas
    for f in (0.2, 0.9, 0.4):  # o mouse foi e voltou antes de o quadro andar
        quadro._arrastar("moveto", str(f))
    bombear(raiz, 1.5)
    visivel = canvas.yview()[1] - canvas.yview()[0]
    assert canvas.yview()[0] == pytest.approx(min(0.4, 1.0 - visivel), abs=0.01)


@pytest.mark.gui
def test_os_cliques_da_barra_continuam_rolando_pelo_comando_padrao(quadro, raiz):
    quadro._arrastar("scroll", "3", "units")
    assert quadro._parent_canvas.yview()[0] > 0
