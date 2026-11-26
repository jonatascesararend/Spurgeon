"""Começo e fim do vídeo, zoom, tratamento de imagem, fade e arquivos de introdução/vinheta."""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from spurgeon.audio.pausas import CalcularCortes
from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import ConfigApp, OpcoesProcessamento, carregar_config, salvar_config
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import localizar_binario, sondar
from spurgeon.core.tempo import Trecho, limitar_trechos
from spurgeon.render import video_longo as vl
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_PRONTO,
    RenderizarShorts,
    assinatura_do_short,
    assinatura_visual,
    detalhes_do_registro,
    filtro_de_video,
    ler_registro,
    situacao_do_short,
)
from spurgeon.render.vinhetas import AdicionarVinhetas, script_do_clipe_completo
from spurgeon.shorts.etapas import gravar_cortes
from tests.test_modos import clipes
from tests.test_render import corte, info, preparar_longo, rodar

FPS = Fraction(25)


# --- começo e fim do vídeo ------------------------------------------------------------------


def test_limitar_trechos_corta_as_pontas_e_descarta_o_que_ficou_de_fora():
    trechos = [Trecho(0, 5.5), Trecho(16.5, 38.5), Trecho(52.5, 60)]
    assert limitar_trechos(trechos, 7, 55) == [Trecho(16.5, 38.5), Trecho(52.5, 55)]
    assert limitar_trechos(trechos, 0, 60) == trechos
    assert limitar_trechos(trechos, 6, 15) == []
    assert limitar_trechos(trechos, 20, 30) == [Trecho(20, 30)]


def test_janela_do_video_nas_opcoes():
    o = OpcoesProcessamento()
    assert not o.tem_janela and o.janela_do_video(100.0) == (0.0, 100.0)
    o = OpcoesProcessamento(video_inicio_s=7, video_fim_s=124)
    assert o.tem_janela and o.janela_do_video(200.0) == (7.0, 124.0)
    assert o.janela_do_video(100.0) == (7.0, 100.0)  # o fim nunca passa do vídeo
    assert OpcoesProcessamento(video_inicio_s=7).janela_do_video(50.0) == (7.0, 50.0)
    with pytest.raises(ValueError, match="terminar depois"):
        OpcoesProcessamento(video_inicio_s=30, video_fim_s=20)


def gravar_pausas(contexto) -> None:
    contexto.pastas.work.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(
        contexto.pastas.pausas,
        {"duracao_s": 60.0, "pausas": [[5.0, 17.0], [25.0, 28.0], [38.0, 53.0]]},
    )


def test_calcular_cortes_so_mantem_o_que_esta_entre_o_comeco_e_o_fim(contexto):
    gravar_pausas(contexto)
    contexto.opcoes.video_inicio_s, contexto.opcoes.video_fim_s = 7.0, 55.0
    rodar(CalcularCortes(), contexto)
    dados = ler_json(contexto.pastas.cortes)
    assert dados["trechos"] == [[16.5, 38.5], [52.5, 55.0]]
    assert dados["janela_s"] == [7.0, 55.0]
    assert dados["duracao_editada_s"] == pytest.approx(24.5)


def test_sem_janela_os_cortes_continuam_como_antes(contexto):
    gravar_pausas(contexto)
    rodar(CalcularCortes(), contexto)
    assert ler_json(contexto.pastas.cortes)["trechos"] == [[0, 5.5], [16.5, 38.5], [52.5, 60.0]]


def test_janela_so_sobre_pausa_cortada_nao_deixa_nada_para_renderizar(contexto):
    gravar_pausas(contexto)
    contexto.opcoes.video_inicio_s, contexto.opcoes.video_fim_s = 6.0, 15.0
    with pytest.raises(ErroFerramenta, match="Não sobrou nenhum trecho"):
        rodar(CalcularCortes(), contexto)


def test_mudar_o_comeco_ou_o_fim_refaz_os_cortes_mas_nao_a_transcricao():
    from spurgeon.transcricao.etapas import Transcrever

    assert {"video_inicio_s", "video_fim_s"} <= set(CalcularCortes.opcoes_relevantes)
    assert "video_inicio_s" not in Transcrever.opcoes_relevantes  # a transcrição vale sempre


