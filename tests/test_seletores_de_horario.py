"""O calendário e o relógio de ponteiros do agendamento."""

from __future__ import annotations

import gc
import tkinter
from datetime import date, datetime
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from spurgeon.ui import seletores_de_horario as sel
from spurgeon.ui.seletores_de_horario import (
    MINUTOS,
    BotaoDoCalendario,
    BotaoDoRelogio,
    CalendarioMensal,
    RelogioDePonteiros,
    angulo_do_ponto,
    celula_do_ponto,
    hora_do_ponto,
    mes_vizinho,
    minuto_do_ponto,
    ponto_da_hora,
    ponto_do_minuto,
    rotulo_do_mes,
    semanas_do_mes,
)
from tests.ambiente_grafico import criar_raiz
from tests.falso_de_rede import Relogio

HOJE = date(2026, 11, 3)  # uma terça-feira; 1º de novembro de 2026 foi domingo
AGORA = datetime(2026, 11, 3, 12, 0).astimezone()
C = sel._CENTRO


# --- geometria (sem Tk) ------------------------------------------------------------------------
def test_o_mes_comeca_no_domingo_e_tem_as_semanas_certas():
    semanas = semanas_do_mes(2026, 11)
    assert semanas[0][0] == date(2026, 11, 1) and semanas[0][6] == date(2026, 11, 7)
    assert len(semanas) == 5 and semanas[-1][1] == date(2026, 11, 30)
    assert all(len(semana) == 7 for semana in semanas)
    outubro = semanas_do_mes(2026, 10)  # começa numa quinta: a primeira semana traz setembro
    assert outubro[0][0] == date(2026, 9, 27)


def test_mes_vizinho_vira_o_ano():
    assert mes_vizinho(2026, 12, +1) == (2027, 1)
    assert mes_vizinho(2026, 1, -1) == (2025, 12)
    assert mes_vizinho(2026, 11, +14) == (2028, 1)
    assert mes_vizinho(2026, 11, 0) == (2026, 11)


def test_rotulo_do_mes():
    assert rotulo_do_mes(2026, 3) == "março de 2026"
    assert rotulo_do_mes(2026, 11) == "novembro de 2026"


def test_celula_do_ponto_so_vale_dentro_do_quadro_dos_dias():
    assert celula_do_ponto(5, 5) is None  # o cabeçalho com as letras dos dias
    assert celula_do_ponto(-1, 60) is None
    assert celula_do_ponto(5, 30) == (0, 0)
    assert celula_do_ponto(7 * 36 - 1, 26 + 6 * 30 - 1) == (5, 6)
    assert celula_do_ponto(7 * 36 + 1, 60) is None
    assert celula_do_ponto(10, 26 + 6 * 30 + 1) is None


def test_o_angulo_conta_do_alto_no_sentido_horario():
    assert angulo_do_ponto(0, -10) == pytest.approx(0)
    assert angulo_do_ponto(10, 0) == pytest.approx(90)
    assert angulo_do_ponto(0, 10) == pytest.approx(180)
    assert angulo_do_ponto(-10, 0) == pytest.approx(270)


@pytest.mark.parametrize("hora", range(24))
def test_clicar_no_numero_de_cada_hora_escolhe_essa_hora(hora):
    x, y = ponto_da_hora(hora)
    assert hora_do_ponto(x - C, y - C) == hora


def test_os_aneis_separam_as_horas_da_manha_das_da_tarde():
    assert hora_do_ponto(0, -85) == 0  # em cima, fora: 00
    assert hora_do_ponto(0, -50) == 12  # em cima, dentro: 12
    assert hora_do_ponto(85, 0) == 3 and hora_do_ponto(50, 0) == 15
    assert hora_do_ponto(0, 85) == 6 and hora_do_ponto(0, 50) == 18
    assert hora_do_ponto(-85, 0) == 9 and hora_do_ponto(-50, 0) == 21


