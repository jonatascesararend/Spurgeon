"""Os modos (tudo, só Shorts, só vinhetas), a limpeza que guarda os .json e a regra de refazer."""

import shutil
import subprocess
from typing import ClassVar

import pytest

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.estado import StatusEtapa
from spurgeon.core.etapas import etapas_registradas
from spurgeon.core.ffmpeg import localizar_binario, sondar
from spurgeon.core.pipeline import Etapa, Pipeline, ResultadoPipeline
from spurgeon.core.projeto import PastasProjeto, limpar_intermediarios
from spurgeon.render import video_longo as vl
from spurgeon.render.vinhetas import AdicionarVinhetas, lista_de_emenda
from spurgeon.transcricao.srt import deslocar_srt
from tests.test_render import preparar_longo, rodar


def ids(opcoes) -> list[str]:
    return [e.id for e in Pipeline(etapas_registradas()).ativas(opcoes)]


# --- validação e quais etapas rodam em cada modo ----------------------------------------------


def test_modo_completo_e_o_padrao():
    o = OpcoesProcessamento()
    assert o.modo == "completo" and o.processa_video_longo and not o.processa_shorts
    assert "renderizar_video_longo" in ids(o) and "adicionar_vinhetas" not in ids(o)


def test_so_shorts_pula_pausas_cortes_legendas_e_o_video_longo():
    o = OpcoesProcessamento(modo="so_shorts", gerar_shorts=True)
    esperadas = ids(o)
    for fora in ("detectar_pausas", "refinar_pausas", "calcular_cortes", "gerar_srt"):
        assert fora not in esperadas
    assert "renderizar_video_longo" not in esperadas and "adicionar_vinhetas" not in esperadas
    for dentro in ("extrair_audio", "transcrever", "analisar_shorts", "renderizar_shorts"):
        assert dentro in esperadas
    assert o.problemas() == []


def test_so_vinhetas_roda_so_a_juncao_e_a_conferencia():
    o = OpcoesProcessamento(modo="so_vinhetas", introducao=True, gerar_video_longo=False)
    assert ids(o) == ["adicionar_vinhetas", "finalizar"]
    assert o.problemas() == []


def test_so_vinhetas_exige_pelo_menos_uma_delas():
    o = OpcoesProcessamento(modo="so_vinhetas")
    assert any("introdução, a vinheta final" in p for p in o.problemas())


def test_so_shorts_exige_shorts_ligados():
    assert OpcoesProcessamento(modo="so_shorts", gerar_shorts=False).problemas()


def test_marca_dagua_pede_a_imagem_e_um_destino():
    assert any("imagem da marca" in p for p in OpcoesProcessamento(marca_dagua=True).problemas())
    sem_destino = OpcoesProcessamento(
        marca_dagua=True, marca_imagem="x.png", marca_no_video_longo=False, marca_nos_shorts=False
    )
    assert any("onde a marca" in p for p in sem_destino.problemas())
    assert OpcoesProcessamento(marca_dagua=True, marca_imagem="x.png").problemas() == []


# --- limpar _work/ guarda a análise ------------------------------------------------------------


def test_a_limpeza_guarda_os_json_e_apaga_o_resto(contexto):
    work = contexto.pastas.work
    (work / "audio_normalizado.wav").write_bytes(b"x" * 100)
    (work / "render_longo").mkdir()
    (work / "render_longo" / "parte_000.mp4").write_bytes(b"x")
    (work / "legendas").mkdir()
    (work / "legendas" / "a.ass").write_text("x")
    escrever_json_atomico(work / "cortes_shorts.json", [{"id": 1}])
    escrever_json_atomico(work / "avaliacoes.json", {"modelo": "m", "avaliacoes": {}})
    limpar_intermediarios(contexto.pastas)
    assert sorted(p.name for p in work.iterdir()) == ["avaliacoes.json", "cortes_shorts.json"]


def test_limpar_sem_work_nao_faz_nada(tmp_path):
    limpar_intermediarios(PastasProjeto(tmp_path / "nao_existe"))


# --- a regra de refazer ------------------------------------------------------------------------


class Anotada(Etapa):
    chamadas: ClassVar[list[str]] = []

    def executar(self, ctx):
        Anotada.chamadas.append(self.id)
        for arquivo in self.artefatos(ctx):
            arquivo.write_text("ok")


class A(Anotada):
    id, nome = "a", "A"
    opcoes_relevantes = ("limpar_audio",)

    def artefatos(self, ctx):
        return [ctx.pastas.work / "a.txt"]


