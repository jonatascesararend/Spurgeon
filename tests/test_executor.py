"""Testes do executor com subprocessos de verdade e as etapas de demonstração."""

import time
from datetime import date

import pytest

from spurgeon.core.config import ConfigApp, OpcoesProcessamento
from spurgeon.core.executor import ExecutorProjeto
from spurgeon.core.projeto import criar_ou_retomar_projeto

LIMITE_S = 40


@pytest.fixture
def executor():
    ex = ExecutorProjeto()
    yield ex
    ex.encerrar(espera_s=5)


@pytest.fixture
def projeto(tmp_path, video_falso):
    def criar(**opcoes):
        return criar_ou_retomar_projeto(
            tmp_path / "Projetos", video_falso, "Teste", date(2026, 10, 12),
            OpcoesProcessamento(**opcoes),
        ).pastas  # fmt: skip

    return criar


def ambiente(passo_s: float) -> dict[str, str]:
    return {"SPURGEON_DEMO": "1", "SPURGEON_DEMO_PASSO_S": str(passo_s)}


def coletar(executor, *, ate=None) -> list[dict]:
    """Junta eventos até `ate(evento)` ser verdadeiro, ou até o processo terminar."""
    recebidos: list[dict] = []
    limite = time.monotonic() + LIMITE_S
    while time.monotonic() < limite:
        for evento in executor.eventos():
            recebidos.append(evento)
            if evento["tipo"] == "processo_encerrado" or (ate and ate(evento)):
                return recebidos
        time.sleep(0.02)
    pytest.fail(f"Tempo esgotado; eventos: {recebidos[-5:]}")


def tipos(eventos):
    return [e["tipo"] for e in eventos]


def test_roda_ate_o_fim(executor, projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.002))
    eventos = coletar(executor)
    assert eventos[-1]["tipo"] == "processo_encerrado"
    assert eventos[-1]["codigo"] == 0
    fim = next(e for e in eventos if e["tipo"] == "fim")
    assert fim["resultado"] == "concluido"
    assert {"etapas", "progresso", "log"} <= set(tipos(eventos))
    assert not executor.ativo


def test_acentos_atravessam_o_protocolo(executor, projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.002))
    eventos = coletar(executor)
    etapas = next(e for e in eventos if e["tipo"] == "etapas")["etapas"]
    assert etapas[0]["nome"] == "Extraindo o áudio (demonstração)"


def test_cancelar_encerra_e_deixa_retomavel(executor, projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.05))
    coletar(executor, ate=lambda e: e["tipo"] == "progresso" and e["fracao"] > 0.1)
    executor.enviar("cancelar")
    eventos = coletar(executor)
    assert next(e for e in eventos if e["tipo"] == "fim")["resultado"] == "cancelado"
    assert pastas.work.exists()


def test_pausar_segura_o_progresso_ate_retomar(executor, projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.02))
    coletar(executor, ate=lambda e: e["tipo"] == "progresso" and e["fracao"] > 0.1)
    executor.enviar("pausar")
    coletar(executor, ate=lambda e: e["tipo"] == "pausa" and e["ativa"])
    time.sleep(0.6)
    assert [e for e in executor.eventos() if e["tipo"] == "progresso"] == []

    executor.enviar("retomar")
    eventos = coletar(executor)
    assert {"tipo": "pausa", "ativa": False} in eventos
    assert next(e for e in eventos if e["tipo"] == "fim")["resultado"] == "concluido"


def test_se_a_interface_some_o_trabalho_e_cancelado(executor, projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.05))
    coletar(executor, ate=lambda e: e["tipo"] == "progresso" and e["fracao"] > 0.1)
    executor._processo.stdin.close()  # como se a interface tivesse caído
    eventos = coletar(executor)
    assert next(e for e in eventos if e["tipo"] == "fim")["resultado"] == "cancelado"


def test_revisao_e_aprovacao_em_duas_execucoes(executor, projeto):
    pastas = projeto()
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.002))
    fim = next(e for e in coletar(executor) if e["tipo"] == "fim")
    assert fim["resultado"] == "aguardando_revisao"
    assert fim["revisao"]["id"] == "demo_revisar"

    executor.iniciar(pastas.raiz, ConfigApp(), aprovar="demo_revisar", ambiente=ambiente(0.002))
    fim = next(e for e in coletar(executor) if e["tipo"] == "fim")
    assert fim["resultado"] == "concluido"


def test_nao_inicia_dois_processamentos_ao_mesmo_tempo(executor, projeto):
    pastas = projeto(pausar_revisar_transcricao=False)
    executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.05))
    with pytest.raises(RuntimeError):
        executor.iniciar(pastas.raiz, ConfigApp(), ambiente=ambiente(0.05))


def test_comando_desconhecido_e_recusado(executor):
    with pytest.raises(ValueError):
        executor.enviar("explodir")


def test_falha_do_processo_chega_com_o_stderr(executor, tmp_path):
    pasta_vazia = tmp_path / "nao_e_projeto"
    pasta_vazia.mkdir()
    executor.iniciar(pasta_vazia, ConfigApp())
    final = coletar(executor)[-1]
    assert final["tipo"] == "processo_encerrado"
    assert final["codigo"] != 0
    assert any("não é um projeto" in linha for linha in final["stderr"])
