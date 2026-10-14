"""Configuração do aplicativo (TOML do usuário) e opções de cada processamento."""

from __future__ import annotations

import contextlib
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
#: o que o processamento faz: tudo; só os Shorts (aproveitando o que já foi feito); ou só juntar
#: introdução e vinheta final a um vídeo longo que já está pronto
Modo = Literal["completo", "so_shorts", "so_vinhetas"]
CantoMarca = Literal["sup_esq", "sup_dir", "inf_esq", "inf_dir"]

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

    @property
    def dicionario_pt_br(self) -> Path:
        """Prefixo dos arquivos Hunspell (`pt_BR.aff` e `pt_BR.dic`) do verificador de português."""
        return self.recursos / "dicionarios" / "pt_BR" / "pt_BR"

    def garantir(self) -> None:
        """Cria as pastas do aplicativo que ainda não existem (entrada, projetos, recursos).

        Falhas de permissão ficam para a pré-verificação, que explica o problema.
        """
        for pasta in (self.entrada, self.projetos, self.recursos, self.fontes):
            with contextlib.suppress(OSError):
                pasta.mkdir(parents=True, exist_ok=True)


class OpcoesProcessamento(BaseModel):
    """Tudo o que o usuário escolhe para um processamento. Fica gravado no estado.json."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    modo: Modo = "completo"

    # Produtos e modificadores
    gerar_video_longo: bool = True
    gerar_shorts: bool = False
    legendas_dinamicas: bool = False
    limpar_audio: bool = False
    normalizar_volume: bool = False
    introducao: bool = False
    vinheta_final: bool = False

    # Marca d'água (logotipo no canto do vídeo longo e/ou dos Shorts)
    marca_dagua: bool = False
    marca_imagem: str = ""
    marca_remover_fundo: bool = False  # só vale para imagem sem transparência própria
    marca_canto: CantoMarca = "inf_dir"
    marca_tamanho_pct: float = Field(15.0, ge=3.0, le=50.0)  # % da largura do vídeo
    marca_no_video_longo: bool = True
    marca_nos_shorts: bool = True

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
    #: abaixo desta nota (0 a 100) um corte vira reserva: aparece na aprovação, mas desmarcado
    shorts_nota_minima: float = Field(45.0, ge=0.0, le=100.0)
    #: quantos candidatos a IA avalia (cada um leva cerca de 1 min na CPU)
    shorts_candidatos_ia: int = Field(40, ge=5, le=200)
    legenda_posicao_vertical: float = Field(0.62, ge=0.3, le=0.9)
    #: deslocamento do recorte 9:16 (% da largura; + para a direita). A câmera fixa do púlpito deixa
    #: o pregador e a cruz a ~54% da largura, então +4% os põe no centro do Short.
    recorte_deslocamento_x_pct: float = Field(4.0, ge=-30.0, le=30.0)

    # Áudio
    lufs_video_longo: float = Field(-16.0, ge=-30.0, le=-5.0)
    lufs_shorts: float = Field(-14.0, ge=-30.0, le=-5.0)

    # Qualidade do encode
    qualidade: Qualidade = "maxima"
    x264_preset_personalizado: str = "medium"
    crf_personalizado: int = Field(20, ge=0, le=51)
    usar_nvenc: bool = False

    manter_intermediarios: bool = False
    #: Durante o processamento, o PC não suspende, não apaga a tela nem bloqueia por inatividade
    #: (as renderizações levam horas e muita gente deixa o processamento rodando de madrugada).
    impedir_bloqueio: bool = True

    @model_validator(mode="after")
    def _duracao_coerente(self) -> OpcoesProcessamento:
        if self.shorts_duracao_min_s > self.shorts_duracao_max_s:
            raise ValueError("A duração mínima dos Shorts não pode passar da máxima.")
        if self.x264_preset_personalizado not in PRESETS_X264:
            raise ValueError(f"Preset x264 inválido: {self.x264_preset_personalizado}")
        return self

    @property
    def so_vinhetas(self) -> bool:
        return self.modo == "so_vinhetas"

    @property
    def processa_video_longo(self) -> bool:
        """Renderiza o vídeo longo (e o que vem antes dele: pausas, cortes e legendas)."""
        return self.modo == "completo" and self.gerar_video_longo

    @property
    def processa_shorts(self) -> bool:
        """Analisa e renderiza os Shorts."""
        return self.modo != "so_vinhetas" and self.gerar_shorts

    @property
    def marca_no_longo(self) -> bool:
        return self.marca_dagua and self.marca_no_video_longo

    @property
    def marca_nos_curtos(self) -> bool:
        return self.marca_dagua and self.marca_nos_shorts

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
        if self.modo == "so_vinhetas":
            if not (self.introducao or self.vinheta_final):
                achados.append("Marque a introdução, a vinheta final ou as duas.")
        elif self.modo == "so_shorts":
            if not self.gerar_shorts:
                achados.append("No modo só Shorts, a geração de Shorts precisa estar ligada.")
        elif not (self.gerar_video_longo or self.gerar_shorts):
            achados.append("Marque ao menos um produto: vídeo longo ou Shorts.")
        if self.legendas_dinamicas and not self.gerar_shorts:
            achados.append("As legendas dinâmicas só existem junto com os Shorts.")
        if (self.introducao or self.vinheta_final) and not (
            self.gerar_video_longo or self.so_vinhetas
        ):
            achados.append("Introdução e vinheta final só valem para o vídeo longo.")
        if self.marca_dagua and not self.marca_imagem.strip():
            achados.append("Escolha a imagem da marca d'água.")
        if self.marca_dagua and not (self.marca_no_video_longo or self.marca_nos_shorts):
            achados.append("Marque onde a marca d'água entra: vídeo longo, Shorts ou os dois.")
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
