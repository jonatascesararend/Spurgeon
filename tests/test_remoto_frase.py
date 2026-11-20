"""Corrigir uma frase da legenda na própria audição (a legenda que acompanha o áudio no celular)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import audicao, dados, edicao, sessao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from spurgeon.shorts.edicao import LimiteInvalido
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto
from tests.test_remoto_audicao import _mudar_corte, _transcrever


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _frases(pastas, id_=7) -> list[str]:
    corte = next(c for c in dados.carregar_cortes(pastas) if c.id == id_)
    legenda = audicao.legenda_do_short(audicao.carregar_palavras(pastas), corte)
    return [" ".join(p.texto for p in f) for f in legenda.frases]


def test_troca_so_a_frase_escolhida(pastas):
    antes = _frases(pastas)
    assert len(antes) > 2
    segunda = antes[1].split()
    nova = " ".join(["Deus", *segunda[1:]])
    edicao.corrigir_frase(pastas, 7, 1, antes[1], nova)
    depois = _frases(pastas)
    assert depois[1] == nova and depois[0] == antes[0] and depois[2:] == antes[2:]
    corte = next(c for c in dados.carregar_cortes(pastas) if c.id == 7)
    assert "Deus" in corte.legenda


def test_frase_que_mudou_no_meio_tempo_e_recusada(pastas):
    antes = _frases(pastas)
    with pytest.raises(LimiteInvalido, match="mudou"):
        edicao.corrigir_frase(pastas, 7, 1, "texto que o celular não via", "x")
    with pytest.raises(LimiteInvalido, match="mudou"):
        edicao.corrigir_frase(pastas, 7, 999, antes[0], "x")


def test_com_salto_no_meio_as_frases_continuam_certas(pastas):
    _mudar_corte(pastas, 7, saltos=[[110.0, 115.0]])
    antes = _frases(pastas)
    ultima = len(antes) - 1
    nova = antes[ultima].replace(antes[ultima].split()[0], "Cristo", 1)
    edicao.corrigir_frase(pastas, 7, ultima, antes[ultima], nova)
    depois = _frases(pastas)
    assert depois[ultima] == nova and depois[:ultima] == antes[:ultima]
    assert not any(f"p{n}" in " ".join(depois).split() for n in range(221, 229))  # o salto


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
    antes = _frases(pastas)
    url = f"/api/projetos/{NOME_DO_PROJETO}/shorts/7/legenda"
    nova = antes[0].replace("p200", "Graça", 1)
    ok = cliente.post(url, json={"frase": 0, "antes": antes[0], "texto": nova})
    assert ok.status_code == 200 and ok.json()["legenda"].startswith("Graça p201")
    velha = cliente.post(url, json={"frase": 0, "antes": antes[0], "texto": "outra"})
    assert velha.status_code == 422 and "mudou" in velha.json()["erro"]
