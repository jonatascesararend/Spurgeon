"""Cortar as pausas dos Shorts: contas, filtros, assinaturas, prévia, formulário e render."""

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from fractions import Fraction
from itertools import accumulate, pairwise
from pathlib import Path

import pytest

from spurgeon.core.config import OpcoesProcessamento, raiz_repositorio
from spurgeon.core.estado import EntradaVideo
from spurgeon.core.ffmpeg import InfoMidia, localizar_binario, sondar
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.render import filtros as f
from spurgeon.render import previa
from spurgeon.render.legenda import PalavraDaLegenda
from spurgeon.render.shorts import (
    RenderizarShorts,
    assinatura_do_short,
    assinatura_visual,
    filtro_de_audio,
    filtro_de_video,
    linhas_do_audio_cortado,
    linhas_do_video_cortado,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import gravar_cortes
from spurgeon.shorts.pausas import (
    RESIDUAL_DA_PAUSA_S,
    duracao_do_short,
    ha_o_que_cortar,
    limiar_valido,
    mapa_do_short,
    pausas_do_corte,
    remapear_palavras,
    residual_da_pausa,
    trechos_mantidos,
)
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao

FPS = Fraction(30000, 1001)


def info(**campos) -> InfoMidia:
    base = {
        "caminho": Path("x.mov"), "duracao_s": 60.0, "tem_video": True, "tem_audio": True,
        "largura": 1920, "altura": 1080, "fps": 29.97, "pix_fmt": "yuv420p10le",
        "transferencia": "arib-std-b67", "primarias": "bt2020", "matriz": "bt2020nc",
    }  # fmt: skip
    return InfoMidia(**{**base, **campos})


def corte(inicio: float, fim: float, **campos) -> Corte:
    base = {
        "id": 1, "inicio": inicio, "fim": fim, "texto": "t", "categoria": "outra", "nota": 80.0,
        "detalhe": {}, "titulo": "Título", "descricao": "D.", "hashtags": ["#fé"],
        "slug": "corte_1",
    }  # fmt: skip
    return Corte(**{**base, **campos})


def fala(*partes: tuple[str, float, float]) -> list[Palavra]:
    return [Palavra(texto, inicio, fim) for texto, inicio, fim in partes]


# Corte de 10 s a 30 s: respiro de 2 s antes da primeira palavra e de 1 s depois da última.
# Pausas internas (em segundos desde o começo do corte): 0,6 (b→c), 3,0 (c→d) e 1,0 (d→e).
PALAVRAS = fala(
    ("antes", 8.0, 9.5),  # fora do corte
    ("a", 12.0, 12.5),
    ("b", 12.7, 13.5),
    ("c", 14.1, 15.0),
    ("d", 18.0, 19.0),
    ("e", 20.0, 28.0),
    ("depois", 31.0, 32.0),  # fora do corte
)
CORTE = corte(10.0, 29.0)


# --- as contas ------------------------------------------------------------------------------


def test_as_pausas_sao_os_vaos_entre_palavras_dentro_do_corte():
    pausas = pausas_do_corte(PALAVRAS, CORTE)
    assert pausas == pytest.approx([(2.5, 2.7), (3.5, 4.1), (5.0, 8.0), (9.0, 10.0)])


def test_o_respiro_do_comeco_e_do_fim_nao_e_pausa():
    pausas = pausas_do_corte(PALAVRAS, CORTE)
    assert all(inicio >= 2.5 and fim <= 10.0 for inicio, fim in pausas)  # nem 0→2, nem 18→19


def test_palavra_cortada_pela_ponta_do_corte_conta_e_os_tempos_ficam_dentro_dele():
    palavras = fala(("x", 9.0, 10.4), ("y", 12.0, 12.5), ("z", 18.8, 20.0))
    pausas = pausas_do_corte(palavras, corte(10.0, 19.0))
    assert pausas == pytest.approx([(0.4, 2.0), (2.5, 8.8)])
    assert all(0.0 <= i < f <= 9.0 for i, f in pausas)


def test_pausa_igual_ao_limiar_e_cortada_e_a_menor_nao():
    exata = fala(("a", 10.0, 11.0), ("b", 12.0, 13.0), ("c", 13.9, 15.0))  # vãos de 1,0 e 0,9
    trechos = trechos_mantidos(exata, corte(10.0, 15.0), 1.0)
    assert len(trechos) == 2  # só a de 1,0 s (ruído de ponto flutuante não decide)
    assert trechos[0].fim == pytest.approx(1.0 + 0.2) and trechos[1].inicio == pytest.approx(1.8)


def test_cada_pausa_cortada_deixa_o_residual_de_0_4_s():
    trechos = trechos_mantidos(PALAVRAS, CORTE, 1.0)
    # cortadas: 3,0 s (5,0→8,0) e 1,0 s (9,0→10,0); a de 0,6 s fica
    assert [(round(t.inicio, 3), round(t.fim, 3)) for t in trechos] == [
        (0.0, 5.2), (7.8, 9.2), (9.8, 19.0),
    ]  # fmt: skip
    editado = MapaTempo(trechos)
    assert editado.duracao_editada == pytest.approx(19.0 - (3.0 - 0.4) - (1.0 - 0.4))
    assert RESIDUAL_DA_PAUSA_S == 0.4


def test_com_limiar_baixo_o_residual_e_metade_do_limiar():
    assert residual_da_pausa(1.0) == pytest.approx(0.4)
    assert residual_da_pausa(0.5) == pytest.approx(0.25)
    assert residual_da_pausa(0.6) == pytest.approx(0.3)
    assert residual_da_pausa(10.0) == pytest.approx(0.4)
    trechos = trechos_mantidos(PALAVRAS, CORTE, 0.5)  # agora também a de 0,6 s (a de 0,2 s não)
    cortes = [(a.fim, b.inicio) for a, b in pairwise(trechos)]
    assert len(cortes) == 3  # 0,6 s, 3,0 s e 1,0 s
    assert cortes[0][1] - cortes[0][0] == pytest.approx(0.6 - 0.25)


def test_os_trechos_cobrem_o_corte_do_zero_a_duracao():
    for limiar in (0.5, 1.0, 2.0, 5.0):
        trechos = trechos_mantidos(PALAVRAS, CORTE, limiar)
        assert trechos[0].inicio == 0.0 and trechos[-1].fim == pytest.approx(CORTE.duracao)


def test_sem_limiar_ou_sem_pausa_longa_nada_e_cortado():
    assert trechos_mantidos(PALAVRAS, CORTE, None) == [Trecho(0.0, CORTE.duracao)]
    assert not ha_o_que_cortar(trechos_mantidos(PALAVRAS, CORTE, None))
    assert not ha_o_que_cortar(trechos_mantidos(PALAVRAS, CORTE, 10.0))  # a maior é de 3 s
    assert ha_o_que_cortar(trechos_mantidos(PALAVRAS, CORTE, 1.0))


def test_duracao_do_short():
    assert duracao_do_short(PALAVRAS, CORTE, None) == CORTE.duracao
    assert duracao_do_short(PALAVRAS, CORTE, 10.0) == CORTE.duracao  # nada a cortar
    assert duracao_do_short(PALAVRAS, CORTE, 1.0) == pytest.approx(19.0 - 2.6 - 0.6)
    assert duracao_do_short(PALAVRAS, CORTE, 3.0) == pytest.approx(19.0 - 2.6)
    assert duracao_do_short(PALAVRAS, CORTE, 0.5) < duracao_do_short(PALAVRAS, CORTE, 1.0)


def test_corte_sem_pausa_alguma():
    seguido = fala(("a", 10.0, 11.0), ("b", 11.1, 12.0))
    assert duracao_do_short(seguido, corte(10.0, 12.0), 0.5) == pytest.approx(2.0)
    assert duracao_do_short([], corte(10.0, 12.0), 0.5) == pytest.approx(2.0)


def test_o_mapa_leva_o_tempo_do_corte_para_o_short():
    mapa = mapa_do_short(PALAVRAS, CORTE, 1.0)
    assert mapa.original_para_editado(4.0) == pytest.approx(4.0)  # antes de qualquer corte
    assert mapa.original_para_editado(5.0) == pytest.approx(5.0)  # fim da palavra "c"
    assert mapa.original_para_editado(8.0) == pytest.approx(5.4)  # começo de "d": sobrou 0,4 s
    assert mapa.original_para_editado(9.0) == pytest.approx(6.4)
    assert mapa.original_para_editado(10.0) == pytest.approx(6.8)  # começo de "e": 0,4 s depois
    assert mapa.original_para_editado(19.0) == pytest.approx(15.8)


def test_remapear_a_legenda_usa_inicio_e_fim_de_cada_palavra():
    palavras = [
        PalavraDaLegenda("a", 2.0, 2.5), PalavraDaLegenda("c", 4.1, 5.0),
        PalavraDaLegenda("d", 8.0, 9.0), PalavraDaLegenda("e", 10.0, 18.0),
    ]  # fmt: skip
    novas = remapear_palavras(palavras, mapa_do_short(PALAVRAS, CORTE, 1.0))
    assert [(p.texto, round(p.inicio, 3), round(p.fim, 3)) for p in novas] == [
        ("a", 2.0, 2.5), ("c", 4.1, 5.0), ("d", 5.4, 6.4), ("e", 6.8, 14.8),
    ]  # fmt: skip
    # a pausa entre as palavras virou o residual
    assert novas[2].inicio - novas[1].fim == pytest.approx(RESIDUAL_DA_PAUSA_S)


def test_sem_nada_a_cortar_a_legenda_volta_igual():
    palavras = [PalavraDaLegenda("a", 2.0, 2.5), PalavraDaLegenda("b", 3.0, 4.0)]
    mapa = mapa_do_short(PALAVRAS, CORTE, 10.0)
    assert remapear_palavras(palavras, mapa) == palavras
    assert remapear_palavras(palavras, mapa_do_short(PALAVRAS, CORTE, None)) == palavras


# --- os filtros ------------------------------------------------------------------------------

# O que `filtro_de_video` e `filtro_de_audio` geravam antes desta opção existir.
FILTRO_DE_ANTES = (
    "[0:v]fps=30000/1001,crop=608:1080:732:0,zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:"
    "rin=tv:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=1080:1920:flags=lanczos,setsar=1,"
    "ass=legendas/s.ass:fontsdir=fontes,format=yuv420p[v]"
)
AUDIO_DE_ANTES = (
    "aresample=48000,pan=stereo|c0=c0|c1=c0,afade=t=in:st=0:d=0.100,afade=t=out:st=39.9:d=0.100"
)


def test_sem_a_opcao_o_filtro_de_video_e_o_de_antes_exatamente():
    args = {"deslocamento_pct": 4.0, "legenda": "legendas/s.ass"}
    assert filtro_de_video(info(), FPS, **args) == FILTRO_DE_ANTES
    assert filtro_de_video(info(), FPS, **args, trechos=None) == FILTRO_DE_ANTES
    assert filtro_de_audio(1, 40.0, None, -14.0) == AUDIO_DE_ANTES


TRECHOS = [Trecho(0.0, 5.2), Trecho(7.8, 9.2), Trecho(9.8, 19.0)]


def test_com_trechos_o_video_e_montado_em_quadros_exatos_antes_do_recorte():
    grafo = filtro_de_video(info(), FPS, deslocamento_pct=4.0, legenda="l.ass", trechos=TRECHOS)
    linhas = grafo.split(";\n")
    assert linhas[0] == "[0:v]fps=30000/1001:start_time=0,split=3[s0][s1][s2]"
    assert all(re.fullmatch(r"\[s\d\]trim=start=-?[\d.]+:end=[\d.]+,setpts=PTS-STARTPTS\[p\d\]", x)
               for x in linhas[1:4])  # fmt: skip
    assert linhas[4] == "[p0][p1][p2]concat=n=3:v=1:a=0[bruto]"
    # depois a cadeia de sempre, com o mesmo recorte, SDR, ampliação e a legenda por último
    seguinte = linhas[5]
    assert seguinte.startswith("[bruto]crop=608:1080:732:0,zscale=") and seguinte.endswith("[v]")
    assert seguinte.index("scale=1080:1920") < seguinte.index("ass=l.ass") < seguinte.index("[v]")
    assert "fps=" not in seguinte  # o fps já foi no começo


def test_os_quadros_de_cada_trecho_nao_acumulam_erro():
    trechos = [Trecho(0.0, 2.04), Trecho(3.07, 5.51), Trecho(6.0, 8.33)]
    linhas = linhas_do_video_cortado(trechos, Fraction(10))
    quadros = []
    for linha in linhas[1:-1]:
        inicio, fim = (float(x) for x in re.findall(r"=(-?[\d.]+)", linha)[:2])
        quadros.append(round((fim - inicio) * 10))
    esperado = sum(t.duracao for t in trechos)
    assert sum(quadros) == round(esperado * 10) == 68  # o total é o do tempo total
    assert quadros == [20, 25, 23]  # arredondar cada trecho sozinho daria 20 + 24 + 23 = 67


def test_trecho_unico_nao_usa_split():
    linhas = linhas_do_video_cortado([Trecho(1.0, 3.0)], Fraction(10))
    assert len(linhas) == 1 and "split" not in linhas[0] and "concat" not in linhas[0]


def test_o_som_e_cortado_com_atrim_exato_e_micro_fade_so_nas_emendas():
    linhas = linhas_do_audio_cortado(TRECHOS, "CADEIA")
    assert linhas[0] == "[1:a]asplit=3[x0][x1][x2]"
    assert linhas[1] == (
        "[x0]atrim=start=0:end=5.2,asetpts=PTS-STARTPTS,afade=t=out:st=5.19:d=0.01[t0]"
    )
    assert linhas[2] == (
        "[x1]atrim=start=7.8:end=9.2,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.01,"
        "afade=t=out:st=1.39:d=0.01[t1]"
    )
    assert linhas[3] == (
        "[x2]atrim=start=9.8:end=19,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.01[t2]"
    )
    assert linhas[4] == "[t0][t1][t2]concat=n=3:v=0:a=1,CADEIA[a]"


def test_o_som_cortado_sem_cadeia_sai_direto_da_juncao():
    linhas = linhas_do_audio_cortado(TRECHOS, "", entrada="0:a")
    assert linhas[0].startswith("[0:a]asplit=3") and linhas[-1].endswith("concat=n=3:v=0:a=1[a]")


# --- assinaturas ------------------------------------------------------------------------------

#: calculadas com o código de antes da opção (commit d024db7)
ASSINATURA_DO_SHORT = "b707f9be75405d85"
ASSINATURA_DO_SHORT_SEM_LEGENDA = "88a682dddd7061ea"
ASSINATURA_VISUAL = "19bd6a5c2a"
ASSINATURA_DO_SHORT_ESTILIZADO = "5a78048182ba84da"
ASSINATURA_VISUAL_ESTILIZADA = "2fe4995e07"
ASSINATURA_DA_ETAPA = "d53201bb873d"
ASSINATURA_DA_ETAPA_ESTILIZADA = "945c1b7269dd"


def estilizadas() -> OpcoesProcessamento:
    return OpcoesProcessamento(
        zoom_pct=120, shorts_zoom_pct=130, tratamento_ativo=True, tratamento_brilho=10
    )


def test_sem_a_opcao_as_assinaturas_continuam_as_de_antes():
    c = corte(10.0, 50.0)
    o = OpcoesProcessamento()
    assert assinatura_do_short("hash", o, c, "arib-std-b67", True, None) == ASSINATURA_DO_SHORT
    assert assinatura_do_short("hash", o, c, "bt709", False, None) == (
        ASSINATURA_DO_SHORT_SEM_LEGENDA
    )
    assert assinatura_visual(o) == ASSINATURA_VISUAL
    e = estilizadas()
    assert assinatura_do_short("hash", e, c, "arib-std-b67", True, None) == (
        ASSINATURA_DO_SHORT_ESTILIZADO
    )
    assert assinatura_visual(e) == ASSINATURA_VISUAL_ESTILIZADA
    campos = RenderizarShorts.opcoes_relevantes
    assert o.assinatura(campos) == ASSINATURA_DA_ETAPA
    assert e.assinatura(campos) == ASSINATURA_DA_ETAPA_ESTILIZADA


def test_pausa_zero_e_sem_saltos_nao_muda_nenhuma_assinatura():
    c = corte(10.0, 50.0)
    base = OpcoesProcessamento()
    iguais = corte(10.0, 50.0, pausa_s=0.0, saltos=[])
    assert assinatura_do_short("h", base, iguais, "bt709", True, None) == (
        assinatura_do_short("h", base, c, "bt709", True, None)
    )
    campos = RenderizarShorts.opcoes_relevantes
    assert not {"shorts_cortar_pausas", "shorts_pausa_s"} & set(campos)  # já não são opções
    assert not {"shorts_cortar_pausas", "shorts_pausa_s"} & set(OpcoesProcessamento.model_fields)


def test_a_pausa_e_os_saltos_do_corte_mudam_a_assinatura_do_short():
    o = OpcoesProcessamento()
    sem = corte(10.0, 50.0)
    com_pausa = corte(10.0, 50.0, pausa_s=1.0)
    outra_pausa = corte(10.0, 50.0, pausa_s=2.0)
    com_salto = corte(10.0, 50.0, saltos=[[20.0, 22.0]])
    outro_salto = corte(10.0, 50.0, saltos=[[20.0, 23.0]])
    todas = [sem, com_pausa, outra_pausa, com_salto, outro_salto]
    assert len({assinatura_do_short("h", o, c, "bt709", True, None) for c in todas}) == 5


def test_o_registro_lembra_os_saltos_e_a_pausa_e_acusa_a_mudanca():
    from spurgeon.render.shorts import (
        SITUACAO_ALTERADO,
        SITUACAO_PRONTO,
        detalhes_do_registro,
        situacao_do_short,
    )

    c = corte(10.0, 50.0)
    entrada = {"arquivo": "a.mp4", "assinatura": "x", **detalhes_do_registro(c)}
    assert "saltos" not in entrada and "pausa_s" not in entrada  # registro antigo: igual
    assert situacao_do_short(c, entrada, True, com_legenda=True) == SITUACAO_PRONTO
    com_pausa = corte(10.0, 50.0, pausa_s=1.0)
    assert situacao_do_short(com_pausa, entrada, True, com_legenda=True) == SITUACAO_ALTERADO
    com_salto = corte(10.0, 50.0, saltos=[[20.0, 22.0]])
    assert situacao_do_short(com_salto, entrada, True, com_legenda=True) == SITUACAO_ALTERADO
    feito = {"arquivo": "a.mp4", "assinatura": "x", **detalhes_do_registro(com_salto)}
    assert situacao_do_short(com_salto, feito, True, com_legenda=True) == SITUACAO_PRONTO
    assert situacao_do_short(c, feito, True, com_legenda=True) == SITUACAO_ALTERADO


def test_a_pausa_do_corte_vai_de_0_1_a_10_s():
    assert limiar_valido(0.05) == 0.1 and limiar_valido(99) == 10.0
    assert limiar_valido(1.234) == 1.23 and limiar_valido(0.1) == 0.1


def test_o_limiar_minimo_de_0_1_s_corta_pausas_curtas():
    from spurgeon.shorts.pausas import residual_da_pausa as residual

    assert residual(0.1) == pytest.approx(0.05)
    trechos = trechos_mantidos(PALAVRAS, CORTE, 0.1)  # até a de 0,2 s (a→b) entra
    assert len(trechos) == 5
    por_corte = corte(10.0, 29.0, pausa_s=0.1)
    assert trechos_mantidos(PALAVRAS, por_corte) == trechos  # sem passar o limiar: o do corte


# --- a prévia ---------------------------------------------------------------------------------


def previa_com_ffmpeg_falso(monkeypatch, tmp_path, *, cortar_pausas_s=None, com_legenda=False):
    """Roda `previa_do_corte` com um FFmpeg de mentira; devolve (resultado, args, roteiro)."""
    chamadas: list[list] = []
    roteiros: list[str] = []

    def falso(args, **kwargs):
        chamadas.append(list(args))
        roteiros.extend(
            Path(tmp_path / "p" / str(a)).read_text(encoding="utf-8")
            for a in args
            if str(a).endswith(".filter")
        )
        for arquivo in (a for a in args if str(a).endswith(".parcial")):
            Path(arquivo).write_bytes(b"x")

    monkeypatch.setattr(previa, "executar_ffmpeg", falso)
    cortado = replace(CORTE, pausa_s=cortar_pausas_s or 0.0)
    resultado = previa.previa_do_corte(
        Path("v.mov"), info(), cortado, PALAVRAS, tmp_path / "p" / "prev.mp4",
        deslocamento_pct=4.0, posicao_legenda=0.62, com_legenda=com_legenda,
    )  # fmt: skip
    return resultado, chamadas[0], roteiros


def test_previa_sem_a_opcao_usa_o_comando_de_antes(monkeypatch, tmp_path):
    for limiar in (None, 10.0):  # 10 s: não há pausa assim, nada a cortar
        resultado, args, roteiros = previa_com_ffmpeg_falso(
            monkeypatch, tmp_path, cortar_pausas_s=limiar
        )
        assert "-/filter_complex" not in args and not roteiros
        assert args[args.index("-map") + 1] == "0:v:0" and "-vf" in args
        assert "0:a:0" in args
        assert resultado.duracao_s == pytest.approx(CORTE.duracao)


def test_previa_com_a_opcao_corta_imagem_e_som_do_mesmo_jeito(monkeypatch, tmp_path):
    resultado, args, roteiros = previa_com_ffmpeg_falso(monkeypatch, tmp_path, cortar_pausas_s=1.0)
    assert resultado.duracao_s == pytest.approx(
        duracao_do_short(PALAVRAS, replace(CORTE, pausa_s=1.0))
    )
    assert "-vf" not in args and "0:a:0" not in args
    assert args[args.index("-t") + 1] == "19"  # lê o corte original inteiro
    assert args[args.index("-/filter_complex") + 1].endswith(".filter")
    assert [args[i + 1] for i, a in enumerate(args) if a == "-map"] == ["[v]", "[a]"]
    (roteiro,) = roteiros
    assert roteiro.startswith("[0:v]fps=12/1:start_time=0,split=3")
    assert "[bruto]crop=608:1080:732:0,scale=270:480:flags=fast_bilinear" in roteiro
    assert "[0:a]asplit=3" in roteiro and roteiro.endswith("concat=n=3:v=0:a=1[a]")
    # o roteiro e o .ass não ficam na pasta
    assert not list((tmp_path / "p").glob("*.filter")) and not list((tmp_path / "p").glob("*.ass"))


def test_previa_com_legenda_leva_a_legenda_remapeada(monkeypatch, tmp_path):
    textos: list[str] = []
    original = previa.montar_ass

    def espiar(palavras, estilo):
        textos.append(" ".join(f"{p.texto}@{p.inicio:.2f}" for p in palavras))
        return original(palavras, estilo)

    monkeypatch.setattr(previa, "montar_ass", espiar)
    previa_com_ffmpeg_falso(monkeypatch, tmp_path, cortar_pausas_s=1.0, com_legenda=True)
    # "d" abria em 8,0 s do corte e, sem os 2,6 s cortados antes dele, abre em 5,4 s
    assert "d@5.40" in textos[0] and "e@6.80" in textos[0]


# --- render com o FFmpeg de verdade ---------------------------------------------------------


def _ffmpeg(*args) -> list[str]:
    return [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", *args]


def _decodificar(argumentos: list[str]) -> bytes:
    return subprocess.run(_ffmpeg(*argumentos), capture_output=True, check=True).stdout


@pytest.fixture(scope="session")
def material_numerado(tmp_path_factory) -> tuple[Path, Path]:
    """Vídeo vertical de 30 s (90×160, 10 quadros/s) e um WAV de ruído branco com o mesmo som.

    Cada quadro traz o próprio número em binário (nove blocos de 10 px) só na faixa de cima, para
    a legenda (a 62% da altura) não esconder os bits. Já é vertical: o recorte 9:16 não corta nada.
    O som também vai dentro do `.mp4` (AAC), de onde a prévia o lê.
    """
    pasta = tmp_path_factory.mktemp("numerado")
    audio, video = pasta / "ruido.wav", pasta / "numerado.mp4"
    subprocess.run(
        _ffmpeg("-y", "-f", "lavfi", "-i",
                "anoisesrc=color=white:amplitude=0.3:sample_rate=48000:d=30",
                "-ac", "1", "-c:a", "pcm_s16le", str(audio)),
        check=True,
    )  # fmt: skip
    subprocess.run(
        _ffmpeg("-y", "-f", "lavfi", "-i", "color=c=black:s=90x160:r=10:d=30", "-i", str(audio),
                "-vf", "geq=lum='if(lt(Y,32),40+160*mod(floor(floor(T*10+0.5)/pow(2,floor(X/10))),"
                       "2),16)':cb=128:cr=128",
                "-c:v", "libx264", "-qp", "8", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "256k", "-shortest", str(video)),
        check=True,
    )  # fmt: skip
    return video, audio


def _numeros_dos_quadros(arquivo: Path, largura: int, altura: int):
    """O número de cada quadro, lido dos nove blocos da faixa de cima (um quarto da altura... )."""
    np = pytest.importorskip("numpy")
    bruto = _decodificar(
        ["-i", str(arquivo), "-vf", f"scale={largura}:{altura}:flags=area,format=gray",
         "-f", "rawvideo", "-"]
    )  # fmt: skip
    quadros = np.frombuffer(bruto, dtype=np.uint8).reshape(-1, altura, largura)
    topo = quadros[:, : altura // 5 - 4 or 1, :]  # a faixa dos bits (um quinto de cima)
    blocos = topo.reshape(len(quadros), topo.shape[1], 9, largura // 9).mean(axis=(1, 3))
    return ((blocos > 120) * (2 ** np.arange(9))).sum(axis=1)


# Falas do sermão de mentira (segundos do original). O corte vai de 0,73 s a 22,5 s.
FALAS = fala(
    ("um", 1.2, 1.6), ("dois", 2.0, 2.5), ("tres", 5.3, 5.9), ("quatro", 6.2, 7.0),
    ("cinco", 12.37, 13.0), ("seis", 13.4, 14.0), ("sete", 20.05, 21.0),
)  # fmt: skip
CORTE_DO_RENDER = corte(0.73, 22.5, pausa_s=1.0)


@pytest.fixture
def ctx_pausas(contexto, material_numerado):
    video, audio = material_numerado
    o = contexto.opcoes
    o.gerar_shorts = True
    o.qualidade = "rapido"
    o.legendas_dinamicas = True
    o.normalizar_volume = False
    o.recorte_deslocamento_x_pct = 0.0
    contexto.estado.entrada = EntradaVideo.de_arquivo(video)
    contexto.config.pastas.recursos = raiz_repositorio() / "recursos"
    contexto.pastas.work.mkdir(parents=True, exist_ok=True)
    contexto.pastas.audio_original.write_bytes(audio.read_bytes())
    Transcricao("t", 30.0, blocos=[Bloco(0, 30, [Segmento(FALAS)], True)]).salvar(
        contexto.pastas.transcricao
    )
    return contexto


def _correlacao_em_ms(gerado, esperado) -> float:
    np = pytest.importorskip("numpy")
    n = len(gerado)
    cruzada = np.fft.irfft(np.fft.rfft(gerado, 2 * n) * np.conj(np.fft.rfft(esperado, 2 * n)))
    deslocamento = int(np.argmax(cruzada))
    deslocamento = deslocamento if deslocamento < n else deslocamento - 2 * n
    return deslocamento / 16  # 16 kHz: amostras → ms


def _pcm(argumentos: list[str]):
    np = pytest.importorskip("numpy")
    bruto = _decodificar([*argumentos, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"])
    return np.frombuffer(bruto, dtype=np.int16).astype(np.float32)


@pytest.mark.ffmpeg
def test_short_sem_as_pausas_longas_fica_mais_curto_e_com_imagem_som_e_legenda_alinhados(
    ctx_pausas, material_numerado
):
    np = pytest.importorskip("numpy")
    _, audio = material_numerado
    gravar_cortes(ctx_pausas.pastas.cortes_shorts, [CORTE_DO_RENDER])
    ctx_pausas.etapa_id = "renderizar_shorts"
    RenderizarShorts().executar(ctx_pausas)

    saida = ctx_pausas.pastas.shorts / "short_01_corte_1.mp4"
    mapa = mapa_do_short(FALAS, CORTE_DO_RENDER, 1.0)
    esperado_s = mapa.duracao_editada
    assert len(mapa.trechos) == 4 and esperado_s == pytest.approx(21.77 - 2.4 - 4.97 - 5.65)
    final = sondar(saida)
    assert (final.largura, final.altura) == (1080, 1920)
    assert final.duracao_s == pytest.approx(esperado_s, abs=0.15)

    # imagem: o número de cada quadro diz de que quadro do original ele veio
    numeros = _numeros_dos_quadros(saida, 90, 160)
    assert len(numeros) == pytest.approx(esperado_s * 10, abs=1)
    emendas = list(accumulate(x.duracao for x in mapa.trechos))[:-1]  # onde cada corte caiu
    conferidos = 0
    for j, lido in enumerate(numeros):
        if any(abs(j / 10 - emenda) <= 0.2 for emenda in emendas):
            continue  # num quadro de emenda a conta de meio quadro pode cair de qualquer lado
        original = CORTE_DO_RENDER.inicio + mapa.editado_para_original(j / 10)
        assert abs(int(lido) - round(original * 10)) <= 1, f"quadro {j}: {int(lido)}"
        conferidos += 1
    assert conferidos > 60

    # som: ruído branco tem um só pico de correlação; o som do Short tem de bater com o original
    # cortado nos mesmos trechos, antes e depois de cada emenda
    gerado = _pcm(["-i", str(saida)])
    original = _pcm(["-i", str(audio)])
    esperado = np.concatenate(
        [
            original[int((CORTE_DO_RENDER.inicio + t.inicio) * 16000)
                     : int((CORTE_DO_RENDER.inicio + t.fim) * 16000)]
            for t in mapa.trechos
        ]
    )  # fmt: skip
    assert abs(len(gerado) - len(esperado)) <= 16000 * 0.05
    n = min(len(gerado), len(esperado))
    for emenda in emendas:
        i = max(0, int((emenda - 1.5) * 16000))
        j = min(n, int((emenda + 1.5) * 16000))
        deslocamento = _correlacao_em_ms(gerado[i:j], esperado[i:j])
        assert abs(deslocamento) <= 3, f"na emenda de {emenda:.2f} s: {deslocamento:+.1f} ms"

    # legenda: cada palavra abre no tempo do Short já cortado (e a última termina dentro dele)
    ass = (ctx_pausas.pastas.work / "legendas" / "short_01_corte_1.ass").read_text(encoding="utf-8")
    inicios = {m.group(1) for m in re.finditer(r"Dialogue: 0,(\d:\d\d:\d\d\.\d\d),", ass)}
    for palavra in FALAS:
        segundos = mapa.original_para_editado(palavra.inicio - CORTE_DO_RENDER.inicio)
        centesimos = round(segundos * 100)
        rotulo = f"0:00:{centesimos // 100:02d}.{centesimos % 100:02d}"
        assert rotulo in inicios, f"{palavra.texto}: esperava abrir em {rotulo}"
    fins = re.findall(r"Dialogue: 0,[\d:.]+,0:00:(\d\d\.\d\d),", ass)
    assert max(float(x) for x in fins) <= esperado_s + 0.01  # sem o corte passaria de 20 s

    # o roteiro: o fade de saída do som cai no fim do Short JÁ cortado
    roteiro = (ctx_pausas.pastas.work / "legendas" / "short_01_corte_1.filter").read_text(
        encoding="utf-8"
    )
    assert "split=4" in roteiro and "asplit=4" in roteiro
    assert f"afade=t=out:st={f.numero_ffmpeg(esperado_s - 0.1)}:d=0.100" in roteiro


@pytest.mark.ffmpeg
def test_sem_pausa_no_corte_o_short_continua_com_a_duracao_do_corte(ctx_pausas):
    sem = replace(CORTE_DO_RENDER, pausa_s=0.0)
    gravar_cortes(ctx_pausas.pastas.cortes_shorts, [sem])
    ctx_pausas.etapa_id = "renderizar_shorts"
    RenderizarShorts().executar(ctx_pausas)
    saida = ctx_pausas.pastas.shorts / "short_01_corte_1.mp4"
    assert sondar(saida).duracao_s == pytest.approx(sem.duracao, abs=0.15)
    roteiro = (ctx_pausas.pastas.work / "legendas" / "short_01_corte_1.filter").read_text(
        encoding="utf-8"
    )
    assert "split" not in roteiro and "asplit" not in roteiro and "trim" not in roteiro


@pytest.mark.ffmpeg
def test_previa_cortada_dura_o_que_o_short_vai_durar_e_continua_alinhada(
    tmp_path, material_numerado
):
    np = pytest.importorskip("numpy")
    video, audio = material_numerado
    progresso: list[float] = []
    pronta = previa.previa_do_corte(
        video, sondar(video), CORTE_DO_RENDER, FALAS, tmp_path / "p" / "previa_1.mp4",
        deslocamento_pct=0.0, posicao_legenda=0.62, com_legenda=True,
        pasta_fontes=raiz_repositorio() / "recursos" / "fontes", ao_progresso=progresso.append,
    )  # fmt: skip
    mapa = mapa_do_short(FALAS, CORTE_DO_RENDER, 1.0)
    esperado_s = mapa.duracao_editada
    assert pronta.duracao_s == pytest.approx(esperado_s)
    saida = sondar(pronta.video)
    assert (saida.largura, saida.altura) == (270, 480)
    assert saida.duracao_s == pytest.approx(esperado_s, abs=0.2)
    assert pronta.audio is not None
    assert sondar(pronta.audio).duracao_s == pytest.approx(esperado_s, abs=0.1)
    assert progresso and progresso[-1] == pytest.approx(1.0, abs=0.05)
    restos = {p.name for p in (tmp_path / "p").iterdir()} - {"fontes"}
    assert restos == {"previa_1.mp4", "previa_1.wav"}  # sem .parcial, .ass nem roteiro

    # imagem: 12 quadros por segundo; cada um vem do instante certo do original (±2 quadros de
    # 10 fps; um erro de emenda seriam dezenas)
    numeros = _numeros_dos_quadros(pronta.video, 270, 480)
    assert len(numeros) == pytest.approx(esperado_s * 12, abs=2)
    emendas = [t.fim for t in mapa.trechos[:-1]]
    editadas = [mapa.original_para_editado(e) for e in emendas]
    for j, lido in enumerate(numeros):
        if any(abs(j / 12 - e) <= 0.2 for e in editadas):
            continue
        original = CORTE_DO_RENDER.inicio + mapa.editado_para_original(j / 12)
        assert abs(int(lido) - round(original * 10)) <= 2, f"quadro {j}: {int(lido)}"

    # som: bate com o original cortado nos mesmos trechos (a prévia vem do AAC do .mp4)
    gerado = _pcm(["-i", str(pronta.audio)])
    original_pcm = _pcm(["-i", str(audio)])
    esperado = np.concatenate(
        [
            original_pcm[int((CORTE_DO_RENDER.inicio + t.inicio) * 16000)
                         : int((CORTE_DO_RENDER.inicio + t.fim) * 16000)]
            for t in mapa.trechos
        ]
    )  # fmt: skip
    n = min(len(gerado), len(esperado))
    for e in editadas:
        i, j = max(0, int((e - 1.5) * 16000)), min(n, int((e + 1.5) * 16000))
        deslocamento = _correlacao_em_ms(gerado[i:j], esperado[i:j])
        assert abs(deslocamento) <= 25, f"na emenda de {e:.2f} s: {deslocamento:+.1f} ms"
