"""Controle remoto, Fase R3 (Remoto.md): o Shorts livre pelo celular. A IA é trocada por uma função
de mentira; nenhum teste abre o Ollama nem toca a rede."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from spurgeon.core.estado import Estado, salvar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import audicao, dados, livre, sessao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.cli import avisar_da_criacao
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from spurgeon.shorts.dados import ORIGEM_MANUAL, Corte
from spurgeon.shorts.livre import Trecho
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto
from tests.test_remoto_audicao import _rodar_de_mentira, _transcrever

BASE = f"/api/projetos/{NOME_DO_PROJETO}/livre"


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _estado(pastas) -> Estado:
    return Estado.model_validate_json(pastas.estado.read_text("utf-8"))


def _corte_criado(pastas, texto, trecho, andamento) -> Corte:
    """O que `gerar_corte_livre` devolveria, sem a IA."""
    andamento("A IA está avaliando o trecho…")
    base = Corte(
        id=1_000_000, inicio=0.0, fim=0.0, texto="", categoria="graca", nota=77.0, detalhe={},
        titulo="Um título novo", slug="um_titulo_novo", origem=ORIGEM_MANUAL,
    )  # fmt: skip
    return replace(livre.corte_de_previa(texto, trecho), **{
        k: getattr(base, k) for k in ("id", "categoria", "nota", "titulo", "slug", "origem")
    })  # fmt: skip


# --- o texto e o trecho ---------------------------------------------------------------------


def test_texto_respeita_a_janela_do_video(pastas):
    inteiro = livre.carregar_texto(pastas)
    assert len(inteiro.palavras) == 800 and inteiro.frases
    estado = _estado(pastas)
    estado.opcoes.video_inicio_s, estado.opcoes.video_fim_s = 50.0, 100.0
    salvar_estado(pastas.raiz, estado)
    janela = livre.carregar_texto(pastas)
    assert janela.palavras[0].texto == "p100" and janela.palavras[-1].texto == "p199"


def test_texto_para_o_celular_marca_as_palavras_dos_shorts(pastas):
    texto = livre.carregar_texto(pastas)
    resposta = livre.texto_para_json(pastas, texto)
    palavras = [p for f in resposta["frases"] for p in f["palavras"]]
    assert len(palavras) == 800
    por_texto = {t: sid for t, sid in palavras}
    assert por_texto["p120"] == 3 and por_texto["p220"] == 7 and por_texto["p10"] == 0
    assert resposta["shorts"]["3"]["titulo"] == "Título 3"
    assert all(f["de"] >= 0 and "i" in f for f in resposta["frases"])


def test_trecho_do_pedido_e_o_resumo(pastas):
    texto = livre.carregar_texto(pastas)
    trecho = livre.trecho_do_pedido(texto, "20", 79, ["40", 41, 20, 999])
    assert trecho == Trecho(20, 79, frozenset({40, 41}))  # as pontas e o que está fora não contam
    resumo = livre.resumo_do_trecho(texto, trecho)
    assert resumo["duracao"] == pytest.approx(28.9, abs=0.1) and resumo["problema"] == ""
    assert "Começa em “p20" in resumo["pontas"]
    curto = livre.resumo_do_trecho(texto, livre.trecho_do_pedido(texto, 20, 30))
    assert "pelo menos 10" in curto["problema"]
    longo = livre.resumo_do_trecho(texto, livre.trecho_do_pedido(texto, 0, 700))
    assert "máximo" in longo["problema"]
    for ruim in (("x", 3), (5, 2), (-1, 3), (0, 10_000)):
        with pytest.raises(livre.TrechoInvalido):
            livre.trecho_do_pedido(texto, *ruim)


def test_corte_de_previa_tira_os_pedacos_de_fora(pastas):
    texto = livre.carregar_texto(pastas)
    corte = livre.corte_de_previa(texto, livre.trecho_do_pedido(texto, 20, 79, range(40, 50)))
    assert corte.inicio == pytest.approx(10.0, abs=0.06) and corte.fim == pytest.approx(
        39.9, abs=0.06
    )
    assert len(corte.saltos) == 1 and corte.saltos[0][0] == pytest.approx(19.9, abs=0.06)


def test_busca_por_palavras(pastas):
    texto = livre.carregar_texto(pastas)
    achados = livre.buscar(texto, "p333", "palavras")
    assert achados and any(a["de"] <= 333 <= a["ate"] for a in achados)
    assert livre.buscar(texto, "   ", "palavras") == []


# --- criar ----------------------------------------------------------------------------------


def test_criar_grava_o_corte_e_segura_a_trava(pastas):
    texto = livre.carregar_texto(pastas)
    terminadas = []
    criacoes = livre.Criacoes(criar=_corte_criado, ao_terminar=terminadas.append, renovar_s=0.01)
    criacao = criacoes.iniciar(pastas, texto, livre.trecho_do_pedido(texto, 20, 79))
    assert criacoes.esperar(5) and not criacao.rodando
    assert criacao.corte_id == 1_000_000 and "Um título novo" in criacao.texto
    assert terminadas == [criacao]
    novo = [c for c in dados.carregar_cortes(pastas) if c.id == 1_000_000]
    assert novo and novo[0].origem == ORIGEM_MANUAL and len(dados.carregar_cortes(pastas)) == 4
    assert sessao.ativa(pastas)  # a trava valia na hora de gravar


def test_criar_que_falha_nao_grava_nada(pastas):
    texto = livre.carregar_texto(pastas)

    def sem_ia(*_a):
        raise RuntimeError("O Ollama não está respondendo")

    criacoes = livre.Criacoes(criar=sem_ia)
    criacao = criacoes.iniciar(pastas, texto, livre.trecho_do_pedido(texto, 20, 79))
    criacoes.esperar(5)
    assert criacao.erro == "O Ollama não está respondendo" and criacao.corte_id is None
    assert len(dados.carregar_cortes(pastas)) == 3


def test_uma_criacao_por_vez(pastas):
    texto = livre.carregar_texto(pastas)
    import threading

    solta = threading.Event()

    def demorada(*a):
        solta.wait(5)
        return _corte_criado(*a)

    criacoes = livre.Criacoes(criar=demorada)
    criacoes.iniciar(pastas, texto, livre.trecho_do_pedido(texto, 20, 79))
    with pytest.raises(livre.TrechoInvalido, match="já está criando"):
        criacoes.iniciar(pastas, texto, livre.trecho_do_pedido(texto, 100, 179))
    solta.set()
    criacoes.esperar(5)


def test_aviso_da_criacao_leva_ao_short(pastas):
    rede = TransporteFalso()
    cofre = Cofre(pastas.raiz / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(ConfigRemoto(usuarios=[CONTA]), pastas.raiz.parent, Avisos(cofre, rede), "x",
                   gravar_config=lambda _c: None)  # fmt: skip
    enviados = []
    ctx.avisos.enviar = enviados.append
    pronta = livre.Criacao(NOME_DO_PROJETO, texto="Short criado: “X”.", rodando=False, corte_id=7)
    avisar_da_criacao(ctx, pronta)
    falhou = livre.Criacao(NOME_DO_PROJETO, rodando=False, erro="sem Ollama")
    avisar_da_criacao(ctx, falhou)
    assert enviados[0].url.endswith("/s/7") and enviados[1].url.endswith("/livre")
    assert "sem Ollama" in enviados[1].texto
    ctx.config = ctx.config.model_copy(update={"dormindo": True})
    avisar_da_criacao(ctx, pronta)
    assert len(enviados) == 2  # dormindo, não avisa


# --- pelo servidor --------------------------------------------------------------------------


@pytest.fixture
def cliente(pastas, tmp_path, monkeypatch):
    monkeypatch.setattr(audicao, "executar_ffmpeg", _rodar_de_mentira([]))
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), pastas.raiz.parent, Avisos(cofre, TransporteFalso()), "x",
        gravar_config=lambda _c: None,
        criacoes=livre.Criacoes(criar=_corte_criado, renovar_s=0.01),
    )  # fmt: skip
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    cliente.ctx = ctx
    return cliente


def test_texto_busca_e_trecho_pelo_servidor(cliente, pastas, tmp_path):
    texto = cliente.get(f"{BASE}/texto").json()
    assert texto["frases"] and texto["criacao"] is None
    assert cliente.get(f"{BASE}/buscar", params={"q": "p333"}).json()
    trecho = cliente.get(f"{BASE}/trecho", params={"de": 20, "ate": 79, "fora": "40,41"}).json()
    assert trecho["duracao"] == pytest.approx(28.9, abs=0.1) and not trecho["tem_som"]
    assert trecho["legenda"]["frases"][0][0]["t"] == "p20"
    assert cliente.get(f"{BASE}/trecho", params={"de": 5, "ate": 2}).status_code == 422
    assert cliente.get(f"{BASE}/audio", params={"de": 20, "ate": 79}).status_code == 404

    video = tmp_path / "sermao.mp4"
    video.write_bytes(b"x")
    estado = _estado(pastas)
    estado.entrada.caminho = str(video)
    salvar_estado(pastas.raiz, estado)
    audio = cliente.get(f"{BASE}/audio", params={"de": 20, "ate": 79})
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mp4"
    assert list((pastas.work / "remoto").glob("trecho_livre_*.m4a"))


def test_criar_pelo_servidor(cliente, pastas):
    pedido = {"de": 20, "ate": 79, "fora": []}
    sem_trava = cliente.post(f"{BASE}/criar", json=pedido)
    assert sem_trava.status_code == 409 and sem_trava.json()["erro"] == "sem_trava"
    cliente.post(f"/api/projetos/{NOME_DO_PROJETO}/sessao")
    curto = cliente.post(f"{BASE}/criar", json={"de": 20, "ate": 30})
    assert curto.status_code == 422 and "pelo menos 10" in curto.json()["erro"]
    criada = cliente.post(f"{BASE}/criar", json=pedido)
    assert criada.status_code == 200 and criada.json()["projeto"] == NOME_DO_PROJETO
    cliente.ctx.criacoes.esperar(5)
    estado = cliente.get(f"{BASE}/criacao").json()
    assert not estado["rodando"] and estado["corte_id"] == 1_000_000
    assert any(
        s["id"] == 1_000_000 for s in cliente.get(f"/api/projetos/{NOME_DO_PROJETO}/shorts").json()
    )


def test_shorts_livre_so_abre_com_os_cortes_calculados(cliente, pastas):
    Path(pastas.cortes_shorts).unlink()
    assert cliente.get(f"{BASE}/texto").status_code == 409
