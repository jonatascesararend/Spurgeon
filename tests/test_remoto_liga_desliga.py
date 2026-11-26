"""Os atalhos "ligar" e "fechar" do controle remoto: achar o servidor, perguntar antes de
interromper um trabalho. Nada aqui fecha ou abre processo de verdade."""

# ruff: noqa: F811  (o fixture `servidor` vem de test_remoto)

from __future__ import annotations

import pytest

from spurgeon.remoto import liga_desliga
from spurgeon.remoto.inicializacao import script_do_atalho
from tests.test_remoto import cofre, projetos, servidor  # noqa: F401  (fixtures)


@pytest.mark.parametrize(
    ("linha", "e"),
    [
        (["C:/x/.venv/Scripts/pythonw.exe", "-m", "spurgeon", "remoto", "iniciar"], True),
        (["C:/Python311/pythonw.exe", "-m", "spurgeon", "remoto"], True),
        (["C:/x/.venv/Scripts/spurgeon.exe", "remoto", "iniciar"], True),
        (["pythonw.exe", "-m", "spurgeon", "remoto", "parar"], False),  # o próprio atalho
        (["pythonw.exe", "-m", "spurgeon", "gui"], False),
        (["C:/Users/x/Documents/Spurgeon/.venv/Scripts/python.exe", "-m", "pytest"], False),
        ([], False),
    ],
)
def test_reconhece_a_linha_de_comando_do_servidor(linha, e):
    assert liga_desliga.e_o_servidor(linha) is e


class Processo:
    def __init__(self, pid, linha, filhos=()):
        self.pid, self._linha, self._filhos = pid, linha, list(filhos)
        self.terminado = False

    def cmdline(self):
        return self._linha

    def children(self, recursive=False):
        return self._filhos

    def terminate(self):
        self.terminado = True


SERVIDOR = ["pythonw.exe", "-m", "spurgeon", "remoto", "iniciar"]


@pytest.fixture
def sem_espera(monkeypatch):
    monkeypatch.setattr(liga_desliga.psutil, "wait_procs", lambda procs, timeout: (procs, []))


def test_fechar_um_servidor_parado_so_avisa(monkeypatch, sem_espera):
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [])
    vistos = []
    assert liga_desliga.parar(avisar=lambda t, **_k: vistos.append(t) or True) == 0
    assert "já está fechado" in vistos[0]


def test_fechar_pergunta_se_ha_trabalho_e_respeita_o_nao(monkeypatch, sem_espera):
    trabalho = Processo(30, ["ffmpeg.exe"])
    lancador = Processo(10, SERVIDOR, [Processo(11, SERVIDOR), trabalho])
    servidor = Processo(11, SERVIDOR, [trabalho])
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [lancador, servidor])
    perguntas = []

    def nao(texto, pergunta=False):
        perguntas.append(pergunta)
        return not pergunta

    assert liga_desliga.parar(avisar=nao) == 1
    assert perguntas == [True] and not servidor.terminado and not trabalho.terminado
    assert liga_desliga.parar(avisar=lambda _t, **_k: True) == 0
    assert lancador.terminado and servidor.terminado and trabalho.terminado


def test_fechar_sem_trabalho_nao_pergunta(monkeypatch, sem_espera):
    servidor = Processo(11, SERVIDOR)
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [servidor])
    perguntas = []
    liga_desliga.parar(avisar=lambda _t, pergunta=False: perguntas.append(pergunta) or True)
    assert perguntas == [False] and servidor.terminado


def test_ligar_so_abre_se_nao_esta_rodando(monkeypatch):
    abertos, vistos = [], []
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [Processo(11, SERVIDOR)])
    liga_desliga.ligar(avisar=lambda t, **_k: vistos.append(t), abrir=lambda: abertos.append(1))
    assert abertos == [] and "já está ligado" in vistos[-1]
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [])
    liga_desliga.ligar(avisar=lambda t, **_k: vistos.append(t), abrir=lambda: abertos.append(1))
    assert abertos == [1] and "ligado" in vistos[-1]


def test_o_atalho_leva_o_icone_e_a_descricao(tmp_path):
    script = script_do_atalho(
        tmp_path / "a.lnk", tmp_path / "pythonw.exe", "-m spurgeon remoto parar", tmp_path,
        icone=tmp_path / "icone_studio_spurgeon.ico", descricao="Fecha o servidor",
    )  # fmt: skip
    assert "IconLocation" in script and "icone_studio_spurgeon.ico,0" in script
    assert "'Fecha o servidor'" in script and "remoto parar" in script


def test_reiniciar_fecha_e_abre(monkeypatch, sem_espera):
    servidor = Processo(11, SERVIDOR)
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [servidor])
    abertos, vistos = [], []
    assert liga_desliga.reiniciar(avisar=lambda t, **_k: vistos.append(t) or True,
                                  abrir=lambda: abertos.append(1)) == 0  # fmt: skip
    assert servidor.terminado and abertos == [1] and vistos == [vistos[-1]]
    assert "reiniciado" in vistos[-1]  # uma caixa só, no fim


def test_reiniciar_com_trabalho_e_o_nao_fica_como_esta(monkeypatch, sem_espera):
    trabalho = Processo(30, ["ffmpeg.exe"])
    servidor = Processo(11, SERVIDOR, [trabalho])
    monkeypatch.setattr(liga_desliga, "servidores", lambda: [servidor])
    abertos = []
    resposta = liga_desliga.reiniciar(
        avisar=lambda _t, pergunta=False: not pergunta, abrir=lambda: abertos.append(1)
    )
    assert resposta == 1 and abertos == [] and not servidor.terminado


def test_reiniciar_pelo_celular(servidor):
    servidor.ctx.postagens.atual = type("Envio", (), {"rodando": True})()
    recusado = servidor.post("/api/sistema/reiniciar")
    assert recusado.status_code == 409 and "postando" in recusado.json()["erro"]
    assert not servidor.ctx.reiniciar and servidor.encerrado == []
    servidor.ctx.postagens.atual = None
    ok = servidor.post("/api/sistema/reiniciar")
    assert ok.json() == {"reiniciando": True}
    assert servidor.ctx.reiniciar and servidor.encerrado == [True]
