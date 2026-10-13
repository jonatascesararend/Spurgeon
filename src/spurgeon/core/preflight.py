"""Pré-verificação: confere o ambiente antes de processar (e no comando `spurgeon doctor`)."""

from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from spurgeon.core.config import ConfigApp, OpcoesProcessamento
from spurgeon.core.erros import ErroConfiguracao
from spurgeon.core.ffmpeg import (
    codificadores_disponiveis,
    filtros_disponiveis,
    localizar_binario,
    nvenc_funciona,
    versao_ffmpeg,
)
from spurgeon.core.hardware import detectar_hardware, recomendar

URL_OLLAMA = "http://127.0.0.1:11434"
_FILTROS_NECESSARIOS = ("ass", "loudnorm")
_CODIFICADORES_NECESSARIOS = ("libx264", "aac")
_DISCO_MINIMO_GB = 10.0
_RAM_LIVRE_MINIMA_GB = 3.0
_FATOR_ESPACO_VIDEO = 3  # o projeto precisa de ~3x o tamanho do vídeo em disco


class Nivel(StrEnum):
    OK = "ok"
    AVISO = "aviso"
    ERRO = "erro"


@dataclass(frozen=True)
class Verificacao:
    nome: str
    nivel: Nivel
    detalhe: str


def tem_erro(verificacoes: list[Verificacao]) -> bool:
    return any(v.nivel == Nivel.ERRO for v in verificacoes)


def executar_preflight(
    config: ConfigApp,
    *,
    video: Path | None = None,
    opcoes: OpcoesProcessamento | None = None,
) -> list[Verificacao]:
    """Sem `opcoes`, é uma checagem geral; com elas, só falha no que o processamento exige."""
    opcoes = opcoes or config.padroes
    especifico = video is not None
    resultados = [_python(), *_ffmpeg(opcoes), *_pastas(config, opcoes, especifico)]
    if opcoes.limpar_audio or not especifico:
        resultados.append(_deep_filter(config))
    resultados += _whisper(config, opcoes)
    resultados += _hardware_e_disco(config, video)
    if opcoes.gerar_shorts or not especifico:
        resultados.append(_ollama(opcoes, exigido=opcoes.gerar_shorts and especifico))
    return resultados


def _python() -> Verificacao:
    # O projeto exige Python 3.11+ (pyproject); em versão menor nem o import chegaria aqui.
    return Verificacao("Python", Nivel.OK, ".".join(map(str, sys.version_info[:3])))


def _ffmpeg(opcoes: OpcoesProcessamento) -> list[Verificacao]:
    try:
        localizar_binario("ffprobe")
        resultados = [Verificacao("FFmpeg", Nivel.OK, versao_ffmpeg())]
    except ErroConfiguracao as erro:
        return [Verificacao("FFmpeg", Nivel.ERRO, str(erro))]
    faltam = [f for f in _FILTROS_NECESSARIOS if f not in filtros_disponiveis()]
    faltam += [c for c in _CODIFICADORES_NECESSARIOS if c not in codificadores_disponiveis()]
    if faltam:
        resultados.append(
            Verificacao("FFmpeg: recursos", Nivel.ERRO, "faltam: " + ", ".join(faltam))
        )
    else:
        resultados.append(Verificacao("FFmpeg: recursos", Nivel.OK, "ass, loudnorm, libx264, aac"))
    if opcoes.usar_nvenc and not nvenc_funciona():
        resultados.append(Verificacao("NVENC", Nivel.ERRO, "pedido nas opções, mas não funciona"))
    else:
        detalhe = "disponível" if nvenc_funciona() else "indisponível (usa libx264 na CPU)"
        resultados.append(Verificacao("NVENC", Nivel.OK, detalhe))
    return resultados


def _pastas(config: ConfigApp, opcoes: OpcoesProcessamento, especifico: bool) -> list[Verificacao]:
    pastas = config.pastas
    pastas.garantir()  # o aplicativo cria Downloads/, Projetos/ e recursos/ quando faltam
    resultados = []
    if pastas.entrada.exists():
        resultados.append(Verificacao("Pasta de entrada", Nivel.OK, str(pastas.entrada)))
    else:
        resultados.append(
            Verificacao("Pasta de entrada", Nivel.AVISO, f"não existe: {pastas.entrada}")
        )
    try:
        pastas.projetos.mkdir(parents=True, exist_ok=True)
        teste = pastas.projetos / ".escrita_teste"
        teste.write_text("ok", encoding="utf-8")
        teste.unlink()
        resultados.append(Verificacao("Pasta de projetos", Nivel.OK, str(pastas.projetos)))
    except OSError as erro:
        resultados.append(Verificacao("Pasta de projetos", Nivel.ERRO, f"sem escrita: {erro}"))
    for pedido, nome, caminho in (
        (opcoes.introducao, "Introdução", pastas.introducao),
        (opcoes.vinheta_final, "Vinheta final", pastas.vinheta_final),
    ):
        if pedido:
            nivel = Nivel.OK if caminho.exists() else Nivel.ERRO
            resultados.append(Verificacao(nome, nivel, str(caminho)))
        elif not especifico:
            detalhe = str(caminho) if caminho.exists() else f"ainda não existe: {caminho.name}"
            resultados.append(Verificacao(nome, Nivel.OK, detalhe))
    return resultados


