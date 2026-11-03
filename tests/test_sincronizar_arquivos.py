"""Depois de editar no Studio, os Shorts renderizados acompanham o título (nome, pasta, .txt)."""

from __future__ import annotations

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render import shorts as render_shorts
from spurgeon.render.shorts import (
    ler_registro,
    planejar_shorts,
    renomear_prontos,
    sincronizar_arquivos,
    texto_de_publicacao,
)
from spurgeon.shorts.dados import Corte


def _corte(id_: int, inicio: float, **mudancas) -> Corte:
    base = {
        "id": id_, "inicio": inicio, "fim": inicio + 50.0, "texto": "um trecho",
        "categoria": "outra", "nota": 80.0, "detalhe": {}, "titulo": f"Título {id_}",
        "descricao": "d", "hashtags": ["#fé"], "slug": f"corte_{id_}",
    }  # fmt: skip
    return Corte(**{**base, **mudancas})


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    p = PastasProjeto(tmp_path / "proj")
    p.criar()
    p.shorts.mkdir(parents=True, exist_ok=True)
    return p


def _pronto(pastas: PastasProjeto, cortes: list[Corte]) -> None:
    """Os Shorts de `cortes` renderizados com os nomes que eles têm agora."""
    registro = {}
    for curto in planejar_shorts(cortes):
        caminho = pastas.shorts / curto.video
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(f"video {curto.corte.id}".encode())
        caminho.with_suffix(".txt").write_text(texto_de_publicacao(curto.corte), encoding="utf-8")
        registro[str(curto.corte.id)] = {"arquivo": curto.video, "assinatura": "a"}
    escrever_json_atomico(pastas.shorts_renderizados, registro)


def _arquivo(pastas: PastasProjeto, corte: Corte) -> str:
    return ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]


