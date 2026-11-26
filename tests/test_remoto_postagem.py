"""Controle remoto, Fase R4 (Remoto.md): postar no YouTube pelo celular. O navegador é trocado por
uma função de mentira; nenhum teste abre o Chrome nem toca o YouTube."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from spurgeon.canal.postar import FalhaNaPostagem, ResultadoDaPostagem
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.modelo import AGENDADO, ENVIADO, ERRO, RASCUNHO
from spurgeon.remoto import dados, postagem
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.cli import avisar_da_postagem
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto

AGORA = datetime(2026, 11, 20, 14, 0)
FEITO = ResultadoDaPostagem("XyZ98765abc", "https://youtube.com/shorts/XyZ98765abc",
                            "09. Sermão de teste", "2026-11-20T18:30")  # fmt: skip


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    # o corte 3 está renderizado em `postados/`; para postar, ele volta a ser só "pronto"
    (pastas.shorts / "postados" / "short_01_titulo_3.mp4").rename(
        pastas.shorts / "short_01_titulo_3.mp4"
    )
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        c["postado"], c["postado_em"] = False, ""
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    registro = {"3": {"arquivo": "short_01_titulo_3.mp4", "assinatura": "a"}}
    pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")
    return pastas


def _corte(pastas, id_):
    return next(c for c in dados.carregar_cortes(pastas) if c.id == id_)


# --- o pedido -------------------------------------------------------------------------------


def test_pedido_programado_precisa_de_folga():
    pedido = postagem.ler_pedido(
        {"quando": "2026-11-20T18:30", "relacionado": " Sermão "}, 3, AGORA
    )
    assert pedido.quando == datetime(2026, 11, 20, 18, 30) and pedido.relacionado == "Sermão"
    assert postagem.ler_pedido({}, 3, AGORA).quando is None  # publicar agora
    with pytest.raises(postagem.PostagemInvalida, match="15 minutos"):
        postagem.ler_pedido({"quando": "2026-11-20T14:10"}, 3, AGORA)
    with pytest.raises(postagem.PostagemInvalida, match="inválida"):
        postagem.ler_pedido({"quando": "amanhã"}, 3, AGORA)


def test_so_posta_o_short_renderizado_e_uma_vez(pastas):
    with pytest.raises(postagem.PostagemInvalida, match="Renderize"):
        postagem.conferir(pastas, postagem.Pedido(7, None, ""))
    short, rascunho = postagem.conferir(pastas, postagem.Pedido(3, None, ""))
    assert short.corte.id == 3 and not rascunho
    postagem.registrar_sucesso(pastas, 3, FEITO, datetime(2026, 11, 20, 18, 30))
    with pytest.raises(postagem.PostagemInvalida, match="programado"):
        postagem.conferir(pastas, postagem.Pedido(3, None, ""))
    assert postagem.conferir(pastas, postagem.Pedido(3, None, "", forcar=True))


def test_registrar_programado_marca_o_corte_e_move_o_arquivo(pastas):
    resultado = postagem.registrar_sucesso(pastas, 3, FEITO, datetime(2026, 11, 20, 18, 30))
    assert resultado.estado == AGENDADO and resultado.id_remoto == "XyZ98765abc"
    assert resultado.detalhe == "Vídeo relacionado: 09. Sermão de teste"
    assert _corte(pastas, 3).agendado_para == "2026-11-20T18:30"
    assert (pastas.shorts / "agendados" / "short_01_titulo_3.mp4").is_file()
    assert postagem.do_youtube(pastas, 3).url.endswith("XyZ98765abc")
    assert dados.shorts(pastas)[0]["youtube"]["estado"] == AGENDADO


def test_registrar_publicado_agora_marca_postado(pastas):
    postagem.registrar_sucesso(pastas, 3, FEITO, None, agora=datetime(2026, 11, 20, 15, 5))
    corte = _corte(pastas, 3)
    assert corte.postado and corte.postado_em == "2026-11-20T15:05" and not corte.agendado_para
    assert postagem.do_youtube(pastas, 3).estado == ENVIADO
    assert (pastas.shorts / "postados" / "short_01_titulo_3.mp4").is_file()


def test_com_processamento_rodando_o_arquivo_nao_se_mexe(pastas):
    postagem.registrar_sucesso(
        pastas, 3, FEITO, datetime(2026, 11, 20, 18, 30), mover_arquivos=False
    )
    assert (pastas.shorts / "short_01_titulo_3.mp4").is_file()
    assert _corte(pastas, 3).agendado_para


# --- a postagem em segundo plano ------------------------------------------------------------


def test_postagem_que_deu_certo(pastas):
    chamadas, terminados = [], []

    def postar(pastas_, short, pedido, rascunho, andamento):
        andamento("Enviando o arquivo…")
        chamadas.append((short.corte.id, pedido.relacionado, rascunho))
        return FEITO

    postagens = postagem.Postagens(postar=postar, ao_terminar=terminados.append, renovar_s=0.01)
    envio = postagens.iniciar(pastas, postagem.Pedido(3, datetime(2026, 11, 20, 18, 30), "Sermão"))
    assert postagens.esperar(5) and not envio.rodando and not envio.erro
    assert chamadas == [(3, "Sermão", False)] and terminados == [envio]
    assert "Programado no YouTube para 20/11 às 18:30" in envio.texto
    assert envio.link.endswith("XyZ98765abc") and _corte(pastas, 3).agendado_para


def test_falha_depois_do_envio_vira_rascunho_e_a_proxima_retoma(pastas):
    def falha(*_a):
        raise FalhaNaPostagem("parou no passo “concluir”", enviado=True, video_id="XyZ98765abc")

    postagens = postagem.Postagens(postar=falha, renovar_s=0.01)
    envio = postagens.iniciar(pastas, postagem.Pedido(3, None, ""))
    postagens.esperar(5)
    assert "concluir" in envio.erro
    assert postagem.do_youtube(pastas, 3).estado == RASCUNHO
    _, rascunho = postagem.conferir(pastas, postagem.Pedido(3, None, ""))
    assert rascunho  # a próxima tentativa retoma o rascunho em vez de mandar de novo
    assert not _corte(pastas, 3).agendado_para


def test_falha_antes_do_envio_fica_como_erro(pastas):
    def sem_navegador(*_a):
        raise RuntimeError("O YouTube Studio não abriu logado")

    postagens = postagem.Postagens(postar=sem_navegador, renovar_s=0.01)
    postagens.iniciar(pastas, postagem.Pedido(3, None, ""))
    postagens.esperar(5)
    assert postagem.do_youtube(pastas, 3).estado == ERRO


def test_aviso_da_postagem(pastas):
    cofre = Cofre(pastas.raiz / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    avisos = Avisos(cofre, TransporteFalso())
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]),
        pastas.raiz.parent,
        avisos,
        "x",
        gravar_config=lambda _c: None,
    )
    enviados = []
    ctx.avisos.enviar = enviados.append
    envio = postagem.Envio(NOME_DO_PROJETO, 3, texto="Publicado no YouTube.", rodando=False)
    avisar_da_postagem(ctx, envio)
    envio.erro = "sem login"
    avisar_da_postagem(ctx, envio)
    assert enviados[0].url.endswith("/s/3") and "sem login" in enviados[1].texto


# --- pelo servidor --------------------------------------------------------------------------


def test_postar_pelo_servidor_entra_na_fila(pastas, tmp_path):
    """Postar pelo celular põe o Short na fila do PC (um não renderizado também: a fila o renderiza
    antes); o despachante o programa quando chega a vez."""
    from spurgeon.remoto.despachante import Despachante, Vez
    from spurgeon.shorts import fila

    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), pastas.raiz.parent, Avisos(cofre, TransporteFalso()), "x",
        gravar_config=lambda _c: None,
    )  # fmt: skip
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    base = f"/api/projetos/{NOME_DO_PROJETO}"
    preparo = cliente.get(f"{base}/shorts/3/postar").json()
    assert preparo["relacionado"] == "09. Sermão de teste" and preparo["youtube"] is None
    assert preparo["fila"] is None and preparo["youtube_quando"] == ""
    pedido = {"quando": "2099-01-01T18:30", "relacionado": "09. Sermão de teste"}
    assert cliente.post(f"{base}/shorts/3/postar", json=pedido).json() == {"erro": "sem_trava"}
    cliente.post(f"{base}/sessao")
    ainda_nao = cliente.post(f"{base}/shorts/7/postar", json=pedido)  # não renderizado: na fila
    assert ainda_nao.status_code == 200
    assert ainda_nao.json()["fila"]["situacao"] == fila.NA_FILA_DO_RENDER
    iniciado = cliente.post(f"{base}/shorts/3/postar", json=pedido)
    assert iniciado.status_code == 200
    assert iniciado.json()["fila"]["situacao"] == fila.NA_FILA_DA_PROGRAMACAO
    assert cliente.post(f"{base}/shorts/3/fila/tirar").status_code == 200
    assert cliente.get(f"{base}/shorts/3/postar").json()["fila"] is None
    fila.tirar_da_fila(pastas, 7)
    cliente.post(f"{base}/shorts/3/postar", json=pedido)

    def programar(p, item, andamento):
        postagem.registrar_sucesso(p, item.corte, FEITO, item.horario(), mover_arquivos=False)
        return "Programado."

    class SemRender:
        rodando = False

    despachante = Despachante(
        lambda: pastas.raiz.parent, fila=SemRender(), vez=Vez(tmp_path / "vez.json"),
        procurar=lambda: [], programar=programar,
    )  # fmt: skip
    despachante.passo()
    preparo = cliente.get(f"{base}/shorts/3/postar").json()
    assert preparo["youtube"]["estado"] == AGENDADO and preparo["fila"] is None
    assert preparo["youtube_quando"] == "2099-01-01T18:30"
    de_novo = cliente.post(f"{base}/shorts/3/postar", json=pedido)
    assert de_novo.status_code == 409 and "já foi programado" in de_novo.json()["erro"]
    reagendar = cliente.post(f"{base}/shorts/3/reagendar", json={"quando": "2099-01-02T09:00"})
    assert reagendar.status_code == 200
    assert reagendar.json()["fila"]["situacao"] == fila.NA_FILA_DA_PROGRAMACAO
    perto = cliente.post(f"{base}/shorts/3/reagendar", json={"quando": "2000-01-01T09:00"})
    assert perto.status_code == 409 and "5 minutos" in perto.json()["erro"]