def test_um_clique_entre_dois_numeros_vai_para_o_mais_perto():
    x, y = sel.posicao_no_mostrador(0, 82)
    antes = hora_do_ponto(x - C + 14, y - C)  # 14° para a direita de 00
    assert antes == 0
    depois = hora_do_ponto(x - C + 40, y - C)  # já mais perto do 01
    assert depois == 1


@pytest.mark.parametrize("minuto", MINUTOS)
def test_clicar_em_cada_bloco_de_minutos_escolhe_esse_bloco(minuto):
    x, y = ponto_do_minuto(minuto)
    assert minuto_do_ponto(x - C, y - C) == minuto


def test_os_minutos_so_existem_de_15_em_15():
    assert {minuto_do_ponto(dx, dy) for dx in range(-90, 91, 6) for dy in range(-90, 91, 6)} == {
        0, 15, 30, 45,
    }  # fmt: skip
    assert minuto_do_ponto(0, -80) == 0 and minuto_do_ponto(80, 0) == 15
    assert minuto_do_ponto(0, 80) == 30 and minuto_do_ponto(-80, 0) == 45
    assert minuto_do_ponto(50, -50) in (0, 15)  # a diagonal cai num vizinho, nunca em 7 ou 22


# --- as janelas (as que abrem a janela levam `gui`: sem tela, são puladas) ----------------------
@pytest.fixture(scope="module")
def raiz():
    try:
        janela = criar_raiz()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    gc.collect()  # as variáveis do Tk têm de morrer antes da janela (senão avisam em outro teste)
    janela.destroy()


def _clique(x: float, y: float):
    return SimpleNamespace(x=x, y=y)


def _achar(pai, classe):
    for filho in pai.winfo_children():
        if isinstance(filho, classe):
            return filho
        dentro = _achar(filho, classe)
        if dentro is not None:
            return dentro
    return None


@pytest.fixture
def calendario(raiz):
    escolhidos: list[date] = []
    cancelou: list[int] = []
    tela = CalendarioMensal(
        raiz, escolhida=None, hoje=HOJE, ao_escolher=escolhidos.append,
        ao_cancelar=lambda: cancelou.append(1),
    )  # fmt: skip
    tela.pack()
    raiz.update()
    tela.escolhidos, tela.cancelou = escolhidos, cancelou  # type: ignore[attr-defined]
    yield tela
    tela.destroy()


@pytest.mark.gui
def test_o_calendario_abre_no_mes_de_hoje_e_mostra_o_nome_do_mes(calendario):
    assert calendario.mes_mostrado == (2026, 11)
    assert calendario.rotulo_do_mes.cget("text") == "novembro de 2026"


@pytest.mark.gui
def test_clicar_num_dia_escolhe_esse_dia(calendario):
    calendario._clicou(_clique(2.5 * 36, 26 + 1.5 * 30))  # 2ª linha, terça: 10/11
    assert calendario.escolhidos == [date(2026, 11, 10)]


@pytest.mark.gui
def test_dia_que_ja_passou_nao_se_escolhe_mas_hoje_sim(calendario):
    assert calendario.escolher(date(2026, 11, 2)) is False
    assert calendario.escolhidos == []
    calendario._clicou(_clique(2.5 * 36, 26 + 0.5 * 30))  # 1ª linha, terça: 03/11 (hoje)
    assert calendario.escolhidos == [HOJE]


@pytest.mark.gui
def test_clicar_fora_dos_dias_do_mes_nao_escolhe_nada(calendario):
    calendario._clicou(_clique(3.5 * 36, 26 + 4.5 * 30))  # 5ª linha, quarta: já é dezembro
    calendario._clicou(_clique(3.5 * 36, 26 + 5.5 * 30))  # uma 6ª linha que o mês não tem
    calendario._clicou(_clique(10, 10))  # as letras dos dias
    assert calendario.escolhidos == []