# --- scripts do FFmpeg (sem rodar) ----------------------------------------------------------


def parte_unica(quadros=250):
    return vl.Parte(0, quadros, ((0.0, quadros / 25),))


def test_script_da_parte_aplica_zoom_sdr_ampliacao_e_cor_nessa_ordem():
    cadeia = vl.script_da_parte(
        parte_unica(), 0.0, info(), FPS,
        enquadramento=Enquadramento(zoom_pct=125), tratamento=TratamentoImagem(brilho=20),
    )  # fmt: skip
    ordem = ["crop=1536:864:192:108", "zscale", "scale=1920:1080", "eq=brightness", "tpad"]
    posicoes = [cadeia.index(trecho) for trecho in ordem]
    assert posicoes == sorted(posicoes)


def test_script_da_parte_sem_estilo_continua_igual_ao_de_antes():
    cadeia = vl.script_da_parte(parte_unica(), 0.0, info(), FPS)
    assert "crop=" not in cadeia and "eq=" not in cadeia and "fade" not in cadeia


def test_fade_de_entrada_e_de_saida_do_video_principal():
    cadeia = vl.script_da_parte(parte_unica(250), 0.0, info(), FPS, fade_entrada_s=0.5,
                                fade_saida_s=0.5)  # fmt: skip
    assert "fade=t=in:st=0:d=0.5" in cadeia and "fade=t=out:st=9.5:d=0.5" in cadeia
    assert cadeia.index("fade=t=out") < cadeia.index("tpad")  # o fade vem antes do tpad


def test_fade_nunca_passa_de_metade_do_trecho():
    assert vl.fade_efetivo(0.5, 0.4) == pytest.approx(0.2)
    assert vl.fade_efetivo(0.0, 10) == 0.0


def test_script_do_clipe_so_da_fade_do_lado_pedido():
    clipe = vl.Clipe(Path("x.mp4"), info(transferencia="bt709"), 75, "parte_intro.mp4")
    saida = vl.script_de_clipe(clipe.info, 160, 120, FPS, quadros=75, fade_saida_s=0.5)
    assert "fade=t=out:st=2.5:d=0.5" in saida and "fade=t=in" not in saida
    entrada = vl.script_de_clipe(clipe.info, 160, 120, FPS, quadros=75, fade_entrada_s=0.5)
    assert "fade=t=in:st=0:d=0.5" in entrada and "fade=t=out" not in entrada
    assert "fade" not in vl.script_de_clipe(clipe.info, 160, 120, FPS, quadros=75)


def test_script_do_audio_com_fade_entra_depois_da_introducao_e_sai_antes_da_vinheta():
    trechos = [Trecho(0, 10)]
    intro = vl.Clipe(Path("i.mp4"), info(), 75, "parte_intro.mp4")
    vinheta = vl.Clipe(Path("v.mp4"), info(), 50, "parte_vinheta.mp4")
    script = vl.script_do_audio(
        trechos, intro=intro, vinheta=vinheta, quadros_principais=250, fps=FPS, canais=1,
        normalizar=False, lufs=-16.0, fade_s=0.5,
    )  # fmt: skip
    assert "asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.5[am]" not in script  # o fade vem depois
    assert "afade=t=in:st=0:d=0.5,afade=t=out:st=9.5:d=0.5[am]" in script
    assert "afade=t=out:st=2.5:d=0.5[ai]" in script  # a introdução sai para o silêncio
    assert "afade=t=in:st=0:d=0.5[av]" in script  # e a vinheta entra dele
    sem = vl.script_do_audio(
        trechos, intro=intro, vinheta=vinheta, quadros_principais=250, fps=FPS, canais=1,
        normalizar=False, lufs=-16.0,
    )  # fmt: skip
    assert "afade" not in sem


