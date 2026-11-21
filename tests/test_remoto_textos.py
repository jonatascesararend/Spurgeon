"""Título, descrição e hashtags editados pelo celular (`edicao.mudar_textos`, rota `/textos`)."""

from __future__ import annotations

import json

import pytest

from spurgeon.remoto import edicao
from spurgeon.shorts.edicao import LimiteInvalido
from tests.test_remoto import NOME_DO_PROJETO
from tests.test_remoto_edicao import _corte, cliente, fila, pastas  # noqa: F401 (fixtures)

BASE = f"/api/projetos/{NOME_DO_PROJETO}"


def _registro(projeto) -> dict:
    return json.loads(projeto.shorts_renderizados.read_text("utf-8"))


def test_titulo_novo_renomeia_o_short_ja_renderizado(pastas):  # noqa: F811
    corte = edicao.mudar_textos(
        pastas, 3, titulo="  Um   título   novo ", descricao=" Texto.\nOutra linha. ",
        hashtags="fé, #graça  fé",
    )  # fmt: skip
    assert corte.titulo == "Um título novo" and corte.slug == "um_titulo_novo"
    assert corte.descricao == "Texto.\nOutra linha." and corte.hashtags == ["#fé", "#graça"]
    salvo = _corte(pastas, 3)
    assert (salvo.titulo, salvo.slug, salvo.hashtags) == (corte.titulo, corte.slug, corte.hashtags)
    arquivo = _registro(pastas)["3"]["arquivo"]
    assert arquivo.startswith("postados/") and "um_titulo_novo" in arquivo  # continua postado
    assert (pastas.shorts / arquivo).is_file()
    assert not (pastas.shorts / "postados" / "short_01_titulo_3.mp4").exists()
    texto = (pastas.shorts / arquivo).with_suffix(".txt").read_text("utf-8")
    assert "Título: Um título novo" in texto and "#fé #graça" in texto


def test_com_processamento_rodando_so_o_json_muda(pastas):  # noqa: F811
    edicao.mudar_textos(pastas, 3, titulo="Outro título", mexer_em_arquivos=False)
    assert _corte(pastas, 3).titulo == "Outro título"
    assert _registro(pastas)["3"]["arquivo"] == "postados/short_01_titulo_3.mp4"


def test_titulo_vazio_ou_comprido_demais_e_recusado(pastas):  # noqa: F811
    with pytest.raises(LimiteInvalido):
        edicao.mudar_textos(pastas, 7, titulo="   ")
    with pytest.raises(LimiteInvalido):
        edicao.mudar_textos(pastas, 7, titulo="x" * 101)
    assert _corte(pastas, 7).titulo == "Título 7"
    with pytest.raises(edicao.CorteNaoEncontrado):
        edicao.mudar_textos(pastas, 999, descricao="x")


def test_pelo_servidor_com_a_trava(cliente):  # noqa: F811
    assert cliente.post(f"{BASE}/shorts/7/textos", json={"titulo": "Novo"}).status_code == 409
    cliente.post(f"{BASE}/sessao")
    resposta = cliente.post(f"{BASE}/shorts/7/textos", json={"descricao": "Uma descrição."})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["descricao"] == "Uma descrição." and corpo["titulo"] == "Título 7"
    hashtags = cliente.post(f"{BASE}/shorts/7/textos", json={"hashtags": "#shorts fé"}).json()
    assert hashtags["hashtags"] == ["#shorts", "#fé"]
    vazio = cliente.post(f"{BASE}/shorts/7/textos", json={"titulo": ""})
    assert vazio.status_code == 422 and "vazio" in vazio.json()["erro"]
    assert cliente.post(f"{BASE}/shorts/7/textos", json={"hashtags": 5}).status_code == 400
