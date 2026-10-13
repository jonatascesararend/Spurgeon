import sys

import pytest

from spurgeon.core import energia


@pytest.fixture
def chamadas(monkeypatch):
    registro: list[int] = []
    monkeypatch.setattr(energia, "_definir", lambda flags: registro.append(flags) or 1)
    monkeypatch.setattr(energia.sys, "platform", "win32")
    return registro


def test_mantem_o_sistema_acordado_so_durante_o_bloco(chamadas):
    with energia.impedir_suspensao():
        assert chamadas == [energia.ES_CONTINUOUS | energia.ES_SYSTEM_REQUIRED]
    assert chamadas[-1] == energia.ES_CONTINUOUS  # volta ao normal


def test_volta_ao_normal_mesmo_se_o_bloco_falhar(chamadas):
    with pytest.raises(RuntimeError), energia.impedir_suspensao():
        raise RuntimeError("falhou")
    assert chamadas[-1] == energia.ES_CONTINUOUS


def test_fora_do_windows_nao_faz_nada(monkeypatch):
    registro: list[int] = []
    monkeypatch.setattr(energia, "_definir", registro.append)
    monkeypatch.setattr(energia.sys, "platform", "linux")
    with energia.impedir_suspensao():
        pass
    assert registro == []


@pytest.mark.skipif(sys.platform != "win32", reason="só existe no Windows")
def test_chamada_real_ao_windows_funciona():
    with energia.impedir_suspensao():
        pass
