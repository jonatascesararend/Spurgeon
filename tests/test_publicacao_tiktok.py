"""TikTok (assistido e rascunhos pela API) e o intermediário Upload-Post."""

from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from spurgeon.publicacao import tiktok as tk
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import (
    ErroDePublicacao,
    ErroDeRede,
    FaltaConfigurar,
    ReconectarNecessario,
)
from spurgeon.publicacao.intermediario import UploadPost
from spurgeon.publicacao.modelo import AGENDADO, ASSISTIDO, ENVIADO, RASCUNHO, Pacote, Resultado
from spurgeon.publicacao.tiktok import TikTok, plano_dos_pedacos
from tests.falso_de_rede import Relogio, TransporteFalso, resposta, sem_dormir

BRASILIA = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 2, 12, 0, tzinfo=BRASILIA)
OK = {"code": "ok", "message": "", "log_id": "L"}
DESTINO = "https://upload.tiktok.exemplo/up"


@pytest.fixture(autouse=True)
def sem_esperar(monkeypatch):
    sem_dormir(monkeypatch, tk)


@pytest.fixture
def cofre(tmp_path):
    return Cofre(tmp_path / "c.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1])


@pytest.fixture
def pacote(tmp_path):
    arquivo = tmp_path / "short.mp4"
    arquivo.write_bytes(b"v" * 5000)
    return Pacote(arquivo, "Deus é fiel", "Uma palavra do Salmo 90.", ("fé",), 45.0)


def _tiktok(cofre, rede) -> TikTok:
    return TikTok(cofre, rede, relogio=Relogio(AGORA))


def _conectar_fake(cofre) -> None:
    cofre.definir("tiktok", "client_key", "KEY")
    cofre.definir("tiktok", "client_secret", "SEG", segredo=True)
    cofre.definir("tiktok", "refresh_token", "RT", segredo=True)
    cofre.definir("tiktok", "access_token", "AT", segredo=True)
    cofre.definir("tiktok", "access_vence", str(time.time() + 3600))


# --- os pedaços do envio --------------------------------------------------------------------------
@pytest.mark.parametrize("megas", [1, 5, 40, 64, 65, 100, 200, 400])
def test_plano_dos_pedacos_obedece_as_regras_do_tiktok(megas):
    tamanho = megas * 1024 * 1024 + 123
    pedaco, total = plano_dos_pedacos(tamanho)
    assert total == tamanho // pedaco  # a regra: total = floor(tamanho / pedaço)
    ultimo = tamanho - pedaco * (total - 1)
    assert pedaco <= 64 * 1024 * 1024 or total == 1
    assert ultimo <= 128 * 1024 * 1024
    if total == 1:
        assert pedaco == tamanho


# --- fluxo assistido ------------------------------------------------------------------------------
def test_sem_chaves_o_tiktok_continua_disponivel_no_modo_assistido(cofre, pacote):
    rede = TransporteFalso()  # o modo assistido não conversa com a rede
    plataforma = _tiktok(cofre, rede)
    assert plataforma.disponivel and not plataforma.conectada
    assert "assistido" in plataforma.resumo_da_conta

    resultado = plataforma.publicar(pacote, None)

    assert resultado.estado == ASSISTIDO and resultado.url == tk.URL_DO_ESTUDIO
    assert (
        resultado.dados["legenda"].startswith("Deus é fiel") and "#fé" in resultado.dados["legenda"]
    )
    assert resultado.dados["arquivo"] == str(pacote.video)
    assert rede.chamadas == []


def test_assistido_com_horario_diz_para_agendar_no_estudio(cofre, pacote):
    resultado = _tiktok(cofre, TransporteFalso()).publicar(pacote, AGORA + timedelta(days=1))
    assert "Agendar" in resultado.detalhe and "03/11/2026 às 12:00" in resultado.detalhe


def test_legenda_do_tiktok_respeita_2200_caracteres(cofre, tmp_path):
    arquivo = tmp_path / "v.mp4"
    arquivo.write_bytes(b"x")
    longo = Pacote(arquivo, "T", "palavra " * 1000, ("fé",), 30)
    assert len(_tiktok(cofre, TransporteFalso()).publicar(longo, None).dados["legenda"]) <= 2200


# --- rascunhos pela API ---------------------------------------------------------------------------
def _rede_de_rascunho(status=("SEND_TO_USER_INBOX",)) -> TransporteFalso:
    rede = TransporteFalso()
    rede.responder(
        "POST", "/post/publish/inbox/video/init/",
        resposta(200, {"data": {"publish_id": "P1", "upload_url": DESTINO}, "error": OK}),
    )  # fmt: skip
    rede.responder("PUT", DESTINO, resposta(201))
    rede.responder(
        "POST", "/post/publish/status/fetch/",
        [resposta(200, {"data": {"status": s}, "error": OK}) for s in status],
    )  # fmt: skip
    return rede


def test_envio_para_os_rascunhos_em_um_pedaco(cofre, pacote):
    _conectar_fake(cofre)
    rede = _rede_de_rascunho(("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"))
    plataforma = _tiktok(cofre, rede)
    assert plataforma.conectada

    resultado = plataforma.publicar(pacote, None)

    assert resultado.estado == RASCUNHO and resultado.id_remoto == "P1"
    assert "notificação" in resultado.detalhe
    iniciar = rede.chamadas_de("POST", "inbox/video/init")[0]
    assert iniciar.cabecalhos["Authorization"] == "Bearer AT"
    assert iniciar.json["source_info"] == {
        "source": "FILE_UPLOAD", "video_size": 5000, "chunk_size": 5000, "total_chunk_count": 1,
    }  # fmt: skip
    envio = rede.chamadas_de("PUT", DESTINO)[0]
    assert envio.cabecalhos["Content-Range"] == "bytes 0-4999/5000" and len(envio.corpo) == 5000
    assert len(rede.chamadas_de("POST", "status/fetch")) == 2


def test_envio_em_varios_pedacos_manda_o_resto_no_ultimo(cofre, pacote, monkeypatch):
    _conectar_fake(cofre)
    monkeypatch.setattr(tk, "MAIOR_PEDACO", 2000)  # 5000 bytes → 3 pedaços de 1666 (o último, 1668)
    rede = _rede_de_rascunho()
    _tiktok(cofre, rede).publicar(pacote, None)
    faixas = [c.cabecalhos["Content-Range"] for c in rede.chamadas_de("PUT", DESTINO)]
    assert faixas == ["bytes 0-1665/5000", "bytes 1666-3331/5000", "bytes 3332-4999/5000"]
    assert (
        rede.chamadas_de("POST", "inbox/video/init")[0].json["source_info"]["total_chunk_count"]
        == 3
    )


def test_tiktok_recusou_o_video(cofre, pacote):
    _conectar_fake(cofre)
    rede = _rede_de_rascunho()
    rede.rotas[2].destino = resposta(
        200, {"data": {"status": "FAILED", "fail_reason": "duration_check_failed"}, "error": OK}
    )
    with pytest.raises(ErroDePublicacao, match="duration_check_failed"):
        _tiktok(cofre, rede).publicar(pacote, None)


def test_erro_no_corpo_mesmo_com_http_200_vira_erro(cofre, pacote):
    _conectar_fake(cofre)
    rede = TransporteFalso().responder(
        "POST", "inbox/video/init",
        resposta(
            200,
            {"error": {"code": "spam_risk_too_many_pending_share", "message": "Muitos envios"}},
        ),
    )  # fmt: skip
    with pytest.raises(ErroDeRede, match="Muitos envios"):
        _tiktok(cofre, rede).publicar(pacote, None)


def test_acesso_vencido_renova_e_guarda_o_refresh_token_novo(cofre, pacote):
    _conectar_fake(cofre)
    cofre.definir("tiktok", "access_vence", str(time.time() - 10))
    rede = _rede_de_rascunho()
    rede.responder(
        "POST", "/v2/oauth/token/",
        resposta(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200}),
    )  # fmt: skip
    _tiktok(cofre, rede).publicar(pacote, None)
    renovar = rede.chamadas_de("POST", "/v2/oauth/token/")[0].formulario
    assert renovar["grant_type"] == "refresh_token" and renovar["refresh_token"] == "RT"
    assert cofre.obter("tiktok", "refresh_token") == "RT2"  # o TikTok troca a cada renovação
    assert (
        rede.chamadas_de("POST", "inbox/video/init")[0].cabecalhos["Authorization"] == "Bearer AT2"
    )


