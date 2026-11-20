"""Exportar a legenda do sermão inteiro (`shorts/exportar.py`), pelo celular e pelo Studio do PC,
e o texto maior em volta do corte no editor de início e fim do celular."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import edicao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from spurgeon.shorts.exportar import (
    nome_do_arquivo_do_sermao,
    tempo_longo,
    texto_da_legenda_do_sermao,
)
from spurgeon.transcricao.formato import Palavra, Transcricao
from tests import test_janela as base
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto
from tests.test_remoto_audicao import _transcrever

janela, tela, projeto_pronto = base.janela, base.tela, base.projeto_pronto


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _cliente(pastas, tmp_path) -> TestClient:
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    avisos = Avisos(cofre, TransporteFalso())
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]),
        pastas.raiz.parent,
        avisos,
        "x",
        gravar_config=lambda _c: None,
    )
    return TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})


def test_tempo_no_video_do_sermao():
    assert tempo_longo(0) == "0:00" and tempo_longo(125) == "2:05"
    assert tempo_longo(3723.4) == "1:02:03"


def test_legenda_do_sermao_uma_frase_por_linha_com_o_tempo():
    palavras = [
        Palavra("Deus", 1.0, 1.4), Palavra("é", 1.5, 1.6), Palavra("fiel.", 1.7, 2.0),
        Palavra("Ele", 65.0, 65.3), Palavra("não", 65.4, 65.6), Palavra("falha.", 65.7, 66.0),
    ]  # fmt: skip
    texto = texto_da_legenda_do_sermao(palavras, sermao="09. Sermão", duracao_s=3723)
    linhas = texto.splitlines()
    assert linhas[0] == "Legenda do sermão: 09. Sermão"
    assert linhas[1] == "Duração: 1:02:03"
    assert linhas[4:] == ["0:01  Deus é fiel.", "1:05  Ele não falha."]
    assert texto_da_legenda_do_sermao([], sermao="x").endswith("\n")
    assert nome_do_arquivo_do_sermao("09. Quando a religião (Mq 3)") == (
        "legenda_09_Quando_a_religião_Mq_3.txt"
    )


def test_celular_baixa_a_legenda_do_sermao(pastas, tmp_path):
    cliente = _cliente(pastas, tmp_path)
    detalhe = cliente.get(f"/api/projetos/{NOME_DO_PROJETO}").json()
    assert detalhe["tem_transcricao"]
    resposta = cliente.get(f"/api/projetos/{NOME_DO_PROJETO}/legenda.txt")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/plain")
    assert "attachment" in resposta.headers["content-disposition"]
    assert "legenda_09_Serm" in resposta.headers["content-disposition"]
    assert resposta.text.startswith("Legenda do sermão: 09. Sermão de teste")
    assert "p0" in resposta.text and "p799" in resposta.text  # o sermão inteiro
    pastas.transcricao.unlink()
    assert cliente.get(f"/api/projetos/{NOME_DO_PROJETO}/legenda.txt").status_code == 404


def test_editor_traz_muito_mais_texto_em_volta(pastas, tmp_path):
    ed = edicao.para_editar(pastas, 7)  # o corte vai de p200 a p279; a transcrição tem 800 palavras
    assert ed["contexto"][0]["n"] == 0 and ed["contexto"][-1]["n"] == 799  # 600 de cada lado cobrem
    assert not ed["tem_mais_antes"] and not ed["tem_mais_depois"]
    pouco = edicao.para_editar(pastas, 7, antes=10, depois=10)
    assert pouco["contexto"][0]["n"] == 190 and pouco["contexto"][-1]["n"] == 289
    assert pouco["tem_mais_antes"] and pouco["tem_mais_depois"]
    url = f"/api/projetos/{NOME_DO_PROJETO}/shorts/7/edicao"
    resposta = _cliente(pastas, tmp_path).get(url, params={"antes": 5, "depois": 3})
    assert resposta.json()["contexto"][0]["n"] == 195


@pytest.mark.gui
def test_studio_do_pc_exporta_a_legenda_do_sermao(tela, projeto_pronto, tmp_path, monkeypatch):
    from spurgeon.ui import studio_shorts

    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    editor = tela.studio.editor
    nome, texto = editor.texto_da_legenda_exportada()
    assert nome.startswith("legenda_") and nome.endswith(".txt")
    assert texto.startswith("Legenda do sermão: Sermão Teste")
    palavras = Transcricao.carregar(projeto_pronto.pastas.transcricao).palavras
    assert palavras[-1].texto in texto  # até a última palavra do sermão
    destino = tmp_path / "legenda.txt"
    monkeypatch.setattr(studio_shorts.filedialog, "asksaveasfilename", lambda **_k: str(destino))
    editor._exportar_legenda()
    assert destino.read_text("utf-8") == texto
    assert "Legenda exportada" in editor._rotulo_ajuste.cget("text")