def test_nome_novo_do_corte_renomeia_o_video_o_txt_e_o_registro(pastas):
    cortes = [_corte(1, 0.0), _corte(2, 100.0)]
    _pronto(pastas, cortes)
    cortes[0].slug = "deus_e_fiel"

    r = sincronizar_arquivos(pastas, cortes)

    assert r.movidos == ["short_01_deus_e_fiel.mp4"] and r.falhas == []
    assert _arquivo(pastas, cortes[0]) == "short_01_deus_e_fiel.mp4"
    assert (pastas.shorts / "short_01_deus_e_fiel.mp4").read_bytes() == b"video 1"
    assert (pastas.shorts / "short_01_deus_e_fiel.txt").exists()
    assert not (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert not (pastas.shorts / "short_01_corte_1.txt").exists()
    assert (pastas.shorts / "short_02_corte_2.mp4").exists()  # o outro não foi mexido


def test_sem_mudanca_nada_e_movido_nem_reescrito(pastas, monkeypatch):
    cortes = [_corte(1, 0.0)]
    _pronto(pastas, cortes)
    escritas: list = []
    monkeypatch.setattr(render_shorts, "escrever_texto_atomico", lambda *a, **k: escritas.append(a))
    r = sincronizar_arquivos(pastas, cortes)
    assert r.movidos == [] and r.falhas == [] and escritas == []


def test_o_txt_leva_os_textos_de_agora_mesmo_sem_renomear(pastas):
    cortes = [_corte(1, 0.0)]
    _pronto(pastas, cortes)
    cortes[0].descricao = "Uma descrição nova."
    cortes[0].hashtags = ["#salmo90"]
    assert sincronizar_arquivos(pastas, cortes).movidos == []
    texto = (pastas.shorts / "short_01_corte_1.txt").read_text("utf-8")
    assert "Uma descrição nova." in texto and "#salmo90" in texto


def test_numeros_que_trocam_de_lugar_nao_se_atropelam(pastas):
    cortes = [_corte(1, 0.0), _corte(2, 100.0)]
    _pronto(pastas, cortes)
    cortes[0].inicio, cortes[0].fim = 200.0, 250.0  # agora o corte 1 é o segundo do sermão
    r = sincronizar_arquivos(pastas, cortes)
    assert r.falhas == [] and len(r.movidos) == 2
    assert _arquivo(pastas, cortes[1]) == "short_01_corte_2.mp4"
    assert _arquivo(pastas, cortes[0]) == "short_02_corte_1.mp4"
    assert (pastas.shorts / "short_01_corte_2.mp4").read_bytes() == b"video 2"
    assert (pastas.shorts / "short_02_corte_1.mp4").read_bytes() == b"video 1"


def test_postado_e_novo_nome_ao_mesmo_tempo(pastas):
    cortes = [_corte(1, 0.0)]
    _pronto(pastas, cortes)
    cortes[0].slug, cortes[0].postado = "outro_nome", True
    sincronizar_arquivos(pastas, cortes)
    assert _arquivo(pastas, cortes[0]) == "postados/short_01_outro_nome.mp4"
    assert (pastas.shorts / "postados" / "short_01_outro_nome.mp4").exists()
    assert (
        (pastas.shorts / "postados" / "short_01_outro_nome.txt")
        .read_text("utf-8")
        .startswith("JÁ POSTADO")
    )
    assert list(pastas.shorts.glob("*.mp4")) == []


def test_nome_ocupado_por_outro_arquivo_nao_e_sobrescrito(pastas):
    cortes = [_corte(1, 0.0)]
    _pronto(pastas, cortes)
    intruso = pastas.shorts / "short_01_deus_e_fiel.mp4"
    intruso.write_bytes(b"outro arquivo, de outra origem")
    cortes[0].slug = "deus_e_fiel"
    r = sincronizar_arquivos(pastas, cortes)
    assert r.movidos == [] and "já existe" in r.falhas[0]
    assert intruso.read_bytes() == b"outro arquivo, de outra origem"
    assert (pastas.shorts / "short_01_corte_1.mp4").read_bytes() == b"video 1"  # voltou ao lugar
    assert (pastas.shorts / "short_01_corte_1.txt").exists()
    assert _arquivo(pastas, cortes[0]) == "short_01_corte_1.mp4"


def test_arquivo_preso_vira_falha_e_os_outros_seguem(pastas, monkeypatch):
    cortes = [_corte(1, 0.0), _corte(2, 100.0)]
    _pronto(pastas, cortes)
    cortes[0].slug, cortes[1].slug = "primeiro_novo", "segundo_novo"
    original = render_shorts._mover_par

    def mover(origem, destino):
        if "corte_1" in origem.name:
            raise PermissionError(13, "usado por outro processo")
        original(origem, destino)

    monkeypatch.setattr(render_shorts, "_mover_par", mover)
    r = sincronizar_arquivos(pastas, cortes)
    assert r.movidos == ["short_02_segundo_novo.mp4"]
    assert (
        len(r.falhas) == 1
        and "corte_1" in r.falhas[0]
        and "usado por outro processo" in r.falhas[0]
    )
    assert (pastas.shorts / "short_01_corte_1.mp4").exists()
    assert _arquivo(pastas, cortes[0]) == "short_01_corte_1.mp4"
    assert _arquivo(pastas, cortes[1]) == "short_02_segundo_novo.mp4"


def test_corte_sem_arquivo_ou_desmarcado_nao_gera_nada(pastas):
    cortes = [_corte(1, 0.0), _corte(2, 100.0, incluir=False)]
    r = sincronizar_arquivos(pastas, cortes)  # nada renderizado ainda
    assert r.movidos == [] and r.falhas == []
    assert not list(pastas.shorts.iterdir())


def test_renomear_prontos_devolve_o_resultado_sem_tocar_no_que_nao_precisa(pastas):
    cortes = [_corte(1, 0.0)]
    _pronto(pastas, cortes)
    registro = ler_registro(pastas.shorts_renderizados)
    r = renomear_prontos(pastas.shorts, planejar_shorts(cortes), registro)
    assert (r.movidos, r.falhas) == ([], [])
