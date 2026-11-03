"""YouTube: conectar, enviar em pedaços, agendar (privado + publishAt) e conferir o resultado."""

from __future__ import annotations

import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from spurgeon.publicacao import youtube as yt
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDePublicacao, FaltaConfigurar, ReconectarNecessario
from spurgeon.publicacao.modelo import AGENDADO, ENVIADO, Pacote, iso_utc
from spurgeon.publicacao.youtube import YouTube
from tests.falso_de_rede import Relogio, TransporteFalso, resposta, sem_dormir

BRASILIA = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 2, 12, 0, tzinfo=BRASILIA)
SESSAO = "https://upload.exemplo/sessao-1"
TOKEN_OK = resposta(200, {"access_token": "AT", "expires_in": 3600})


@pytest.fixture(autouse=True)
def sem_esperar(monkeypatch):
    sem_dormir(monkeypatch, yt)


@pytest.fixture
def cofre(tmp_path):
    c = Cofre(tmp_path / "c.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1])
    c.definir("youtube", "client_id", "ID")
    c.definir("youtube", "client_secret", "SEGREDO", segredo=True)
    return c


@pytest.fixture
def conectado(cofre):
    cofre.definir("youtube", "refresh_token", "REFRESH", segredo=True)
    return cofre


@pytest.fixture
def pacote(tmp_path, monkeypatch):
    monkeypatch.setattr(yt, "PEDACO_DO_ENVIO", 256 * 1024)  # 3 pedaços com o arquivo abaixo
    arquivo = tmp_path / "short_01_x.mp4"
    arquivo.write_bytes(b"v" * 600_000)
    return Pacote(
        arquivo, "Deus é fiel em toda geração", "Uma palavra do Salmo 90.", ("fé", "salmo90"), 45.0
    )


def _youtube(cofre, transporte) -> YouTube:
    return YouTube(cofre, transporte, relogio=Relogio(AGORA))


def _rotas_de_envio(transporte: TransporteFalso, *, leitura) -> TransporteFalso:
    return (
        transporte.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
        .responder("POST", "upload/youtube/v3/videos", resposta(200, None, {"Location": SESSAO}))
        .responder(
            "PUT",
            SESSAO,
            [
                resposta(308, None, {"Range": "bytes=0-262143"}),
                resposta(308, None, {"Range": "bytes=0-524287"}),
                resposta(201, {"id": "VID123"}),
            ],
        )
        .responder("GET", "youtube/v3/videos?part=status", leitura)
    )


# --- textos ---------------------------------------------------------------------------------------
def test_titulo_respeita_100_caracteres_e_tira_sinais_proibidos():
    assert yt.titulo_do_youtube("  Uma <b>palavra</b>   boa ") == "Uma bpalavra/b boa"
    longo = "palavra " * 30
    cortado = yt.titulo_do_youtube(longo)
    assert len(cortado) <= 100 and cortado.endswith("…")


def test_descricao_cabe_nos_5000_bytes_mesmo_com_acentos(tmp_path):
    p = Pacote(tmp_path / "v.mp4", "T", "ção " * 3000, ("fé",), 30)
    texto = yt.descricao_do_youtube(p)
    assert len(texto.encode("utf-8")) <= yt.LIMITE_DA_DESCRICAO_BYTES


def test_tags_ate_500_caracteres_sem_o_jogo_da_velha(tmp_path):
    p = Pacote(tmp_path / "v.mp4", "T", "", tuple(f"etiqueta{i:03d}" for i in range(100)), 30)
    tags = yt.tags_do_youtube(p)
    assert tags[0] == "etiqueta000" and "#" not in "".join(tags)
    assert sum(len(t) + 1 for t in tags) <= yt.LIMITE_DAS_TAGS and 0 < len(tags) < 100


# --- conferir antes de enviar ---------------------------------------------------------------------
def test_problemas_apontam_o_que_impede(cofre, pacote):
    plataforma = _youtube(cofre, TransporteFalso())
    achados = " ".join(plataforma.problemas(pacote, None))
    assert "não está conectado" in achados
    sem_titulo = Pacote(pacote.video, "  ", "", (), 30)
    assert "sem título" in " ".join(_youtube(cofre, TransporteFalso()).problemas(sem_titulo, None))


def test_problemas_horario_muito_proximo_ou_passado_e_short_longo(conectado, pacote):
    plataforma = _youtube(conectado, TransporteFalso())
    assert "1 minuto" in " ".join(plataforma.problemas(pacote, AGORA + timedelta(seconds=30)))
    assert "1 minuto" in " ".join(plataforma.problemas(pacote, AGORA - timedelta(hours=1)))
    assert not plataforma.problemas(pacote, AGORA + timedelta(minutes=5))
    longo = Pacote(pacote.video, "T", "", (), 200.0)
    assert "3 minutos" in " ".join(plataforma.problemas(longo, None))


# --- publicar agora -------------------------------------------------------------------------------
def test_publicar_agora_envia_em_pedacos_e_confere(conectado, pacote):
    leitura = resposta(200, {"items": [{"status": {"privacyStatus": "public"}}]})
    rede = _rotas_de_envio(TransporteFalso(), leitura=leitura)
    progresso: list[float] = []
    resultado = _youtube(conectado, rede).publicar(pacote, None, ao_progresso=progresso.append)

    assert (resultado.estado, resultado.id_remoto) == (ENVIADO, "VID123")
    assert resultado.url == "https://www.youtube.com/shorts/VID123" and not resultado.aviso
    abertura = rede.chamadas_de("POST", "upload/youtube/v3/videos")[0]
    assert abertura.cabecalhos["Authorization"] == "Bearer AT"
    assert abertura.cabecalhos["X-Upload-Content-Length"] == "600000"
    corpo = abertura.json
    assert corpo["snippet"]["title"] == "Deus é fiel em toda geração"
    assert "#fé #salmo90" in corpo["snippet"]["description"]
    assert corpo["snippet"]["tags"] == ["fé", "salmo90"]
    assert corpo["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    faixas = [c.cabecalhos["Content-Range"] for c in rede.chamadas_de("PUT", SESSAO)]
    assert faixas == [
        "bytes 0-262143/600000",
        "bytes 262144-524287/600000",
        "bytes 524288-599999/600000",
    ]
    assert progresso[-1] == 1.0 and progresso == sorted(progresso)


def test_publicar_agendado_manda_privado_com_publishat_em_utc(conectado, pacote):
    quando = AGORA + timedelta(days=2)
    leitura = resposta(
        200, {"items": [{"status": {"privacyStatus": "private", "publishAt": iso_utc(quando)}}]}
    )
    rede = _rotas_de_envio(TransporteFalso(), leitura=leitura)
    resultado = _youtube(conectado, rede).publicar(pacote, quando)

    assert resultado.estado == AGENDADO and resultado.quando == "2026-11-04T15:00:00Z"
    assert not resultado.aviso
    status = rede.chamadas_de("POST", "upload/youtube/v3/videos")[0].json["status"]
    assert status == {
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": False,
        "publishAt": "2026-11-04T15:00:00Z",
    }


def test_avisa_quando_o_youtube_travou_o_video_como_privado(conectado, pacote):
    leitura = resposta(200, {"items": [{"status": {"privacyStatus": "private"}}]})
    resultado = _youtube(conectado, _rotas_de_envio(TransporteFalso(), leitura=leitura)).publicar(
        pacote, None
    )
    assert resultado.estado == ENVIADO
    assert "privado" in resultado.aviso and "verificação" in resultado.aviso


def test_avisa_quando_o_horario_marcado_nao_foi_registrado(conectado, pacote):
    leitura = resposta(200, {"items": [{"status": {"privacyStatus": "private"}}]})
    rede = _rotas_de_envio(TransporteFalso(), leitura=leitura)
    resultado = _youtube(conectado, rede).publicar(pacote, AGORA + timedelta(days=1))
    assert resultado.estado == AGENDADO and "não registrou o horário" in resultado.aviso


def test_o_video_subiu_mas_a_conferencia_falhou_nao_e_erro(conectado, pacote):
    rede = _rotas_de_envio(TransporteFalso(), leitura=resposta(500, {"error": {"message": "x"}}))
    resultado = _youtube(conectado, rede).publicar(pacote, None)
    assert resultado.estado == ENVIADO and resultado.id_remoto == "VID123"


def test_envio_retoma_de_onde_parou_depois_de_uma_falha_passageira(conectado, pacote):
    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder("POST", "upload/youtube/v3/videos", resposta(200, None, {"Location": SESSAO}))
    rede.responder(
        "PUT",
        SESSAO,
        [
            resposta(503, {"error": {"message": "indisponível"}}),  # 1º pedaço cai
            resposta(308, None, {"Range": "bytes=0-262143"}),  # "bytes */total": já foi o 1º
            resposta(308, None, {"Range": "bytes=0-524287"}),
            resposta(201, {"id": "VID9"}),
        ],
    )
    rede.responder("GET", "videos?part=status", resposta(200, {"items": [{"status": {}}]}))
    resultado = _youtube(conectado, rede).publicar(pacote, None)
    assert resultado.id_remoto == "VID9"
    faixas = [c.cabecalhos["Content-Range"] for c in rede.chamadas_de("PUT", SESSAO)]
    assert faixas[1] == "bytes */600000" and faixas[2] == "bytes 262144-524287/600000"


def test_envio_desiste_depois_de_falhas_demais(conectado, pacote):
    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder("POST", "upload/youtube/v3/videos", resposta(200, None, {"Location": SESSAO}))
    rede.responder("PUT", SESSAO, resposta(503, {"error": {"message": "fora do ar"}}))
    with pytest.raises(ErroDePublicacao, match="503"):
        _youtube(conectado, rede).publicar(pacote, None)


def test_publicar_sem_conectar_ou_com_problema_nao_toca_a_rede(cofre, pacote):
    rede = TransporteFalso()  # qualquer pedido falharia o teste
    with pytest.raises(ErroDePublicacao, match="não está conectado"):
        _youtube(cofre, rede).publicar(pacote, None)
    assert rede.chamadas == []


# --- acesso ---------------------------------------------------------------------------------------
def test_refresh_recusado_pede_para_conectar_de_novo(conectado, pacote):
    rede = TransporteFalso().responder(
        "POST", "oauth2.googleapis.com/token",
        resposta(400, {"error": "invalid_grant", "error_description": "Token has been expired"}),
    )  # fmt: skip
    with pytest.raises(ReconectarNecessario, match="Conecte o YouTube de novo"):
        _youtube(conectado, rede).publicar(pacote, None)


def test_acesso_vencido_no_meio_renova_e_repete(conectado):
    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder(
        "GET", "channels",
        [resposta(401, {"error": {"message": "Invalid Credentials"}}),
         resposta(200, {"items": [{"snippet": {"title": "Igreja"}}]})],
    )  # fmt: skip
    assert _youtube(conectado, rede)._canal() == "Igreja"
    assert len(rede.chamadas_de("POST", "oauth2.googleapis.com/token")) == 2


def test_acesso_ainda_valido_nao_renova_a_cada_pedido(conectado):
    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder("GET", "channels", resposta(200, {"items": [{"snippet": {"title": "Igreja"}}]}))
    plataforma = _youtube(conectado, rede)
    plataforma._canal()
    plataforma._canal()
    assert len(rede.chamadas_de("POST", "oauth2.googleapis.com/token")) == 1


# --- conectar -------------------------------------------------------------------------------------
def _navegador():
    pedidos: list[str] = []

    def abrir(url: str) -> None:
        pedidos.append(url)
        consulta = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        destino = f"{consulta['redirect_uri']}?code=COD&state={consulta['state']}"
        threading.Timer(0.05, lambda: urllib.request.urlopen(destino, timeout=5).read()).start()

    return abrir, pedidos


def test_conectar_guarda_o_refresh_token_no_cofre_e_mostra_o_canal(cofre):
    rede = TransporteFalso()
    rede.responder(
        "POST", "oauth2.googleapis.com/token",
        resposta(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}),
    )  # fmt: skip
    rede.responder(
        "GET", "channels", resposta(200, {"items": [{"snippet": {"title": "Igreja X"}}]})
    )
    abrir, pedidos = _navegador()
    plataforma = _youtube(cofre, rede)
    assert not plataforma.conectada

    assert plataforma.conectar(abrir=abrir) == "Igreja X"

    assert plataforma.conectada and plataforma.resumo_da_conta == "Igreja X"
    assert cofre.obter("youtube", "refresh_token") == "RT"
    autorizacao = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(pedidos[0]).query))
    assert autorizacao["scope"] == yt.ESCOPO and autorizacao["code_challenge_method"] == "S256"
    assert autorizacao["access_type"] == "offline" and autorizacao["prompt"] == "consent"
    troca = rede.chamadas_de("POST", "oauth2.googleapis.com/token")[0].formulario
    assert troca["code"] == "COD" and troca["client_secret"] == "SEGREDO"
    assert troca["code_verifier"] and troca["grant_type"] == "authorization_code"


