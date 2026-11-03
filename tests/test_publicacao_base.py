"""A base da publicação: textos do pacote, cofre de segredos, rede e OAuth de desktop."""

from __future__ import annotations

import hashlib
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from spurgeon.core.erros import Cancelado
from spurgeon.publicacao import oauth
from spurgeon.publicacao.cofre import Cofre, proteger_com_o_windows, revelar_com_o_windows
from spurgeon.publicacao.erros import ErroDePublicacao, ErroDeRede
from spurgeon.publicacao.modelo import Pacote, Resultado, iso_utc, limpar_hashtag
from spurgeon.publicacao.rede import (
    codificar_formulario,
    com_tentativas,
    exigir_ok,
    mensagem_da_resposta,
    montar_multipart,
)
from tests.falso_de_rede import resposta


def _pacote(tmp_path: Path, **extra) -> Pacote:
    arquivo = tmp_path / "short.mp4"
    arquivo.write_bytes(b"x" * 100)
    base = {"video": arquivo, "titulo": "Deus é fiel", "duracao_s": 40.0}
    return Pacote(**{**base, **extra})


# --- texto do pacote ------------------------------------------------------------------------------
def test_hashtag_sem_espaco_nem_pontuacao():
    assert limpar_hashtag("#Fé em Cristo!") == "#FéEmCristo"
    assert limpar_hashtag("  ") == ""
    assert limpar_hashtag("#") == ""


def test_etiquetas_tiram_vazias_e_repetidas_sem_diferenciar_caixa(tmp_path):
    p = _pacote(tmp_path, hashtags=("#Fé", "fé", "", "Salmo 90"))
    assert p.etiquetas == ["#Fé", "#Salmo90"]


def test_legenda_junta_titulo_descricao_e_hashtags(tmp_path):
    p = _pacote(tmp_path, descricao="Uma palavra de esperança.", hashtags=("fé", "esperança"))
    assert p.legenda() == "Deus é fiel\n\nUma palavra de esperança.\n\n#fé #esperança"
    assert p.legenda(com_titulo=False) == "Uma palavra de esperança.\n\n#fé #esperança"


def test_legenda_longa_encurta_a_descricao_e_preserva_titulo_e_hashtags(tmp_path):
    p = _pacote(tmp_path, descricao="palavra " * 100, hashtags=("fé",))
    curta = p.legenda(limite=120)
    assert len(curta) <= 120
    assert curta.startswith("Deus é fiel\n\n") and curta.endswith("\n\n#fé")
    assert "…" in curta


def test_legenda_que_cabe_nao_muda(tmp_path):
    p = _pacote(tmp_path, descricao="curta")
    assert p.legenda(limite=500) == p.legenda()


def test_iso_utc_converte_o_horario_com_fuso():
    brasilia = timezone(timedelta(hours=-3))
    assert iso_utc(datetime(2026, 11, 10, 11, 30, tzinfo=brasilia)) == "2026-11-10T14:30:00Z"


def test_resultado_vai_e_volta_do_dicionario_ignorando_campos_desconhecidos():
    r = Resultado("youtube", "agendado", "abc", "https://y/abc", "2026-11-10T14:30:00Z")
    d = r.para_dict()
    assert Resultado.de_dict({**d, "novo_campo_de_uma_versao_futura": 1}) == r
    assert r.rotulo == "agendado na rede"


# --- cofre ----------------------------------------------------------------------------------------
def _cofre(tmp_path: Path) -> Cofre:
    return Cofre(
        tmp_path / "integracoes.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1]
    )


def test_cofre_guarda_segredo_fora_do_texto_e_devolve_igual(tmp_path):
    cofre = _cofre(tmp_path)
    cofre.definir("youtube", "client_id", "id-publico")
    cofre.definir("youtube", "client_secret", "SEGREDO-123", segredo=True)
    arquivo = (tmp_path / "integracoes.json").read_text("utf-8")
    assert "id-publico" in arquivo and "SEGREDO-123" not in arquivo
    assert cofre.obter("youtube", "client_secret") == "SEGREDO-123"
    assert cofre.obter("youtube", "client_id") == "id-publico"
    assert cofre.tem("youtube", "client_secret") and not cofre.tem("youtube", "outro")
    assert cofre.obter("youtube", "outro", "padrão") == "padrão"


def test_cofre_valor_vazio_apaga_e_conta_vazia_some(tmp_path):
    cofre = _cofre(tmp_path)
    cofre.definir("tiktok", "client_key", "k")
    cofre.definir("tiktok", "client_key", "")
    assert cofre.contas() == []
    cofre.definir("meta", "app_id", "1")
    cofre.definir("meta", "app_secret", "2", segredo=True)
    cofre.remover("meta", "app_id")
    assert cofre.campos_da_conta("meta") == ["app_secret"]
    cofre.remover("meta")
    assert cofre.contas() == []