class B(Anotada):
    id, nome, depende_de = "b", "B", ("a",)

    def artefatos(self, ctx):
        return [ctx.pastas.work / "b.txt"]


def test_etapa_refeita_so_porque_o_arquivo_sumiu_nao_invalida_quem_vem_depois(contexto):
    Anotada.chamadas = []
    pipeline = Pipeline([A(), B()])
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    (contexto.pastas.work / "a.txt").unlink()  # a limpeza apagou o arquivo da etapa A
    Anotada.chamadas.clear()
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    assert Anotada.chamadas == ["a"]  # o B continua valendo


def test_etapa_cuja_opcao_mudou_continua_invalidando_quem_depende_dela(contexto):
    Anotada.chamadas = []
    pipeline = Pipeline([A(), B()])
    pipeline.executar(contexto)
    Anotada.chamadas.clear()
    contexto.opcoes.limpar_audio = True
    pipeline.executar(contexto)
    assert Anotada.chamadas == ["a", "b"]


class Vinhetas(Anotada):
    id, nome, no_modo_vinhetas = "v", "V", True

    def ativa(self, opcoes):
        return opcoes.so_vinhetas

    def artefatos(self, ctx):
        return [ctx.pastas.work / "v.txt"]


def test_o_que_ja_foi_feito_nao_se_perde_ao_trocar_de_modo(contexto):
    Anotada.chamadas = []
    Pipeline([A(), B()]).executar(contexto)

    outro = Pipeline([A(), B(), Vinhetas()])
    contexto.opcoes.modo = "so_vinhetas"
    contexto.opcoes.introducao = True
    outro.executar(contexto)
    assert [e.id for e in contexto.estado.etapas] == ["v"]
    assert {e.id for e in contexto.estado.etapas_guardadas} == {"a", "b"}

    contexto.opcoes.modo = "completo"
    contexto.opcoes.introducao = False
    Anotada.chamadas.clear()
    outro.executar(contexto)
    assert Anotada.chamadas == []  # A e B voltaram como estavam: concluídas
    assert all(e.status == StatusEtapa.CONCLUIDA for e in contexto.estado.etapas)


# --- vinhetas, com o FFmpeg de verdade ------------------------------------------------------------


def clipes(pasta):
    base = [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(
        [*base, "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(pasta / "introducao.mp4")],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [*base, "-f", "lavfi", "-i", "smptebars=size=640x480:rate=24:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pasta / "vinheta_final.mp4")],
        check=True,
    )  # fmt: skip


SRT_DE_EXEMPLO = (
    "1\n00:00:01,000 --> 00:00:03,500\nOi, tudo bem?\n\n2\n00:00:04,000 --> 00:00:06,000\nTchau\n"
)


@pytest.fixture
def projeto_com_video_pronto(contexto, video_com_pausas, audio_com_pausas):
    esperado = preparar_longo(contexto, video_com_pausas, audio_com_pausas)
    rodar(vl.RenderizarVideoLongo(), contexto)
    contexto.pastas.srt.write_text(SRT_DE_EXEMPLO, encoding="utf-8")
    contexto.config.pastas.recursos.mkdir(parents=True, exist_ok=True)
    clipes(contexto.config.pastas.recursos)
    contexto.esperado = esperado
    return contexto


@pytest.mark.ffmpeg
def test_o_render_do_video_longo_anota_o_que_o_arquivo_tem(projeto_com_video_pronto):
    anotado = ler_json(projeto_com_video_pronto.pastas.video_longo_info)
    assert anotado["introducao"] is False and anotado["vinheta_final"] is False
    assert anotado["preset"] == "veryfast" and anotado["crf"] == 23


@pytest.mark.ffmpeg
def test_juntar_introducao_e_vinheta_sem_renderizar_o_video_de_novo(projeto_com_video_pronto):
    ctx = projeto_com_video_pronto
    base = ctx.pastas.video_longo
    antes = (base.stat().st_mtime_ns, base.stat().st_size)
    ctx.opcoes.modo = "so_vinhetas"
    ctx.opcoes.introducao = ctx.opcoes.vinheta_final = True
    rodar(AdicionarVinhetas(), ctx)

    novo = sondar(ctx.pastas.video_com_vinhetas)
    assert novo.duracao_s == pytest.approx(ctx.esperado + 3 + 2, abs=0.4)
    assert novo.tem_audio and (novo.largura, novo.altura) == (160, 120)
    assert (base.stat().st_mtime_ns, base.stat().st_size) == antes  # o original não foi tocado
    srt = ctx.pastas.srt_com_vinhetas.read_text(encoding="utf-8")
    assert "00:00:04,000 --> 00:00:06,500" in srt and "Oi, tudo bem?" in srt
    assert "00:00:01,000 --> 00:00:03,500" in ctx.pastas.srt.read_text(encoding="utf-8")


