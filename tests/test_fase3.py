"""Etapas da Fase 3 com o Whisper de verdade (modelo `tiny`) e com arquivos montados à mão."""

import subprocess
import threading
import time
from datetime import date

import pytest

from spurgeon.audio.etapas import etapas_audio_base
from spurgeon.audio.nivel import MedirNivel
from spurgeon.audio.pausas import CalcularCortes, DetectarPausas
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import ConfigApp, OpcoesProcessamento, raiz_repositorio
from spurgeon.core.erros import Cancelado, ErroFerramenta
from spurgeon.core.ffmpeg import localizar_binario
from spurgeon.core.pipeline import ResultadoEtapa, ResultadoPipeline
from spurgeon.core.projeto import criar_ou_retomar_projeto
from spurgeon.core.trabalhador import executar
from spurgeon.transcricao import etapas as etapas_mod
from spurgeon.transcricao.blocos import planejar_blocos
from spurgeon.transcricao.etapas import (
    GerarSrt,
    RefinarPausas,
    RevisarTranscricao,
    Transcrever,
)
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao
from spurgeon.transcricao.modelo import baixar_modelo, modelo_baixado, pasta_modelo

MODELOS_DO_PROJETO = raiz_repositorio() / "modelos"


def exige_modelo(nome: str = "tiny"):
    config = ConfigApp()
    config.pastas.modelos = MODELOS_DO_PROJETO
    if not modelo_baixado(config, nome):
        pytest.skip(f"o modelo Whisper {nome} não está baixado")
    return config


@pytest.fixture
def com_modelo(contexto):
    """O contexto de teste usando a pasta de modelos do projeto e um vocabulário pequeno."""
    exige_modelo()
    contexto.config.pastas.modelos = MODELOS_DO_PROJETO
    contexto.config.pastas.recursos.mkdir(parents=True, exist_ok=True)
    contexto.config.pastas.vocabulario.write_text("Efésios\nSpurgeon\n", encoding="utf-8")
    contexto.opcoes.whisper_modelo = "tiny"
    return contexto


# --- modelo --------------------------------------------------------------------------------


def test_baixar_modelo_acompanha_o_progresso_e_confere_os_arquivos(config_tmp):
    progresso: list[float] = []

    def baixar_de_mentira(nome, output_dir):
        time.sleep(0.5)
        pasta = pasta_modelo(config_tmp, nome)
        pasta.mkdir(parents=True, exist_ok=True)
        for arquivo in ("model.bin", "config.json", "tokenizer.json"):
            (pasta / arquivo).write_bytes(b"x" * 40_000_000 if arquivo == "model.bin" else b"{}")

    assert not modelo_baixado(config_tmp, "tiny")
    pasta = baixar_modelo(
        config_tmp, "tiny", ao_progresso=progresso.append, baixar=baixar_de_mentira
    )
    assert pasta == config_tmp.pastas.modelos / "whisper" / "tiny"
    assert modelo_baixado(config_tmp, "tiny")
    assert progresso and all(0 <= p <= 0.99 for p in progresso)


def test_baixar_modelo_que_ja_existe_nao_baixa_de_novo(config_tmp):
    pasta = pasta_modelo(config_tmp, "tiny")
    pasta.mkdir(parents=True)
    for arquivo in ("model.bin", "config.json", "tokenizer.json"):
        (pasta / arquivo).write_bytes(b"x")

    def nao_deve_baixar(*_a, **_k):
        raise AssertionError("baixou de novo")

    assert baixar_modelo(config_tmp, "tiny", baixar=nao_deve_baixar) == pasta


def test_falha_no_download_explica_o_que_fazer(config_tmp):
    def sem_internet(nome, output_dir):
        raise OSError("sem rede")

    with pytest.raises(ErroFerramenta, match="internet"):
        baixar_modelo(config_tmp, "tiny", baixar=sem_internet)


def test_cancelar_o_download(config_tmp):
    cancelar = threading.Event()
    cancelar.set()

    def demorado(nome, output_dir):
        time.sleep(5)

    with pytest.raises(Cancelado):
        baixar_modelo(config_tmp, "tiny", cancelar=cancelar, baixar=demorado)


# --- transcrição com o modelo de verdade ---------------------------------------------------


@pytest.mark.ffmpeg
@pytest.mark.modelo
def test_transcrever_gera_palavras_com_tempo_e_conhece_o_vocabulario(com_modelo, fala_pt):
    import shutil

    shutil.copy(fala_pt, com_modelo.pastas.audio_original)
    com_modelo.etapa_id = "medir_nivel"
    MedirNivel().executar(com_modelo)
    com_modelo.etapa_id = "transcrever"
    Transcrever().executar(com_modelo)

    t = Transcricao.carregar(com_modelo.pastas.transcricao)
    texto = " ".join(p.texto for p in t.palavras).casefold()
    assert "graça" in texto or "deus" in texto
    assert t.modelo == "tiny"
    assert all(b.concluido for b in t.blocos)
    palavras = t.palavras
    assert len(palavras) > 20
    assert all(p.fim >= p.inicio for p in palavras)
    assert palavras == sorted(palavras, key=lambda p: p.inicio)  # em ordem no tempo
    assert palavras[-1].fim <= t.duracao_s + 1


