"""Configuração do aplicativo (TOML do usuário) e opções de cada processamento."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import platformdirs
import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from spurgeon.core.arquivos import escrever_texto_atomico
from spurgeon.core.erros import ErroConfiguracao

NOME_APP = "Spurgeon"

ModeloWhisper = Literal["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
Qualidade = Literal["maxima", "alta", "equilibrado", "rapido", "personalizado"]

# Texto mostrado na lista de modelos da interface (Fase 1).
INFO_MODELOS_WHISPER: dict[str, tuple[str, str]] = {
    "tiny": ("muito leve", "só para testes; fraco em pt-BR"),
    "base": ("leve", "rascunho"),
    "small": ("médio", "aceitável"),
    "medium": ("pesado", "boa precisão"),
    "large-v3-turbo": ("médio-pesado", "recomendado; bem mais rápido que o large-v3"),
    "large-v3": ("o mais pesado", "precisão máxima; em CPU pode levar horas"),
}


def raiz_repositorio() -> Path:
    """Pasta do projeto (onde ficam Downloads/, recursos/, modelos/ e Projetos/)."""
    return Path(__file__).resolve().parents[3]


class PresetQualidade(BaseModel):
    nome: str
    preset: str
    crf: int


PRESETS_QUALIDADE: dict[str, PresetQualidade] = {
    "maxima": PresetQualidade(nome="Máxima qualidade", preset="veryslow", crf=16),
    "alta": PresetQualidade(nome="Alta", preset="slow", crf=18),
    "equilibrado": PresetQualidade(nome="Equilibrado", preset="medium", crf=20),
    "rapido": PresetQualidade(nome="Rápido", preset="veryfast", crf=23),
}

PRESETS_X264 = (
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
)  # fmt: skip


class Pastas(BaseModel):
    entrada: Path = Field(default_factory=lambda: raiz_repositorio() / "Downloads")
    projetos: Path = Field(default_factory=lambda: raiz_repositorio() / "Projetos")
    recursos: Path = Field(default_factory=lambda: raiz_repositorio() / "recursos")
    modelos: Path = Field(default_factory=lambda: raiz_repositorio() / "modelos")

    @property
    def introducao(self) -> Path:
        return self.recursos / "introducao.mp4"

    @property
    def vinheta_final(self) -> Path:
        return self.recursos / "vinheta_final.mp4"

    @property
    def vocabulario(self) -> Path:
        return self.recursos / "vocabulario_teologico.txt"

    @property
    def fontes(self) -> Path:
        return self.recursos / "fontes"


class OpcoesProcessamento(BaseModel):
    """Tudo o que o usuário escolhe para um processamento. Fica gravado no estado.json."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # Produtos e modificadores
    gerar_video_longo: bool = True
    gerar_shorts: bool = False
    legendas_dinamicas: bool = False
    limpar_audio: bool = False
    normalizar_volume: bool = False
    introducao: bool = False
    vinheta_final: bool = False

    # Recursos
    uso_cpu_percentual: Literal[75, 100] = 75
    whisper_modelo: ModeloWhisper = "large-v3-turbo"
    ollama_modelo: str = "qwen3:8b"

    # Silêncios
    limiar_pausa_s: float = Field(10.0, ge=0.5, le=30.0)
    pausa_residual_s: float = Field(1.0, ge=0.0, le=5.0)

    # Pontos de revisão humana
    pausar_revisar_transcricao: bool = True
    pausar_aprovar_cortes: bool = True

    # Legendas do vídeo longo (.srt)
    srt_max_linhas: int = Field(2, ge=1, le=3)
    srt_max_caracteres_por_linha: int = Field(42, ge=20, le=80)

    # Shorts
    shorts_duracao_min_s: float = Field(30.0, ge=10.0, le=60.0)
    shorts_duracao_max_s: float = Field(60.0, ge=10.0, le=60.0)
    shorts_limite_seguranca: int = Field(20, ge=1, le=100)
    shorts_pausa_interna_max_s: float = Field(3.0, ge=0.5, le=30.0)
    legenda_posicao_vertical: float = Field(0.62, ge=0.3, le=0.9)
    recorte_deslocamento_x_pct: float = Field(0.0, ge=-30.0, le=30.0)

    # Áudio
    lufs_video_longo: float = Field(-16.0, ge=-30.0, le=-5.0)
    lufs_shorts: float = Field(-14.0, ge=-30.0, le=-5.0)

    # Qualidade do encode
    qualidade: Qualidade = "maxima"
    x264_preset_personalizado: str = "medium"
    crf_personalizado: int = Field(20, ge=0, le=51)
    usar_nvenc: bool = False

    manter_intermediarios: bool = False

    @model_validator(mode="after")
    def _duracao_coerente(self) -> OpcoesProcessamento:
        if self.shorts_duracao_min_s > self.shorts_duracao_max_s:
            raise ValueError("A duração mínima dos Shorts não pode passar da máxima.")
        if self.x264_preset_personalizado not in PRESETS_X264:
            raise ValueError(f"Preset x264 inválido: {self.x264_preset_personalizado}")
        return self

    def preset_qualidade(self) -> PresetQualidade:
        if self.qualidade == "personalizado":
            return PresetQualidade(
                nome="Personalizado",
                preset=self.x264_preset_personalizado,
                crf=self.crf_personalizado,
            )
        return PRESETS_QUALIDADE[self.qualidade]

    def problemas(self) -> list[str]:
        """Combinações de opções que a interface não deve deixar iniciar."""
        achados = []
        if not (self.gerar_video_longo or self.gerar_shorts):
            achados.append("Marque ao menos um produto: vídeo longo ou Shorts.")
        if self.legendas_dinamicas and not self.gerar_shorts:
            achados.append("As legendas dinâmicas só existem junto com os Shorts.")
        if (self.introducao or self.vinheta_final) and not self.gerar_video_longo:
            achados.append("Introdução e vinheta final só valem para o vídeo longo.")
        return achados

    def validar_para_execucao(self) -> None:
        achados = self.problemas()
        if achados:
            raise ErroConfiguracao(" ".join(achados))

    def assinatura(self, campos: Iterable[str]) -> str:
        """Hash curto dos campos indicados; detecta mudança de opção entre execuções."""
        dados = self.model_dump(mode="json")
        recorte = {campo: dados[campo] for campo in sorted(campos)}
        bruto = json.dumps(recorte, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:12]


class ConfigApp(BaseModel):
    """Preferências do usuário, guardadas fora do repositório."""

    model_config = ConfigDict(extra="ignore")

    pastas: Pastas = Field(default_factory=Pastas)
    padroes: OpcoesProcessamento = Field(default_factory=OpcoesProcessamento)


def caminho_config() -> Path:
    return platformdirs.user_config_path(NOME_APP, appauthor=False) / "config.toml"


def carregar_config(caminho: Path | None = None) -> ConfigApp:
    caminho = Path(caminho) if caminho else caminho_config()
    if not caminho.exists():
        return ConfigApp()
    try:
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
        return ConfigApp.model_validate(dados)
    except (tomllib.TOMLDecodeError, ValidationError) as erro:
        raise ErroConfiguracao(f"Configuração inválida em {caminho}: {erro}") from erro


def salvar_config(config: ConfigApp, caminho: Path | None = None) -> Path:
    caminho = Path(caminho) if caminho else caminho_config()
    escrever_texto_atomico(caminho, tomli_w.dumps(config.model_dump(mode="json")))
    return caminho
