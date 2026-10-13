import threading
from datetime import date

import pytest

from spurgeon.core.config import ConfigApp, OpcoesProcessamento
from spurgeon.core.erros import ErroConfiguracao
from spurgeon.core.estado import carregar_estado
from spurgeon.core.etapas_demo import etapas_demo
from spurgeon.core.pipeline import Etapa, ResultadoPipeline
from spurgeon.core.projeto import criar_ou_retomar_projeto
from spurgeon.core.trabalhador import executar


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0")


@pytest.fixture
def projeto(tmp_path, video_falso):
    def criar(**opcoes):
        return criar_ou_retomar_projeto(
            tmp_path / "Projetos", video_falso, "Teste", date(2026, 10, 12),
            OpcoesProcessamento(**opcoes),
        ).pastas  # fmt: skip

    return criar


class Rodada:
    """Roda o trabalhador em processo e guarda os eventos emitidos."""

    def __init__(self, pastas, **extra):
        self.eventos: list[dict] = []
        self.cancelar = threading.Event()
        self.pausar = threading.Event()
        self.pastas = pastas
        self.extra = extra

    def rodar(self, **args):
        return executar(
            self.pastas.raiz,
            ConfigApp(),
            emitir=self._emitir,
            cancelar=self.cancelar,
            pausar=self.pausar,
            **{**self.extra, **args},
        )

    def _emitir(self, evento):
        self.eventos.append(evento)

    def tipos(self):
        return [e["tipo"] for e in self.eventos]

    def fim(self):
        return next(e for e in reversed(self.eventos) if e["tipo"] == "fim")


def test_conclui_e_apaga_os_intermediarios(projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    rodada = Rodada(pastas, etapas=etapas_demo())
    assert rodada.rodar() == ResultadoPipeline.CONCLUIDO
    assert rodada.fim()["resultado"] == "concluido"
    assert not pastas.work.exists()
    assert [r.status.value for r in carregar_estado(pastas.raiz).etapas] == ["concluida"] * 3


def test_primeiro_evento_lista_as_etapas_e_o_progresso_so_cresce(projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    rodada = Rodada(pastas, etapas=etapas_demo())
    rodada.rodar()
    assert rodada.eventos[0]["tipo"] == "etapas"
    assert [e["id"] for e in rodada.eventos[0]["etapas"]] == [
        "demo_extrair", "demo_transcrever", "demo_renderizar",
    ]  # fmt: skip
    totais = [e["total"] for e in rodada.eventos if e["tipo"] == "progresso"]
    assert totais == sorted(totais)
    assert totais[-1] == pytest.approx(1.0)


def test_log_chega_como_evento(projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    rodada = Rodada(pastas, etapas=etapas_demo())
    rodada.rodar()
    textos = [e["texto"] for e in rodada.eventos if e["tipo"] == "log"]
    assert any("Iniciando: Extraindo o áudio" in t for t in textos)
    assert "Iniciando: Extraindo o áudio" in pastas.log.read_text(encoding="utf-8")


def test_ponto_de_revisao_para_e_so_segue_apos_aprovar(projeto):
    pastas = projeto()
    primeira = Rodada(pastas, etapas=etapas_demo())
    assert primeira.rodar() == ResultadoPipeline.AGUARDANDO_REVISAO
    assert primeira.fim()["revisao"]["id"] == "demo_revisar"
    assert pastas.work.exists()  # nada é apagado antes do fim

    segunda = Rodada(pastas, etapas=etapas_demo())
    assert segunda.rodar(aprovar="demo_revisar") == ResultadoPipeline.CONCLUIDO
    etapas_rodadas = {e["etapa"] for e in segunda.eventos if e["tipo"] == "progresso"}
    assert etapas_rodadas == {"demo_renderizar"}  # a transcrição não foi refeita


def test_cancelamento_deixa_retomavel(projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    rodada = Rodada(pastas, etapas=etapas_demo())
    original = rodada._emitir

    def emitir_e_cancelar(evento):
        original(evento)
        if evento["tipo"] == "progresso" and evento["etapa"] == "demo_transcrever":
            rodada.cancelar.set()

    rodada._emitir = emitir_e_cancelar
    assert rodada.rodar() == ResultadoPipeline.CANCELADO
    assert rodada.fim()["resultado"] == "cancelado"
    assert pastas.work.exists()

    retomada = Rodada(pastas, etapas=etapas_demo())
    assert retomada.rodar() == ResultadoPipeline.CONCLUIDO
    refeitas = {e["etapa"] for e in retomada.eventos if e["tipo"] == "progresso"}
    assert "demo_extrair" not in refeitas


def test_erro_vira_evento_fim_com_a_mensagem(projeto):
    class Quebra(Etapa):
        id, nome = "quebra", "Etapa que falha"

        def executar(self, ctx):
            raise RuntimeError("deu ruim")

    rodada = Rodada(projeto(), etapas=[Quebra()])
    assert rodada.rodar() == ResultadoPipeline.ERRO
    assert "deu ruim" in rodada.fim()["erro"]


def test_sem_etapas_conclui_avisando(projeto):
    rodada = Rodada(projeto(), etapas=[])
    assert rodada.rodar() == ResultadoPipeline.CONCLUIDO
    textos = [e["texto"] for e in rodada.eventos if e["tipo"] == "log"]
    assert any("Nenhuma etapa" in t for t in textos)


def test_sem_etapa_de_entrega_os_intermediarios_sao_o_resultado_e_ficam(projeto):
    class Intermediaria(Etapa):
        id, nome = "intermediaria", "Etapa que só gera intermediário"

        def executar(self, ctx):
            (ctx.pastas.work / "resultado.txt").write_text("ok")

    pastas = projeto()
    rodada = Rodada(pastas, etapas=[Intermediaria()])
    assert rodada.rodar() == ResultadoPipeline.CONCLUIDO
    assert (pastas.work / "resultado.txt").exists()
    textos = [e["texto"] for e in rodada.eventos if e["tipo"] == "log"]
    assert any("nenhuma etapa gera o vídeo final" in t for t in textos)


def test_manter_intermediarios_nao_apaga_o_work(projeto):
    pastas = projeto(pausar_revisar_transcricao=False, manter_intermediarios=True)
    Rodada(pastas, etapas=etapas_demo()).rodar()
    assert pastas.work.exists()


def test_pasta_que_nao_e_projeto_da_erro(tmp_path):
    rodada = Rodada(type("P", (), {"raiz": tmp_path})())
    with pytest.raises(ErroConfiguracao):
        rodada.rodar()
