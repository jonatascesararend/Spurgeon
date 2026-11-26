"""A chave do Gemini: guardar, ler e testar (sem internet: o `urlopen` é falso)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from spurgeon.publicacao.cofre import Cofre
from spurgeon.shorts import gemini


@pytest.fixture
def cofre(tmp_path):
    return Cofre(tmp_path / "c.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1])


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _falso(monkeypatch, respostas: dict[str, object]):
    pedidos = []

    def urlopen(pedido, timeout):
        pedidos.append(pedido)
        for trecho, resposta in respostas.items():
            if trecho in pedido.full_url:
                if isinstance(resposta, Exception):
                    raise resposta
                return _Resposta(json.dumps(resposta).encode())
        raise AssertionError(pedido.full_url)

    monkeypatch.setattr(gemini.urllib.request, "urlopen", urlopen)
    return pedidos


_MODELOS = {
    "models": [
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]
}
_OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


def test_sem_chave_a_revisao_fica_desligada_e_o_modelo_tem_padrao(cofre):
    assert gemini.configuracao(cofre) is None
    cofre.definir(gemini.CONTA, gemini.CAMPO_CHAVE, " AIzaTeste ", segredo=True)
    assert gemini.configuracao(cofre) == gemini.Configuracao("AIzaTeste", gemini.MODELO_PADRAO)
    cofre.definir(gemini.CONTA, gemini.CAMPO_MODELO, "gemini-2.5-flash")
    assert gemini.configuracao(cofre).modelo == "gemini-2.5-flash"


def test_testar_lista_os_modelos_e_faz_um_pedido_minimo_com_a_chave_no_cabecalho(monkeypatch):
    pedidos = _falso(monkeypatch, {"models?": _MODELOS, ":generateContent": _OK})
    resumo = gemini.testar(gemini.Configuracao("AIzaTeste"))
    assert "gemini-flash-latest" in resumo
    assert [p.get_method() for p in pedidos] == ["GET", "POST"]
    assert all(p.get_header("X-goog-api-key") == "AIzaTeste" for p in pedidos)
    assert all("AIzaTeste" not in p.full_url for p in pedidos)  # a chave não vai na URL
    assert json.loads(pedidos[1].data)["contents"][0]["parts"][0]["text"]


def test_modelo_que_a_chave_nao_tem_sugere_os_que_ela_aceita(monkeypatch):
    _falso(monkeypatch, {"models?": _MODELOS})
    with pytest.raises(gemini.ErroDoGemini, match=r"gemini-2.5-flash"):
        gemini.testar(gemini.Configuracao("AIzaTeste", "gemini-9-ultra"))


def _erro_http(codigo: int, mensagem: str) -> urllib.error.HTTPError:
    corpo = json.dumps({"error": {"message": mensagem}}).encode()
    return urllib.error.HTTPError("u", codigo, "x", {}, io.BytesIO(corpo))


@pytest.mark.parametrize(
    ("erro", "trecho"),
    [
        (_erro_http(400, "API key not valid. Please pass a valid API key."), "não foi aceita"),
        (_erro_http(429, "Resource exhausted"), "limite"),
        (urllib.error.URLError("sem rede"), "sem internet"),
    ],
)
def test_erros_viram_mensagens_claras(monkeypatch, erro, trecho):
    _falso(monkeypatch, {"models?": erro})
    with pytest.raises(gemini.ErroDoGemini, match=trecho):
        gemini.testar(gemini.Configuracao("AIzaTeste"))


def test_gerar_com_esquema_pede_json_e_resposta_bloqueada_e_erro(monkeypatch):
    pedidos = _falso(monkeypatch, {":generateContent": {"promptFeedback": {"blockReason": "X"}}})
    with pytest.raises(gemini.ErroDoGemini, match="bloqueio: X"):
        gemini.gerar(gemini.Configuracao("k"), "oi", sistema="s", esquema={"type": "OBJECT"})
    corpo = json.loads(pedidos[0].data)
    assert corpo["generationConfig"]["responseMimeType"] == "application/json"
    assert corpo["systemInstruction"]["parts"][0]["text"] == "s"


def _sequencia(monkeypatch, respostas: list[object]):
    """Cada pedido recebe a próxima resposta da lista (e a espera entre tentativas é zero)."""
    pedidos, esperas = [], []

    def urlopen(pedido, timeout):
        pedidos.append(pedido)
        resposta = respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return _Resposta(json.dumps(resposta).encode())

    monkeypatch.setattr(gemini.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(gemini, "_dormir", esperas.append)
    return pedidos, esperas


def test_alta_demanda_espera_e_tenta_de_novo(monkeypatch):
    lotado = _erro_http(503, "The model is overloaded. Please try again later.")
    pedidos, esperas = _sequencia(monkeypatch, [lotado, _OK])
    assert gemini.gerar(gemini.Configuracao("k"), "oi") == "ok"
    assert len(pedidos) == 2 and esperas == [gemini._ESPERAS_S[0]]


def test_modelo_lotado_depois_das_tentativas_passa_para_o_reserva(monkeypatch):
    lotado = _erro_http(503, "overloaded")
    tentativas = len(gemini._ESPERAS_S) + 1
    pedidos, _ = _sequencia(monkeypatch, [_MODELOS] + [lotado] * tentativas + [_OK])
    resumo = gemini.testar(gemini.Configuracao("k"))
    assert gemini.MODELO_RESERVA in pedidos[-1].full_url and "alta demanda" in resumo


def test_reserva_tambem_lotado_explica_que_e_passageiro(monkeypatch):
    lotado = _erro_http(503, "overloaded")
    _sequencia(monkeypatch, [lotado] * (2 * (len(gemini._ESPERAS_S) + 1)))
    with pytest.raises(gemini.ErroDoGemini, match="alta demanda") as erro:
        gemini.gerar(gemini.Configuracao("k"), "oi")
    assert erro.value.sobrecarregado


def test_chave_recusada_nao_insiste(monkeypatch):
    pedidos, esperas = _sequencia(monkeypatch, [_erro_http(400, "API key not valid")])
    with pytest.raises(gemini.ErroDoGemini, match="não foi aceita"):
        gemini.gerar(gemini.Configuracao("k"), "oi")
    assert len(pedidos) == 1 and esperas == []