def test_clipe_completo_do_modo_so_vinhetas_tem_fade_na_imagem_e_no_som():
    clipe = vl.Clipe(Path("v.mp4"), info(transferencia="bt709"), 50, "parte_vinheta.mp4")
    roteiro = script_do_clipe_completo(
        clipe, 160, 120, FPS, normalizar=False, lufs=-16.0, fade_entrada_s=0.5
    )
    assert "fade=t=in:st=0:d=0.5" in roteiro and "afade=t=in:st=0:d=0.5[aout]" in roteiro


def test_filtro_de_video_do_short_trata_a_cor_antes_de_ampliar_e_recorta_dentro_do_zoom():
    cadeia = filtro_de_video(
        info(), FPS, deslocamento_pct=4.0, legenda=None,
        enquadramento=Enquadramento(zoom_pct=125), tratamento=TratamentoImagem(contraste=20),
    )  # fmt: skip
    ordem = ["crop=", "zscale", "eq=contrast", "scale=1080:1920"]
    posicoes = [cadeia.index(trecho) for trecho in ordem]
    assert posicoes == sorted(posicoes)
    sem = filtro_de_video(info(), FPS, deslocamento_pct=4.0, legenda=None)
    assert "eq=" not in sem and sem.count("crop=") == 1


# --- render de verdade ----------------------------------------------------------------------


