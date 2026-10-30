"""O aplicativo aberto pelo atalho (pythonw.exe) não tem console: nada pode depender dele."""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

from spurgeon.core import sem_console
from spurgeon.core.executor import python_do_trabalhador


def como_o_pythonw(monkeypatch) -> None:
    """Como no pythonw.exe: `sys.stdout` e `sys.stderr` são `None`.

    Tem de ser chamado dentro do teste: o pytest recoloca o `sys.stdout` dele ao começar cada fase,
    e desfaria o que uma fixture tivesse feito.
    """
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sem_console, "faulthandler", SimpleNamespace(enable=lambda _arquivo: None))


def test_com_console_nao_muda_nada(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    antes = sys.stdout, sys.stderr
    assert sem_console.preparar_sem_console(tmp_path) is None
    assert (sys.stdout, sys.stderr) == antes
    assert not (tmp_path / sem_console.NOME_DO_LOG).exists()


def test_sem_console_stdout_e_stderr_vao_para_o_log(monkeypatch, tmp_path):
    como_o_pythonw(monkeypatch)
    caminho = sem_console.preparar_sem_console(tmp_path)
    assert caminho == tmp_path / sem_console.NOME_DO_LOG
    print("uma linha qualquer")
    print("um erro qualquer", file=sys.stderr)
    sys.stderr.flush()
    texto = caminho.read_text(encoding="utf-8")
    assert "abertura do aplicativo" in texto
    assert "uma linha qualquer" in texto
    assert "um erro qualquer" in texto


def test_o_log_acumula_as_aberturas_e_recomeca_quando_passa_do_limite(monkeypatch, tmp_path):
    como_o_pythonw(monkeypatch)
    caminho = sem_console.preparar_sem_console(tmp_path)
    caminho.write_text("x" * 50, encoding="utf-8")
    como_o_pythonw(monkeypatch)
    sem_console.preparar_sem_console(tmp_path, limite_bytes=1000)
    assert caminho.read_text(encoding="utf-8").startswith("x" * 50)  # abaixo do limite: acrescenta
    como_o_pythonw(monkeypatch)
    sem_console.preparar_sem_console(tmp_path, limite_bytes=10)
    assert "x" * 50 not in caminho.read_text(encoding="utf-8")  # passou do limite: recomeça


def test_log_que_nao_abre_nao_derruba_o_aplicativo(monkeypatch, tmp_path):
    como_o_pythonw(monkeypatch)
    ocupado = tmp_path / "arquivo"
    ocupado.write_text("não é uma pasta", encoding="utf-8")
    sem_console.preparar_sem_console(ocupado / "dentro")
    print("não pode levantar erro")
    print("nem esta", file=sys.stderr)


def test_o_trabalhador_usa_o_python_com_console_quando_a_janela_e_do_pythonw(monkeypatch, tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert python_do_trabalhador() == str(tmp_path / "python.exe")


def test_o_trabalhador_mantem_o_interpretador_se_nao_ha_python_exe_ao_lado(monkeypatch, tmp_path):
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert python_do_trabalhador() == str(tmp_path / "pythonw.exe")


def test_o_trabalhador_mantem_o_python_normal(monkeypatch, tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    assert python_do_trabalhador() == str(tmp_path / "python.exe")
