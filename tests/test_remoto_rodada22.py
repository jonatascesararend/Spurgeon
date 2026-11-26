"""Controle remoto: 👍 / 👎 dos Shorts, "Renderizar só este" / "Refazer" e a prévia do tratamento de
imagem no celular (com o download só do vídeo). Nada aqui roda o trabalhador nem toca a rede."""

# ruff: noqa: F811  (os fixtures `pastas`, `fila` e `cliente` vêm de test_remoto_edicao)

from __future__ import annotations

import io
import json
import time

import pytest
from PIL import Image, ImageStat

from spurgeon.core.estado import StatusEtapa, salvar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.remoto import configuracao, edicao, novo, tratamento
from spurgeon.render.shorts import (
    ARQUIVO_DO_PEDIDO,
    VALIDADE_DO_PEDIDO_S,
    consumir_pedido_de_render,
    pedir_render_de,
)
from spurgeon.shorts.edicao import LimiteInvalido
from tests.test_remoto_edicao import (  # noqa: F401
    BASE,
    ExecutorFalso,
    _corte,
    _estado,
    cliente,
    fila,
    pastas,
)
from tests.test_remoto_novo import VIDEO, _baixar_falso, _config

# --- 👍 / 👎 --------------------------------------------------------------------------------


def test_opiniao_do_short(pastas):
    assert _corte(pastas, 7).opiniao == ""
    assert edicao.dar_opiniao(pastas, 7, "bom").opiniao == "bom"
    assert _corte(pastas, 7).opiniao == "bom" and _corte(pastas, 3).opiniao == ""
    assert edicao.dar_opiniao(pastas, 7, "").opiniao == ""
    with pytest.raises(LimiteInvalido):
        edicao.dar_opiniao(pastas, 7, "otimo")


def test_opiniao_pelo_servidor(cliente):
    cliente.post(f"{BASE}/sessao")
    resposta = cliente.post(f"{BASE}/shorts/7/opiniao", json={"opiniao": "ruim"})
    assert resposta.json()["opiniao"] == "ruim"
    lista = {s["id"]: s for s in cliente.get(f"{BASE}/shorts").json()}
    assert lista[7]["opiniao"] == "ruim" and lista[3]["opiniao"] == ""
    assert cliente.post(f"{BASE}/shorts/7/opiniao", json={"opiniao": 1}).status_code == 400
    assert cliente.post(f"{BASE}/shorts/7/opiniao", json={"opiniao": "x"}).status_code == 422
    assert cliente.post(f"{BASE}/shorts/999/opiniao", json={"opiniao": "bom"}).status_code == 404


# --- renderizar só este Short --------------------------------------------------------------


def test_pedido_de_render_vale_uma_vez(tmp_path):
    pastas = PastasProjeto(tmp_path / "p")
    pedir_render_de(pastas, [7], refazer=True)
    pedido = consumir_pedido_de_render(pastas)
    assert pedido is not None and pedido.ids == {7} and pedido.refazer
    assert consumir_pedido_de_render(pastas) is None  # foi apagado
    pedir_render_de(pastas, [7])
    velho = json.loads((pastas.work / ARQUIVO_DO_PEDIDO).read_text("utf-8"))
    velho["quando"] = time.time() - VALIDADE_DO_PEDIDO_S - 1
    (pastas.work / ARQUIVO_DO_PEDIDO).write_text(json.dumps(velho), "utf-8")
    assert consumir_pedido_de_render(pastas) is None  # resto antigo não vale
    assert not (pastas.work / ARQUIVO_DO_PEDIDO).exists()


def _com_video(pastas, tmp_path):
    video = tmp_path / "sermao.mp4"
    video.write_bytes(b"x")
    estado = _estado(pastas)
    estado.entrada.caminho = str(video)
    salvar_estado(pastas.raiz, estado)


def test_renderizar_so_este_short(cliente, pastas, tmp_path):
    assert cliente.post(f"{BASE}/shorts/9/renderizar", json={}).status_code == 409  # sem aprovação
    pronto = cliente.post(f"{BASE}/shorts/3/renderizar", json={})
    assert "Refazer" in pronto.json()["erro"]
    assert "vídeo original" in cliente.post(f"{BASE}/shorts/7/renderizar", json={}).json()["erro"]
    assert cliente.post(f"{BASE}/shorts/999/renderizar", json={}).status_code == 404
    assert cliente.post(f"{BASE}/shorts/7/renderizar", json={"refazer": "sim"}).status_code == 400
    _com_video(pastas, tmp_path)
    ok = cliente.post(f"{BASE}/shorts/3/renderizar", json={"refazer": True})
    assert ok.status_code == 200 and ok.json()["shorts"] == 1
    assert _estado(pastas).opcoes.modo == "so_shorts"
    pedido = consumir_pedido_de_render(pastas)
    assert pedido is not None and pedido.ids == {3} and pedido.refazer
    assert _corte(pastas, 7).incluir  # os outros continuam aprovados
    na_fila = cliente.post(f"{BASE}/shorts/7/renderizar", json={})  # já rodando: entra na fila
    assert na_fila.status_code == 200 and na_fila.json()["na_fila"]
    from spurgeon.shorts.fila import FilaDoProjeto

    assert FilaDoProjeto(pastas).do_corte(7).renderizar


