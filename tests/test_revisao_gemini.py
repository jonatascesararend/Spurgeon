"""A revisão de um Short pelo Gemini: o parecer, a gravação no corte, o celular e o Studio do PC.

Nada aqui fala com a internet: `gemini.responder` é trocado por uma resposta pronta.
"""

# ruff: noqa: F811  (os fixtures `pastas`, `fila` e `cliente` vêm de test_remoto_edicao)

from __future__ import annotations

import json
from datetime import datetime

import pytest

from spurgeon.shorts import gemini, revisao_gemini
from spurgeon.shorts.dados import Corte
from tests.test_remoto_edicao import (  # noqa: F401
    BASE,
    _corte,
    cliente,
    fila,
    pastas,
)

CONFIG = gemini.Configuracao("AIzaTeste")
TEXTO = "Deus é fiel mesmo quando nós somos infiéis, e a graça dele nos sustenta até o fim."


def _parecer(**mudar) -> str:
    dados = {
        "veredito": "ajustar",
        "nota": 7,
        "comentario": "A ideia é boa, mas o título promete mais do que o trecho entrega.",
        "corte_completo": True,
        "titulo_combina": False,
        "descricao_combina": True,
        "titulo_sugerido": "Deus continua fiel quando falhamos",
        "descricao_sugerida": "",
    }
    dados.update(mudar)
    return json.dumps(dados)


@pytest.fixture
def responder(monkeypatch):
    pedidos = []
    resposta = {"bruto": _parecer(), "modelo": "gemini-flash-latest"}

    def falso(config, texto, *, sistema="", esquema=None, **_):
        pedidos.append({"config": config, "texto": texto, "sistema": sistema, "esquema": esquema})
        if isinstance(resposta["bruto"], Exception):
            raise resposta["bruto"]
        return resposta["bruto"], resposta["modelo"]

    monkeypatch.setattr(gemini, "responder", falso)
    falso.pedidos = pedidos  # type: ignore[attr-defined]
    falso.resposta = resposta  # type: ignore[attr-defined]
    return falso


def _um_corte(**mudar) -> Corte:
    corte = Corte(
        id=1, inicio=10, fim=40, texto=TEXTO, categoria="exortacao", nota=80, detalhe={},
        titulo="O segredo que ninguém te contou", descricao="Sobre a fé.",
    )  # fmt: skip
    for nome, valor in mudar.items():
        setattr(corte, nome, valor)
    return corte


# --- o parecer -------------------------------------------------------------------------------


def test_revisar_manda_o_texto_e_le_o_parecer(responder):
    corte = _um_corte()
    agora = datetime(2026, 11, 23, 10, 30)
    r = revisao_gemini.revisar(CONFIG, corte, TEXTO, sermao="A fidelidade de Deus", agora=agora)
    pedido = responder.pedidos[0]
    assert TEXTO in pedido["texto"] and "O segredo que ninguém te contou" in pedido["texto"]
    assert "A fidelidade de Deus" in pedido["texto"] and pedido["esquema"] is revisao_gemini.ESQUEMA
    assert (r.veredito, r.nota, r.rotulo) == ("ajustar", 7, "Bom, com ajustes")
    assert r.titulo_sugerido == "Deus continua fiel quando falhamos" and not r.titulo_combina
    assert r.quando == "2026-11-23T10:30" and r.modelo == "gemini-flash-latest"
    assert r.titulo_revisado == corte.titulo and not r.desatualizada(corte)


def test_parecer_com_nota_fora_da_faixa_e_sugestao_igual_a_atual(responder):
    corte = _um_corte()
    responder.resposta["bruto"] = _parecer(nota=14, titulo_sugerido=f"  {corte.titulo} ")
    r = revisao_gemini.revisar(CONFIG, corte, TEXTO)
    assert r.nota == 10 and r.titulo_sugerido == ""  # sugerir o mesmo título não é sugestão


@pytest.mark.parametrize("bruto", ["isto não é JSON", _parecer(veredito="ótimo"), "[1, 2]"])
def test_parecer_fora_do_formato_vira_erro_do_gemini(responder, bruto):
    responder.resposta["bruto"] = bruto
    with pytest.raises(gemini.ErroDoGemini):
        revisao_gemini.revisar(CONFIG, _um_corte(), TEXTO)


