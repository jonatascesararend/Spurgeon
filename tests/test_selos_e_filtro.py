"""O selo de postado/agendado e o filtro da lista do Studio Shorts (as regras, sem janela)."""

from __future__ import annotations

from datetime import datetime

from spurgeon.shorts.dados import Corte
from spurgeon.ui.studio_shorts import (
    FILTRO_AGENDADOS,
    FILTRO_POSTADOS,
    FILTRO_SEM_POSTAR,
    FILTRO_TODOS,
    estado_de_publicacao,
    passa_no_filtro,
    selo_de_publicacao,
    texto_do_postado,
)

AGORA = datetime(2026, 11, 3, 12, 0).astimezone()


def _corte(**mudancas) -> Corte:
    base = {
        "id": 1, "inicio": 0.0, "fim": 50.0, "texto": "um trecho", "categoria": "outra",
        "nota": 80.0, "detalhe": {},
    }  # fmt: skip
    return Corte(**{**base, **mudancas})


def test_sem_postar_nem_agendar_nao_tem_selo():
    assert selo_de_publicacao(_corte(), AGORA) is None
    assert estado_de_publicacao(_corte()) == ""


def test_selo_do_postado_traz_o_dia_e_a_hora():
    texto, cor = selo_de_publicacao(_corte(postado=True, postado_em="2026-11-02T09:15"), AGORA)
    assert texto == "✔ Postado 02/11 às 09:15" and cor.startswith("#")


def test_selo_de_outro_ano_mostra_o_ano():
    texto, _ = selo_de_publicacao(_corte(postado=True, postado_em="2025-12-31T23:59"), AGORA)
    assert texto == "✔ Postado 31/12/2025 às 23:59"


def test_selo_de_projeto_antigo_so_tem_o_dia_e_sem_data_so_diz_postado():
    antigo, _ = selo_de_publicacao(_corte(postado=True, postado_em="2026-11-02"), AGORA)
    assert antigo == "✔ Postado 02/11"
    sem_data, _ = selo_de_publicacao(_corte(postado=True, postado_em=""), AGORA)
    assert sem_data == "✔ Postado"


def test_selo_do_agendado_traz_o_horario_marcado():
    texto, cor = selo_de_publicacao(_corte(agendado_para="2026-11-07T19:00"), AGORA)
    assert texto == "📅 Agendado 07/11 às 19:00"
    assert cor != selo_de_publicacao(_corte(postado=True), AGORA)[1]  # cores diferentes


def test_postado_vale_mais_que_agendado_e_horario_ilegivel_nao_conta():
    os_dois = _corte(postado=True, postado_em="2026-11-02T09:15", agendado_para="2026-11-07T19:00")
    assert estado_de_publicacao(os_dois) == "postado"
    assert selo_de_publicacao(os_dois, AGORA)[0].startswith("✔ Postado")
    assert estado_de_publicacao(_corte(agendado_para="lixo")) == ""
    assert selo_de_publicacao(_corte(agendado_para="lixo"), AGORA) is None


def test_o_filtro_separa_postados_agendados_e_o_que_falta():
    postado = _corte(postado=True, postado_em="2026-11-02T09:15")
    agendado = _corte(agendado_para="2026-11-07T19:00")
    livre = _corte()
    esperado = {
        FILTRO_TODOS: (True, True, True),
        FILTRO_POSTADOS: (True, False, False),
        FILTRO_AGENDADOS: (False, True, False),
        FILTRO_SEM_POSTAR: (False, False, True),
    }
    for filtro, achado in esperado.items():
        assert tuple(passa_no_filtro(c, filtro) for c in (postado, agendado, livre)) == achado


def test_texto_do_postado_diz_o_dia_e_a_hora_e_o_adiamento():
    postado = _corte(postado=True, postado_em="2026-11-02T09:15")
    texto = texto_do_postado(postado, tem_arquivo=True, alterado=False)
    assert "Postado em 02/11/2026 às 09:15" in texto
    adiado = texto_do_postado(postado, tem_arquivo=True, alterado=False, adiado=True)
    assert "quando o processamento terminar" in adiado
    antigo = _corte(postado=True, postado_em="2026-11-02")
    assert "Postado em 02/11/2026." in texto_do_postado(antigo, tem_arquivo=True, alterado=False)
