import sys
import time

import pytest

from spurgeon.core import energia

PADRAO = energia.ES_CONTINUOUS | energia.ES_SYSTEM_REQUIRED
SEM_BLOQUEIO = PADRAO | energia.ES_DISPLAY_REQUIRED


@pytest.fixture
def chamadas(monkeypatch):
    registro = {"flags": [], "atividade": 0}
    monkeypatch.setattr(energia, "_definir", lambda flags: registro["flags"].append(flags) or 1)
    monkeypatch.setattr(
        energia,
        "_registrar_atividade",
        lambda: registro.__setitem__("atividade", registro["atividade"] + 1),
    )
    monkeypatch.setattr(energia.sys, "platform", "win32")
    return registro


def test_mantem_o_sistema_acordado_so_durante_o_bloco(chamadas):
    with energia.impedir_suspensao():
        assert chamadas["flags"] == [PADRAO]
    assert chamadas["flags"][-1] == energia.ES_CONTINUOUS  # volta ao normal
    # sem o modo "sem bloqueio", o contador de ociosidade não é mexido
    assert chamadas["atividade"] == 0


def test_sem_bloqueio_pede_a_tela_ligada_e_registra_atividade_de_tempos_em_tempos(chamadas):
    with energia.impedir_suspensao(sem_bloqueio=True, intervalo_s=0.05):
        assert chamadas["flags"] == [SEM_BLOQUEIO]
        time.sleep(0.4)
    assert chamadas["atividade"] >= 4  # 1 na entrada + os periódicos
    assert chamadas["flags"][-1] == energia.ES_CONTINUOUS


def test_a_atividade_para_quando_o_bloco_termina(chamadas):
    with energia.impedir_suspensao(sem_bloqueio=True, intervalo_s=0.05):
        time.sleep(0.2)
    depois = chamadas["atividade"]
    time.sleep(0.3)
    assert chamadas["atividade"] == depois


def test_volta_ao_normal_mesmo_se_o_bloco_falhar(chamadas):
    with pytest.raises(RuntimeError), energia.impedir_suspensao(sem_bloqueio=True):
        raise RuntimeError("falhou")
    assert chamadas["flags"][-1] == energia.ES_CONTINUOUS


def test_fora_do_windows_nao_faz_nada(monkeypatch):
    registro: list[int] = []
    monkeypatch.setattr(energia, "_definir", registro.append)
    monkeypatch.setattr(energia.sys, "platform", "linux")
    with energia.impedir_suspensao(sem_bloqueio=True):
        pass
    assert registro == []


@pytest.mark.skipif(sys.platform != "win32", reason="só existe no Windows")
def test_chamada_real_ao_windows_funciona():
    with energia.impedir_suspensao(sem_bloqueio=True, intervalo_s=0.05):
        time.sleep(0.15)