def _whisper(config: ConfigApp, opcoes: OpcoesProcessamento) -> list[Verificacao]:
    """O pacote faster-whisper (erro se faltar) e o modelo escolhido (aviso se faltar baixar)."""
    from importlib.util import find_spec

    from spurgeon.transcricao.modelo import TAMANHO_MB, modelo_baixado

    if find_spec("faster_whisper") is None:
        return [
            Verificacao(
                "faster-whisper",
                Nivel.ERRO,
                "não instalado; rode o instalar.bat (ou: uv sync --extra transcricao)",
            )
        ]
    nome = opcoes.whisper_modelo
    if modelo_baixado(config, nome):
        return [Verificacao("Modelo Whisper", Nivel.OK, f"{nome} instalado")]
    return [
        Verificacao(
            "Modelo Whisper",
            Nivel.AVISO,
            f"{nome} ainda não foi baixado: serão cerca de {TAMANHO_MB.get(nome, 0)} MB na "
            "primeira transcrição, e é preciso ter internet nesse momento",
        )
    ]


def _deep_filter(config: ConfigApp) -> Verificacao:
    from spurgeon.audio.deep_filter import caminho_deep_filter, deep_filter_instalado

    if deep_filter_instalado(config):
        return Verificacao(
            "deep-filter", Nivel.OK, f"instalado e conferido: {caminho_deep_filter(config)}"
        )
    return Verificacao(
        "deep-filter",
        Nivel.AVISO,
        "ainda não baixado: será baixado (cerca de 26 MB) na primeira limpeza de áudio; "
        "precisa de internet nesse momento",
    )


def _hardware_e_disco(config: ConfigApp, video: Path | None) -> list[Verificacao]:
    perfil = detectar_hardware(config.pastas.projetos)
    recomendacao = recomendar(perfil)
    gpu = ", ".join(g.nome for g in perfil.gpus) or "nenhuma"
    resultados = [
        Verificacao(
            "Hardware",
            Nivel.OK,
            f"{perfil.cpu_nome}, {perfil.threads_logicos} threads, "
            f"{perfil.ram_total_gb} GB de RAM, GPU: {gpu}",
        ),
        Verificacao(
            "Recomendação",
            Nivel.OK,
            f"Whisper {recomendacao.whisper_modelo} ({recomendacao.whisper_dispositivo}, "
            f"{recomendacao.whisper_compute_type}); LLM {recomendacao.llm_modelo}",
        ),
    ]
    nivel = Nivel.OK if perfil.ram_livre_gb >= _RAM_LIVRE_MINIMA_GB else Nivel.AVISO
    resultados.append(Verificacao("RAM livre", nivel, f"{perfil.ram_livre_gb} GB"))

    if video is not None:
        necessario = _FATOR_ESPACO_VIDEO * Path(video).stat().st_size / 1024**3
        nivel = Nivel.OK if perfil.disco_livre_gb >= necessario else Nivel.ERRO
        detalhe = f"{perfil.disco_livre_gb} GB livres; o projeto precisa de ~{necessario:.1f} GB"
    else:
        nivel = Nivel.OK if perfil.disco_livre_gb >= _DISCO_MINIMO_GB else Nivel.AVISO
        detalhe = f"{perfil.disco_livre_gb} GB livres"
    resultados.append(Verificacao("Espaço em disco", nivel, detalhe))
    return resultados


def _modelos_ollama() -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{URL_OLLAMA}/api/tags", timeout=1.5) as resposta:
            return [m["name"] for m in json.load(resposta).get("models", [])]
    except (OSError, ValueError):
        return None


def _ollama(opcoes: OpcoesProcessamento, *, exigido: bool) -> Verificacao:
    falha = Nivel.ERRO if exigido else Nivel.AVISO
    modelos = _modelos_ollama()
    if modelos is None:
        return Verificacao(
            "Ollama", falha, f"não respondeu em {URL_OLLAMA}; abra o aplicativo Ollama"
        )
    nome = opcoes.ollama_modelo
    if nome in modelos or f"{nome}:latest" in modelos:
        return Verificacao("Ollama", Nivel.OK, f"ativo, modelo {nome} instalado")
    # O aplicativo baixa o modelo sozinho na primeira vez; por isso é só um aviso.
    return Verificacao(
        "Ollama",
        Nivel.AVISO,
        f"ativo, mas falta o modelo {nome}; será baixado no primeiro uso (ou: ollama pull {nome})",
    )
