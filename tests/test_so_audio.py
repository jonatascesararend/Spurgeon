"""Modo "Só o áudio" e a trilha de áudio de fora (no PC e pelo celular)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from spurgeon.audio.exportar import ExportarAudio, filtro_da_janela
from spurgeon.audio.extrair import ExtrairAudio
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.estado import StatusEtapa, carregar_estado, salvar_estado
from spurgeon.core.etapas import etapas_registradas
from spurgeon.core.ffmpeg import localizar_binario, sondar
from spurgeon.core.pipeline import Pipeline
from spurgeon.core.preflight import Nivel, _audio_externo
from spurgeon.core.projeto import PastasProjeto, resumo_dos_resultados
from spurgeon.remoto import configuracao, dados, trilha
from spurgeon.render.finalizar import Finalizar
from spurgeon.ui.estimativa import estimar_tempo_total
from spurgeon.ui.formulario import (
    ErroFormulario,
    ValoresFormulario,
    calcular_habilitacoes,
    para_opcoes,
)
from tests import test_remoto
from tests.test_remoto import NOME_DO_PROJETO, criar_projeto

# as fixtures do servidor do celular (as mesmas de `test_remoto.py`)
cofre, projetos, servidor = test_remoto.cofre, test_remoto.projetos, test_remoto.servidor


def _ids(opcoes: OpcoesProcessamento) -> list[str]:
    return [e.id for e in Pipeline(etapas_registradas()).ativas(opcoes)]


def _wav(destino: Path, segundos: float, frequencia: int = 440) -> Path:
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={frequencia}:sample_rate=48000:duration={segundos}",
            "-c:a", "pcm_s16le", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


# --- núcleo ---------------------------------------------------------------------------------


def test_so_audio_roda_so_as_etapas_do_som():
    assert _ids(OpcoesProcessamento(modo="so_audio")) == [
        "extrair_audio", "exportar_audio", "finalizar",
    ]  # fmt: skip
    com_tudo = OpcoesProcessamento(
        modo="so_audio", limpar_audio=True, normalizar_volume=True, gerar_shorts=True
    )
    assert _ids(com_tudo) == [
        "extrair_audio", "limpar_audio", "normalizar_audio", "exportar_audio", "finalizar",
    ]  # fmt: skip
    assert not com_tudo.processa_shorts and not com_tudo.processa_video_longo
    assert com_tudo.problemas() == []
    assert "exportar_audio" not in _ids(OpcoesProcessamento())  # os outros modos não exportam


def test_campos_novos_nao_mudam_a_assinatura_de_projetos_antigos():
    padrao = OpcoesProcessamento()
    assert padrao.assinatura(ExtrairAudio.opcoes_relevantes) == padrao.assinatura([])
    trocada = OpcoesProcessamento(audio_externo="C:/melhorado.wav")
    assert trocada.assinatura(ExtrairAudio.opcoes_relevantes) != padrao.assinatura([])


def test_filtro_da_janela_so_corta_quando_ha_corte():
    assert filtro_da_janela(0.0, 60.0, 60.0) is None
    assert filtro_da_janela(2.0, 5.0, 60.0) == "atrim=start=2.000:end=5.000,asetpts=PTS-STARTPTS"


def test_estimativa_do_so_audio_nao_conta_transcricao_nem_render():
    total, render = estimar_tempo_total(3600, OpcoesProcessamento(modo="so_audio"), hdr=True)
    completo, _ = estimar_tempo_total(3600, OpcoesProcessamento(), hdr=True)
    assert render == 0.0 and total < completo / 5


def test_preflight_da_trilha_de_fora(tmp_path):
    falta = _audio_externo(OpcoesProcessamento(audio_externo=str(tmp_path / "nao_tem.wav")))
    assert falta.nivel == Nivel.ERRO and "não encontrado" in falta.detalhe


@pytest.mark.ffmpeg
def test_extrair_usa_a_trilha_de_fora_no_lugar_do_som_do_video(contexto, video_teste, tmp_path):
    contexto.estado.entrada.caminho = str(video_teste)  # 20 s
    contexto.opcoes.audio_externo = str(_wav(tmp_path / "melhorado.wav", 7, 880))
    ExtrairAudio().executar(contexto)
    assert sondar(contexto.pastas.audio_original).duracao_s == pytest.approx(7.0, abs=0.1)
    log = contexto.pastas.log.read_text("utf-8")
    assert "trilha de áudio de fora" in log and "começa junto com o vídeo" in log


@pytest.mark.ffmpeg
def test_extrair_com_a_trilha_sumida_da_erro_claro(contexto, video_teste, tmp_path):
    contexto.estado.entrada.caminho = str(video_teste)
    contexto.opcoes.audio_externo = str(tmp_path / "sumiu.wav")
    with pytest.raises(Exception, match="trilha de áudio escolhida"):
        ExtrairAudio().executar(contexto)


@pytest.mark.ffmpeg
@pytest.mark.parametrize(("formato", "codec"), [("wav", "pcm_s16le"), ("mp3", "mp3")])
def test_exportar_grava_o_trecho_escolhido_e_a_conferencia_aceita(
    contexto, audio_com_pausas, formato, codec
):
    shutil.copy(audio_com_pausas, contexto.pastas.audio_original)  # 60 s
    contexto.opcoes.modo = "so_audio"
    contexto.opcoes.audio_formato = formato
    contexto.opcoes.video_inicio_s, contexto.opcoes.video_fim_s = 2.0, 5.0
    ExportarAudio().executar(contexto)
    arquivo = contexto.pastas.audio_exportado(formato)
    info = sondar(arquivo)
    assert (info.codec_audio, info.taxa_audio) == (codec, 48000)
    assert info.duracao_s == pytest.approx(3.0, abs=0.08)
    assert not list(contexto.pastas.raiz.glob("*.parcial"))
    Finalizar().executar(contexto)  # não levanta: o arquivo tem a duração esperada
    assert "áudio do sermão" in resumo_dos_resultados(contexto.pastas)


# --- formulário do PC -----------------------------------------------------------------------


def test_formulario_no_so_audio():
    v = ValoresFormulario(
        modo="so_audio", gerar_video_longo=False, audio_formato="mp3", tratamento_ativo=True
    )
    h = calcular_habilitacoes(v, introducao_existe=True, vinheta_existe=True)
    assert h.pode_iniciar and h.audio_formato and h.limpar_audio and h.aparar
    assert not (h.zoom or h.tratamento or h.marca_dagua or h.revisar_transcricao or h.introducao)
    opcoes = para_opcoes(v)
    assert (opcoes.modo, opcoes.audio_formato) == ("so_audio", "mp3")
    assert opcoes.tratamento_ativo  # guardado para quando voltar ao modo Tudo
    assert not calcular_habilitacoes(
        ValoresFormulario(), introducao_existe=True, vinheta_existe=True
    ).audio_formato


def test_formulario_confere_a_trilha_de_fora(tmp_path):
    with pytest.raises(ErroFormulario, match="trilha de áudio"):
        para_opcoes(ValoresFormulario(audio_externo=str(tmp_path / "nao_tem.wav")))
    arquivo = tmp_path / "melhorado.wav"
    arquivo.write_bytes(b"x")
    assert para_opcoes(ValoresFormulario(audio_externo=f" {arquivo} ")).audio_externo == str(
        arquivo
    )
    assert ValoresFormulario.de_opcoes(OpcoesProcessamento(audio_externo="a.wav")).audio_externo


# --- celular --------------------------------------------------------------------------------


@pytest.fixture
def pastas_nao_comecou(projetos) -> PastasProjeto:
    pastas = PastasProjeto(projetos / NOME_DO_PROJETO)
    estado = carregar_estado(pastas.raiz)
    for etapa in estado.etapas:
        etapa.status, etapa.progresso = StatusEtapa.PENDENTE, 0.0
    salvar_estado(pastas.raiz, estado)
    return pastas


def _campo(config: dict, nome: str) -> dict:
    return next(c for g in config["grupos"] for c in g["campos"] if c["campo"] == nome)


def test_celular_ve_so_o_nome_da_trilha_e_nao_escreve_caminho(tmp_path):
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    estado = carregar_estado(pastas.raiz)
    for etapa in estado.etapas:
        etapa.status = StatusEtapa.PENDENTE
    estado.opcoes.audio_externo = r"C:\Users\Alguém\Áudios\sermão melhorado.wav"
    salvar_estado(pastas.raiz, estado)
    config = configuracao.configuracao(pastas)
    assert _campo(config, "audio_externo") == {
        **_campo(config, "audio_externo"), "tipo": "trilha", "valor": "sermão melhorado.wav",
    }  # fmt: skip
    modos = [valor for valor, _ in _campo(config, "modo")["opcoes"]]
    assert "so_audio" in modos
    with pytest.raises(configuracao.OpcaoInvalida, match="Envie o arquivo"):
        configuracao.mudar(pastas, {"audio_externo": r"C:\Windows\win.ini"})
    configuracao.mudar(pastas, {"audio_externo": "", "audio_formato": "mp3", "modo": "so_audio"})
    opcoes = carregar_estado(pastas.raiz).opcoes
    assert (opcoes.audio_externo, opcoes.audio_formato, opcoes.modo) == ("", "mp3", "so_audio")


def test_nome_da_trilha_e_seguro():
    assert trilha.nome_da_trilha("../../x/Meu Áudio.WAV") == "Meu_Áudio.wav"
    with pytest.raises(configuracao.OpcaoInvalida, match="não é de áudio"):
        trilha.nome_da_trilha("planilha.xlsx")


@pytest.mark.ffmpeg
def test_celular_envia_a_trilha_e_volta_ao_som_do_video(
    servidor,
    pastas_nao_comecou,
    tmp_path,
):
    pastas = pastas_nao_comecou
    url = f"/api/projetos/{NOME_DO_PROJETO}/trilha"
    corpo = _wav(tmp_path / "melhorado.wav", 2).read_bytes()

    r = servidor.post(f"{url}?nome=planilha.xlsx", content=corpo)
    assert r.status_code == 422 and "não é de áudio" in r.json()["erro"]
    r = servidor.post(f"{url}?nome=lixo.wav", content=b"isto nao e audio" * 10)
    assert r.status_code == 422 and not list(pastas.trilhas_externas.iterdir())

    r = servidor.post(f"{url}?nome=Sermão melhorado.wav", content=corpo)
    assert r.status_code == 200
    assert _campo(r.json(), "audio_externo")["valor"] == "Sermão_melhorado.wav"
    guardada = Path(carregar_estado(pastas.raiz).opcoes.audio_externo)
    assert guardada.parent == pastas.trilhas_externas and guardada.read_bytes() == corpo

    # trocar apaga a anterior; voltar ao som do vídeo apaga a enviada
    r = servidor.post(f"{url}?nome=outro.wav", content=corpo)
    assert [p.name for p in pastas.trilhas_externas.iterdir()] == ["outro.wav"]
    r = servidor.post(f"/api/projetos/{NOME_DO_PROJETO}/opcoes", json={"audio_externo": ""})
    assert r.status_code == 200 and not list(pastas.trilhas_externas.iterdir())

    # depois de começar, não muda mais
    estado = carregar_estado(pastas.raiz)
    estado.etapas[0].status = StatusEtapa.CONCLUIDA
    salvar_estado(pastas.raiz, estado)
    assert servidor.post(f"{url}?nome=x.wav", content=corpo).status_code == 422


def test_celular_baixa_o_audio_pronto(servidor, projetos):
    pastas = PastasProjeto(projetos / NOME_DO_PROJETO)
    url = f"/api/projetos/{NOME_DO_PROJETO}"
    assert servidor.get(f"{url}/audio").status_code == 404
    assert servidor.get(url).json()["audio_pronto"] == ""
    pastas.audio_exportado("mp3").write_bytes(b"ID3audio")
    assert dados.audio_pronto(pastas) == pastas.audio_exportado("mp3")
    assert servidor.get(url).json()["audio_pronto"] == "audio_do_sermao.mp3"
    r = servidor.get(f"{url}/audio")
    assert r.status_code == 200 and r.content == b"ID3audio"
    assert r.headers["content-type"] == "audio/mpeg"
    assert "Serm%C3%A3o" in r.headers["content-disposition"]
