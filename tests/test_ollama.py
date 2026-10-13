"""Abrir o servidor do Ollama quando ele está fechado (sem abrir nada de verdade)."""

from pathlib import Path

from spurgeon.core import ollama


def test_servidor_ja_ativo_nao_abre_nada():
    abertos = []
    assert ollama.garantir_servidor(ativo=lambda: True, abrir=abertos.append) is True
    assert abertos == []


def test_servidor_fechado_e_aberto_e_a_espera_termina_quando_ele_responde(monkeypatch, tmp_path):
    monkeypatch.setattr(ollama, "localizar_ollama", lambda: tmp_path / "ollama.exe")
    estado = {"aberto": False, "consultas": 0}

    def ativo():
        estado["consultas"] += 1
        return estado["aberto"] and estado["consultas"] > 3  # demora a responder

    def abrir(executavel: Path):
        assert executavel.name == "ollama.exe"
        estado["aberto"] = True

    monkeypatch.setattr(ollama.time, "sleep", lambda _s: None)
    assert ollama.garantir_servidor(ativo=ativo, abrir=abrir) is True
    assert estado["consultas"] > 3


def test_sem_o_executavel_nao_ha_o_que_abrir(monkeypatch):
    monkeypatch.setattr(ollama, "localizar_ollama", lambda: None)
    abertos = []
    assert ollama.garantir_servidor(ativo=lambda: False, abrir=abertos.append) is False
    assert abertos == []


def test_se_nunca_responde_desiste_depois_do_prazo(monkeypatch, tmp_path):
    monkeypatch.setattr(ollama, "localizar_ollama", lambda: tmp_path / "ollama.exe")
    relogio = iter(range(0, 1000))
    monkeypatch.setattr(ollama.time, "monotonic", lambda: next(relogio))
    monkeypatch.setattr(ollama.time, "sleep", lambda _s: None)
    assert (
        ollama.garantir_servidor(esperar_s=5, ativo=lambda: False, abrir=lambda _p: None) is False
    )


def test_localiza_o_ollama_no_path_ou_na_pasta_do_instalador(monkeypatch, tmp_path):
    monkeypatch.setattr(ollama.shutil, "which", lambda _n: str(tmp_path / "no_path" / "ollama.exe"))
    assert ollama.localizar_ollama() == tmp_path / "no_path" / "ollama.exe"

    monkeypatch.setattr(ollama.shutil, "which", lambda _n: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ollama.localizar_ollama() is None
    instalado = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    instalado.parent.mkdir(parents=True)
    instalado.write_bytes(b"x")
    assert ollama.localizar_ollama() == instalado


def test_servidor_ativo_responde_falso_quando_nada_escuta():
    assert ollama.servidor_ativo("http://127.0.0.1:9") is False