@pytest.mark.gui
def test_o_botao_hoje_escolhe_hoje(calendario):
    calendario.botao_hoje.invoke()
    assert calendario.escolhidos == [HOJE]


@pytest.mark.gui
def test_nao_da_para_voltar_antes_do_mes_de_hoje(calendario):
    assert str(calendario.botao_anterior.cget("state")) == "disabled"
    calendario.ir_para_o_mes(-1)
    assert calendario.mes_mostrado == (2026, 11)


@pytest.mark.gui
def test_avancar_e_voltar_entre_os_meses(calendario):
    calendario.botao_proximo.invoke()
    assert calendario.mes_mostrado == (2026, 12)
    assert calendario.rotulo_do_mes.cget("text") == "dezembro de 2026"
    assert str(calendario.botao_anterior.cget("state")) == "normal"
    calendario.ir_para_o_mes(+1)
    assert calendario.mes_mostrado == (2027, 1)
    calendario.ir_para_o_mes(-2)
    assert calendario.mes_mostrado == (2026, 11)
    calendario._clicou(_clique(0.5 * 36, 26 + 0.5 * 30))  # 01/11, domingo, já passou
    assert calendario.escolhidos == []


@pytest.mark.gui
def test_em_outro_mes_o_clique_vale_para_o_dia_daquele_mes(calendario):
    calendario.ir_para_o_mes(+1)  # dezembro de 2026 começa numa terça
    calendario._clicou(_clique(2.5 * 36, 26 + 0.5 * 30))
    assert calendario.escolhidos == [date(2026, 12, 1)]


@pytest.mark.gui
def test_o_calendario_abre_no_mes_da_data_escolhida(raiz):
    tela = CalendarioMensal(
        raiz, escolhida=date(2027, 2, 14), hoje=HOJE, ao_escolher=lambda _d: None
    )
    assert tela.mes_mostrado == (2027, 2)
    passada = CalendarioMensal(
        raiz, escolhida=date(2026, 5, 1), hoje=HOJE, ao_escolher=lambda _d: None
    )
    assert passada.mes_mostrado == (2026, 11)  # uma data que já passou não leva o mês para trás
    tela.destroy()
    passada.destroy()


@pytest.mark.gui
def test_o_cancelar_do_calendario_avisa(calendario):
    calendario.botao_cancelar.invoke()
    assert calendario.cancelou == [1]


@pytest.fixture
def relogio(raiz):
    escolhidos: list[tuple[int, int]] = []
    tela = RelogioDePonteiros(
        raiz, hora=18, minuto=30, ao_escolher=lambda h, m: escolhidos.append((h, m)),
        ao_cancelar=lambda: escolhidos.append((-1, -1)),
    )  # fmt: skip
    tela.pack()
    raiz.update()
    tela.escolhidos = escolhidos  # type: ignore[attr-defined]
    yield tela
    tela.destroy()


def _clicar_na_hora(relogio, hora: int) -> None:
    x, y = ponto_da_hora(hora)
    relogio._clicou(_clique(x, y))


def _clicar_nos_minutos(relogio, minuto: int) -> None:
    x, y = ponto_do_minuto(minuto)
    relogio._clicou(_clique(x, y))


@pytest.mark.gui
def test_o_relogio_comeca_pedindo_a_hora_e_mostra_o_que_ja_estava_escolhido(relogio):
    assert relogio.passo == "hora"
    assert relogio.botao_hora.cget("text") == "18" and relogio.botao_minuto.cget("text") == "30"
    assert relogio.instrucao.cget("text") == "Escolha a hora"


@pytest.mark.gui
def test_escolher_a_hora_passa_para_os_minutos_e_so_o_minuto_entrega(relogio):
    _clicar_na_hora(relogio, 9)
    assert relogio.passo == "minuto" and relogio.escolhidos == []
    assert relogio.botao_hora.cget("text") == "09"
    assert relogio.instrucao.cget("text") == "Escolha os minutos"
    _clicar_nos_minutos(relogio, 45)
    assert relogio.escolhidos == [(9, 45)]
    assert relogio.botao_minuto.cget("text") == "45"