def test_refresh_do_tiktok_recusado_pede_para_conectar_de_novo(cofre, pacote):
    _conectar_fake(cofre)
    cofre.definir("tiktok", "access_vence", str(time.time() - 10))
    rede = TransporteFalso().responder(
        "POST", "/v2/oauth/token/", resposta(400, {"error": "invalid_grant"})
    )
    with pytest.raises(ReconectarNecessario):
        _tiktok(cofre, rede).publicar(pacote, None)


def test_video_com_mais_de_10_minutos_e_recusado_so_no_modo_api(cofre, pacote):
    longo = Pacote(pacote.video, "T", "", (), 700.0)
    assert _tiktok(cofre, TransporteFalso()).publicar(longo, None).estado == ASSISTIDO
    _conectar_fake(cofre)
    with pytest.raises(ErroDePublicacao, match="10 minutos"):
        _tiktok(cofre, TransporteFalso()).publicar(longo, None)


def test_conectar_o_tiktok_usa_porta_fixa_pkce_hexadecimal_e_guarda_os_acessos(cofre):
    cofre.definir("tiktok", "client_key", "KEY")
    cofre.definir("tiktok", "client_secret", "SEG", segredo=True)
    rede = TransporteFalso()
    rede.responder(
        "POST", "/v2/oauth/token/",
        resposta(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 7200}),
    )  # fmt: skip
    rede.responder(
        "GET", "/user/info/",
        resposta(200, {"data": {"user": {"display_name": "Igreja no TikTok"}}, "error": OK}),
    )  # fmt: skip
    pedidos: list[str] = []

    def abrir(url: str) -> None:
        pedidos.append(url)
        consulta = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        destino = f"{consulta['redirect_uri']}?code=COD&state={consulta['state']}"
        threading.Timer(0.05, lambda: urllib.request.urlopen(destino, timeout=5).read()).start()

    plataforma = _tiktok(cofre, rede)
    assert plataforma.conectar(abrir=abrir) == "Igreja no TikTok"

    autorizacao = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(pedidos[0]).query))
    assert (
        autorizacao["redirect_uri"] == tk.ENDERECO_DE_RETORNO == "http://127.0.0.1:53682/callback/"
    )
    assert autorizacao["scope"] == "user.info.basic,video.upload"
    assert len(autorizacao["code_challenge"]) == 64  # SHA-256 em hexadecimal, não em base64
    troca = rede.chamadas_de("POST", "/v2/oauth/token/")[0].formulario
    assert troca["client_key"] == "KEY" and troca["code_verifier"] and troca["code"] == "COD"
    assert plataforma.conectada and plataforma.resumo_da_conta == "Igreja no TikTok"
    assert cofre.obter("tiktok", "refresh_token") == "RT"