def test_cofre_trocar_um_campo_de_aberto_para_secreto_nao_deixa_copia_em_claro(tmp_path):
    cofre = _cofre(tmp_path)
    cofre.definir("meta", "token", "ABERTO")
    cofre.definir("meta", "token", "SECRETO", segredo=True)
    assert "ABERTO" not in (tmp_path / "integracoes.json").read_text("utf-8")
    assert cofre.obter("meta", "token") == "SECRETO"


def test_cofre_arquivo_estragado_ou_segredo_ilegivel_nao_derruba(tmp_path):
    caminho = tmp_path / "integracoes.json"
    caminho.write_text("{isto não é json", "utf-8")
    cofre = _cofre(tmp_path)
    assert cofre.obter("youtube", "client_id") == ""
    cofre.definir("youtube", "client_secret", "S", segredo=True)

    def quebrado(_b: bytes) -> bytes:
        raise OSError("outro usuário")

    ilegivel = Cofre(caminho, proteger=lambda b: b, revelar=quebrado)
    assert ilegivel.obter("youtube", "client_secret", "nada") == "nada"


@pytest.mark.skipif(sys.platform != "win32", reason="o cofre usa o DPAPI do Windows")
def test_dpapi_de_verdade_ida_e_volta_e_nao_deixa_o_texto_a_vista():
    segredo = "chave-de-api-com-acentuação-ç"
    bruto = proteger_com_o_windows(segredo.encode("utf-8"))
    assert segredo.encode("utf-8") not in bruto
    assert revelar_com_o_windows(bruto).decode("utf-8") == segredo
    with pytest.raises(OSError):
        revelar_com_o_windows(b"lixo que nao e dpapi")


@pytest.mark.skipif(sys.platform != "win32", reason="o cofre usa o DPAPI do Windows")
def test_cofre_padrao_usa_o_dpapi_e_o_caminho_isolado_pelo_conftest(tmp_path):
    cofre = Cofre()  # o conftest troca `user_config_path` por uma pasta de teste
    assert "config_do_usuario" in str(cofre.caminho)
    cofre.definir("youtube", "client_secret", "SEGREDO-REAL", segredo=True)
    assert "SEGREDO-REAL" not in cofre.caminho.read_text("utf-8")
    assert cofre.obter("youtube", "client_secret") == "SEGREDO-REAL"


# --- rede -----------------------------------------------------------------------------------------
def test_formulario_converte_bool_e_omite_none():
    corpo = codificar_formulario({"a": True, "b": False, "c": None, "d": "x y"}).decode()
    assert dict(urllib.parse.parse_qsl(corpo)) == {"a": "true", "b": "false", "d": "x y"}


@pytest.mark.parametrize(
    ("dados", "esperado"),
    [
        ({"error": {"message": "Quota", "errors": [{"reason": "quotaExceeded"}]}}, "quotaExceeded"),
        ({"error": {"message": "Token", "code": 190}}, "190"),
        ({"error": "invalid_grant", "error_description": "Bad Request"}, "invalid_grant | Bad"),
        ({"message": "chave inválida"}, "chave inválida"),
        ({"error": {"code": "access_token_invalid", "message": "expirou"}}, "expirou"),
    ],
)
def test_mensagem_da_resposta_entende_google_meta_e_tiktok(dados, esperado):
    assert esperado in mensagem_da_resposta(resposta(400, dados))


def test_exigir_ok_levanta_com_o_motivo_e_o_codigo():
    with pytest.raises(ErroDeRede) as erro:
        exigir_ok(resposta(403, {"error": {"message": "sem permissão"}}), "Enviar")
    assert erro.value.codigo == 403 and "sem permissão" in str(erro.value)
    assert not erro.value.passageiro
    assert ErroDeRede("x", codigo=503).passageiro and ErroDeRede("x", codigo=0).passageiro
    assert ErroDeRede("x", codigo=429).passageiro


def test_tentativas_repete_so_o_que_e_passageiro():
    esperas: list[float] = []
    tentativas = iter([ErroDeRede("a", codigo=503), ErroDeRede("b", codigo=0), "pronto"])

    def fazer():
        proximo = next(tentativas)
        if isinstance(proximo, Exception):
            raise proximo
        return proximo

    assert com_tentativas(fazer, dormir=esperas.append) == "pronto"
    assert len(esperas) == 2

    with pytest.raises(ErroDeRede):  # 403 não adianta repetir
        com_tentativas(
            lambda: (_ for _ in ()).throw(ErroDeRede("x", codigo=403)), dormir=esperas.append
        )
    assert len(esperas) == 2