@pytest.mark.gui
def test_hora_da_tarde_pelo_anel_de_dentro(relogio):
    _clicar_na_hora(relogio, 21)
    _clicar_nos_minutos(relogio, 0)
    assert relogio.escolhidos == [(21, 0)]


@pytest.mark.gui
def test_tocar_na_hora_do_alto_volta_a_escolher_a_hora(relogio):
    _clicar_na_hora(relogio, 7)
    relogio.botao_hora.invoke()
    assert relogio.passo == "hora"
    _clicar_na_hora(relogio, 8)
    _clicar_nos_minutos(relogio, 15)
    assert relogio.escolhidos == [(8, 15)]


@pytest.mark.gui
def test_tocar_nos_minutos_do_alto_vai_direto_para_os_minutos(relogio):
    relogio.botao_minuto.invoke()
    assert relogio.passo == "minuto"
    _clicar_nos_minutos(relogio, 30)
    assert relogio.escolhidos == [(18, 30)]  # a hora que já estava vale


@pytest.mark.gui
def test_clique_no_miolo_ou_fora_do_mostrador_nao_escolhe_nada(relogio):
    relogio._clicou(_clique(C, C))
    relogio._clicou(_clique(C + 5, C - 5))
    relogio._clicou(_clique(2, 2))  # o canto do quadro, fora do círculo
    assert relogio.passo == "hora" and relogio.escolhidos == []


@pytest.mark.gui
def test_so_aceita_minutos_de_15_em_15(relogio):
    relogio.escolher_minuto(7)
    assert relogio.escolhidos == []


@pytest.mark.gui
def test_minuto_que_nao_e_de_15_em_15_chega_sem_marca(raiz):
    tela = RelogioDePonteiros(raiz, hora=10, minuto=7, ao_escolher=lambda _h, _m: None)
    assert tela.botao_minuto.cget("text") == "--" and tela.escolha == (10, None)
    tela.destroy()


@pytest.mark.gui
def test_o_cancelar_do_relogio_avisa(relogio):
    relogio.botao_cancelar.invoke()
    assert relogio.escolhidos == [(-1, -1)]


@pytest.mark.gui
def test_passar_o_mouse_acende_o_valor_sem_escolher(relogio):
    x, y = ponto_da_hora(3)
    relogio._passou(_clique(x, y))
    assert relogio._sobre == 3 and relogio.escolhidos == []
    relogio._saiu()
    assert relogio._sobre is None


@pytest.mark.gui
def test_o_texto_da_dica_acompanha_o_passo(relogio):
    antes = relogio._texto_da_dica()
    relogio.ir_para_os_minutos()
    assert relogio._texto_da_dica() != antes and "15" in relogio._texto_da_dica()


# --- os botões e a janelinha ---------------------------------------------------------------------
@pytest.mark.gui
def test_o_botao_do_calendario_abre_no_dia_escrito_e_devolve_o_dia_escolhido(raiz):
    var = tkinter.StringVar(master=raiz, value="10/12/2026")
    escolhidos: list[date] = []
    botao = BotaoDoCalendario(
        raiz, var_data=var, ao_escolher=escolhidos.append, relogio=Relogio(AGORA)
    )
    botao.pack()
    botao.abrir()
    calendario = _achar(botao.janela_aberta, CalendarioMensal)
    assert calendario is not None and calendario.mes_mostrado == (2026, 12)
    calendario.escolher(date(2026, 12, 20))
    assert escolhidos == [date(2026, 12, 20)]
    assert not botao.janela_aberta.winfo_exists()  # a janelinha fechou sozinha
    botao.destroy()


