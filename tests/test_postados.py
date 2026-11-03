"""Shorts já postados no YouTube: o arquivo vai para `shorts_gerados/postados/` e volta."""

import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render import shorts as render_shorts
from spurgeon.render.shorts import (
    PASTA_DOS_DESMARCADOS,
    PASTA_DOS_POSTADOS,
    ShortPlanejado,
    aplicar_marcas_de_postado,
    ler_registro,
    organizar_pasta,
    planejar_shorts,
    texto_de_publicacao,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes, gravar_postado


def _corte(id_: int, inicio: float = 0.0, **mudancas) -> Corte:
    base = {
        "id": id_, "inicio": inicio, "fim": inicio + 50.0, "texto": "um trecho",
        "categoria": "outra", "nota": 80.0, "detalhe": {}, "titulo": f"Título {id_}",
        "descricao": "d", "hashtags": ["#shorts"], "slug": f"corte_{id_}",
    }  # fmt: skip
    return Corte(**{**base, **mudancas})


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    pastas.shorts.mkdir(parents=True, exist_ok=True)
    return pastas


def _renderizado(pastas: PastasProjeto, corte: Corte, arquivo: str) -> None:
    """Um Short "já renderizado": o .mp4 e o .txt no lugar e a entrada no registro."""
    caminho = pastas.shorts / arquivo
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"video")
    caminho.with_suffix(".txt").write_text(texto_de_publicacao(corte), encoding="utf-8")
    registro = ler_registro(pastas.shorts_renderizados)
    registro[str(corte.id)] = {"arquivo": arquivo, "assinatura": "a"}
    escrever_json_atomico(pastas.shorts_renderizados, registro)


def _arquivo_no_registro(pastas: PastasProjeto, corte: Corte) -> str:
    return ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]


# --- o lugar planejado ---------------------------------------------------------------------


def test_o_short_postado_mora_em_postados_e_os_outros_na_pasta_principal():
    comum = ShortPlanejado(1, _corte(1))
    postado = ShortPlanejado(2, _corte(2, postado=True))
    assert comum.video == "short_01_corte_1.mp4" and comum.texto == "short_01_corte_1.txt"
    assert postado.video == "postados/short_02_corte_2.mp4"
    assert postado.texto == "postados/short_02_corte_2.txt"
    assert postado.nome == "short_02_corte_2"  # o nome em si não muda


def test_o_texto_de_publicacao_diz_que_ja_foi_postado():
    postado = _corte(1, postado=True, postado_em="2026-11-02")
    texto = texto_de_publicacao(postado)
    assert texto.startswith("JÁ POSTADO (marcado em 02/11/2026)\n\nTítulo: Título 1")
    sem_data = texto_de_publicacao(_corte(1, postado=True))
    assert sem_data.startswith("JÁ POSTADO\n\n")
    assert "POSTADO" not in texto_de_publicacao(_corte(1))


def test_corte_antigo_sem_o_campo_postado_continua_carregando():
    antigo = _corte(1).para_dict()
    del antigo["postado"], antigo["postado_em"]
    assert Corte.de_dict(antigo).postado is False


# --- mover o arquivo -----------------------------------------------------------------------


