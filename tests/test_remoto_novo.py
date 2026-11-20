"""Controle remoto, Fase R5 (Remoto.md): começar pelo celular. O YouTube Studio é trocado por
funções de mentira; o projeto é criado de verdade numa pasta temporária."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from spurgeon.canal.baixar import nome_do_arquivo
from spurgeon.canal.listar import VideoDoCanal, ler_data, ler_duracao, ler_linha
from spurgeon.core.config import ConfigApp
from spurgeon.core.estado import carregar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import novo, postagem
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, criar_projeto
from tests.test_remoto_edicao import ExecutorFalso

TITULO = "10. Um sermão de exemplo com um título bem comprido (Livro 4.1-5)"
CURTO = "10. Exemplo (Lv 4)"
VIDEO = {"id": "Vid00000001", "titulo": TITULO, "duracao_s": 4135, "publicado": "2026-11-17",
         "situacao": "Publicado", "visibilidade": "Público"}  # fmt: skip


# --- ler a lista do Studio ------------------------------------------------------------------


def test_le_duracao_e_data_como_o_studio_mostra():
    assert ler_duracao("1:08:31 | 09. Um sermão") == 4111
    assert ler_duracao("54:12 título") == 3252 and ler_duracao("sem duração") == 0
    assert ler_data("17 de nov. de 2026 Publicado") == date(2026, 11, 17)
    assert ler_data("9 de ago. de 2020 Estreou") == date(2020, 8, 9)
    assert ler_data("ontem") is None


def test_le_uma_linha_da_tabela():
    video = ler_linha({
        "href": "/video/Vid00000001/edit", "titulo": f" {TITULO} ", "texto": "1:08:55 10. Um",
        "data": "17 de nov. de 2026\nPublicado", "visibilidade": "Público",
    })  # fmt: skip
    assert video == VideoDoCanal("Vid00000001", TITULO, 4135, date(2026, 11, 17), "Publicado",
                                 "Público")  # fmt: skip
    assert ler_linha({"href": "", "titulo": "x"}) is None


def test_nome_do_arquivo_baixado():
    assert nome_do_arquivo("09. Um: sermão?.mp4", "x") == "09._Um_sermão.mp4"
    assert nome_do_arquivo("", "Título") == "Título.mp4"


# --- o projeto novo -------------------------------------------------------------------------


def _config(tmp_path) -> ConfigApp:
    config = ConfigApp()
    config.pastas.entrada = tmp_path / "Downloads"
    config.pastas.projetos = tmp_path / "Projetos"
    config.padroes.gerar_shorts = True
    config.padroes.legendas_dinamicas = True
    return config


def _baixar_falso(video, pasta: Path, andamento) -> Path:
    andamento("Baixando…")
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / nome_do_arquivo("", video["titulo"])
    arquivo.write_bytes(b"video" * 1000)
    return arquivo


def test_baixa_cria_o_projeto_com_a_data_do_canal_e_processa(tmp_path):
    config = _config(tmp_path)
    iniciados, terminadas = [], []
    novos = novo.NovosProjetos(
        iniciar_processamento=iniciados.append, baixar=_baixar_falso,
        conferir=lambda *_a: None, carregar=lambda: config, ao_terminar=terminadas.append,
    )  # fmt: skip
    tarefa = novos.iniciar({**VIDEO, "titulo": CURTO})
    assert novos.esperar(5) and not tarefa.erro and terminadas == [tarefa]
    pastas = iniciados[0]
    assert pastas.raiz.name == "2026-11-17_10._Exemplo_(Lv_4)"
    assert tarefa.projeto == pastas.raiz.name
    assert novo.ler_origem(pastas) == {"video_id": "Vid00000001", "titulo": CURTO,
                                       "publicado": "2026-11-17"}  # fmt: skip
    estado = carregar_estado(pastas.raiz)
    assert estado.opcoes.gerar_shorts and estado.opcoes.legendas_dinamicas
    assert postagem.sermao_do_projeto(pastas) == CURTO  # o título exato do canal
    assert novo.projetos_por_video(config.pastas.projetos) == {"Vid00000001": pastas.raiz.name}


def test_ambiente_que_nao_esta_pronto_nao_processa(tmp_path):
    config = _config(tmp_path)
    iniciados = []

    def sem_ffmpeg(*_a):
        raise novo.NovoInvalido("O PC não está pronto para processar: FFmpeg: não encontrado")

    novos = novo.NovosProjetos(
        iniciar_processamento=iniciados.append, baixar=_baixar_falso, conferir=sem_ffmpeg,
        carregar=lambda: config,
    )  # fmt: skip
    tarefa = novos.iniciar({**VIDEO, "titulo": CURTO})
    novos.esperar(5)
    assert "FFmpeg" in tarefa.erro and iniciados == [] and tarefa.projeto  # o projeto fica criado


def test_nao_comeca_com_o_pc_ocupado(tmp_path):
    novos = novo.NovosProjetos(iniciar_processamento=lambda _p: None,
                               ocupada=lambda: "O PC já está processando.")  # fmt: skip
    with pytest.raises(novo.NovoInvalido, match="processando"):
        novos.iniciar(VIDEO)
    with pytest.raises(novo.NovoInvalido):
        novos.iniciar({"id": "", "titulo": ""})


def test_projeto_de_antes_da_r5_e_achado_pelo_nome(tmp_path):
    base = tmp_path / "Projetos"
    criar_projeto(
        base,
        "2026-11-17_10._Um_sermão_de_exemplo_com_um_título_bem_comprido_(Livro_4.1-5)",
    )
    assert novo.projeto_do_video(base, VIDEO, {}).startswith("2026-11-17_10.")
    assert novo.projeto_do_video(base, {**VIDEO, "publicado": "2026-11-18"}, {}) == ""


def test_resumo_das_opcoes_salvas(tmp_path):
    config = _config(tmp_path)
    config.padroes.pausar_aprovar_cortes = True
    resumo = novo.resumo_das_opcoes(config)
    assert "Shorts com legenda dinâmica" in resumo and "Para para você aprovar os cortes" in resumo


# --- pelo servidor --------------------------------------------------------------------------


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("spurgeon.remoto.rotas.canal.carregar_config", lambda: config)
    ExecutorFalso.criados = []
    from spurgeon.remoto.fila import Fila

    fila = Fila(criar_executor=ExecutorFalso, procurar=lambda: [])
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), config.pastas.projetos, Avisos(cofre, TransporteFalso()),
        "x", gravar_config=lambda _c: None, fila=fila,
        listar_canal=lambda: [ler_linha({"href": f"/video/{VIDEO['id']}/edit", "titulo": CURTO,
                                         "texto": "1:08:55", "data": "17 de nov. de 2026 Publicado",
                                         "visibilidade": "Público"})],
    )  # fmt: skip
    ctx.novos = novo.NovosProjetos(
        iniciar_processamento=lambda p: ctx.fila.iniciar(p), ocupada=ctx.fila.ocupada,
        baixar=_baixar_falso, conferir=lambda *_a: None, carregar=lambda: config,
    )  # fmt: skip
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    cliente.ctx = ctx
    return cliente


def test_canal_pelo_servidor(cliente):
    lista = cliente.get("/api/canal/videos").json()
    assert lista["videos"][0]["titulo"] == CURTO and lista["videos"][0]["projeto"] == ""
    assert lista["opcoes"] and lista["tarefa"] is None
    assert cliente.post("/api/canal/novo", json={"id": "outro"}).status_code == 404
    comecou = cliente.post("/api/canal/novo", json={"id": VIDEO["id"]})
    assert comecou.status_code == 200
    cliente.ctx.novos.esperar(5)
    tarefa = cliente.get("/api/canal/tarefa").json()
    assert not tarefa["erro"] and tarefa["projeto"] == "2026-11-17_10._Exemplo_(Lv_4)"
    assert ExecutorFalso.criados and ExecutorFalso.criados[0].iniciado[2] is None
    assert cliente.get("/api/canal/videos").json()["videos"][0]["projeto"] == tarefa["projeto"]


def test_processar_um_projeto_parado(cliente, tmp_path):
    base = cliente.ctx.pasta_projetos
    pasta = criar_projeto(base)
    nome = pasta.name
    sem_video = cliente.post(f"/api/projetos/{nome}/processar")
    assert sem_video.status_code == 409 and "vídeo original" in sem_video.json()["erro"]
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    estado = json.loads((pasta / "estado.json").read_text("utf-8"))
    estado["entrada"]["caminho"] = str(video)
    (pasta / "estado.json").write_text(json.dumps(estado), "utf-8")
    assert cliente.post(f"/api/projetos/{nome}/processar").json()["fila"]["rodando"]
    assert PastasProjeto(pasta).raiz.name == nome
