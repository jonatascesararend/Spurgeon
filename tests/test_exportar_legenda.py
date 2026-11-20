"""Exportar a legenda de um Short (`shorts/exportar.py`), pelo celular e pelo Studio do PC, e o
texto maior em volta do corte no editor de início e fim do celular."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import dados, edicao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from spurgeon.shorts.exportar import (
    legenda_no_tempo_do_short,
    nome_do_arquivo,
    tempo_curto,
    tempo_longo,
    texto_da_legenda,
)
from spurgeon.transcricao.formato import Transcricao
from tests import test_janela as base
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto
from tests.test_remoto_audicao import _mudar_corte, _transcrever

janela, tela, projeto_pronto = base.janela, base.tela, base.projeto_pronto


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _corte(pastas, id_=7):
    return next(c for c in dados.carregar_cortes(pastas) if c.id == id_)


def test_tempos_do_arquivo():
    assert tempo_curto(0) == "0:00,0" and tempo_curto(65.34) == "1:05,3"
    assert tempo_longo(125) == "2:05" and tempo_longo(3723) == "1:02:03"


def test_texto_da_legenda_com_os_tempos_no_short_pronto(pastas):
    _mudar_corte(pastas, 7, saltos=[[110.0, 115.0]])
    corte = _corte(pastas)
    palavras = Transcricao.carregar(pastas.transcricao).palavras
    legenda = legenda_no_tempo_do_short(palavras, corte)
    texto = texto_da_legenda(corte, legenda, sermao="09. Sermão de teste", numero=2)
    linhas = texto.splitlines()
    assert linhas[0] == "Legenda do Short 2: Título 7"
    assert linhas[1] == "Sermão: 09. Sermão de teste"
    assert "Trecho do sermão: 1:40 a 2:20" in linhas[2] and "duração do Short: 0:35,0" in linhas[2]
    frases = linhas[5:]
    assert frases[0].startswith("0:00,0 – ") and "p200" in frases[0]
    assert len(frases) == len(legenda.frases)
    assert "p225" not in texto  # caiu no salto: não está no Short
    assert nome_do_arquivo(corte, 2) == "legenda_short_02_titulo_7.txt"
    assert nome_do_arquivo(corte) == "legenda_corte_7_titulo_7.txt"


def test_celular_baixa_o_arquivo(pastas, tmp_path):
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
    resposta = cliente.get(f"/api/projetos/{NOME_DO_PROJETO}/shorts/7/legenda.txt")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/plain")
    assert "attachment" in resposta.headers["content-disposition"]
    assert "legenda_short_02_titulo_7.txt" in resposta.headers["content-disposition"]
    assert resposta.text.startswith("Legenda do Short 2: Título 7")
    assert cliente.get(f"/api/projetos/{NOME_DO_PROJETO}/shorts/999/legenda.txt").status_code == 404


def test_editor_traz_muito_mais_texto_em_volta(pastas, tmp_path):
    ed = edicao.para_editar(pastas, 7)  # o corte vai de p200 a p279; a transcrição tem 800 palavras
    assert ed["contexto"][0]["n"] == 0 and ed["contexto"][-1]["n"] == 799  # 600 de cada lado cobrem
    assert not ed["tem_mais_antes"] and not ed["tem_mais_depois"]
    pouco = edicao.para_editar(pastas, 7, antes=10, depois=10)
    assert pouco["contexto"][0]["n"] == 190 and pouco["contexto"][-1]["n"] == 289
    assert pouco["tem_mais_antes"] and pouco["tem_mais_depois"]
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
    url = f"/api/projetos/{NOME_DO_PROJETO}/shorts/7/edicao"
    assert cliente.get(url, params={"antes": 5, "depois": 3}).json()["contexto"][0]["n"] == 195


@pytest.mark.gui
def test_studio_do_pc_exporta_a_legenda(tela, projeto_pronto, tmp_path, monkeypatch):
    from spurgeon.ui import studio_shorts

    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    editor = tela.studio.editor
    nome, texto = editor.texto_da_legenda_exportada()
    assert nome.startswith("legenda_short_") and nome.endswith(".txt")
    assert texto.startswith("Legenda do Short") and " – " in texto
    destino = tmp_path / "legenda.txt"
    monkeypatch.setattr(studio_shorts.filedialog, "asksaveasfilename", lambda **_k: str(destino))
    editor._exportar_legenda()
    assert destino.read_text("utf-8") == texto
    assert "Legenda exportada" in editor._rotulo_ajuste.cget("text")