def test_conectar_sem_as_chaves_pede_para_preencher(tmp_path):
    vazio = Cofre(tmp_path / "v.json", proteger=lambda b: b, revelar=lambda b: b)
    with pytest.raises(FaltaConfigurar, match="ID do cliente"):
        _youtube(vazio, TransporteFalso()).conectar()


def test_conectar_sem_refresh_token_explica_como_resolver(cofre):
    rede = TransporteFalso().responder(
        "POST", "oauth2.googleapis.com/token", resposta(200, {"access_token": "AT"})
    )
    abrir, _ = _navegador()
    with pytest.raises(ErroDePublicacao, match="permissions"):
        _youtube(cofre, rede).conectar(abrir=abrir)
    assert not cofre.tem("youtube", "refresh_token")


def test_conta_google_sem_canal_e_explicada(cofre):
    rede = TransporteFalso()
    rede.responder(
        "POST", "oauth2.googleapis.com/token",
        resposta(200, {"access_token": "AT", "refresh_token": "RT"}),
    )  # fmt: skip
    rede.responder("GET", "channels", resposta(200, {"items": []}))
    abrir, _ = _navegador()
    with pytest.raises(ErroDePublicacao, match="não tem um canal"):
        _youtube(cofre, rede).conectar(abrir=abrir)


