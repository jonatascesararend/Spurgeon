"""As opções do projeto ajustadas pelo celular antes de começar (`remoto/configuracao.py`)."""

from __future__ import annotations

import pytest

from spurgeon.core.estado import StatusEtapa, carregar_estado, salvar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.remoto import configuracao
from tests.test_remoto import criar_projeto


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    """Um projeto que ainda não começou (todas as etapas pendentes)."""
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    estado = carregar_estado(pastas.raiz)
    for etapa in estado.etapas:
        etapa.status, etapa.progresso = StatusEtapa.PENDENTE, 0.0
    salvar_estado(pastas.raiz, estado)
    return pastas


def _campo(config: dict, nome: str) -> dict:
    return next(c for g in config["grupos"] for c in g["campos"] if c["campo"] == nome)


def test_descreve_a_tela_com_os_valores_do_projeto(pastas):
    config = configuracao.configuracao(pastas)
    assert config["pode_mudar"] and config["motivo"] == ""
    titulos = [g["titulo"] for g in config["grupos"]]
    assert titulos[:2] == ["O que gerar", "Trecho do vídeo"]
    zoom = _campo(config, "zoom_pct")
    assert (zoom["tipo"], zoom["valor"], zoom["min"], zoom["max"]) == ("numero", 100.0, 100, 300)
    assert _campo(config, "video_inicio_s")["tipo"] == "tempo"
    assert "introdução" in _campo(config, "introducao")["desligado"]  # sem arquivo escolhido
    # todo campo da tela existe nas opções do PC
    estado = carregar_estado(pastas.raiz)
    assert all(hasattr(estado.opcoes, nome) for nome in configuracao.CAMPOS)


def test_muda_e_grava_com_a_validacao_do_pc(pastas):
    config = configuracao.mudar(pastas, {"zoom_pct": 125, "video_inicio_s": 90, "modo": "so_shorts",
                                         "uso_cpu_percentual": 100})  # fmt: skip
    assert _campo(config, "zoom_pct")["valor"] == 125
    opcoes = carregar_estado(pastas.raiz).opcoes
    assert (opcoes.zoom_pct, opcoes.video_inicio_s, opcoes.modo) == (125, 90, "so_shorts")
    assert opcoes.uso_cpu_percentual == 100
    with pytest.raises(configuracao.OpcaoInvalida, match=r"Zoom.*de 100 a 300 %"):
        configuracao.mudar(pastas, {"zoom_pct": 50})
    with pytest.raises(configuracao.OpcaoInvalida, match=r"de 0,5 a 30 s"):
        configuracao.mudar(pastas, {"limiar_pausa_s": 130})
    with pytest.raises(configuracao.OpcaoInvalida, match="terminar depois"):
        configuracao.mudar(pastas, {"video_fim_s": 30})  # antes do começo (90 s)
    with pytest.raises(configuracao.OpcaoInvalida, match="mínima"):
        configuracao.mudar(pastas, {"shorts_duracao_min_s": 120, "shorts_duracao_max_s": 60})
    with pytest.raises(configuracao.OpcaoInvalida, match="não muda"):
        configuracao.mudar(pastas, {"whisper_modelo": "tiny"})
    with pytest.raises(configuracao.OpcaoInvalida, match="introdução"):
        configuracao.mudar(pastas, {"introducao": True})
    assert carregar_estado(pastas.raiz).opcoes.zoom_pct == 125  # o que falhou não gravou nada


def test_depois_de_comecar_as_opcoes_nao_mudam(pastas):
    estado = carregar_estado(pastas.raiz)
    estado.etapas[0].status = StatusEtapa.CONCLUIDA
    salvar_estado(pastas.raiz, estado)
    config = configuracao.configuracao(pastas)
    assert not config["pode_mudar"] and "já começou" in config["motivo"]
    with pytest.raises(configuracao.OpcaoInvalida, match="já começou"):
        configuracao.mudar(pastas, {"zoom_pct": 120})
