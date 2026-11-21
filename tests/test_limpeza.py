"""Liberar espaço pelo celular (`remoto/limpeza.py`): varrer e apagar o projeto, com confirmação."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from spurgeon.core.estado import carregar_estado, salvar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import limpeza, sessao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.fila import Fila
from spurgeon.remoto.servidor import criar_app
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto


def _com_original(pastas: PastasProjeto, original: Path) -> None:
    estado = carregar_estado(pastas.raiz)
    estado.entrada.caminho = str(original)
    salvar_estado(pastas.raiz, estado)


@pytest.fixture
def projeto(tmp_path):
    """Um projeto com Shorts renderizados, vídeo longo, intermediários e o original em Downloads."""
    base = tmp_path / "Projetos"
    pastas = PastasProjeto(criar_projeto(base))  # já tem postados/short_01_titulo_3.mp4 (1000 B)
    (pastas.shorts / "postados" / "short_01_titulo_3.txt").write_text("Título: x", "utf-8")
    (pastas.shorts / "short_02_titulo_7.mp4").write_bytes(b"v" * 500)
    pastas.video_longo.write_bytes(b"l" * 2000)
    pastas.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nolá\n", "utf-8")
    pastas.audio_limpo.write_bytes(b"a" * 3000)
    (pastas.work / "partes").mkdir()
    (pastas.work / "partes" / "p1.mp4").write_bytes(b"p" * 400)
    pastas.transcricao.write_text("{}", "utf-8")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    original = downloads / "sermao.mp4"
    original.write_bytes(b"o" * 7000)
    _com_original(pastas, original)
    return base, pastas, original


def test_varrer_apaga_os_videos_e_guarda_a_analise(projeto):
    base, pastas, original = projeto
    plano = limpeza.plano_de_varrer(pastas, base)
    assert plano.original == original and plano.bytes_do_original == 7000
    assert plano.bytes_do_projeto == 1000 + 500 + 2000 + 3000 + 400
    resultado = limpeza.varrer(pastas, base, agora=datetime(2026, 11, 21, 10, 0))
    assert resultado.falhas == [] and resultado.bytes == plano.bytes
    assert not original.exists() and not pastas.video_longo.exists()
    assert not list(pastas.shorts.rglob("*.mp4")) and not pastas.audio_limpo.exists()
    assert not (pastas.work / "partes").exists()
    # o que fica: a análise, as legendas, os textos e o registro
    assert (pastas.shorts / "postados" / "short_01_titulo_3.txt").is_file()
    assert pastas.cortes_shorts.is_file() and pastas.shorts_renderizados.is_file()
    assert pastas.transcricao.is_file() and pastas.srt.is_file() and pastas.estado.is_file()
    marca = limpeza.ler_marca(pastas)
    assert marca["quando"] == "2026-11-21T10:00" and marca["original_apagado"]
    assert marca["bytes"] == plano.bytes
    assert limpeza.plano_de_varrer(pastas, base).bytes == 0  # nada mais a varrer


def test_original_usado_por_outro_projeto_ou_que_nao_e_video_fica(projeto):
    base, pastas, original = projeto
    outro = PastasProjeto(criar_projeto(base, "2026-11-11_10._Outro"))
    _com_original(outro, original)
    plano = limpeza.plano_de_varrer(pastas, base)
    assert plano.original is None and "2026-11-11_10._Outro" in plano.original_fica
    limpeza.varrer(pastas, base)
    assert original.exists()  # o outro projeto ainda precisa dele

    texto = original.with_suffix(".txt")
    texto.write_text("não sou vídeo", "utf-8")
    _com_original(outro, texto)
    _com_original(pastas, texto)
    assert "não parece um vídeo" in limpeza.plano_de_apagar(pastas, base).original_fica


def test_apagar_tira_a_pasta_e_o_original(projeto):
    base, pastas, original = projeto
    plano = limpeza.plano_de_apagar(pastas, base)
    assert plano.bytes == plano.bytes_do_projeto + 7000
    resultado = limpeza.apagar_projeto(pastas, base)
    assert not pastas.raiz.exists() and not original.exists() and resultado.falhas == []


def _cliente(base: Path, tmp_path: Path, fila: Fila | None = None) -> TestClient:
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), base, Avisos(cofre, TransporteFalso()), "x",
        gravar_config=lambda _c: None, fila=fila or Fila(procurar=lambda: []),
    )  # fmt: skip
    return TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})


URL = f"/api/projetos/{NOME_DO_PROJETO}"


def test_pelo_celular_com_confirmacao_e_impedimentos(projeto, tmp_path, monkeypatch):
    base, pastas, original = projeto
    monkeypatch.setattr(sessao, "esperar_o_pc", lambda *_a, **_k: "sem_janela")
    cliente = _cliente(base, tmp_path)
    espaco = cliente.get(f"{URL}/espaco").json()
    assert espaco["varrer"]["original"] == "sermao.mp4" and espaco["varrido"] is None
    assert espaco["impedimento_varrer"] == "" and espaco["impedimento_apagar"] == ""
    assert cliente.post(f"{URL}/varrer", json={}).status_code == 400  # sem confirmar
    assert cliente.post(f"{URL}/varrer", json={"confirmar": "outro"}).status_code == 400

    ocupado = _cliente(base, tmp_path, Fila(procurar=lambda: [4321]))
    resposta = ocupado.post(f"{URL}/varrer", json={"confirmar": NOME_DO_PROJETO})
    assert resposta.status_code == 409 and "Espere terminar" in resposta.json()["erro"]

    feito = cliente.post(f"{URL}/varrer", json={"confirmar": NOME_DO_PROJETO})
    assert feito.status_code == 200 and feito.json()["bytes"] > 7000 and not original.exists()
    assert not sessao.ativa(pastas)  # a trava foi solta
    assert cliente.get(f"{URL}/espaco").json()["varrido"]["bytes"] > 7000

    sessao.anunciar_janela(pastas.raiz)  # a janela do PC está com o projeto aberto
    try:
        resposta = cliente.post(f"{URL}/apagar", json={"confirmar": NOME_DO_PROJETO})
        assert resposta.status_code == 409 and "janela do PC" in resposta.json()["erro"]
    finally:
        sessao.fechar_janela()
    apagado = cliente.post(f"{URL}/apagar", json={"confirmar": NOME_DO_PROJETO})
    assert apagado.status_code == 200 and not pastas.raiz.exists()
    assert cliente.get(URL).status_code == 404
    assert NOME_DO_PROJETO not in json.dumps(cliente.get("/api/projetos").json())


def test_lista_de_projetos_mostra_o_varrido(projeto):
    from spurgeon.remoto import dados

    base, pastas, _original = projeto
    item = lambda: next(p for p in dados.projetos(base) if p["id"] == pastas.raiz.name)  # noqa: E731
    assert item()["varrido"] is None
    limpeza.varrer(pastas, base, agora=datetime(2026, 11, 21, 0, 22))
    varrido = item()["varrido"]
    assert varrido["quando"] == "2026-11-21T00:22" and varrido["bytes"] > 7000