def luma_em(video: Path, instante: float, tmp: Path) -> float:
    png = tmp / f"quadro_{video.stem}_{instante:.2f}.png"
    subprocess.run(
        [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{instante}", "-i", str(video), "-frames:v", "1", str(png)],
        check=True,
    )  # fmt: skip
    with Image.open(png) as imagem:
        return ImageStat.Stat(imagem.convert("L")).mean[0]


def diferenca(a: Path, b: Path, instante: float, tmp: Path) -> float:
    luma_em(a, instante, tmp), luma_em(b, instante, tmp)
    with (
        Image.open(tmp / f"quadro_{a.stem}_{instante:.2f}.png") as x,
        Image.open(tmp / f"quadro_{b.stem}_{instante:.2f}.png") as y,
    ):
        return ImageStat.Stat(ImageChops.difference(x.convert("L"), y.convert("L"))).mean[0]


@pytest.mark.ffmpeg
def test_video_longo_com_zoom_e_tratamento_tem_o_mesmo_tamanho_e_outra_imagem(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    escrever_json_atomico(contexto.pastas.cortes, {"trechos": [[0.0, 4.0]]})
    rodar(vl.RenderizarVideoLongo(), contexto)
    sem = tmp_path / "sem.mp4"
    contexto.pastas.video_longo.replace(sem)

    contexto.opcoes.zoom_pct = 200.0
    contexto.opcoes.tratamento_ativo = True
    contexto.opcoes.tratamento_brilho = 40.0
    contexto.opcoes.tratamento_sombras = 30.0
    rodar(vl.RenderizarVideoLongo(), contexto)
    com = contexto.pastas.video_longo
    assert (sondar(com).largura, sondar(com).altura) == (160, 120)
    assert sondar(com).duracao_s == pytest.approx(4.0, abs=0.15)
    assert diferenca(sem, com, 2.0, tmp_path) > 8
    assert luma_em(com, 2.0, tmp_path) > luma_em(sem, 2.0, tmp_path) - 1  # clareou (ou igual)


@pytest.mark.ffmpeg
def test_tratamento_desligado_mantem_os_ajustes_sem_aplicar(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    escrever_json_atomico(contexto.pastas.cortes, {"trechos": [[0.0, 3.0]]})
    rodar(vl.RenderizarVideoLongo(), contexto)
    sem = tmp_path / "sem.mp4"
    contexto.pastas.video_longo.replace(sem)
    contexto.opcoes.tratamento_brilho = 80.0  # os valores ficam, mas o tratamento está desligado
    assert contexto.opcoes.tratamento.neutro
    rodar(vl.RenderizarVideoLongo(), contexto)
    assert diferenca(sem, contexto.pastas.video_longo, 1.0, tmp_path) < 1.0


@pytest.mark.ffmpeg
def test_fade_escurece_a_emenda_com_a_introducao_e_a_vinheta(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    preparar_longo(
        contexto, video_com_pausas, audio_com_pausas, introducao=True, vinheta_final=True,
        fade_vinhetas=True, fade_vinhetas_s=0.6, fade_estilo="preto",
    )  # fmt: skip
    escrever_json_atomico(contexto.pastas.cortes, {"trechos": [[10.0, 16.0]]})
    contexto.config.pastas.recursos.mkdir(parents=True, exist_ok=True)
    clipes(contexto.config.pastas.recursos)  # introdução de 3 s e vinheta de 2 s
    rodar(vl.RenderizarVideoLongo(), contexto)
    final = contexto.pastas.video_longo
    assert sondar(final).duracao_s == pytest.approx(3 + 6 + 2, abs=0.3)  # o fade não muda a duração
    meio_da_intro = luma_em(final, 1.0, tmp_path)
    fim_da_intro = luma_em(final, 2.85, tmp_path)
    comeco_do_video = luma_em(final, 3.0, tmp_path)
    depois = luma_em(final, 3.9, tmp_path)
    assert fim_da_intro < meio_da_intro * 0.5  # a introdução escureceu até o preto
    assert comeco_do_video < depois * 0.5  # e o vídeo surgiu dele
    fim_do_video = luma_em(final, 8.95, tmp_path)
    assert fim_do_video < luma_em(final, 7.5, tmp_path) * 0.5  # sai para o preto na vinheta


@pytest.mark.ffmpeg
def test_sem_fade_a_emenda_continua_seca(contexto, video_com_pausas, audio_com_pausas, tmp_path):
    preparar_longo(contexto, video_com_pausas, audio_com_pausas, introducao=True)
    escrever_json_atomico(contexto.pastas.cortes, {"trechos": [[10.0, 16.0]]})
    contexto.config.pastas.recursos.mkdir(parents=True, exist_ok=True)
    clipes(contexto.config.pastas.recursos)
    rodar(vl.RenderizarVideoLongo(), contexto)
    final = contexto.pastas.video_longo
    assert luma_em(final, 3.0, tmp_path) > 20  # o vídeo já começa claro


@pytest.mark.ffmpeg
def test_introducao_e_vinheta_escolhidas_fora_de_recursos(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    pasta = tmp_path / "meus_clipes"
    pasta.mkdir()
    clipes(pasta)
    (pasta / "introducao.mp4").rename(pasta / "abertura.mp4")
    preparar_longo(
        contexto, video_com_pausas, audio_com_pausas, introducao=True,
        introducao_arquivo=str(pasta / "abertura.mp4"),
    )  # fmt: skip
    escrever_json_atomico(contexto.pastas.cortes, {"trechos": [[10.0, 14.0]]})
    # recursos/ nem existe: só vale o arquivo escolhido
    rodar(vl.RenderizarVideoLongo(), contexto)
    assert sondar(contexto.pastas.video_longo).duracao_s == pytest.approx(3 + 4, abs=0.3)


@pytest.mark.ffmpeg
def test_arquivo_escolhido_que_sumiu_da_erro_com_o_nome(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    preparar_longo(
        contexto, video_com_pausas, audio_com_pausas, vinheta_final=True,
        vinheta_arquivo=str(tmp_path / "sumiu.mp4"),
    )  # fmt: skip
    with pytest.raises(ErroFerramenta, match=r"sumiu.mp4"):
        rodar(vl.RenderizarVideoLongo(), contexto)


@pytest.mark.ffmpeg
def test_so_vinhetas_com_fade_e_arquivo_escolhido(
    contexto, video_com_pausas, audio_com_pausas, tmp_path
):
    esperado = preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    rodar(vl.RenderizarVideoLongo(), contexto)
    pasta = tmp_path / "clipes"
    pasta.mkdir()
    clipes(pasta)
    contexto.opcoes.modo = "so_vinhetas"
    contexto.opcoes.introducao = contexto.opcoes.vinheta_final = True
    contexto.opcoes.introducao_arquivo = str(pasta / "introducao.mp4")
    contexto.opcoes.vinheta_arquivo = str(pasta / "vinheta_final.mp4")
    contexto.opcoes.fade_vinhetas = True
    rodar(AdicionarVinhetas(), contexto)
    novo = contexto.pastas.video_com_vinhetas
    assert sondar(novo).duracao_s == pytest.approx(esperado + 3 + 2, abs=0.4)
    assert (
        luma_em(novo, 2.85, tmp_path) < luma_em(novo, 1.0, tmp_path) * 0.5
    )  # a intro sai p/ preto
    assert luma_em(novo, 3.0, tmp_path) > 15  # o vídeo pronto não ganha fade: foi só emendado


# --- Shorts com zoom e tratamento -----------------------------------------------------------


@pytest.mark.ffmpeg
def test_mudar_zoom_ou_tratamento_renderiza_o_short_de_novo(ctx_shorts):
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    rodar(RenderizarShorts(), ctx_shorts)
    video = ctx_shorts.pastas.shorts / "short_01_corte_1.mp4"
    antes = video.stat().st_mtime_ns

    rodar(RenderizarShorts(), ctx_shorts)  # nada mudou: reaproveita
    assert video.stat().st_mtime_ns == antes

    ctx_shorts.opcoes.zoom_pct = 150.0
    rodar(RenderizarShorts(), ctx_shorts)
    assert video.stat().st_mtime_ns != antes
    depois_do_zoom = video.stat().st_mtime_ns

    ctx_shorts.opcoes.tratamento_ativo = True
    ctx_shorts.opcoes.tratamento_contraste = 30.0
    rodar(RenderizarShorts(), ctx_shorts)
    assert video.stat().st_mtime_ns != depois_do_zoom
    assert (sondar(video).largura, sondar(video).altura) == (1080, 1920)


@pytest.mark.ffmpeg
def test_zoom_e_tratamento_so_nos_shorts_quando_pedido(ctx_shorts):
    ctx_shorts.opcoes.zoom_pct = 150.0
    ctx_shorts.opcoes.tratamento_ativo = True
    ctx_shorts.opcoes.tratamento_brilho = 30.0
    c = corte(1, 0.0, 2.5)
    com = assinatura_do_short("h", ctx_shorts.opcoes, c, "bt709", False, None)
    ctx_shorts.opcoes.zoom_nos_shorts = False
    ctx_shorts.opcoes.tratamento_nos_shorts = False
    sem = assinatura_do_short("h", ctx_shorts.opcoes, c, "bt709", False, None)
    assert com != sem
    puro = ctx_shorts.opcoes.model_copy(update={"zoom_pct": 100.0, "tratamento_ativo": False})
    assert sem == assinatura_do_short("h", puro, c, "bt709", False, None)


def test_assinatura_visual_muda_com_zoom_cor_recorte_e_marca(tmp_path):
    base = assinatura_visual(OpcoesProcessamento())
    assert assinatura_visual(OpcoesProcessamento(zoom_pct=120)) != base
    assert (
        assinatura_visual(OpcoesProcessamento(tratamento_ativo=True, tratamento_brilho=5)) != base
    )
    assert assinatura_visual(OpcoesProcessamento(recorte_deslocamento_x_pct=0)) != base
    assert assinatura_visual(OpcoesProcessamento(zoom_pct=120, zoom_nos_shorts=False)) == base
    marca = tmp_path / "m.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(marca)
    com_marca = OpcoesProcessamento(marca_dagua=True, marca_imagem=str(marca))
    assert assinatura_visual(com_marca) != base
    assert assinatura_visual(OpcoesProcessamento(marca_dagua=True, marca_imagem=str(marca))) == (
        assinatura_visual(com_marca)
    )


def test_situacao_do_short_nota_que_a_aparencia_mudou():
    c = corte(1, 0.0, 30.0)
    visual = assinatura_visual(OpcoesProcessamento())
    entrada = {"arquivo": "a.mp4", "assinatura": "x", **detalhes_do_registro(c, visual)}
    assert situacao_do_short(c, entrada, True, com_legenda=False, visual=visual) == SITUACAO_PRONTO
    outro = assinatura_visual(OpcoesProcessamento(zoom_pct=130))
    assert situacao_do_short(c, entrada, True, com_legenda=False, visual=outro) == SITUACAO_ALTERADO
    antigo = {"arquivo": "a.mp4", "assinatura": "x", **detalhes_do_registro(c)}  # sem o "visual"
    assert situacao_do_short(c, antigo, True, com_legenda=False, visual=outro) == SITUACAO_PRONTO


@pytest.mark.ffmpeg
def test_registro_guarda_a_aparencia_do_short_renderizado(ctx_shorts):
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5)])
    rodar(RenderizarShorts(), ctx_shorts)
    registro = ler_registro(ctx_shorts.pastas.shorts_renderizados)["1"]
    assert registro["visual"] == assinatura_visual(ctx_shorts.opcoes)


# --- configuração e presets -----------------------------------------------------------------


def test_config_antiga_com_short_de_60s_passa_para_180s(tmp_path):
    caminho = tmp_path / "config.toml"
    caminho.write_text("[padroes]\nshorts_duracao_max_s = 60.0\nlimpar_audio = true\n")
    config = carregar_config(caminho)
    assert config.padroes.shorts_duracao_max_s == 180.0 and config.padroes.limpar_audio
    assert config.versao == 2
    caminho.write_text("[padroes]\nshorts_duracao_max_s = 45.0\n")
    assert carregar_config(caminho).padroes.shorts_duracao_max_s == 45.0  # escolha da pessoa
    caminho.write_text("versao = 2\n[padroes]\nshorts_duracao_max_s = 60.0\n")
    assert carregar_config(caminho).padroes.shorts_duracao_max_s == 60.0  # já migrada: respeita


def test_presets_ida_e_volta_pela_configuracao(tmp_path):
    caminho = tmp_path / "config.toml"
    config = ConfigApp()
    config.presets_enquadramento["Igreja A"] = Enquadramento(zoom_pct=130, centro_x_pct=54)
    config.presets_tratamento["Quente"] = TratamentoImagem(temperatura=20, sombras=15)
    salvar_config(config, caminho)
    lido = carregar_config(caminho)
    assert lido.presets_enquadramento["Igreja A"].zoom_pct == 130
    assert lido.presets_enquadramento["Igreja A"].centro_x_pct == 54
    assert lido.presets_tratamento["Quente"].sombras == 15


def test_estilo_nas_opcoes_vira_enquadramento_e_tratamento():
    o = OpcoesProcessamento(
        zoom_pct=140, zoom_centro_x_pct=54, tratamento_ativo=True, tratamento_saturacao=12,
        zoom_nos_shorts=False, tratamento_nos_shorts=False,
    )  # fmt: skip
    assert o.enquadramento.zoom_pct == 140 and o.enquadramento.centro_x_pct == 54
    assert o.tratamento.saturacao == 12
    assert not o.enquadramento_nos_curtos.ativo and o.tratamento_nos_curtos.neutro
    assert OpcoesProcessamento(tratamento_brilho=9).tratamento.neutro  # desligado


@pytest.mark.ffmpeg
def test_pedido_do_celular_renderiza_ou_refaz_so_um_short(ctx_shorts):
    from spurgeon.render.shorts import pedir_render_de

    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 1.2), corte(2, 1.3, 2.5)])
    pedir_render_de(ctx_shorts.pastas, [2])
    rodar(RenderizarShorts(), ctx_shorts)
    primeiro = ctx_shorts.pastas.shorts / "short_01_corte_1.mp4"
    segundo = ctx_shorts.pastas.shorts / "short_02_corte_2.mp4"
    assert segundo.exists() and not primeiro.exists()  # só o pedido

    rodar(RenderizarShorts(), ctx_shorts)  # sem pedido: o que falta, e o pronto fica
    antes = {v: v.stat().st_mtime_ns for v in (primeiro, segundo)}
    pedir_render_de(ctx_shorts.pastas, [1], refazer=True)
    rodar(RenderizarShorts(), ctx_shorts)
    assert primeiro.stat().st_mtime_ns != antes[primeiro]  # refeito, mesmo valendo
    assert segundo.stat().st_mtime_ns == antes[segundo]  # o outro não foi tocado