def test_short_sem_texto_nem_chega_ao_gemini(responder):
    with pytest.raises(revisao_gemini.TextoCurto):
        revisao_gemini.revisar(CONFIG, _um_corte(), "Amém. Glória a Deus.")
    assert responder.pedidos == []


def test_mudar_o_titulo_depois_deixa_a_revisao_desatualizada_mas_usar_a_sugestao_nao(responder):
    corte = _um_corte()
    r = revisao_gemini.revisar(CONFIG, corte, TEXTO)
    corte.titulo = r.titulo_sugerido
    assert not r.desatualizada(corte)
    corte.titulo = "Outro título qualquer"
    assert r.desatualizada(corte)


def test_revisao_gravada_volta_do_arquivo_e_corte_antigo_nao_tem(pastas, responder):
    assert _corte(pastas, 7).revisao_gemini == {}  # o cortes_shorts.json de antes não tem o campo
    assert revisao_gemini.da_revisao(_corte(pastas, 7)) is None
    r = revisao_gemini.revisar(CONFIG, _corte(pastas, 7), TEXTO)
    assert revisao_gemini.gravar(pastas.cortes_shorts, 7, r)
    assert revisao_gemini.da_revisao(_corte(pastas, 7)) == r
    assert _corte(pastas, 3).revisao_gemini == {}
    assert revisao_gemini.gravar(pastas.cortes_shorts, 7, None)
    assert revisao_gemini.da_revisao(_corte(pastas, 7)) is None


def test_quando_legivel():
    assert revisao_gemini.quando_legivel("2026-11-23T10:30") == "23/11 às 10:30"
    assert revisao_gemini.quando_legivel("ontem") == "ontem"


# --- pelo celular ----------------------------------------------------------------------------


def test_sem_chave_o_celular_explica_onde_colar(cliente, responder):
    cliente.post(f"{BASE}/sessao")
    resposta = cliente.post(f"{BASE}/shorts/7/gemini")
    assert resposta.status_code == 409 and "Integrações" in resposta.json()["erro"]
    assert responder.pedidos == []


def test_celular_pede_a_revisao_e_o_short_volta_marcado(cliente, responder):
    cliente.ctx.gemini = lambda: CONFIG
    assert cliente.post(f"{BASE}/shorts/7/gemini").json()["erro"] == "sem_trava"
    cliente.post(f"{BASE}/sessao")
    resposta = cliente.post(f"{BASE}/shorts/7/gemini")
    assert resposta.status_code == 200
    parecer = resposta.json()["gemini"]
    assert parecer["veredito"] == "ajustar" and parecer["rotulo"] == "Bom, com ajustes"
    assert parecer["quando_rotulo"] and parecer["desatualizada"] is False
    lista = {s["id"]: s for s in cliente.get(f"{BASE}/shorts").json()}
    assert lista[7]["gemini"]["nota"] == 7 and lista[3]["gemini"] is None
    assert revisao_gemini.da_revisao(_corte(cliente.pastas, 7)).veredito == "ajustar"
    # usar o título sugerido pelo caminho de sempre (o /textos) não deixa a revisão desatualizada
    novo = cliente.post(f"{BASE}/shorts/7/textos", json={"titulo": parecer["titulo_sugerido"]})
    assert novo.json()["titulo"] == parecer["titulo_sugerido"]
    assert novo.json()["gemini"]["desatualizada"] is False


def test_celular_mostra_o_erro_do_gemini(cliente, responder):
    cliente.ctx.gemini = lambda: CONFIG
    cliente.post(f"{BASE}/sessao")
    responder.resposta["bruto"] = gemini.ErroDoGemini("O Gemini está com alta demanda agora")
    resposta = cliente.post(f"{BASE}/shorts/7/gemini")
    assert resposta.status_code == 502 and "alta demanda" in resposta.json()["erro"]
    assert cliente.post(f"{BASE}/shorts/999/gemini").status_code == 404
    assert revisao_gemini.da_revisao(_corte(cliente.pastas, 7)) is None
