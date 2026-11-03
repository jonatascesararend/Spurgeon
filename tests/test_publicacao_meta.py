"""Meta: conectar (token → token da Página), Instagram com fila local e Facebook com agendamento."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spurgeon.core.erros import Cancelado
from spurgeon.publicacao import meta
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDePublicacao, FaltaConfigurar, ReconectarNecessario
from spurgeon.publicacao.meta import Facebook, Instagram, legenda_do_instagram
from spurgeon.publicacao.modelo import AGENDADO, ENVIADO, NA_FILA, Pacote, Resultado
from tests.falso_de_rede import Relogio, TransporteFalso, resposta, sem_dormir

BRASILIA = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 2, 12, 0, tzinfo=BRASILIA)
PAGINA = {
    "id": "PG1", "name": "Igreja Exemplo", "access_token": "PAGE-TOKEN",
    "instagram_business_account": {"id": "IG1", "username": "igrejaexemplo"},
}  # fmt: skip
RUPLOAD_IG = "https://rupload.facebook.com/ig-api-upload/v23.0/C1"


@pytest.fixture(autouse=True)
def sem_esperar(monkeypatch):
    sem_dormir(monkeypatch, meta)


@pytest.fixture
def cofre(tmp_path):
    c = Cofre(tmp_path / "c.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1])
    c.definir("meta", "app_id", "APP")
    c.definir("meta", "app_secret", "SEGREDO", segredo=True)
    c.definir("meta", "token", "TOKEN-CURTO", segredo=True)
    return c


@pytest.fixture
def conectado(cofre):
    cofre.definir("meta", "page_id", "PG1")
    cofre.definir("meta", "page_token", "PAGE-TOKEN", segredo=True)
    cofre.definir("meta", "ig_id", "IG1")
    cofre.remover("meta", "token")
    return cofre


@pytest.fixture
def pacote(tmp_path):
    arquivo = tmp_path / "short.mp4"
    arquivo.write_bytes(b"v" * 5000)
    return Pacote(arquivo, "Deus é fiel", "Uma palavra do Salmo 90.", ("fé", "salmo90"), 45.0)


def _instagram(cofre, rede, relogio=None) -> Instagram:
    return Instagram(cofre, rede, relogio=relogio or Relogio(AGORA))


def _facebook(cofre, rede, relogio=None) -> Facebook:
    return Facebook(cofre, rede, relogio=relogio or Relogio(AGORA))


def _rede_do_instagram(*, status=("FINISHED",)) -> TransporteFalso:
    rede = TransporteFalso()
    rede.responder("POST", "/IG1/media_publish", resposta(200, {"id": "M1"}))
    rede.responder("POST", "/IG1/media", resposta(200, {"id": "C1", "uri": RUPLOAD_IG}))
    rede.responder("POST", RUPLOAD_IG, resposta(200, {"success": True}))
    rede.responder("GET", "/C1", [resposta(200, {"status_code": s}) for s in status])
    rede.responder(
        "GET", "/M1", resposta(200, {"permalink": "https://www.instagram.com/reel/abc/"})
    )
    return rede


# --- conectar -------------------------------------------------------------------------------------
def _rede_de_conexao(paginas) -> TransporteFalso:
    rede = TransporteFalso()
    rede.responder("GET", "/oauth/access_token", resposta(200, {"access_token": "LONGO"}))
    rede.responder("GET", "/me/accounts", resposta(200, {"data": paginas}))
    return rede


def test_conectar_troca_o_token_e_guarda_o_da_pagina(cofre):
    rede = _rede_de_conexao([PAGINA])
    plataforma = _instagram(cofre, rede)
    assert not plataforma.conectada

    resumo = plataforma.conectar()

    assert resumo == "Página Igreja Exemplo · Instagram @igrejaexemplo"
    assert plataforma.conectada and _facebook(cofre, rede).conectada  # uma conta para as duas
    assert cofre.obter("meta", "page_token") == "PAGE-TOKEN"
    assert cofre.obter("meta", "ig_id") == "IG1"
    assert not cofre.tem("meta", "token")  # o token colado não fica guardado
    troca = rede.chamadas_de("GET", "/oauth/access_token")[0].consulta
    assert (
        troca["grant_type"] == "fb_exchange_token" and troca["fb_exchange_token"] == "TOKEN-CURTO"
    )
    assert rede.chamadas_de("GET", "/me/accounts")[0].consulta["access_token"] == "LONGO"
    assert "/v23.0/" in rede.chamadas[0].url


def test_conectar_com_varias_paginas_pede_para_escolher(cofre):
    outra = {"id": "PG2", "name": "Outra Página", "access_token": "T2"}
    with pytest.raises(ErroDePublicacao, match="mais de uma Página"):
        _instagram(cofre, _rede_de_conexao([PAGINA, outra])).conectar()
    cofre.definir("meta", "pagina", "outra página")
    _instagram(cofre, _rede_de_conexao([PAGINA, outra])).conectar()
    assert cofre.obter("meta", "page_id") == "PG2"
    assert cofre.obter("meta", "ig_id") == ""  # a Página escolhida não tem Instagram


def test_conectar_pagina_inexistente_ou_token_sem_paginas(cofre):
    cofre.definir("meta", "pagina", "Não existe")
    with pytest.raises(ErroDePublicacao, match="Não achei a Página"):
        _instagram(cofre, _rede_de_conexao([PAGINA])).conectar()
    cofre.definir("meta", "pagina", "")
    with pytest.raises(ErroDePublicacao, match="nenhuma Página"):
        _instagram(cofre, _rede_de_conexao([])).conectar()


def test_conectar_sem_os_dados_pede_para_preencher(tmp_path):
    vazio = Cofre(tmp_path / "v.json", proteger=lambda b: b, revelar=lambda b: b)
    with pytest.raises(FaltaConfigurar, match="ID do app"):
        _instagram(vazio, TransporteFalso()).conectar()


# --- Instagram ------------------------------------------------------------------------------------
def test_instagram_publica_agora_enviando_o_arquivo_direto(conectado, pacote):
    rede = _rede_do_instagram(status=("IN_PROGRESS", "FINISHED"))
    progresso: list[float] = []
    resultado = _instagram(conectado, rede).publicar(pacote, None, ao_progresso=progresso.append)

    assert resultado.estado == ENVIADO and resultado.id_remoto == "M1"
    assert resultado.url == "https://www.instagram.com/reel/abc/"
    criar = rede.chamadas_de("POST", "/IG1/media")[0].formulario
    assert criar["media_type"] == "REELS" and criar["upload_type"] == "resumable"
    assert criar["share_to_feed"] == "true" and criar["access_token"] == "PAGE-TOKEN"
    assert (
        criar["caption"].startswith("Deus é fiel\n\nUma palavra")
        and "#fé #salmo90" in criar["caption"]
    )
    envio = rede.chamadas_de("POST", RUPLOAD_IG)[0]
    assert envio.cabecalhos["Authorization"] == "OAuth PAGE-TOKEN"
    assert envio.cabecalhos["offset"] == "0" and envio.cabecalhos["file_size"] == "5000"
    assert envio.corpo == b"v" * 5000
    assert rede.chamadas_de("POST", "/IG1/media_publish")[0].formulario["creation_id"] == "C1"
    assert len(rede.chamadas_de("GET", "/C1")) == 2  # esperou o processamento
    assert progresso == sorted(progresso) and progresso[-1] >= 0.9


def test_instagram_sem_uri_na_resposta_monta_o_endereco_do_upload(conectado, pacote):
    rede = _rede_do_instagram()
    rede.rotas[1].destino = resposta(200, {"id": "C1"})  # sem `uri`
    _instagram(conectado, rede).publicar(pacote, None)
    assert rede.chamadas_de("POST", "rupload.facebook.com/ig-api-upload/v23.0/C1")


def test_instagram_recusado_pelo_processamento_explica_as_regras(conectado, pacote):
    rede = _rede_do_instagram(status=("ERROR",))
    with pytest.raises(ErroDePublicacao, match="300 MB"):
        _instagram(conectado, rede).publicar(pacote, None)
    assert not rede.chamadas_de("POST", "/IG1/media_publish")


def test_instagram_cancelar_para_antes_de_publicar(conectado, pacote):
    rede = _rede_do_instagram(status=("IN_PROGRESS",))
    with pytest.raises(Cancelado):
        _instagram(conectado, rede).publicar(pacote, None, cancelado=lambda: True)
    assert not rede.chamadas_de("POST", "/IG1/media_publish")


def test_instagram_problemas(conectado, cofre, pacote, monkeypatch):
    ig = _instagram(conectado, TransporteFalso())
    assert not ig.problemas(pacote, None)
    assert "já passou" in " ".join(ig.problemas(pacote, AGORA - timedelta(minutes=1)))
    curto = Pacote(pacote.video, "T", "", (), 1.0)
    assert "3 segundos" in " ".join(ig.problemas(curto, None))
    monkeypatch.setattr(meta, "INSTAGRAM_LIMITE_DO_ARQUIVO", 100)
    assert "300 MB" in " ".join(ig.problemas(pacote, None))
    conectado.remover("meta", "ig_id")
    assert "profissional do Instagram" in " ".join(ig.problemas(pacote, None))
    conectado.remover("meta")
    assert "não está conectada" in " ".join(ig.problemas(pacote, None))


def test_legenda_do_instagram_limita_hashtags_e_tamanho(tmp_path):
    arquivo = tmp_path / "v.mp4"
    arquivo.write_bytes(b"x")
    p = Pacote(arquivo, "Título", "texto " * 600, tuple(f"tag{i}" for i in range(50)), 30)
    legenda = legenda_do_instagram(p)
    assert len(legenda) <= 2200 and legenda.count("#") == 30
    assert legenda.startswith("Título")


# --- Instagram: a fila do Spurgeon (a API não agenda) ---------------------------------------------
def test_instagram_agendado_fica_na_fila_sem_tocar_a_rede(conectado, pacote):
    rede = TransporteFalso()  # qualquer pedido falharia o teste
    quando = AGORA + timedelta(days=1)
    resultado = _instagram(conectado, rede).publicar(pacote, quando)
    assert resultado.estado == NA_FILA and resultado.dados == {"fase": "aguardando"}
    assert resultado.quando == quando.isoformat() and "Spurgeon publica" in resultado.detalhe
    assert rede.chamadas == []


def test_fila_do_instagram_prepara_45_minutos_antes_e_publica_na_hora(conectado, pacote):
    quando = AGORA + timedelta(hours=5)
    relogio = Relogio(AGORA)
    rede = _rede_do_instagram()
    ig = _instagram(conectado, rede, relogio)
    r = ig.publicar(pacote, quando)

    relogio.agora = quando - timedelta(minutes=50)  # ainda cedo: nada acontece
    assert ig.retomar(r, pacote) is r and rede.chamadas == []

    relogio.agora = quando - timedelta(minutes=40)  # hora de preparar: cria e envia, não publica
    r = ig.retomar(r, pacote)
    assert r.estado == NA_FILA and r.dados["fase"] == "pronto" and r.dados["conteiner"] == "C1"
    assert rede.chamadas_de("POST", "/IG1/media") and not rede.chamadas_de("POST", "media_publish")

    relogio.agora = quando - timedelta(minutes=5)  # pronto, mas ainda não é a hora
    assert ig.retomar(r, pacote) is r and not rede.chamadas_de("POST", "media_publish")

    relogio.agora = quando + timedelta(seconds=30)  # na hora: publica o contêiner já pronto
    r = ig.retomar(r, pacote)
    assert r.estado == ENVIADO and r.id_remoto == "M1" and not r.aviso
    criacoes = [c for c in rede.chamadas if c.metodo == "POST" and c.url.endswith("/IG1/media")]
    assert len(criacoes) == 1  # o envio não se repetiu


def test_fila_do_instagram_com_o_aplicativo_fechado_publica_com_atraso(conectado, pacote):
    quando = AGORA + timedelta(hours=1)
    relogio = Relogio(AGORA)
    rede = _rede_do_instagram()
    ig = _instagram(conectado, rede, relogio)
    r = ig.publicar(pacote, quando)
    relogio.agora = quando + timedelta(hours=2)  # o PC ficou desligado
    r = ig.retomar(r, pacote)
    assert r.estado == ENVIADO and "atraso" in r.aviso


def test_fila_do_instagram_refaz_o_conteiner_que_venceu(conectado, pacote):
    quando = AGORA + timedelta(days=2)
    relogio = Relogio(quando - timedelta(minutes=30))
    rede = _rede_do_instagram()
    ig = _instagram(conectado, rede, relogio)
    r = Resultado(
        "instagram", NA_FILA, quando=quando.isoformat(),
        dados={
            "fase": "pronto", "conteiner": "VELHO",
            "preparado_em": (AGORA - timedelta(days=1)).isoformat(),
        },
    )  # fmt: skip
    r = ig.retomar(r, pacote)
    assert r.dados["conteiner"] == "C1"  # criou outro, não usou o "VELHO"


def test_retomar_ignora_o_que_nao_esta_na_fila(conectado, pacote):
    r = Resultado("instagram", ENVIADO, "M1")
    assert _instagram(conectado, TransporteFalso()).retomar(r, pacote) is r


# --- Facebook -------------------------------------------------------------------------------------
def _rede_do_facebook(fase="complete") -> TransporteFalso:
    rede = TransporteFalso()
    rede.responder(
        "POST", "/PG1/video_reels",
        lambda c: resposta(200, {"video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/V1"})
        if c.formulario["upload_phase"] == "start"
        else resposta(200, {"success": True}),
    )  # fmt: skip
    rede.responder("POST", "rupload.facebook.com/video-upload/V1", resposta(200, {"success": True}))
    rede.responder("GET", "/V1", resposta(200, {"status": {"uploading_phase": {"status": fase}}}))
    return rede


def test_facebook_publica_agora(conectado, pacote):
    rede = _rede_do_facebook()
    resultado = _facebook(conectado, rede).publicar(pacote, None)
    assert resultado.estado == ENVIADO and resultado.url == "https://www.facebook.com/reel/V1"
    fases = [c.formulario for c in rede.chamadas_de("POST", "/PG1/video_reels")]
    assert [f["upload_phase"] for f in fases] == ["start", "finish"]
    assert fases[1]["video_state"] == "PUBLISHED" and fases[1]["video_id"] == "V1"
    assert (
        fases[1]["description"].startswith("Deus é fiel")
        and "scheduled_publish_time" not in fases[1]
    )
    envio = rede.chamadas_de("POST", "video-upload/V1")[0]
    assert envio.cabecalhos["Authorization"] == "OAuth PAGE-TOKEN" and envio.corpo == b"v" * 5000


def test_facebook_agenda_na_propria_rede_com_horario_unix(conectado, pacote):
    rede = _rede_do_facebook()
    quando = AGORA + timedelta(days=3)
    resultado = _facebook(conectado, rede).publicar(pacote, quando)
    assert resultado.estado == AGENDADO and resultado.quando == quando.isoformat()
    final = rede.chamadas_de("POST", "/PG1/video_reels")[1].formulario
    assert final["video_state"] == "SCHEDULED"
    assert final["scheduled_publish_time"] == str(int(quando.timestamp()))


def test_facebook_espera_o_envio_terminar(conectado, pacote):
    rede = _rede_do_facebook(fase="in_progress")
    rede.rotas[2].destino = [
        resposta(200, {"status": {"uploading_phase": {"status": "in_progress"}}}),
        resposta(200, {"status": {"uploading_phase": {"status": "complete"}}}),
    ]
    _facebook(conectado, rede).publicar(pacote, None)
    assert len(rede.chamadas_de("GET", "/V1")) == 2


def test_facebook_problemas_duracao_e_janela_de_agendamento(conectado, pacote):
    fb = _facebook(conectado, TransporteFalso())
    assert not fb.problemas(pacote, AGORA + timedelta(days=1))
    longo = Pacote(pacote.video, "T", "", (), 120.0)
    assert "3 a 90 segundos" in " ".join(fb.problemas(longo, None))
    assert "10 minutos" in " ".join(fb.problemas(pacote, AGORA + timedelta(minutes=5)))
    assert "29 dias" in " ".join(fb.problemas(pacote, AGORA + timedelta(days=40)))


def test_facebook_consultar_agendado_que_saiu_vira_publicado(conectado):
    rede = TransporteFalso().responder(
        "GET",
        "/V1",
        resposta(200, {"status": {"publishing_phase": {"publish_status": "published"}}}),
    )
    r = _facebook(conectado, rede).consultar(Resultado("facebook", AGENDADO, "V1"))
    assert r.estado == ENVIADO


# --- token vencido --------------------------------------------------------------------------------
def test_token_da_pagina_revogado_pede_para_conectar_de_novo(conectado, pacote):
    rede = TransporteFalso().responder(
        "POST", "/PG1/video_reels",
        resposta(400, {"error": {"message": "Error validating access token", "code": 190}}),
    )  # fmt: skip
    with pytest.raises(ReconectarNecessario, match="Gere um token novo"):
        _facebook(conectado, rede).publicar(pacote, None)


def test_sem_conectar_nenhuma_chamada_e_feita(cofre, pacote):
    rede = TransporteFalso()
    with pytest.raises(ErroDePublicacao, match="não está conectada"):
        _facebook(cofre, rede).publicar(pacote, None)
    assert rede.chamadas == []