# --- depois de enviado ----------------------------------------------------------------------------
def test_consultar_agendado_que_foi_ao_ar_vira_publicado(conectado):
    from spurgeon.publicacao.modelo import Resultado

    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder(
        "GET", "videos?part=status",
        resposta(200, {"items": [{"status": {"privacyStatus": "public"}}]}),
    )  # fmt: skip
    r = Resultado("youtube", AGENDADO, "VID1", quando="2026-11-04T15:00:00Z", aviso="velho")
    r = _youtube(conectado, rede).consultar(r)
    assert r.estado == ENVIADO and r.aviso == ""


def test_consultar_video_apagado_avisa(conectado):
    from spurgeon.publicacao.modelo import Resultado

    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder("GET", "videos?part=status", resposta(200, {"items": []}))
    r = _youtube(conectado, rede).consultar(Resultado("youtube", AGENDADO, "VID1"))
    assert "não existe mais" in r.aviso


def test_reagendar_reenvia_o_status_inteiro(conectado):
    from spurgeon.publicacao.modelo import Resultado

    rede = TransporteFalso()
    rede.responder("POST", "oauth2.googleapis.com/token", TOKEN_OK)
    rede.responder("PUT", "youtube/v3/videos?part=status", resposta(200, {"id": "VID1"}))
    novo = AGORA + timedelta(days=3)
    r = _youtube(conectado, rede).reagendar(Resultado("youtube", AGENDADO, "VID1"), novo)
    corpo = rede.chamadas_de("PUT", "part=status")[0].json
    assert corpo["id"] == "VID1" and corpo["status"]["privacyStatus"] == "private"
    assert corpo["status"]["publishAt"] == iso_utc(novo) == r.quando
    with pytest.raises(ErroDePublicacao, match="1 minuto"):
        _youtube(conectado, rede).reagendar(r, AGORA)


def test_dados_do_arquivo_de_teste_existem(pacote: Pacote):
    assert Path(pacote.video).stat().st_size == 600_000