@pytest.mark.ffmpeg
def test_so_a_vinheta_nao_desloca_as_legendas(projeto_com_video_pronto):
    ctx = projeto_com_video_pronto
    ctx.opcoes.modo = "so_vinhetas"
    ctx.opcoes.vinheta_final = True
    rodar(AdicionarVinhetas(), ctx)
    assert sondar(ctx.pastas.video_com_vinhetas).duracao_s == pytest.approx(
        ctx.esperado + 2, abs=0.4
    )
    srt = ctx.pastas.srt_com_vinhetas.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:03,500" in srt


@pytest.mark.ffmpeg
def test_nao_junta_de_novo_o_que_o_video_ja_tem(projeto_com_video_pronto):
    ctx = projeto_com_video_pronto
    anotado = ler_json(ctx.pastas.video_longo_info)
    anotado["introducao"] = True
    escrever_json_atomico(ctx.pastas.video_longo_info, anotado)
    ctx.opcoes.modo = "so_vinhetas"
    ctx.opcoes.introducao = True
    with pytest.raises(ErroFerramenta, match="já tem a introdução"):
        rodar(AdicionarVinhetas(), ctx)


@pytest.mark.ffmpeg
def test_sem_video_longo_pronto_o_erro_manda_gerar_primeiro(contexto):
    contexto.opcoes.modo = "so_vinhetas"
    contexto.opcoes.introducao = True
    with pytest.raises(ErroFerramenta, match="ainda não tem o vídeo longo pronto"):
        rodar(AdicionarVinhetas(), contexto)


@pytest.mark.ffmpeg
def test_pipeline_do_modo_so_vinhetas_conclui_e_a_conferencia_aprova(projeto_com_video_pronto):
    ctx = projeto_com_video_pronto
    ctx.opcoes.modo = "so_vinhetas"
    ctx.opcoes.gerar_video_longo = False
    ctx.opcoes.introducao = ctx.opcoes.vinheta_final = True
    assert Pipeline(etapas_registradas()).executar(ctx) == ResultadoPipeline.CONCLUIDO
    assert ctx.pastas.video_com_vinhetas.exists()
    assert all(e.status == StatusEtapa.CONCLUIDA for e in ctx.estado.etapas)
    shutil.rmtree(ctx.pastas.work / "vinhetas", ignore_errors=True)


# --- funções puras --------------------------------------------------------------------------------


def test_lista_de_emenda_escapa_aspas(tmp_path):
    texto = lista_de_emenda([tmp_path / "o'a.mp4", tmp_path / "b.mp4"])
    linhas = texto.splitlines()
    assert linhas[0].startswith("file '") and r"o'\''a.mp4" in linhas[0]
    assert linhas[1].endswith("b.mp4'")


def test_deslocar_srt_so_mexe_nos_tempos():
    original = (
        "1\n00:00:01,000 --> 00:01:59,900\nUm texto 00:00:05,000\n\n"
        "2\n01:00:00,500 --> 01:00:02,000\nFim\n"
    )
    novo = deslocar_srt(original, 3.0)
    assert "00:00:04,000 --> 00:02:02,900" in novo
    assert "01:00:03,500 --> 01:00:05,000" in novo
    assert "Um texto 00:00:05,000" in novo  # o texto não é tocado
    assert deslocar_srt(original, 0.0) == original


def test_renderizar_recusa_short_com_mais_de_60_segundos_antes_de_comecar(ctx_shorts, video_hdr):
    from spurgeon.core.estado import EntradaVideo
    from spurgeon.render.shorts import RenderizarShorts
    from spurgeon.shorts.etapas import gravar_cortes
    from tests.test_render import corte

    ctx_shorts.estado.entrada = EntradaVideo.de_arquivo(video_hdr)
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, [corte(1, 0.0, 2.5), corte(2, 10.0, 80.0)])
    with pytest.raises(ErroFerramenta, match="Com mais de 60 segundos") as erro:
        RenderizarShorts().executar(ctx_shorts)
    assert "Título 2" in str(erro.value) and "Título 1" not in str(erro.value)
    assert not list(ctx_shorts.pastas.shorts.glob("*.mp4"))  # nenhum Short foi renderizado