@pytest.mark.ffmpeg
@pytest.mark.modelo
def test_transcricao_interrompida_retoma_do_bloco_seguinte(com_modelo, fala_pt, monkeypatch):
    import shutil

    shutil.copy(fala_pt, com_modelo.pastas.audio_original)
    # blocos de ~7 s em vez de 10 min, para a fala de teste dar 3 ou 4 blocos
    monkeypatch.setattr(
        etapas_mod, "planejar_blocos", lambda d, p: planejar_blocos(d, p, alvo_s=7.0, folga_s=1.0)
    )
    com_modelo.etapa_id = "medir_nivel"
    MedirNivel().executar(com_modelo)

    cancelar = com_modelo.cancelar
    com_modelo.etapa_id = "transcrever"
    com_modelo.ao_progresso = lambda _id, fracao: cancelar.set() if fracao > 0.25 else None
    with pytest.raises(Cancelado):
        Transcrever().executar(com_modelo)
    parcial = Transcricao.carregar(com_modelo.pastas.transcricao)
    concluidos = sum(b.concluido for b in parcial.blocos)
    assert 1 <= concluidos < len(parcial.blocos)

    cancelar.clear()
    com_modelo.ao_progresso = None
    Transcrever().executar(com_modelo)
    final = Transcricao.carregar(com_modelo.pastas.transcricao)
    assert all(b.concluido for b in final.blocos)
    log = com_modelo.pastas.log.read_text(encoding="utf-8")
    assert f"Retomando: {concluidos} de {len(final.blocos)} blocos" in log


@pytest.mark.ffmpeg
@pytest.mark.modelo
def test_mudar_o_modelo_descarta_os_blocos_gravados(com_modelo, fala_pt, monkeypatch):
    import shutil

    shutil.copy(fala_pt, com_modelo.pastas.audio_original)
    com_modelo.etapa_id = "medir_nivel"
    MedirNivel().executar(com_modelo)
    antiga = Transcricao(
        modelo="outro-modelo",
        duracao_s=30.0,
        assinatura="de-outra-execucao",
        blocos=[Bloco(0.0, 30.0, [Segmento([Palavra("lixo", 0.0, 0.5)])], True)],
    )
    antiga.salvar(com_modelo.pastas.transcricao)
    Transcrever().executar(com_modelo)
    nova = Transcricao.carregar(com_modelo.pastas.transcricao)
    assert "lixo" not in [p.texto for p in nova.palavras]
    assert nova.modelo == "tiny"


@pytest.mark.ffmpeg
@pytest.mark.modelo
def test_pipeline_completo_da_fala_ate_as_legendas(tmp_path, video_com_fala):
    config = exige_modelo()
    config.pastas.modelos = MODELOS_DO_PROJETO
    config.pastas.recursos = tmp_path / "recursos"
    config.pastas.recursos.mkdir()
    opcoes = OpcoesProcessamento(
        whisper_modelo="tiny",
        pausar_revisar_transcricao=False,
        limiar_pausa_s=0.5,
        pausa_residual_s=0.1,
    )
    pastas = criar_ou_retomar_projeto(
        tmp_path / "Projetos", video_com_fala, "Fala", date(2026, 10, 12), opcoes
    ).pastas
    eventos: list[dict] = []
    from spurgeon.core.etapas import etapas_registradas

    resultado = executar(
        pastas.raiz, config, emitir=eventos.append, cancelar=threading.Event(),
        pausar=threading.Event(), etapas=etapas_registradas(),
    )  # fmt: skip
    assert resultado == ResultadoPipeline.CONCLUIDO, [e for e in eventos if e["tipo"] == "fim"]

    srt = pastas.srt.read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:")
    assert " --> " in srt
    cortes = ler_json(pastas.cortes)
    assert cortes["refinadas_pela_transcricao"] is True
    assert pastas.transcricao.exists()


# --- etapas montadas à mão (sem modelo) ----------------------------------------------------


def transcricao_de_exemplo(contexto, textos_e_inicios):
    """Grava uma transcricao.json com um segmento por (texto, início)."""
    segmentos = []
    for texto, inicio in textos_e_inicios:
        palavras = [
            Palavra(t, inicio + i * 0.4, inicio + i * 0.4 + 0.35, 0.95)
            for i, t in enumerate(texto.split())
        ]
        segmentos.append(Segmento(palavras))
    t = Transcricao("tiny", 60.0, blocos=[Bloco(0.0, 60.0, segmentos, True)])
    t.salvar(contexto.pastas.transcricao)
    return t


