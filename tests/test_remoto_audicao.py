"""Ouvir o Short com a legenda pelo celular (`remoto/audicao.py`): o som sem o vídeo.

O áudio precisa ter os MESMOS trechos do Short renderizado (sem os saltos, pausas encurtadas) e a
legenda o mesmo tempo. Só o último teste roda o FFmpeg de verdade.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from spurgeon.core.estado import Estado, salvar_estado
from spurgeon.core.ffmpeg import localizar_binario, sondar
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import audicao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto


def _transcrever(pastas: PastasProjeto, ate_s: float = 400.0) -> None:
    """Uma palavra a cada 0,5 s ("p0", "p1"…), cada uma com 0,4 s de fala."""
    palavras = [Palavra(f"p{i}", i * 0.5, i * 0.5 + 0.4) for i in range(int(ate_s / 0.5))]
    Transcricao(
        "teste", ate_s, blocos=[Bloco(0.0, ate_s, [Segmento(palavras)], concluido=True)]
    ).salvar(pastas.transcricao)


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _mudar_corte(pastas: PastasProjeto, id_: int, **campos) -> None:
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        if c["id"] == id_:
            c.update(campos)
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")


def _corte(pastas: PastasProjeto, id_: int):
    return next(s.corte for s in audicao.shorts_no_disco(pastas) if s.corte.id == id_)


def _rodar_de_mentira(chamadas: list):
    def rodar(args, duracao_s=None):
        chamadas.append((args, duracao_s))
        Path(args[-1]).write_bytes(b"m4a de mentira")

    return rodar


# --- a legenda no tempo do Short ------------------------------------------------------------


def test_legenda_sem_o_salto_e_no_tempo_do_short(pastas):
    _mudar_corte(pastas, 7, saltos=[[110.0, 115.0]])  # o corte 7 vai de 100 a 140 s
    legenda = audicao.legenda_do_short(audicao.carregar_palavras(pastas), _corte(pastas, 7))
    palavras = [p for frase in legenda.frases for p in frase]
    assert legenda.duracao == pytest.approx(35.0)
    assert palavras[0].texto == "p200" and palavras[0].inicio == pytest.approx(0.0)
    textos = {p.texto: p for p in palavras}
    assert "p225" not in textos  # 112,5 s caiu no salto
    assert textos["p240"].inicio == pytest.approx(15.0)  # 120 s − 100 − 5 do salto
    assert [t.inicio for t in legenda.trechos] == pytest.approx([0.0, 15.0])


def test_legenda_em_json_para_o_celular(pastas):
    legenda = audicao.legenda_do_short(audicao.carregar_palavras(pastas), _corte(pastas, 3))
    dados = audicao.legenda_para_json(legenda)
    assert dados["duracao"] == pytest.approx(40.0)
    primeira = dados["frases"][0][0]
    assert primeira == {"t": "p100", "i": 0.0, "f": 0.4}


# --- de onde vem o som ----------------------------------------------------------------------


def test_fonte_do_som_na_ordem_tratado_original_short(pastas, tmp_path):
    estado = Estado.model_validate_json(pastas.estado.read_text("utf-8"))
    short_3 = next(s for s in audicao.shorts_no_disco(pastas) if s.corte.id == 3)
    short_7 = next(s for s in audicao.shorts_no_disco(pastas) if s.corte.id == 7)

    # nada além do Short renderizado: o som sai dele (corte 3) ou de lugar nenhum (corte 7)
    fonte = audicao.fonte_do_audio(pastas, short_3)
    assert fonte.tipo == "short" and fonte.aviso == ""
    with pytest.raises(audicao.SemFonteDeAudio):
        audicao.fonte_do_audio(pastas, short_7)

    original = tmp_path / "sermao.mp4"
    original.write_bytes(b"x")
    estado.entrada.caminho = str(original)
    salvar_estado(pastas.raiz, estado)
    assert audicao.fonte_do_audio(pastas, short_7).caminho == original

    tratado = pastas.audio_final(estado.opcoes)
    tratado.write_bytes(b"wav")
    assert audicao.fonte_do_audio(pastas, short_7) == audicao.FonteDoAudio(tratado, "original")


def test_short_renderizado_que_mudou_avisa(pastas):
    registro = {"3": {"arquivo": "postados/short_01_titulo_3.mp4", "assinatura": "a",
                      "corte": [1.0, 2.0]}}  # fmt: skip
    pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")
    short = next(s for s in audicao.shorts_no_disco(pastas) if s.corte.id == 3)
    assert "pode não bater" in audicao.fonte_do_audio(pastas, short).aviso


def test_argumentos_cortam_o_original_e_so_extraem_do_short(pastas):
    _mudar_corte(pastas, 7, saltos=[[110.0, 115.0]])
    corte = _corte(pastas, 7)
    legenda = audicao.legenda_do_short(audicao.carregar_palavras(pastas), corte)
    do_original = audicao.argumentos_do_ffmpeg(
        audicao.FonteDoAudio(Path("v.mp4"), "original"), corte, legenda, Path("s.m4a")
    )
    assert do_original[do_original.index("-ss") + 1] == "100"
    grafo = do_original[do_original.index("-filter_complex") + 1]
    assert grafo.count("atrim=") == 2 and "concat=n=2" in grafo and "[0:a]" in grafo
    assert do_original[do_original.index("-t", do_original.index("-i")) + 1] == "35"
    do_short = audicao.argumentos_do_ffmpeg(
        audicao.FonteDoAudio(Path("s.mp4"), "short"), corte, legenda, Path("s.m4a")
    )
    assert "-ss" not in do_short and "atrim" not in " ".join(do_short)


def test_audio_guardado_e_refeito_quando_o_corte_muda(pastas):
    chamadas: list = []
    primeiro = audicao.audio_do_short(pastas, 3, rodar=_rodar_de_mentira(chamadas))
    de_novo = audicao.audio_do_short(pastas, 3, rodar=_rodar_de_mentira(chamadas))
    assert primeiro.arquivo == de_novo.arquivo and len(chamadas) == 1
    assert primeiro.arquivo.parent == pastas.work / "remoto"
    _mudar_corte(pastas, 3, fim=80.0)
    novo = audicao.audio_do_short(pastas, 3, rodar=_rodar_de_mentira(chamadas))
    assert novo.arquivo != primeiro.arquivo and len(chamadas) == 2
    assert not primeiro.arquivo.exists()  # o do corte antigo foi apagado
    assert not list((pastas.work / "remoto").glob("*.parcial"))


# --- pelo servidor --------------------------------------------------------------------------


@pytest.fixture
def cliente(pastas, tmp_path, monkeypatch):
    chamadas: list = []
    monkeypatch.setattr(audicao, "executar_ffmpeg", _rodar_de_mentira(chamadas))
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), pastas.raiz.parent, Avisos(cofre, TransporteFalso()), "x",
        gravar_config=lambda _c: None,
    )  # fmt: skip
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    cliente.chamadas = chamadas
    return cliente


def test_servidor_entrega_legenda_e_audio(cliente):
    base = f"/api/projetos/{NOME_DO_PROJETO}/shorts"
    legenda = cliente.get(f"{base}/3/legenda").json()
    assert legenda["fonte"] == "short" and legenda["frases"][0][0]["t"] == "p100"
    audio = cliente.get(f"{base}/3/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mp4"
    assert audio.content == b"m4a de mentira" and len(cliente.chamadas) == 1

    sem_fonte = cliente.get(f"{base}/7/legenda").json()
    assert sem_fonte["fonte"] == "" and "não foi renderizado" in sem_fonte["aviso"]
    assert cliente.get(f"{base}/7/audio").status_code == 404
    assert cliente.get(f"{base}/12345/legenda").status_code == 404


# --- com o FFmpeg de verdade ----------------------------------------------------------------


@pytest.mark.ffmpeg
def test_audio_de_verdade_tem_a_duracao_do_short(pastas):
    estado = Estado.model_validate_json(pastas.estado.read_text("utf-8"))
    tratado = pastas.audio_final(estado.opcoes)
    subprocess.run(
        [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=120",
         "-ac", "2", "-c:a", "pcm_s16le", str(tratado)],
        check=True,
    )  # fmt: skip
    _mudar_corte(pastas, 3, saltos=[[60.0, 70.0]])  # 50 a 90 s, menos 10 s
    pronto = audicao.audio_do_short(pastas, 3)
    info = sondar(pronto.arquivo)
    assert info.tem_audio and info.duracao_s == pytest.approx(30.0, abs=0.1)
    assert info.canais_audio == 1
