"""Shorts agendados: o arquivo vai para `shorts_gerados/agendados/` e, passada a hora, para
`postados/`; tirar o horário o devolve."""

import logging
from types import SimpleNamespace

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render.shorts import (
    PASTA_DOS_AGENDADOS,
    PASTA_DOS_DESMARCADOS,
    PASTA_DOS_POSTADOS,
    ShortPlanejado,
    aplicar_marcas_de_postado,
    ler_registro,
    organizar_pasta,
    pasta_do_estado,
    planejar_shorts,
    sincronizar_arquivos,
    texto_de_publicacao,
)
from spurgeon.shorts.dados import Corte

QUANDO = "2026-11-04T18:00"


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
def test_a_pasta_do_estado_prefere_postado_a_agendado():
    assert pasta_do_estado(_corte(1)) == ""
    assert pasta_do_estado(_corte(1, agendado_para=QUANDO)) == PASTA_DOS_AGENDADOS
    assert pasta_do_estado(_corte(1, postado=True)) == PASTA_DOS_POSTADOS
    assert pasta_do_estado(_corte(1, postado=True, agendado_para=QUANDO)) == PASTA_DOS_POSTADOS


def test_o_short_agendado_mora_em_agendados():
    agendado = ShortPlanejado(1, _corte(1, agendado_para=QUANDO))
    assert agendado.video == "agendados/short_01_corte_1.mp4"
    assert agendado.texto == "agendados/short_01_corte_1.txt"
    assert agendado.nome == "short_01_corte_1"  # o nome em si não muda


def test_o_texto_de_publicacao_diz_quando_vai_ao_ar():
    texto = texto_de_publicacao(_corte(1, agendado_para=QUANDO))
    assert texto.startswith("AGENDADO PARA 04/11/2026 às 18:00\n\nTítulo: Título 1")
    assert "AGENDADO" not in texto_de_publicacao(_corte(1))
    assert "AGENDADO" not in texto_de_publicacao(_corte(1, agendado_para="lixo"))
    ja_foi = texto_de_publicacao(_corte(1, postado=True, agendado_para=QUANDO))
    assert ja_foi.startswith("JÁ POSTADO") and "AGENDADO" not in ja_foi


