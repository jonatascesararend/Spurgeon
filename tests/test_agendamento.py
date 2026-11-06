"""O horário agendado de um Short: ler, mostrar e virar "já postado" quando a hora passa."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from spurgeon.shorts import agendamento as ag

AGORA = datetime(2026, 11, 2, 12, 0).astimezone()


def _corte(agendado_para: str = "", **extra):
    base = {"agendado_para": agendado_para, "postado": False, "postado_em": ""}
    return SimpleNamespace(**{**base, **extra})


def test_guardar_e_ler_dao_o_mesmo_horario_na_hora_do_computador():
    quando = datetime(2026, 11, 5, 18, 30).astimezone()
    texto = ag.guardar_horario(quando)
    assert texto == "2026-11-05T18:30"
    assert ag.ler_horario(texto) == quando
    assert ag.ler_horario("") is None and ag.ler_horario("amanhã") is None


@pytest.mark.parametrize(
    ("data", "hora", "esperado"),
    [
        ("05/11/2026", "18:30", (2026, 11, 5, 18, 30)),
        ("5/11/26", "18h", (2026, 11, 5, 18, 0)),
        ("05.11.2026", "9", (2026, 11, 5, 9, 0)),
        ("05-11-2026", "18h30", (2026, 11, 5, 18, 30)),
        ("  05/11/2026 ", " 07:05 ", (2026, 11, 5, 7, 5)),
    ],
)
def test_interpretar_aceita_os_jeitos_de_escrever(data, hora, esperado):
    achado = ag.interpretar(data, hora)
    assert achado is not None
    assert (achado.year, achado.month, achado.day, achado.hour, achado.minute) == esperado


def test_ler_data_aceita_so_a_data_e_completa_o_ano():
    hoje = date(2026, 11, 3)
    assert ag.ler_data("05/11/2026", hoje=hoje) == date(2026, 11, 5)
    assert ag.ler_data("5.11.26", hoje=hoje) == date(2026, 11, 5)
    assert ag.ler_data("05/11", hoje=hoje) == date(2026, 11, 5)  # ainda não passou: este ano
    assert ag.ler_data("01/11", hoje=hoje) == date(2027, 11, 1)  # já passou: o próximo ano
    assert ag.ler_data("31/02/2026", hoje=hoje) is None and ag.ler_data("", hoje=hoje) is None
    assert ag.ler_data("amanhã", hoje=hoje) is None


def test_ler_hora_aceita_so_a_hora():
    assert ag.ler_hora("18:30") == (18, 30) and ag.ler_hora("18h") == (18, 0)
    assert ag.ler_hora("9") == (9, 0) and ag.ler_hora(" 07:05 ") == (7, 5)
    assert ag.ler_hora("24:00") is None and ag.ler_hora("12:60") is None
    assert ag.ler_hora("") is None and ag.ler_hora("tarde") is None


@pytest.mark.parametrize(
    ("data", "hora"),
    [("31/02/2026", "10:00"), ("05/11/2026", "25:00"), ("05/11/2026", "10:75"), ("", "10:00"),
     ("05/11/2026", ""), ("ontem", "10:00"), ("05/11/2026", "dez")],
)  # fmt: skip
def test_interpretar_recusa_o_que_nao_faz_sentido(data, hora):
    assert ag.interpretar(data, hora) is None


def test_data_sem_ano_vale_o_proximo_em_que_ainda_nao_passou():
    assert ag.interpretar("05/11", "10:00", hoje=date(2026, 11, 2)).year == 2026
    assert ag.interpretar("01/11", "10:00", hoje=date(2026, 11, 2)).year == 2027
    assert ag.interpretar("02/11", "10:00", hoje=date(2026, 11, 2)).year == 2026  # hoje ainda vale


def test_rotulo_do_horario():
    assert ag.rotulo_do_horario(datetime(2026, 11, 5, 8, 5).astimezone()) == "05/11/2026 às 08:05"


@pytest.mark.parametrize(
    ("delta", "esperado"),
    [
        (timedelta(seconds=20), "agora"),
        (timedelta(minutes=25), "em 25 min"),
        (timedelta(hours=3), "em 3 h"),
        (timedelta(hours=3, minutes=10), "em 3 h e 10 min"),
        (timedelta(days=1), "em 1 dia"),
        (timedelta(days=2, hours=3), "em 2 dias e 3 h"),
        (-timedelta(minutes=10), "há 10 min"),
        (-timedelta(days=3), "há 3 dias"),
    ],
)
def test_faltam_diz_quanto_tempo_falta_ou_passou(delta, esperado):
    assert ag.faltam(AGORA + delta, AGORA) == esperado


def test_vencimento_so_depois_da_hora_marcada():
    corte = _corte(ag.guardar_horario(AGORA + timedelta(hours=1)))
    assert not ag.venceu(corte, AGORA)
    assert not ag.aplicar_vencimento(corte, AGORA) and corte.postado is False
    assert corte.agendado_para  # nada mudou

    depois = AGORA + timedelta(hours=1, minutes=1)
    assert ag.venceu(corte, depois)
    assert ag.aplicar_vencimento(corte, depois)
    assert corte.postado is True and corte.agendado_para == ""
    assert corte.postado_em == ag.guardar_horario(
        AGORA + timedelta(hours=1)
    )  # o dia e a hora marcados


def test_vencimento_de_dias_atras_usa_a_data_marcada_nao_a_de_hoje():
    corte = _corte("2026-10-20T09:00")
    assert ag.aplicar_vencimento(corte, AGORA)
    assert corte.postado_em == "2026-10-20T09:00"


# --- o "postado em": dia e hora hoje, só o dia nos projetos de antes ----------------------------
def test_postado_em_com_hora_e_sem_hora():
    assert ag.ler_postado_em("2026-11-04T18:30") == (date(2026, 11, 4), time(18, 30))
    assert ag.ler_postado_em("2026-11-02") == (date(2026, 11, 2), None)  # projeto antigo
    assert ag.rotulo_do_postado("2026-11-04T18:30") == "04/11/2026 às 18:30"
    assert ag.rotulo_do_postado("2026-11-02") == "02/11/2026"


@pytest.mark.parametrize("texto", ["", "ontem", "2026-13-40", "2026-11-04T25:00"])
def test_postado_em_ilegivel_nao_quebra(texto):
    assert ag.ler_postado_em(texto) is None and ag.rotulo_do_postado(texto) == ""


def test_sem_agendamento_ou_ilegivel_nao_vence_nunca():
    assert not ag.aplicar_vencimento(_corte(""), AGORA)
    assert not ag.aplicar_vencimento(_corte("lixo"), AGORA)
    postado = _corte("", postado=True, postado_em="2026-10-01")
    assert not ag.aplicar_vencimento(postado, AGORA) and postado.postado_em == "2026-10-01"