def test_marcar_move_o_mp4_e_o_txt_para_postados_e_acerta_o_registro(pastas):
    corte = _corte(1)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")
    corte.postado, corte.postado_em = True, "2026-11-02"
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == ["short_01_corte_1.mp4"] and resultado.falhas == []
    assert not (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert not (pastas.shorts / "short_01_corte_1.txt").exists()
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").read_bytes() == b"video"
    texto = (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.txt").read_text("utf-8")
    assert texto.startswith("JÁ POSTADO (marcado em 02/11/2026)")
    assert _arquivo_no_registro(pastas, corte) == "postados/short_01_corte_1.mp4"


def test_desmarcar_traz_o_arquivo_de_volta_e_tira_a_linha_do_txt(pastas):
    corte = _corte(1, postado=True, postado_em="2026-11-02")
    _renderizado(pastas, corte, "postados/short_01_corte_1.mp4")
    corte.postado, corte.postado_em = False, ""
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == ["short_01_corte_1.mp4"]
    assert (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert not (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").exists()
    assert "POSTADO" not in (pastas.shorts / "short_01_corte_1.txt").read_text("utf-8")
    assert _arquivo_no_registro(pastas, corte) == "short_01_corte_1.mp4"


def test_desmarcar_um_corte_que_nao_esta_marcado_para_render_vai_para_desmarcados(pastas):
    corte = _corte(1, postado=True, incluir=False)
    _renderizado(pastas, corte, "postados/short_01_corte_1.mp4")
    corte.postado = False
    aplicar_marcas_de_postado(pastas, [corte])
    assert (pastas.shorts / PASTA_DOS_DESMARCADOS / "short_01_corte_1.mp4").exists()
    assert _arquivo_no_registro(pastas, corte) == "desmarcados/short_01_corte_1.mp4"


def test_marcar_leva_tambem_o_que_estava_em_desmarcados(pastas):
    corte = _corte(1, incluir=False)
    _renderizado(pastas, corte, "desmarcados/short_01_corte_1.mp4")
    corte.postado = True
    aplicar_marcas_de_postado(pastas, [corte])
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").exists()


def test_sem_arquivo_renderizado_nada_acontece_e_repetir_nao_muda_nada(pastas):
    sem_arquivo, pronto = _corte(1, postado=True), _corte(2, postado=True)
    _renderizado(pastas, pronto, "short_02_corte_2.mp4")
    primeira = aplicar_marcas_de_postado(pastas, [sem_arquivo, pronto])
    assert primeira.movidos == ["short_02_corte_2.mp4"]
    segunda = aplicar_marcas_de_postado(pastas, [sem_arquivo, pronto])
    assert segunda.movidos == [] and segunda.falhas == []
    # um corte que não é postado e está na pasta principal fica onde está
    comum = _corte(3)
    _renderizado(pastas, comum, "short_03_corte_3.mp4")
    assert aplicar_marcas_de_postado(pastas, [comum]).movidos == []


def test_nao_sobrescreve_um_arquivo_que_ja_esta_la(pastas):
    corte = _corte(1)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")
    (pastas.shorts / PASTA_DOS_POSTADOS).mkdir()
    (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").write_bytes(b"outro")
    corte.postado = True
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == [] and "já existe" in resultado.falhas[0]
    assert (pastas.shorts / "short_01_corte_1.mp4").read_bytes() == b"video"
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").read_bytes() == b"outro"


def test_arquivo_aberto_em_outro_programa_vira_falha_sem_derrubar_os_outros(pastas, monkeypatch):
    preso, livre = _corte(1, postado=True), _corte(2, postado=True)
    _renderizado(pastas, preso, "short_01_corte_1.mp4")
    _renderizado(pastas, livre, "short_02_corte_2.mp4")
    original = render_shorts._mover_par

    def mover(origem, destino):
        if origem.name == "short_01_corte_1.mp4":
            raise PermissionError(13, "O arquivo está sendo usado por outro processo")
        original(origem, destino)

    monkeypatch.setattr(render_shorts, "_mover_par", mover)
    resultado = aplicar_marcas_de_postado(pastas, [preso, livre])
    assert resultado.movidos == ["short_02_corte_2.mp4"]
    assert len(resultado.falhas) == 1 and "short_01_corte_1.mp4" in resultado.falhas[0]
    assert _arquivo_no_registro(pastas, preso) == "short_01_corte_1.mp4"  # continua onde estava
    assert _arquivo_no_registro(pastas, livre) == "postados/short_02_corte_2.mp4"


# --- gravar só o postado -------------------------------------------------------------------


def test_gravar_postado_mexe_so_no_postado_do_corte(pastas):
    cortes = [_corte(1), _corte(2)]
    gravar_cortes(pastas.cortes_shorts, cortes)
    assert gravar_postado(pastas.cortes_shorts, 2, True, "2026-11-02")
    lidos = carregar_cortes(pastas.cortes_shorts)
    assert [c.postado for c in lidos] == [False, True] and lidos[1].postado_em == "2026-11-02"
    assert lidos[0] == cortes[0] and replace(lidos[1], postado=False, postado_em="") == cortes[1]
    assert gravar_postado(pastas.cortes_shorts, 2, False, "2026-11-03")
    assert carregar_cortes(pastas.cortes_shorts)[1].postado_em == ""  # desmarcar apaga a data


def test_gravar_postado_diz_quando_o_corte_nao_esta_no_arquivo(pastas):
    assert not gravar_postado(pastas.cortes_shorts, 1, True)  # nem o arquivo existe
    gravar_cortes(pastas.cortes_shorts, [_corte(1)])
    assert not gravar_postado(pastas.cortes_shorts, 99, True)
    assert not carregar_cortes(pastas.cortes_shorts)[0].postado


# --- a renderização respeita a pasta -------------------------------------------------------


def _ctx(pastas: PastasProjeto):
    return SimpleNamespace(log=logging.getLogger("teste.postados"), pastas=pastas)


def test_o_organizador_nao_tira_um_postado_de_postados_nem_o_manda_para_desmarcados(pastas):
    postado, comum = _corte(1, 0.0, postado=True), _corte(2, 100.0)
    _renderizado(pastas, postado, "postados/short_01_corte_1.mp4")
    _renderizado(pastas, comum, "short_02_corte_2.mp4")
    postado.incluir = False  # saiu da seleção, mas já foi postado
    planejados = planejar_shorts([postado, comum])  # só o `comum`, que passa a ser o nº 1
    registro = ler_registro(pastas.shorts_renderizados)
    organizar_pasta(_ctx(pastas), pastas.shorts, planejados, registro)
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").exists()
    assert not (pastas.shorts / PASTA_DOS_DESMARCADOS).exists()
    assert (pastas.shorts / "short_01_corte_2.mp4").exists()  # só o outro foi renumerado


def test_marcar_postado_antes_de_renderizar_leva_o_arquivo_para_postados_na_proxima_vez(pastas):
    corte = _corte(1, postado=True)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")  # renderizado antes de ser marcado
    registro = ler_registro(pastas.shorts_renderizados)
    organizar_pasta(_ctx(pastas), pastas.shorts, planejar_shorts([corte]), registro)
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").exists()
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.txt").exists()
    assert not (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert _arquivo_no_registro(pastas, corte) == "postados/short_01_corte_1.mp4"
