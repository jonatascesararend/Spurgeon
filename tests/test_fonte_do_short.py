"""De onde os Shorts são cortados: o vídeo longo pronto (renderizado antes) ou o original."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.estado import hash_rapido
from spurgeon.core.ffmpeg import InfoMidia, sondar
from spurgeon.core.pipeline import Pipeline
from spurgeon.core.projeto import PastasProjeto
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.render import fonte_do_short as fonte_mod
from spurgeon.render import video_longo as vl
from spurgeon.render.fonte_do_short import (
    CHAVE_NO_INFO,
    FonteDoShort,
    dados_do_longo,
    fonte_do_longo,
    fonte_do_original,
    leitura_do_corte,
    motivo_de_nao_servir,
    palavras_na_fonte,
    trechos_na_fonte,
)
from spurgeon.render.legenda import PalavraDaLegenda
from spurgeon.render.shorts import RenderizarShorts, assinatura_do_short, ler_registro
from spurgeon.shorts.etapas import gravar_cortes
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao
from tests.test_render import corte, preparar_longo, rodar


def _info(**campos) -> InfoMidia:
    base = {
        "caminho": Path("longo.mp4"),
        "duracao_s": 60.0,
        "tem_video": True,
        "tem_audio": True,
        "largura": 1920,
        "altura": 1080,
        "fps": 29.97,
        "canais_audio": 2,
        "pix_fmt": "yuv420p",
        "transferencia": "bt709",
        "primarias": "bt709",
        "matriz": "bt709",
    }
    return InfoMidia(**{**base, **campos})


def _longo(mapa: MapaTempo, **campos) -> FonteDoShort:
    """Um vídeo longo de mentira: só o que a conta de tempo e de recorte precisam."""
    base = {
        "video": Path("longo.mp4"),
        "audio": Path("longo.mp4"),
        "info": _info(),
        "canais": 2,
        "fps": Fraction(30000, 1001),
        "enquadramento": None,
        "tratamento": None,
        "mapa": mapa,
        "identidade": "longo:abc",
        "inicio_util_s": mapa.deslocamento,
        "fim_util_s": mapa.deslocamento + mapa.duracao_editada,
    }
    return FonteDoShort(**{**base, **campos})


# 3 s de introdução; o vídeo principal tem dois trechos do original, com 10 s de pausa cortada
MAPA = MapaTempo([Trecho(0.0, 10.0), Trecho(20.0, 30.0)], deslocamento=3.0)


# --- a ordem das etapas ----------------------------------------------------------------------
def test_o_video_longo_e_renderizado_antes_dos_shorts_e_os_shorts_dependem_dele():
    from spurgeon.core.etapas import etapas_registradas

    etapas = etapas_registradas()
    ids = [e.id for e in etapas]
    Pipeline(etapas)  # a ordem continua válida (nenhuma etapa depende de uma que vem depois)
    assert ids.index("aprovar_cortes") < ids.index("renderizar_video_longo")
    assert ids.index("renderizar_video_longo") == ids.index("renderizar_shorts") - 1
    assert ids[-1] == "finalizar"
    assert "renderizar_video_longo" in RenderizarShorts.depende_de


# --- o tempo do corte na fonte ---------------------------------------------------------------
def test_no_original_o_corte_e_lido_como_esta_e_nao_ha_conversao():
    original = fonte_do_original(
        Path("v.mov"), Path("a.wav"), _info(transferencia="arib-std-b67"), 1, OpcoesProcessamento()
    )
    c = corte(1, 100.0, 140.0)
    assert not original.eh_o_longo and original.identidade is None
    assert leitura_do_corte(original, c) == (100.0, 40.0, None)


def test_no_longo_o_corte_vai_para_o_tempo_do_longo_sem_as_pausas_cortadas():
    c = corte(1, 5.0, 25.0)  # atravessa a pausa de 10 a 20 s do original
    inicio, janela, converter = leitura_do_corte(_longo(MAPA), c)
    assert inicio == pytest.approx(8.0)  # 3 s da introdução + 5 s
    assert janela == pytest.approx(10.0)  # 20 s do original, menos 10 s de pausa cortada
    assert converter(0.0) == pytest.approx(0.0)
    assert converter(5.0) == pytest.approx(5.0)  # 10 s do original: o ponto do corte
    assert converter(15.0) == pytest.approx(5.0)  # 20 s do original: o mesmo ponto
    assert converter(20.0) == pytest.approx(10.0)


def test_trechos_e_palavras_acompanham_o_tempo_do_longo():
    _, _, converter = leitura_do_corte(_longo(MAPA), corte(1, 5.0, 25.0))
    # um Short sem nada cortado: o trecho todo vira um só, mais curto
    assert trechos_na_fonte([Trecho(0.0, 20.0)], converter) == [Trecho(0.0, pytest.approx(10.0))]
    # dois trechos que a pausa cortada do longo faz encostar viram um só
    juntos = trechos_na_fonte([Trecho(0.0, 5.0), Trecho(15.0, 20.0)], converter)
    assert len(juntos) == 1 and juntos[0].inicio == 0.0 and juntos[0].fim == pytest.approx(10.0)
    # um trecho que só existia dentro da pausa some
    assert trechos_na_fonte([Trecho(6.0, 14.0)], converter) == []
    palavras = palavras_na_fonte(
        [PalavraDaLegenda("a", 1.0, 2.0), PalavraDaLegenda("b", 16.0, 17.0)], converter
    )
    assert (palavras[0].inicio, palavras[0].fim) == (pytest.approx(1.0), pytest.approx(2.0))
    assert (palavras[1].inicio, palavras[1].fim) == (pytest.approx(6.0), pytest.approx(7.0))


# --- um Short que não cabe no vídeo longo -----------------------------------------------------
def test_o_original_serve_a_qualquer_short():
    original = fonte_do_original(Path("v"), Path("a"), _info(), 1, OpcoesProcessamento())
    assert motivo_de_nao_servir(original, corte(1, 0.0, 5.0), 0.0, None) == ""


def test_short_dentro_do_miolo_do_longo_serve():
    assert motivo_de_nao_servir(_longo(MAPA), corte(1, 2.0, 18.0), 0.0, None) == ""


def test_short_que_pega_a_transicao_da_introducao_ou_da_vinheta_nao_serve():
    longo = _longo(MAPA, inicio_util_s=4.0, fim_util_s=22.0)  # 1 s de mescla em cada ponta
    assert "introdução" in motivo_de_nao_servir(longo, corte(1, 0.0, 8.0), 0.0, None)
    assert "vinheta" in motivo_de_nao_servir(longo, corte(1, 20.0, 30.0), 0.0, None)
    assert motivo_de_nao_servir(longo, corte(1, 2.0, 8.0), 0.0, None) == ""


def test_short_que_so_tinha_pausa_cortada_nao_serve():
    assert "pausas" in motivo_de_nao_servir(_longo(MAPA), corte(1, 11.0, 19.5), 0.0, None)


def test_marca_queimada_no_longo_so_atrapalha_quando_o_recorte_a_alcanca():
    # a marca no canto inferior direito de 1920x1080: x de 1500 a 1850, y de 960 a 1050
    longo = _longo(MAPA, marca_queimada=(1500, 960, 350, 90))
    c = corte(1, 2.0, 18.0)
    assert motivo_de_nao_servir(longo, c, 0.0, None) == ""  # o 9:16 central fica de fora
    assert "marca d'água" in motivo_de_nao_servir(longo, c, 40.0, None)  # deslocado até ela


# --- o vídeo longo como fonte ------------------------------------------------------------------
@pytest.fixture
def projeto_com_longo(tmp_path, monkeypatch):
    pastas = PastasProjeto(tmp_path / "projeto")
    pastas.criar()
    pastas.video_longo.write_bytes(b"video longo de mentira" * 100)
    monkeypatch.setattr(fonte_mod, "sondar", lambda caminho: _info(caminho=caminho))
    opcoes = OpcoesProcessamento()
    assinatura = opcoes.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes)

    def gravar(**mudancas) -> None:
        bloco = dados_do_longo(
            hash_do_original="hash-do-original",
            trechos=[Trecho(0.0, 10.0), Trecho(20.0, 30.0)],
            inicio_s=3.0,
            zona_do_inicio_s=1.0,
            zona_do_fim_s=0.0,
            tamanho=(1920, 1080),
            enquadramento=opcoes.enquadramento_nos_curtos,
            tratamento=opcoes.tratamento_nos_curtos,
            marca=None,
            assinatura_das_opcoes=assinatura,
            janela=(0.0, 0.0),
        )
        bloco.update(mudancas)
        escrever_json_atomico(pastas.video_longo_info, {"duracao_s": 23.0, CHAVE_NO_INFO: bloco})

    gravar()
    return pastas, opcoes, assinatura, gravar


def _fonte(pastas, opcoes, assinatura):
    return fonte_do_longo(
        pastas, opcoes, hash_do_original="hash-do-original", assinatura_do_longo=assinatura
    )


def test_o_longo_serve_quando_e_deste_video_e_destas_opcoes(projeto_com_longo):
    pastas, opcoes, assinatura, _ = projeto_com_longo
    fonte, motivo = _fonte(pastas, opcoes, assinatura)
    assert motivo == "" and fonte is not None and fonte.eh_o_longo
    assert fonte.identidade == f"longo:{hash_rapido(pastas.video_longo)}"
    assert fonte.audio == fonte.video == pastas.video_longo  # o som também sai dele
    assert fonte.enquadramento is None and fonte.tratamento is None  # já estão na imagem
    assert fonte.mapa.original_para_editado(25.0) == pytest.approx(3.0 + 10.0 + 5.0)
    assert fonte.inicio_util_s == pytest.approx(3.0 + 1.1)  # a mescla mais a folga
    assert fonte.fim_util_s == pytest.approx(3.0 + 20.0)


def test_sem_o_arquivo_do_longo_os_shorts_saem_do_original(projeto_com_longo):
    pastas, opcoes, assinatura, _ = projeto_com_longo
    pastas.video_longo.unlink()
    fonte, motivo = _fonte(pastas, opcoes, assinatura)
    assert fonte is None and "ainda não foi gerado" in motivo


def test_longo_de_uma_versao_antiga_nao_traz_o_mapa_e_nao_serve(projeto_com_longo):
    pastas, opcoes, assinatura, _ = projeto_com_longo
    escrever_json_atomico(pastas.video_longo_info, {"duracao_s": 23.0, "preset": "slow"})
    fonte, motivo = _fonte(pastas, opcoes, assinatura)
    assert fonte is None and "versão anterior" in motivo
    pastas.video_longo_info.unlink()  # nem o arquivo de informações
    assert _fonte(pastas, opcoes, assinatura)[0] is None


@pytest.mark.parametrize(
    ("mudanca", "trecho_do_motivo"),
    [
        ({"video": "outro-video"}, "outro vídeo"),
        ({"opcoes": "assinatura-velha"}, "opções do vídeo longo mudaram"),
        ({"janela": [10.0, 50.0]}, "começo e o fim"),
        ({"zoom": {"zoom_pct": 150.0, "centro_x_pct": 50.0, "centro_y_pct": 50.0}}, "zoom"),
        ({"tratamento": {"brilho": 10}}, "cor do vídeo longo"),
        ({"tamanho": [1280, 720]}, "tamanho"),
        ({"trechos": [[5.0, 1.0]]}, "mapa de tempo"),
    ],
)
def test_o_longo_que_nao_vale_mais_e_recusado_com_o_motivo(
    projeto_com_longo, mudanca, trecho_do_motivo
):
    pastas, opcoes, assinatura, gravar = projeto_com_longo
    gravar(**mudanca)
    fonte, motivo = _fonte(pastas, opcoes, assinatura)
    assert fonte is None and trecho_do_motivo in motivo


def test_zoom_ou_cor_so_no_longo_nao_valem_para_os_shorts(projeto_com_longo):
    """O longo foi gravado COM zoom e com cor, mas os Shorts não os querem: saem do original."""
    pastas, _, _, gravar = projeto_com_longo
    com_zoom = OpcoesProcessamento(zoom_pct=140.0, zoom_nos_shorts=False)
    gravar(
        zoom=json.loads(json.dumps(Enquadramento(zoom_pct=140.0).model_dump())),
        opcoes=com_zoom.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes),
    )
    fonte, motivo = _fonte(
        pastas, com_zoom, com_zoom.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes)
    )
    assert fonte is None and "zoom" in motivo

    com_cor = OpcoesProcessamento(tratamento_ativo=True, tratamento_brilho=10.0)
    gravar(
        tratamento=json.loads(json.dumps(TratamentoImagem(brilho=10.0).model_dump())),
        opcoes=com_cor.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes),
    )
    com_cor_so_no_longo = com_cor.model_copy(update={"tratamento_nos_shorts": False})
    fonte, motivo = _fonte(
        pastas,
        com_cor_so_no_longo,
        com_cor_so_no_longo.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes),
    )
    assert fonte is None and "cor" in motivo


def test_zoom_nos_dois_sai_do_longo_sem_aplicar_o_zoom_de_novo(projeto_com_longo):
    pastas, _, _, gravar = projeto_com_longo
    com_zoom = OpcoesProcessamento(zoom_pct=140.0, zoom_nos_shorts=True)
    assinatura = com_zoom.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes)
    gravar(
        zoom=json.loads(json.dumps(com_zoom.enquadramento_nos_curtos.model_dump())),
        opcoes=assinatura,
    )
    fonte, motivo = _fonte(pastas, com_zoom, assinatura)
    assert motivo == "" and fonte is not None
    assert fonte.enquadramento is None  # o zoom já está na imagem do longo


# --- a assinatura -------------------------------------------------------------------------------
def test_a_assinatura_so_leva_a_fonte_quando_o_short_sai_do_longo():
    c = corte(1, 0.0, 2.5)
    o = OpcoesProcessamento()
    sem = assinatura_do_short("h", o, c, "bt709", True, None)
    assert sem == assinatura_do_short("h", o, c, "bt709", True, None, fonte_id=None)
    com = assinatura_do_short("h", o, c, "bt709", True, None, fonte_id="longo:a1")
    assert com != sem  # um Short já pronto, do original, é refeito se passar a sair do longo
    assert com != assinatura_do_short("h", o, c, "bt709", True, None, fonte_id="longo:b2")


# --- de ponta a ponta, com o FFmpeg --------------------------------------------------------------
def _palavras_do_sermao() -> list[Palavra]:
    return [
        Palavra("Graça", 3.2, 3.6), Palavra("de", 3.8, 4.2), Palavra("Deus,", 4.4, 4.9),
        Palavra("amor", 17.0, 17.5), Palavra("perdão.", 17.7, 18.2), Palavra("Amém.", 18.4, 19.0),
    ]  # fmt: skip


def _preparar_shorts(contexto, video, audio) -> None:
    preparar_longo(
        contexto, video, audio,
        normalizar_volume=False, gerar_shorts=True, legendas_dinamicas=False,
    )  # fmt: skip
    segmento = Segmento(_palavras_do_sermao())
    Transcricao("t", 60.0, blocos=[Bloco(0, 60, [segmento], True)]).salvar(
        contexto.pastas.transcricao
    )
    # o Short atravessa a pausa de 5 a 17 s, que o vídeo longo cortou quase toda
    gravar_cortes(contexto.pastas.cortes_shorts, [corte(1, 3.0, 20.0)])


def _log(contexto) -> str:
    return contexto.pastas.log.read_text(encoding="utf-8")


@pytest.mark.ffmpeg
def test_o_video_longo_guarda_o_mapa_de_tempo_para_os_shorts(
    contexto, video_com_pausas, audio_com_pausas
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    cortes = ler_json(contexto.pastas.cortes)["trechos"]
    rodar(vl.RenderizarVideoLongo(), contexto)

    guardado = ler_json(contexto.pastas.video_longo_info)
    assert guardado["crf"] and guardado["preset"]  # o que o modo "só vinhetas" já lia continua lá
    bloco = guardado[CHAVE_NO_INFO]
    assert bloco["trechos"] == cortes and bloco["video"] == contexto.estado.entrada.hash_rapido
    assert bloco["inicio_s"] == 0.0 and bloco["zona_do_inicio_s"] == 0.0  # sem introdução
    assert bloco["tamanho"] == [160, 120] and bloco["marca"] is None
    assert bloco["opcoes"] == contexto.opcoes.assinatura(vl.RenderizarVideoLongo.opcoes_relevantes)


@pytest.mark.ffmpeg
def test_os_shorts_saem_do_video_longo_e_sem_ele_saem_do_original(
    contexto, video_com_pausas, audio_com_pausas
):
    _preparar_shorts(contexto, video_com_pausas, audio_com_pausas)
    rodar(vl.RenderizarVideoLongo(), contexto)
    etapa = RenderizarShorts()
    rodar(etapa, contexto)

    video = contexto.pastas.shorts / "short_01_corte_1.mp4"
    do_longo = sondar(video)
    assert (do_longo.largura, do_longo.altura) == (1080, 1920) and do_longo.tem_audio
    assert do_longo.duracao_s == pytest.approx(6.0, abs=0.25)  # 17 s do original, sem a pausa
    assert "Os Shorts saem do vídeo longo pronto" in _log(contexto)
    assert ler_registro(contexto.pastas.shorts_renderizados)["1"]["fonte"] == "longo"

    # um Short já pronto continua valendo (a assinatura leva o vídeo longo de onde ele saiu)
    marca = video.stat().st_mtime_ns
    rodar(etapa, contexto)
    assert video.stat().st_mtime_ns == marca

    # sem o mapa de tempo (um vídeo longo de versão antiga) o Short sai do original, inteiro
    escrever_json_atomico(contexto.pastas.video_longo_info, {"duracao_s": 35.0})
    rodar(etapa, contexto)
    do_original = sondar(video)
    assert do_original.duracao_s == pytest.approx(17.0, abs=0.25)
    assert "Os Shorts saem do vídeo original" in _log(contexto)
    assert "fonte" not in ler_registro(contexto.pastas.shorts_renderizados)["1"]


@pytest.mark.ffmpeg
def test_o_short_que_nao_cabe_no_longo_sai_do_original_e_os_outros_nao(
    contexto, video_com_pausas, audio_com_pausas
):
    _preparar_shorts(contexto, video_com_pausas, audio_com_pausas)
    rodar(vl.RenderizarVideoLongo(), contexto)
    # o segundo Short é só da pausa que o longo cortou (6 a 16 s do original): nada dele existe lá
    gravar_cortes(
        contexto.pastas.cortes_shorts,
        [corte(1, 3.0, 20.0), corte(2, 6.0, 16.0, slug="so_pausa")],
    )
    rodar(RenderizarShorts(), contexto)
    registro = ler_registro(contexto.pastas.shorts_renderizados)
    assert registro["1"]["fonte"] == "longo" and "fonte" not in registro["2"]
    assert "sai do vídeo original, não do longo" in _log(contexto)
    assert sondar(contexto.pastas.shorts / "short_02_so_pausa.mp4").duracao_s == pytest.approx(
        10.0, abs=0.25
    )