def cortes_de_exemplo(contexto, trechos, duracao=60.0):
    escrever_json_atomico(
        contexto.pastas.cortes,
        {"duracao_original_s": duracao, "trechos": [list(t) for t in trechos]},
    )


def test_revisar_transcricao_para_o_pipeline_para_a_pessoa_revisar(contexto):
    assert RevisarTranscricao().executar(contexto) == ResultadoEtapa.AGUARDANDO_REVISAO


def test_revisao_so_roda_se_pedida(contexto):
    contexto.opcoes.pausar_revisar_transcricao = False
    assert not RevisarTranscricao().ativa(contexto.opcoes)
    contexto.opcoes.pausar_revisar_transcricao = True
    assert RevisarTranscricao().ativa(contexto.opcoes)


def test_refinar_pausas_tira_a_fala_que_a_energia_nao_viu(contexto):
    escrever_json_atomico(
        contexto.pastas.pausas,
        {"duracao_s": 60.0, "pausas": [[10.0, 30.0], [40.0, 45.0]]},
    )
    transcricao_de_exemplo(contexto, [("alguém falou baixinho", 18.0)])
    RefinarPausas().executar(contexto)
    dados = ler_json(contexto.pastas.pausas)
    assert dados["pausas"] == [[10.0, 30.0], [40.0, 45.0]]  # a detecção original fica
    refinadas = dados["pausas_refinadas"]
    assert len(refinadas) == 3  # a primeira pausa foi dividida em duas
    assert refinadas[0][1] < 18.0 and refinadas[1][0] > 19.0


def test_cortes_usam_as_pausas_refinadas_quando_existem(contexto):
    escrever_json_atomico(
        contexto.pastas.pausas,
        {
            "duracao_s": 60.0,
            "pausas": [[10.0, 30.0]],
            "pausas_refinadas": [[10.0, 12.0]],  # só 2 s sobraram
        },
    )
    contexto.opcoes.limiar_pausa_s = 5.0
    CalcularCortes().executar(contexto)
    cortes = ler_json(contexto.pastas.cortes)
    assert cortes["pausas_cortadas"] == 0  # a pausa refinada não chega a 5 s
    assert cortes["refinadas_pela_transcricao"] is True


def test_gerar_srt_remapeia_pelos_cortes(contexto):
    transcricao_de_exemplo(contexto, [("Deus é bom", 5.0), ("Amém então", 40.0)])
    cortes_de_exemplo(contexto, [(0.0, 20.0), (35.0, 60.0)])  # 15 s cortados entre 20 e 35
    GerarSrt().executar(contexto)
    srt = contexto.pastas.srt.read_text(encoding="utf-8")
    assert "00:00:05,000 --> " in srt  # antes do corte: não muda
    assert "00:00:25,000 --> " in srt  # 40 s originais − 15 s cortados
    assert "Deus é bom" in srt and "Amém então" in srt


@pytest.mark.ffmpeg
def test_gerar_srt_soma_a_duracao_da_introducao(contexto, tmp_path):
    intro = contexto.config.pastas.introducao
    intro.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:size=64x64:rate=5:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", str(intro),
        ],
        check=True,
    )  # fmt: skip
    contexto.opcoes.introducao = True
    transcricao_de_exemplo(contexto, [("Deus é bom", 5.0)])
    cortes_de_exemplo(contexto, [(0.0, 60.0)])
    GerarSrt().executar(contexto)
    assert "00:00:09,000 --> " in contexto.pastas.srt.read_text(encoding="utf-8")  # 5 s + 4 s


def test_gerar_srt_respeita_o_limite_de_linhas_e_caracteres(contexto):
    transcricao_de_exemplo(contexto, [("palavra " * 40, 1.0)])
    cortes_de_exemplo(contexto, [(0.0, 60.0)])
    contexto.opcoes.srt_max_linhas = 1
    contexto.opcoes.srt_max_caracteres_por_linha = 20
    GerarSrt().executar(contexto)
    linhas_de_texto = [
        linha
        for linha in contexto.pastas.srt.read_text(encoding="utf-8").splitlines()
        if linha and not linha.isdigit() and "-->" not in linha
    ]
    assert linhas_de_texto and all(len(linha) <= 20 for linha in linhas_de_texto)


def test_ordem_das_etapas_de_audio_base_continua_valida():
    ids = [e.id for e in etapas_audio_base()]
    assert ids == ["extrair_audio", "limpar_audio", "normalizar_audio", "medir_nivel"]
    assert DetectarPausas.depende_de == ("medir_nivel",)
