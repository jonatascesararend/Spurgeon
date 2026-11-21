"""Postar pelo YouTube Studio (Fase R4): as partes que não precisam do navegador. O passo a passo no
Studio de verdade foi conferido à mão no primeiro envio (Remoto.md, R0.3/R4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from spurgeon.canal.postar import PedidoDePostagem, data_do_studio, id_do_link, texto_da_descricao


def test_descricao_com_as_hashtags_numa_linha_propria():
    pedido = PedidoDePostagem(
        Path("s.mp4"), "Título", "  Uma descrição.  ", ["#shorts", "batismo", "#filipenses"]
    )
    assert texto_da_descricao(pedido) == "Uma descrição.\n\n#shorts #batismo #filipenses"
    assert texto_da_descricao(PedidoDePostagem(Path("s.mp4"), "T", "Só texto.")) == "Só texto."


def test_data_como_o_seletor_do_studio_mostra():
    assert data_do_studio(datetime(2026, 11, 20, 18, 30)) == "20 de nov. de 2026"
    assert data_do_studio(datetime(2027, 3, 5)) == "5 de mar. de 2027"


def test_id_do_short_pelo_link():
    assert id_do_link("https://youtube.com/shorts/abcDEF12345") == "abcDEF12345"
    assert id_do_link("https://youtu.be/abcDEF12345") == "abcDEF12345"
    assert id_do_link("https://studio.youtube.com/") == ""


def test_o_canal_vem_da_configuracao_do_pc_e_nunca_do_codigo(monkeypatch):
    import pytest

    from spurgeon.canal import navegador
    from spurgeon.remoto import config

    class PaginaQueNaoPodeSerUsada:
        def goto(self, *_a, **_k):
            raise AssertionError("sem canal configurado, o navegador nem abre o Studio")

    with pytest.raises(navegador.CanalErrado, match="--canal"):
        navegador.conferir_canal(PaginaQueNaoPodeSerUsada(), "  ")
    monkeypatch.setattr(
        config, "carregar_config_remoto", lambda: config.ConfigRemoto(canal=" Canal de teste ")
    )
    assert navegador.nome_do_canal() == "Canal de teste"
    assert not hasattr(navegador, "NOME_DO_CANAL")


class _LocalizadorFalso:
    def __init__(self, visiveis: int, nome: str = ""):
        self.visiveis, self.nome = visiveis, nome

    def count(self) -> int:
        return self.visiveis

    def is_visible(self) -> bool:
        return self.visiveis > 0

    @property
    def first(self):
        return self

    def get_by_role(self, _papel):
        return _LocalizadorFalso(self.visiveis, f"{self.nome} > botão")


class _JanelaDeEnvioFalsa:
    def __init__(self, presentes: set[str]):
        self.presentes = presentes

    def locator(self, seletor: str):
        return _LocalizadorFalso(int(seletor in self.presentes), seletor)


def test_relacionado_abre_pelo_botao_adicionar_do_cartao():
    """Na janela de envio o cartão "Vídeo relacionado" abre pelo botão "Adicionar" (clicar no
    título do cartão não abria o seletor e a espera estourava o tempo)."""
    from spurgeon.canal.postar import _botao_do_relacionado

    cartao = "ytcp-shorts-content-links-picker"
    botao = f"{cartao} #shorts-content-links-add-button"
    assert _botao_do_relacionado(_JanelaDeEnvioFalsa({botao, cartao})).nome == botao
    assert _botao_do_relacionado(_JanelaDeEnvioFalsa({cartao})).nome == f"{cartao} > botão"
    assert _botao_do_relacionado(_JanelaDeEnvioFalsa(set())) is None  # plano B: página de edição
