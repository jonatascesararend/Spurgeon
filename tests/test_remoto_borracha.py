"""O rascunho de início, fim e borracha do celular, gravado de uma vez (`definir_palavras`)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import dados, edicao, sessao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from spurgeon.shorts.edicao import LimiteInvalido
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto
from tests.test_remoto_audicao import _transcrever


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _corte(pastas, id_=7):
    return next(c for c in dados.carregar_cortes(pastas) if c.id == id_)


def test_borracha_no_meio_vira_salto(pastas):
    palavras = [n for n in range(200, 280) if not 220 <= n <= 229]  # tira p220 a p229
    corte = edicao.definir_palavras(pastas, 7, palavras)
    assert len(corte.saltos) == 1
    de, ate = corte.saltos[0]
    assert 109.9 < de < 110.1 and 114.9 < ate < 115.1  # entre o fim de p219 e o começo de p230
    assert corte.duracao == pytest.approx(35.0, abs=0.2)
    assert _corte(pastas).saltos == corte.saltos


def test_inicio_fim_e_borracha_de_uma_vez(pastas):
    palavras = [*range(190, 210), *range(215, 290)]  # começa antes, termina depois, corta 5
    corte = edicao.definir_palavras(pastas, 7, palavras)
    assert corte.texto.split()[0] == "p190" and corte.texto.split()[-1] == "p289"
    assert len(corte.saltos) == 1 and corte.editado


def test_contexto_traz_o_fim_de_cada_palavra(pastas):
    ed = edicao.para_editar(pastas, 7)
    assert all("f" in w and w["f"] > w["i"] for w in ed["contexto"])


def test_rascunho_invalido_nao_grava(pastas):
    antes = _corte(pastas)
    with pytest.raises(LimiteInvalido, match="nenhuma"):
        edicao.definir_palavras(pastas, 7, [])
    with pytest.raises(LimiteInvalido, match="menos de 10"):
        edicao.definir_palavras(pastas, 7, range(200, 205))
    with pytest.raises(LimiteInvalido):
        edicao.definir_palavras(pastas, 7, [200, 10**6])
    assert _corte(pastas) == antes


def test_pelo_servidor(pastas, tmp_path):
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    avisos = Avisos(cofre, TransporteFalso())
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]),
        pastas.raiz.parent,
        avisos,
        "x",
        gravar_config=lambda _c: None,
    )
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    sessao.tomar(pastas)
    url = f"/api/projetos/{NOME_DO_PROJETO}/shorts/7/pontas"
    palavras = [n for n in range(200, 280) if n != 240]
    resposta = cliente.post(url, json={"indices": palavras})
    assert resposta.status_code == 200 and resposta.json()["duracao"] < 40.0
    curto = cliente.post(url, json={"indices": [200, 201]})
    assert curto.status_code == 422
