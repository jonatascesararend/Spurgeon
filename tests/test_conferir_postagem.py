"""A postagem conferida no canal (o Studio pode programar mesmo com um erro no fim) e os agendados
que já saíram virando "já postado" no disco, com a janela do PC fechada."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from spurgeon.canal.cronograma import PROGRAMADO, PUBLICADO, SHORT, ItemDoCronograma, achar_na_lista
from spurgeon.canal.postar import FalhaNaPostagem, ResultadoDaPostagem
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.modelo import Resultado
from spurgeon.remoto import dados, postagem
from spurgeon.remoto.vigia import Vigia
from spurgeon.shorts.dados import Corte
from tests.test_remoto import criar_projeto
from tests.test_remoto_postagem import pastas as pastas_prontas  # noqa: F401


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    return PastasProjeto(criar_projeto(tmp_path / "Projetos"))


def _corte(pastas, id_) -> Corte:
    return next(c for c in dados.carregar_cortes(pastas) if c.id == id_)


@contextmanager
def _studio_falso():
    yield SimpleNamespace(pagina=None, canal="UCxyz")


PROGRAMADO_NO_CANAL = ItemDoCronograma(
    "Canal000001", "Título 7", SHORT, PROGRAMADO, datetime(2026, 11, 22, 14, 0), 82
)


def _short(pastas) -> SimpleNamespace:
    return SimpleNamespace(corte=_corte(pastas, 7), arquivo=Path("s.mp4"))


def _pedido(quando=datetime(2026, 11, 22, 18, 30)) -> postagem.Pedido:
    return postagem.Pedido(7, quando, "09. Sermão de teste", False)


def test_acha_o_short_na_lista_do_studio_pelo_id_ou_pelo_titulo():
    videos = [{"videoId": "a1", "title": "Outro"}, {"videoId": "b2", "title": "Um  título  Novo"}]
    assert achar_na_lista(videos, video_id="b2")["videoId"] == "b2"
    assert achar_na_lista(videos, titulo="um título novo")["videoId"] == "b2"
    assert achar_na_lista(videos, video_id="zz", titulo="Outro") is None  # o id manda
    assert achar_na_lista(videos, titulo="") is None


def test_erro_no_fim_mas_o_canal_mostra_programado_vale_como_sucesso(pastas):
    procurados = []

    def postar_que_falha_no_fim(*_a, **_k):
        raise FalhaNaPostagem("conferir a visibilidade", enviado=True, video_id="Canal000001")

    def procurar(_studio, *, video_id, titulo):
        procurados.append((video_id, titulo))
        return PROGRAMADO_NO_CANAL

    feito = postagem._postar_de_verdade(
        pastas, _short(pastas), _pedido(), False, lambda _t: None,
        abrir=_studio_falso, postar_no_studio=postar_que_falha_no_fim, procurar=procurar,
    )  # fmt: skip
    assert procurados == [("Canal000001", "Título 7")]
    assert feito.video_id == "Canal000001" and feito.programado_para == "2026-11-22T14:00"


def test_erro_antes_de_enviar_ou_canal_sem_o_short_continua_erro(pastas):
    def antes_de_enviar(*_a, **_k):
        raise FalhaNaPostagem("abrir o envio", enviado=False)

    def no_fim(*_a, **_k):
        raise FalhaNaPostagem("concluir", enviado=True, video_id="Canal000001")

    nunca = []
    with pytest.raises(FalhaNaPostagem, match="abrir o envio"):
        postagem._postar_de_verdade(
            pastas, _short(pastas), _pedido(), False, lambda _t: None, abrir=_studio_falso,
            postar_no_studio=antes_de_enviar, procurar=lambda *_a, **_k: nunca.append(1),
        )  # fmt: skip
    assert nunca == []  # nem foi ao canal
    with pytest.raises(FalhaNaPostagem, match="concluir"):
        postagem._postar_de_verdade(
            pastas, _short(pastas), _pedido(), False, lambda _t: None, abrir=_studio_falso,
            postar_no_studio=no_fim, procurar=lambda *_a, **_k: None,
        )  # fmt: skip

    def conferir_que_quebra(*_a, **_k):
        raise RuntimeError("Studio fora do ar")

    with pytest.raises(FalhaNaPostagem, match="concluir"):  # conferir falhou: o erro original
        postagem._postar_de_verdade(
            pastas, _short(pastas), _pedido(), False, lambda _t: None, abrir=_studio_falso,
            postar_no_studio=no_fim, procurar=conferir_que_quebra,
        )  # fmt: skip


def test_retomar_rascunho_que_ja_esta_programado_nao_envia_de_novo(pastas):
    postagem.registro(pastas).gravar(7, Resultado("youtube", "rascunho", "", detalhe="parou"))
    enviados = []

    def procurar(_studio, *, video_id, titulo):  # sem id guardado: acha pelo título
        return PROGRAMADO_NO_CANAL if not video_id and titulo == "Título 7" else None

    feito = postagem._postar_de_verdade(
        pastas, _short(pastas), _pedido(), True, lambda _t: None, abrir=_studio_falso,
        postar_no_studio=lambda *a, **k: enviados.append(1), procurar=procurar,
    )  # fmt: skip
    assert enviados == [] and feito.programado_para == "2026-11-22T14:00"


def test_vale_a_hora_que_o_canal_diz(pastas_prontas):  # noqa: F811
    pastas = pastas_prontas  # o corte 3 está renderizado e pronto
    canal = ResultadoDaPostagem("Canal000001", "https://youtube.com/shorts/Canal000001", "",
                                "2026-11-22T14:00")  # fmt: skip
    postagens = postagem.Postagens(postar=lambda *a: canal, renovar_s=0.01)
    envio = postagens.iniciar(pastas, postagem.Pedido(3, datetime(2030, 1, 1, 18, 30), "", False))
    assert postagens.esperar(5) and not envio.erro
    assert _corte(pastas, 3).agendado_para == "2026-11-22T14:00"
    assert postagem.do_youtube(pastas, 3).estado == "agendado"
    assert "22/11 às 14:00" in envio.texto
    publicado = ItemDoCronograma("x", "t", SHORT, PUBLICADO, datetime(2026, 11, 20, 9), 30)
    assert postagem.resultado_do_canal(publicado).programado_para == ""


def test_agendado_que_venceu_vira_postado_no_disco(pastas):
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        if c["id"] == 7:
            c["agendado_para"] = "2026-11-20T18:30"
        if c["id"] == 9:
            c["agendado_para"] = "2026-11-25T18:30"
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    (pastas.shorts / "agendados").mkdir()
    (pastas.shorts / "agendados" / "short_02_titulo_7.mp4").write_bytes(b"x")
    registro = json.loads(pastas.shorts_renderizados.read_text("utf-8"))
    registro["7"] = {"arquivo": "agendados/short_02_titulo_7.mp4", "assinatura": "b"}
    pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")

    agora = datetime(2026, 11, 20, 23, 45)
    mudaram = dados.vencer_agendados_de_todos(pastas.raiz.parent, agora, pode_mover=lambda: True)
    assert mudaram == {pastas.raiz.name: [7]}
    corte = _corte(pastas, 7)
    assert corte.postado and corte.postado_em == "2026-11-20T18:30" and not corte.agendado_para
    assert _corte(pastas, 9).agendado_para == "2026-11-25T18:30"  # ainda não chegou a hora
    arquivo = json.loads(pastas.shorts_renderizados.read_text("utf-8"))["7"]["arquivo"]
    assert arquivo.startswith("postados/") and (pastas.shorts / arquivo).is_file()
    resumo = next(s for s in dados.shorts(pastas) if s["id"] == 7)
    assert resumo["postado"] and "20/11" in resumo["postado_rotulo"]
    assert dados.vencer_agendados_de_todos(pastas.raiz.parent, agora, pode_mover=lambda: True) == {}


def test_com_processamento_rodando_o_arquivo_fica_onde_esta(pastas):
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    cortes[0]["agendado_para"] = "2026-11-20T18:30"
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    antes = pastas.shorts_renderizados.read_text("utf-8")
    ids = dados.vencer_agendados(pastas, datetime(2026, 11, 21), mover_arquivos=False)
    assert ids == [cortes[0]["id"]] and pastas.shorts_renderizados.read_text("utf-8") == antes


def test_vigia_roda_a_tarefa_a_cada_olhada(tmp_path):
    chamadas = []
    vigia = Vigia(tmp_path, lambda _a: None, intervalo_s=0.01,
                  a_cada_olhada=lambda: chamadas.append(1))  # fmt: skip
    vigia.iniciar()
    try:
        for _ in range(200):
            if len(chamadas) >= 2:
                break
            vigia._parar.wait(0.01)
    finally:
        vigia.parar()
    assert len(chamadas) >= 2


def test_lista_de_projetos_resume_a_publicacao_dos_shorts(pastas):
    # o projeto de teste: corte 3 postado, 7 marcado e 9 desmarcado
    resumo = dados.publicacao_dos_shorts(pastas)
    assert resumo == {"postados": 1, "agendados": 0, "a_postar": 1, "proximo": ""}
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        if c["id"] in (7, 9):  # agendado conta mesmo desmarcado; o mais cedo é o próximo
            c["agendado_para"] = "2026-11-25T14:00" if c["id"] == 7 else "2026-11-24T18:30"
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    resumo = dados.publicacao_dos_shorts(pastas)
    assert resumo == {"postados": 1, "agendados": 2, "a_postar": 0, "proximo": "2026-11-24T18:30"}
    item = next(p for p in dados.projetos(pastas.raiz.parent) if p["id"] == pastas.raiz.name)
    assert item["publicacao"] == resumo