# --- mover o arquivo -----------------------------------------------------------------------
def test_agendar_move_o_mp4_e_o_txt_para_agendados_e_acerta_o_registro(pastas):
    corte = _corte(1)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")
    corte.agendado_para = QUANDO
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == ["short_01_corte_1.mp4"] and resultado.falhas == []
    assert not (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert not (pastas.shorts / "short_01_corte_1.txt").exists()
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").read_bytes() == b"video"
    texto = (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.txt").read_text("utf-8")
    assert texto.startswith("AGENDADO PARA 04/11/2026 às 18:00")
    assert _arquivo_no_registro(pastas, corte) == "agendados/short_01_corte_1.mp4"


def test_mudar_o_horario_nao_mexe_no_arquivo_e_repetir_nao_move_nada(pastas):
    corte = _corte(1, agendado_para=QUANDO)
    _renderizado(pastas, corte, "agendados/short_01_corte_1.mp4")
    corte.agendado_para = "2026-11-10T09:30"
    assert aplicar_marcas_de_postado(pastas, [corte]).movidos == []
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()


def test_tirar_o_horario_traz_o_arquivo_de_volta_e_tira_a_linha_do_txt(pastas):
    corte = _corte(1, agendado_para=QUANDO)
    _renderizado(pastas, corte, "agendados/short_01_corte_1.mp4")
    corte.agendado_para = ""
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == ["short_01_corte_1.mp4"]
    assert (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert not (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()
    assert "AGENDADO" not in (pastas.shorts / "short_01_corte_1.txt").read_text("utf-8")
    assert _arquivo_no_registro(pastas, corte) == "short_01_corte_1.mp4"


def test_passada_a_hora_o_arquivo_vai_de_agendados_para_postados(pastas):
    corte = _corte(1, agendado_para=QUANDO)
    _renderizado(pastas, corte, "agendados/short_01_corte_1.mp4")
    corte.postado, corte.postado_em, corte.agendado_para = True, "2026-11-04", ""
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == ["short_01_corte_1.mp4"]
    assert not (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()
    assert (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.mp4").exists()
    texto = (pastas.shorts / PASTA_DOS_POSTADOS / "short_01_corte_1.txt").read_text("utf-8")
    assert texto.startswith("JÁ POSTADO (marcado em 04/11/2026)")
    assert _arquivo_no_registro(pastas, corte) == "postados/short_01_corte_1.mp4"


def test_desfazer_o_postado_de_um_short_que_ainda_esta_agendado_o_leva_a_agendados(pastas):
    corte = _corte(1, postado=True, agendado_para=QUANDO)
    _renderizado(pastas, corte, "postados/short_01_corte_1.mp4")
    corte.postado = False
    aplicar_marcas_de_postado(pastas, [corte])
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()


def test_agendado_e_desmarcado_fica_em_agendados_e_so_depois_vai_para_desmarcados(pastas):
    corte = _corte(1, agendado_para=QUANDO, incluir=False)
    _renderizado(pastas, corte, "desmarcados/short_01_corte_1.mp4")
    aplicar_marcas_de_postado(pastas, [corte])
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()
    corte.agendado_para = ""
    aplicar_marcas_de_postado(pastas, [corte])
    assert (pastas.shorts / PASTA_DOS_DESMARCADOS / "short_01_corte_1.mp4").exists()
    assert _arquivo_no_registro(pastas, corte) == "desmarcados/short_01_corte_1.mp4"


def test_nao_sobrescreve_um_arquivo_que_ja_esta_em_agendados(pastas):
    corte = _corte(1)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")
    (pastas.shorts / PASTA_DOS_AGENDADOS).mkdir()
    (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").write_bytes(b"outro")
    corte.agendado_para = QUANDO
    resultado = aplicar_marcas_de_postado(pastas, [corte])
    assert resultado.movidos == [] and "já existe" in resultado.falhas[0]
    assert (pastas.shorts / "short_01_corte_1.mp4").read_bytes() == b"video"
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").read_bytes() == b"outro"


def test_cortes_sem_agendamento_nem_postado_ficam_onde_estao(pastas):
    comum = _corte(3)
    _renderizado(pastas, comum, "short_03_corte_3.mp4")
    assert aplicar_marcas_de_postado(pastas, [comum]).movidos == []


# --- salvar no Studio e renderizar ---------------------------------------------------------
def test_salvar_com_titulo_novo_e_agendado_renomeia_e_vai_para_agendados(pastas):
    corte = _corte(1)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")
    corte.slug, corte.agendado_para = "deus_e_fiel", QUANDO
    resultado = sincronizar_arquivos(pastas, [corte])
    assert resultado.falhas == []
    assert _arquivo_no_registro(pastas, corte) == "agendados/short_01_deus_e_fiel.mp4"
    pasta = pastas.shorts / PASTA_DOS_AGENDADOS
    assert (pasta / "short_01_deus_e_fiel.mp4").read_bytes() == b"video"
    assert (pasta / "short_01_deus_e_fiel.txt").read_text("utf-8").startswith("AGENDADO PARA")
    assert list(pastas.shorts.glob("*.mp4")) == []


def _ctx(pastas: PastasProjeto):
    return SimpleNamespace(log=logging.getLogger("teste.agendados"), pastas=pastas)


def test_o_organizador_deixa_o_agendado_em_agendados_e_apaga_restos_parciais(pastas):
    agendado, comum = _corte(1, 0.0, agendado_para=QUANDO), _corte(2, 100.0)
    _renderizado(pastas, agendado, "agendados/short_01_corte_1.mp4")
    _renderizado(pastas, comum, "short_02_corte_2.mp4")
    resto = pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4.parcial"
    resto.write_bytes(b"x")
    agendado.incluir = False  # saiu da seleção, mas tem hora marcada
    registro = ler_registro(pastas.shorts_renderizados)
    organizar_pasta(_ctx(pastas), pastas.shorts, planejar_shorts([agendado, comum]), registro)
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()
    assert not resto.exists()
    assert not (pastas.shorts / PASTA_DOS_DESMARCADOS).exists()
    assert (pastas.shorts / "short_01_corte_2.mp4").exists()  # só o outro foi renumerado


def test_agendar_antes_de_renderizar_leva_o_arquivo_para_agendados_na_proxima_vez(pastas):
    corte = _corte(1, agendado_para=QUANDO)
    _renderizado(pastas, corte, "short_01_corte_1.mp4")  # renderizado antes de ser agendado
    registro = ler_registro(pastas.shorts_renderizados)
    organizar_pasta(_ctx(pastas), pastas.shorts, planejar_shorts([corte]), registro)
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.mp4").exists()
    assert (pastas.shorts / PASTA_DOS_AGENDADOS / "short_01_corte_1.txt").exists()
    assert not (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert _arquivo_no_registro(pastas, corte) == "agendados/short_01_corte_1.mp4"