def test_conectar_o_tiktok_sem_chaves_pede_para_preencher(cofre):
    with pytest.raises(FaltaConfigurar, match="chave"):
        _tiktok(cofre, TransporteFalso()).conectar()


# --- Upload-Post ----------------------------------------------------------------------------------
@pytest.fixture
def upload_post(cofre):
    cofre.definir("upload_post", "chave_da_api", "CHAVE", segredo=True)
    cofre.definir("upload_post", "usuario", "igreja")
    return cofre


def _up(cofre, rede) -> UploadPost:
    return UploadPost(cofre, rede, relogio=Relogio(AGORA))


def test_upload_post_validar_a_chave_ao_conectar(upload_post):
    rede = TransporteFalso().responder(
        "GET", "/api/uploadposts/me", resposta(200, {"email": "igreja@exemplo.org"})
    )
    resumo = _up(upload_post, rede).conectar()
    assert resumo == "perfil igreja (igreja@exemplo.org)"
    assert rede.chamadas[0].cabecalhos["Authorization"] == "Apikey CHAVE"


def test_upload_post_publica_nas_quatro_redes_de_uma_vez(upload_post, pacote):
    rede = TransporteFalso().responder("POST", "/api/upload", resposta(202, {"job_id": "J1"}))
    resultado = _up(upload_post, rede).publicar(pacote, None)
    assert resultado.estado == ENVIADO and resultado.id_remoto == "J1"
    chamada = rede.chamadas_de("POST", "/api/upload")[0]
    assert chamada.cabecalhos["Content-Type"].startswith("multipart/form-data; boundary=")
    corpo = chamada.corpo.decode("utf-8", errors="replace")
    for rede_alvo in ("youtube", "instagram", "facebook", "tiktok"):
        assert f'name="platform[]"\r\n\r\n{rede_alvo}' in corpo
    assert 'name="user"\r\n\r\nigreja' in corpo
    assert (
        "PUBLIC_TO_EVERYONE" in corpo and "DIRECT_POST" in corpo and "scheduled_date" not in corpo
    )
    assert 'name="tiktok_title"' in corpo and "#fé" in corpo


def test_upload_post_agenda_em_utc_e_respeita_as_redes_escolhidas(upload_post, pacote):
    upload_post.definir("upload_post", "redes", "YouTube, tiktok, lixo")
    rede = TransporteFalso().responder("POST", "/api/upload", resposta(202, {"request_id": "R1"}))
    resultado = _up(upload_post, rede).publicar(pacote, AGORA + timedelta(days=2))
    assert resultado.estado == AGENDADO and resultado.id_remoto == "R1"
    corpo = rede.chamadas[0].corpo.decode("utf-8", errors="replace")
    assert 'name="scheduled_date"\r\n\r\n2026-11-04T15:00:00Z' in corpo
    assert 'name="timezone"\r\n\r\nUTC' in corpo
    assert "instagram" not in corpo.split('name="platform[]"')[1].split('name="title"')[0]


def test_upload_post_recusa_com_o_motivo_da_rede(upload_post, pacote):
    rede = TransporteFalso().responder(
        "POST", "/api/upload", resposta(402, {"message": "Plano não inclui TikTok"})
    )
    with pytest.raises(ErroDePublicacao, match="Plano não inclui TikTok"):
        _up(upload_post, rede).publicar(pacote, None)


def test_upload_post_consultar_agendado_concluido_vira_publicado(upload_post):
    rede = TransporteFalso().responder(
        "GET", "/api/uploadposts/status", resposta(200, {"status": "completed"})
    )
    r = _up(upload_post, rede).consultar(Resultado("upload_post", AGENDADO, "J1"))
    assert r.estado == ENVIADO


def test_upload_post_sem_conectar_nao_toca_a_rede(cofre, pacote):
    rede = TransporteFalso()
    with pytest.raises(ErroDePublicacao, match="não está conectado"):
        _up(cofre, rede).publicar(pacote, None)
    assert rede.chamadas == []