def test_fila_esquece_o_pedido_que_nao_chegou_a_valer(pastas, fila):
    fila.iniciar(pastas, so_os_shorts=[7])
    assert (pastas.work / ARQUIVO_DO_PEDIDO).exists()
    [executor] = ExecutorFalso.criados
    executor.ativo = False
    fila._acompanhar(executor, pastas)
    assert not (pastas.work / ARQUIVO_DO_PEDIDO).exists()


# --- tratamento de imagem ------------------------------------------------------------------


def _sem_comecar(pastas, video=None):
    """O projeto antes de começar (as opções ainda mudam), com o vídeo no PC se houver."""
    estado = _estado(pastas)
    for etapa in estado.etapas:
        etapa.status = StatusEtapa.PENDENTE
    if video is not None:
        estado.entrada.caminho, estado.entrada.tamanho = str(video), video.stat().st_size
    salvar_estado(pastas.raiz, estado)


def test_o_tratamento_tem_um_grupo_proprio_com_as_reguas(pastas):
    _sem_comecar(pastas)
    tela = configuracao.configuracao(pastas)
    [grupo] = [g for g in tela["grupos"] if g["tratamento"]]
    campos = [c["campo"] for c in grupo["campos"]]
    assert campos[0] == "tratamento_ativo" and "tratamento_brilho" in campos
    reguas = [c for c in grupo["campos"] if c["tipo"] == "ajuste"]
    assert len(reguas) == 7 and all(c["min"] == -100 and c["max"] == 100 for c in reguas)
    assert tela["video_no_pc"] is False  # o projeto de teste não tem o vídeo
    nova = configuracao.mudar(pastas, {"tratamento_brilho": 12, "tratamento_ativo": True})
    assert _estado(pastas).opcoes.tratamento_brilho == 12 and nova["grupos"]
    with pytest.raises(configuracao.OpcaoInvalida):
        configuracao.mudar(pastas, {"tratamento_contraste": 300})


def test_instantes_ficam_no_trecho_usado():
    class Opcoes:
        video_inicio_s, video_fim_s = 60.0, 120.0

    assert all(60 < t < 120 for t in tratamento.instantes(600.0, Opcoes))

    class Inteiro:
        video_inicio_s, video_fim_s = 0.0, 0.0

    assert len(tratamento.instantes(600.0, Inteiro)) == tratamento.QUADROS_DE_EXEMPLO


def test_previa_sem_o_video_no_pc(cliente):
    resposta = cliente.get(f"{BASE}/tratamento/quadro?i=0")
    assert resposta.status_code == 409 and "não está no PC" in resposta.json()["erro"]
    assert cliente.get(f"{BASE}/tratamento/quadro?brilho=abc").status_code == 400
    assert cliente.post(f"{BASE}/tratamento/sugerir").status_code == 409


def test_previa_e_sugestao_com_um_video_de_verdade(cliente, pastas, video_teste):
    _sem_comecar(pastas, video_teste)
    original = cliente.get(f"{BASE}/tratamento/quadro?i=1&original=1")
    escuro = cliente.get(f"{BASE}/tratamento/quadro?i=1&brilho=-80")
    assert original.status_code == escuro.status_code == 200
    assert original.headers["content-type"] == "image/png" and original.content != escuro.content

    def luz(png):
        with Image.open(io.BytesIO(png)) as img:
            return ImageStat.Stat(img.convert("L")).mean[0]

    assert luz(escuro.content) < luz(original.content)
    tela = cliente.post(f"{BASE}/tratamento/sugerir").json()
    assert "sugestao_neutra" in tela and tela["video_no_pc"]


def test_so_baixar_o_video_nao_comeca_o_processamento(tmp_path):
    config = _config(tmp_path)
    iniciados, terminadas = [], []
    novos = novo.NovosProjetos(
        iniciar_processamento=iniciados.append, baixar=_baixar_falso,
        conferir=lambda *_a: None, carregar=lambda: config, ao_terminar=terminadas.append,
    )  # fmt: skip
    pastas = novos.criar(VIDEO)
    tarefa = novos.comecar(pastas, processar=False)
    assert novos.esperar(5) and not tarefa.erro and terminadas == [tarefa]
    assert novo.video_baixado(pastas) and iniciados == []
    assert tarefa.para_json()["processar"] is False and "prévia" in tarefa.texto
    with pytest.raises(novo.NovoInvalido, match="já está no PC"):
        novos.comecar(pastas, processar=False)