@pytest.mark.gui
def test_o_calendario_sem_data_escrita_abre_em_hoje(raiz):
    var = tkinter.StringVar(master=raiz, value="")
    botao = BotaoDoCalendario(
        raiz, var_data=var, ao_escolher=lambda _d: None, relogio=Relogio(AGORA)
    )
    botao.abrir()
    assert _achar(botao.janela_aberta, CalendarioMensal).mes_mostrado == (2026, 11)
    botao.janela_aberta.fechar()
    botao.destroy()


@pytest.mark.gui
def test_o_botao_do_relogio_abre_na_hora_escrita_e_devolve_hora_e_minuto(raiz):
    var = tkinter.StringVar(master=raiz, value="09:15")
    escolhidos: list[tuple[int, int]] = []
    botao = BotaoDoRelogio(raiz, var_hora=var, ao_escolher=lambda h, m: escolhidos.append((h, m)))
    botao.abrir()
    relogio = _achar(botao.janela_aberta, RelogioDePonteiros)
    assert relogio is not None and relogio.escolha == (9, 15)
    _clicar_na_hora(relogio, 14)
    _clicar_nos_minutos(relogio, 45)
    assert escolhidos == [(14, 45)]
    assert not botao.janela_aberta.winfo_exists()
    botao.destroy()


@pytest.mark.gui
def test_o_relogio_sem_hora_escrita_abre_na_hora_padrao(raiz):
    var = tkinter.StringVar(master=raiz, value="lixo")
    botao = BotaoDoRelogio(raiz, var_hora=var, ao_escolher=lambda _h, _m: None, padrao=(18, 0))
    botao.abrir()
    assert _achar(botao.janela_aberta, RelogioDePonteiros).escolha == (18, 0)
    botao.janela_aberta.fechar()
    botao.destroy()


@pytest.mark.gui
def test_abrir_duas_vezes_nao_cria_duas_janelinhas_e_fechar_deixa_abrir_de_novo(raiz):
    botao = BotaoDoRelogio(raiz, var_hora=tkinter.StringVar(master=raiz), ao_escolher=print)
    botao.abrir()
    primeira = botao.janela_aberta
    botao.abrir()
    assert botao.janela_aberta is primeira
    primeira.fechar()
    assert not primeira.winfo_exists()
    botao.abrir()
    assert botao.janela_aberta is not primeira and botao.janela_aberta.winfo_exists()
    botao.janela_aberta.fechar()
    botao.destroy()


@pytest.mark.gui
def test_cancelar_fecha_sem_devolver_nada(raiz):
    escolhidos: list = []
    botao = BotaoDoCalendario(
        raiz, var_data=tkinter.StringVar(master=raiz), ao_escolher=escolhidos.append,
        relogio=Relogio(AGORA),
    )  # fmt: skip
    botao.abrir()
    _achar(botao.janela_aberta, CalendarioMensal).botao_cancelar.invoke()
    assert escolhidos == [] and not botao.janela_aberta.winfo_exists()
    botao.destroy()


@pytest.mark.gui
def test_todo_controle_dos_seletores_tem_dica(raiz, calendario, relogio):
    def dica(widget) -> str:
        objeto = getattr(widget, "_dica_spurgeon", None)
        return objeto.texto if objeto is not None else ""

    var = tkinter.StringVar(master=raiz)
    botoes = (
        BotaoDoCalendario(raiz, var_data=var, ao_escolher=print),
        BotaoDoRelogio(raiz, var_hora=var, ao_escolher=print),
    )
    controles = (
        *botoes, calendario._tela, calendario.botao_anterior, calendario.botao_proximo,
        calendario.botao_hoje, calendario.botao_cancelar, relogio._tela, relogio.botao_hora,
        relogio.botao_minuto, relogio.botao_cancelar,
    )  # fmt: skip
    for controle in controles:
        assert len(dica(controle)) > 10, controle
    for botao in botoes:
        botao.destroy()


def test_as_janelinhas_herdam_do_toplevel_do_customtkinter():
    assert issubclass(sel.JanelaDeSeletor, ctk.CTkToplevel)