def test_tentativas_desistem_depois_da_ultima_espera_e_respeitam_o_cancelamento():
    esperas: list[float] = []
    with pytest.raises(ErroDeRede):
        com_tentativas(
            lambda: (_ for _ in ()).throw(ErroDeRede("x", codigo=500)),
            esperas=(1.0, 2.0),
            dormir=esperas.append,
        )
    assert esperas == [1.0, 2.0]
    with pytest.raises(Cancelado):
        com_tentativas(lambda: "x", cancelado=lambda: True)


def test_multipart_leva_campos_listas_e_o_arquivo(tmp_path):
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"BINARIO")
    corpo, tipo = montar_multipart(
        {"user": "igreja", "platform[]": ["youtube", "tiktok"], "async_upload": True, "x": None},
        {"video": arquivo},
    )
    limite = tipo.split("boundary=")[1]
    texto = corpo.decode("utf-8")
    assert texto.count(f"--{limite}\r\n") == 5 and texto.endswith(f"--{limite}--\r\n")
    assert 'name="platform[]"\r\n\r\nyoutube' in texto and "tiktok" in texto
    assert 'name="async_upload"\r\n\r\ntrue' in texto and 'name="x"' not in texto
    assert 'name="video"; filename="video.mp4"' in texto and "BINARIO" in texto
    assert "Content-Type: video/mp4" in texto


# --- OAuth de desktop -----------------------------------------------------------------------------
def test_pkce_base64url_e_hexadecimal():
    verificador, desafio = oauth.gerar_pkce()
    assert 43 <= len(verificador) <= 128
    assert "=" not in desafio and len(desafio) == 43  # sha256 em base64url, sem preenchimento
    verificador, desafio = oauth.gerar_pkce("hex")
    assert desafio == hashlib.sha256(verificador.encode()).hexdigest()


def _navegador_que_aceita(parametros: str = "code=CODIGO123&state={estado}"):
    """Um 'navegador' que, logo depois, abre o endereço de retorno com o que a rede devolveria."""
    abertos: list[str] = []

    def abrir(url: str) -> None:
        abertos.append(url)
        consulta = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        retorno = consulta["redirect_uri"]
        destino = f"{retorno}?{parametros.format(estado=consulta['state'])}"
        threading.Timer(0.05, lambda: urllib.request.urlopen(destino, timeout=5).read()).start()

    return abrir, abertos


def _montar(redirect_uri: str, estado: str) -> str:
    return "https://rede.exemplo/auth?" + urllib.parse.urlencode(
        {"redirect_uri": redirect_uri, "state": estado}
    )


def test_oauth_loopback_recebe_o_codigo_pelo_endereco_local():
    abrir, abertos = _navegador_que_aceita()
    auth = oauth.aguardar_codigo(_montar, abrir=abrir, tempo_limite_s=10)
    assert auth.codigo == "CODIGO123"
    assert auth.redirect_uri.startswith("http://127.0.0.1:") and auth.redirect_uri.endswith(
        "/callback"
    )
    assert len(abertos) == 1


def test_oauth_loopback_com_porta_e_caminho_fixos_como_o_tiktok_exige():
    abrir, _ = _navegador_que_aceita()
    auth = oauth.aguardar_codigo(
        _montar, porta=53699, caminho="/callback/", abrir=abrir, tempo_limite_s=10
    )
    assert auth.redirect_uri == "http://127.0.0.1:53699/callback/"


def test_oauth_recusa_state_diferente():
    abrir, _ = _navegador_que_aceita("code=X&state=outro")
    with pytest.raises(ErroDePublicacao, match="não confere"):
        oauth.aguardar_codigo(_montar, abrir=abrir, tempo_limite_s=10)


def test_oauth_leva_a_recusa_da_pessoa_ao_erro():
    abrir, _ = _navegador_que_aceita("error=access_denied&state={estado}")
    with pytest.raises(ErroDePublicacao, match="access_denied"):
        oauth.aguardar_codigo(_montar, abrir=abrir, tempo_limite_s=10)


def test_oauth_desiste_se_ninguem_autoriza_e_respeita_o_cancelamento():
    with pytest.raises(ErroDePublicacao, match="a tempo"):
        oauth.aguardar_codigo(_montar, abrir=lambda _u: None, tempo_limite_s=0.5)
    with pytest.raises(Cancelado):
        oauth.aguardar_codigo(
            _montar, abrir=lambda _u: None, tempo_limite_s=10, cancelado=lambda: True
        )


def test_oauth_avisa_quando_a_porta_esta_ocupada():
    abrir, _ = _navegador_que_aceita()
    import socket

    with socket.socket() as ocupante:
        ocupante.bind(("127.0.0.1", 0))
        ocupante.listen()
        porta = ocupante.getsockname()[1]
        with pytest.raises(ErroDePublicacao, match="porta"):
            oauth.aguardar_codigo(_montar, porta=porta, abrir=abrir, tempo_limite_s=2)
