"""O pipeline de áudio da Fase 2 de ponta a ponta, com retomada e invalidação."""

import threading
from datetime import date

import pytest

from spurgeon.audio.etapas import etapas_audio
from spurgeon.core.arquivos import ler_json
from spurgeon.core.config import ConfigApp, OpcoesProcessamento
from spurgeon.core.pipeline import ResultadoPipeline
from spurgeon.core.projeto import criar_ou_retomar_projeto
from spurgeon.core.trabalhador import executar

pytestmark = pytest.mark.ffmpeg


class Rodada:
    def __init__(self):
        self.eventos: list[dict] = []

    def rodar(self, pasta):
        return executar(
            pasta,
            ConfigApp(),
            emitir=self.eventos.append,
            cancelar=threading.Event(),
            pausar=threading.Event(),
            etapas=etapas_audio(),
        )

    def etapas_que_rodaram(self) -> list[str]:
        vistas: list[str] = []
        for e in self.eventos:
            if e["tipo"] == "progresso" and e["etapa"] not in vistas:
                vistas.append(e["etapa"])
        return vistas


def criar(tmp_path, video, **opcoes):
    return criar_ou_retomar_projeto(
        tmp_path / "Projetos", video, "Teste", date(2026, 10, 12), OpcoesProcessamento(**opcoes)
    ).pastas


def test_roda_as_etapas_de_audio_e_deixa_os_resultados(tmp_path, video_com_pausas):
    pastas = criar(tmp_path, video_com_pausas, normalizar_volume=True)
    rodada = Rodada()
    assert rodada.rodar(pastas.raiz) == ResultadoPipeline.CONCLUIDO

    # A limpeza não foi pedida, então não aparece.
    assert rodada.etapas_que_rodaram() == [
        "extrair_audio",
        "normalizar_audio",
        "medir_nivel",
        "detectar_pausas",
        "calcular_cortes",
    ]
    for arquivo in (pastas.audio_original, pastas.audio_normalizado, pastas.pausas, pastas.cortes):
        assert arquivo.exists(), arquivo.name
    assert not pastas.audio_limpo.exists()
    assert (
        pastas.audio_final(OpcoesProcessamento(normalizar_volume=True)) == pastas.audio_normalizado
    )

    # Nenhuma etapa gera o vídeo final ainda: os intermediários são o resultado e ficam.
    assert pastas.work.exists()
    cortes = ler_json(pastas.cortes)
    assert cortes["pausas_cortadas"] == 2
    assert cortes["duracao_editada_s"] == pytest.approx(35.0, abs=1.0)


def test_rodar_de_novo_nao_refaz_nada(tmp_path, video_com_pausas):
    pastas = criar(tmp_path, video_com_pausas)
    Rodada().rodar(pastas.raiz)
    segunda = Rodada()
    assert segunda.rodar(pastas.raiz) == ResultadoPipeline.CONCLUIDO
    assert segunda.etapas_que_rodaram() == []


def test_mudar_so_o_limiar_refaz_so_os_cortes(tmp_path, video_com_pausas):
    pastas = criar(tmp_path, video_com_pausas)
    Rodada().rodar(pastas.raiz)
    assert ler_json(pastas.cortes)["pausas_cortadas"] == 2

    criar(tmp_path, video_com_pausas, limiar_pausa_s=2.5, pausa_residual_s=0.5)
    segunda = Rodada()
    segunda.rodar(pastas.raiz)
    assert segunda.etapas_que_rodaram() == ["calcular_cortes"]  # a detecção não foi repetida
    assert ler_json(pastas.cortes)["pausas_cortadas"] == 3


def test_pedir_a_normalizacao_depois_so_acrescenta_essa_etapa(tmp_path, video_com_pausas):
    pastas = criar(tmp_path, video_com_pausas)
    Rodada().rodar(pastas.raiz)

    criar(tmp_path, video_com_pausas, normalizar_volume=True)
    segunda = Rodada()
    segunda.rodar(pastas.raiz)
    assert segunda.etapas_que_rodaram() == ["normalizar_audio"]
    assert pastas.audio_normalizado.exists()


def test_sem_video_longo_nao_procura_pausas(tmp_path, video_com_pausas):
    pastas = criar(tmp_path, video_com_pausas, gerar_video_longo=False, gerar_shorts=True)
    rodada = Rodada()
    assert rodada.rodar(pastas.raiz) == ResultadoPipeline.CONCLUIDO
    assert rodada.etapas_que_rodaram() == ["extrair_audio", "medir_nivel"]
    assert not pastas.pausas.exists()
