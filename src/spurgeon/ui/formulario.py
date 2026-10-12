"""Lógica do formulário, sem widgets: transforma o que o usuário digitou em opções válidas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from spurgeon.core.config import (
    INFO_MODELOS_WHISPER,
    PRESETS_QUALIDADE,
    OpcoesProcessamento,
)

EXTENSOES_VIDEO = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mts")
ORDEM_WHISPER = ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3")
ROTULO_USO_75 = "75% (recomendado)"
ROTULO_USO_100 = "100% (máximo; o PC fica praticamente inutilizável)"
_LIMITE_PAUSA = (0.5, 30.0)
_LIMITE_RESIDUAL = (0.0, 5.0)
_LIMITE_SHORT_S = (10.0, 60.0)
_LIMITE_QUANTIDADE = (1, 100)
_LIMITE_DESLOCAMENTO = (-30.0, 30.0)
_LIMITE_POSICAO = (30.0, 90.0)


class ErroFormulario(Exception):
    """Um ou mais campos inválidos; `mensagens` está pronta para mostrar ao usuário."""

    def __init__(self, mensagens: list[str]):
        super().__init__("\n".join(mensagens))
        self.mensagens = mensagens


def rotulos_whisper(recomendado: str | None = None) -> dict[str, str]:
    """Rótulo mostrado na lista → nome do modelo, do mais leve ao mais pesado."""
    rotulos = {}
    for nome in ORDEM_WHISPER:
        peso, _ = INFO_MODELOS_WHISPER[nome]
        rotulo = f"{nome} · {peso}"
        if nome == recomendado:
            rotulo += " · recomendado para este PC"
        rotulos[rotulo] = nome
    return rotulos


def rotulos_qualidade() -> dict[str, str]:
    """Rótulo mostrado na lista → chave do preset."""
    rotulos = {
        f"{p.nome} ({p.preset}, CRF {p.crf})": chave for chave, p in PRESETS_QUALIDADE.items()
    }
    rotulos["Personalizado"] = "personalizado"
    return rotulos


@dataclass
class ValoresFormulario:
    """O que está na tela, ainda como texto onde o usuário digita."""

    # Processamento
    uso_cpu: int = 75
    whisper_modelo: str = "large-v3-turbo"
    qualidade: str = "maxima"
    # O que gerar
    limpar_audio: bool = False
    normalizar_volume: bool = False
    gerar_video_longo: bool = True
    limiar_pausa: str = "10,0"
    introducao: bool = False
    vinheta_final: bool = False
    gerar_shorts: bool = False
    legendas_dinamicas: bool = False
    # Pontos de revisão
    revisar_transcricao: bool = True
    aprovar_cortes: bool = True
    # Avançado
    pausa_residual: str = "1,0"
    shorts_min: str = "30"
    shorts_max: str = "60"
    limite_shorts: str = "20"
    ollama_modelo: str = "qwen3:8b"
    deslocamento_x: str = "0"
    posicao_legenda: str = "62"
    x264_preset: str = "medium"
    crf: str = "20"
    manter_intermediarios: bool = False

    @classmethod
    def de_opcoes(cls, o: OpcoesProcessamento) -> ValoresFormulario:
        return cls(
            uso_cpu=o.uso_cpu_percentual,
            whisper_modelo=o.whisper_modelo,
            qualidade=o.qualidade,
            limpar_audio=o.limpar_audio,
            normalizar_volume=o.normalizar_volume,
            gerar_video_longo=o.gerar_video_longo,
            limiar_pausa=_texto_decimal(o.limiar_pausa_s),
            introducao=o.introducao,
            vinheta_final=o.vinheta_final,
            gerar_shorts=o.gerar_shorts,
            legendas_dinamicas=o.legendas_dinamicas,
            revisar_transcricao=o.pausar_revisar_transcricao,
            aprovar_cortes=o.pausar_aprovar_cortes,
            pausa_residual=_texto_decimal(o.pausa_residual_s),
            shorts_min=_texto(o.shorts_duracao_min_s),
            shorts_max=_texto(o.shorts_duracao_max_s),
            limite_shorts=str(o.shorts_limite_seguranca),
            ollama_modelo=o.ollama_modelo,
            deslocamento_x=_texto(o.recorte_deslocamento_x_pct),
            posicao_legenda=_texto(round(o.legenda_posicao_vertical * 100, 1)),
            x264_preset=o.x264_preset_personalizado,
            crf=str(o.crf_personalizado),
            manter_intermediarios=o.manter_intermediarios,
        )


@dataclass(frozen=True)
class Habilitacoes:
    """O que fica ativo ou apagado na tela conforme as escolhas feitas."""

    limiar_pausa: bool
    introducao: bool
    vinheta_final: bool
    legendas_dinamicas: bool
    aprovar_cortes: bool
    campos_shorts: bool
    qualidade_personalizada: bool
    pode_iniciar: bool


def calcular_habilitacoes(
    v: ValoresFormulario, *, introducao_existe: bool, vinheta_existe: bool
) -> Habilitacoes:
    return Habilitacoes(
        limiar_pausa=v.gerar_video_longo,
        introducao=v.gerar_video_longo and introducao_existe,
        vinheta_final=v.gerar_video_longo and vinheta_existe,
        legendas_dinamicas=v.gerar_shorts,
        aprovar_cortes=v.gerar_shorts,
        campos_shorts=v.gerar_shorts,
        qualidade_personalizada=v.qualidade == "personalizado",
        pode_iniciar=v.gerar_video_longo or v.gerar_shorts,
    )


def para_opcoes(
    v: ValoresFormulario, base: OpcoesProcessamento | None = None
) -> OpcoesProcessamento:
    """Valida o formulário e devolve as opções. `base` guarda o que a tela não mostra."""
    erros: list[str] = []
    mudancas: dict = {
        "uso_cpu_percentual": v.uso_cpu,
        "whisper_modelo": v.whisper_modelo,
        "qualidade": v.qualidade,
        "limpar_audio": v.limpar_audio,
        "normalizar_volume": v.normalizar_volume,
        "gerar_video_longo": v.gerar_video_longo,
        "introducao": v.introducao and v.gerar_video_longo,
        "vinheta_final": v.vinheta_final and v.gerar_video_longo,
        "gerar_shorts": v.gerar_shorts,
        "legendas_dinamicas": v.legendas_dinamicas and v.gerar_shorts,
        "pausar_revisar_transcricao": v.revisar_transcricao,
        "pausar_aprovar_cortes": v.aprovar_cortes,
        "ollama_modelo": v.ollama_modelo.strip() or "qwen3:8b",
        "manter_intermediarios": v.manter_intermediarios,
    }

    def decimal(texto, rotulo, faixa):
        return _numero(texto, rotulo, faixa, erros, inteiro=False)

    if v.gerar_video_longo:
        mudancas["limiar_pausa_s"] = decimal(
            v.limiar_pausa, "Tamanho mínimo da pausa", _LIMITE_PAUSA
        )
    mudancas["pausa_residual_s"] = decimal(v.pausa_residual, "Pausa residual", _LIMITE_RESIDUAL)
    mudancas["recorte_deslocamento_x_pct"] = decimal(
        v.deslocamento_x, "Deslocamento do recorte", _LIMITE_DESLOCAMENTO
    )
    posicao = decimal(v.posicao_legenda, "Posição da legenda", _LIMITE_POSICAO)
    mudancas["legenda_posicao_vertical"] = None if posicao is None else posicao / 100
    if v.gerar_shorts:
        mudancas["shorts_duracao_min_s"] = decimal(
            v.shorts_min, "Duração mínima do Short", _LIMITE_SHORT_S
        )
        mudancas["shorts_duracao_max_s"] = decimal(
            v.shorts_max, "Duração máxima do Short", _LIMITE_SHORT_S
        )
        limite = _numero(
            v.limite_shorts, "Limite de Shorts", _LIMITE_QUANTIDADE, erros, inteiro=True
        )
        mudancas["shorts_limite_seguranca"] = limite
    if v.qualidade == "personalizado":
        mudancas["x264_preset_personalizado"] = v.x264_preset
        mudancas["crf_personalizado"] = _numero(v.crf, "CRF", (0, 51), erros, inteiro=True)

    if erros:
        raise ErroFormulario(erros)
    dados = (base or OpcoesProcessamento()).model_dump()
    dados.update({k: x for k, x in mudancas.items() if x is not None})
    try:
        opcoes = OpcoesProcessamento.model_validate(dados)
    except ValidationError as erro:
        raise ErroFormulario([_mensagem_pydantic(e) for e in erro.errors()]) from erro
    if problemas := opcoes.problemas():
        raise ErroFormulario(problemas)
    return opcoes


def sugerir_nome(caminho: Path) -> str:
    """Nome sugerido pelo arquivo: `sermao_graca_soberana.mp4` → `sermao graca soberana`.

    Só o sublinhado vira espaço: o hífen pode ser parte do nome (`Miqueias 2. 6-11`).
    """
    return re.sub(r"\s+", " ", re.sub(r"_+", " ", Path(caminho).stem)).strip()


def formatar_data(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def ler_data(texto: str) -> date:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError as erro:
        raise ErroFormulario(
            ["Data da gravação: use o formato dd/mm/aaaa (ex.: 12/10/2026)."]
        ) from erro


def aviso_whisper(modelo: str, *, cuda_utilizavel: bool) -> str | None:
    if modelo in ("tiny", "base"):
        return "Qualidade baixa em pt-BR; use só para testar."
    if cuda_utilizavel:
        return None
    if modelo == "large-v3":
        return "Em CPU o large-v3 pode levar horas por sermão. O large-v3-turbo é bem mais rápido."
    if modelo == "medium":
        return "Em CPU o medium é pesado e pode levar mais que a duração do vídeo."
    return None


def aviso_qualidade(qualidade: str, *, nvenc: bool) -> str | None:
    if qualidade == "maxima" and not nvenc:
        return (
            "Em CPU a máxima qualidade é muito lenta e pode levar várias vezes a duração "
            "do vídeo. Para testar, use Equilibrado ou Rápido."
        )
    if qualidade == "rapido":
        return "Mais rápido, com arquivo maior e um pouco menos de qualidade."
    return None


def _texto(valor: float) -> str:
    """Número no formato do usuário: vírgula decimal, sem zeros desnecessários (30,0 vira 30)."""
    return f"{valor:g}".replace(".", ",") if valor != int(valor) else f"{int(valor)}"


def _texto_decimal(valor: float) -> str:
    """Campos de segundos mostram sempre uma casa: 10,0 e 0,5."""
    return f"{valor:.1f}".replace(".", ",")


def _numero(texto, rotulo, faixa, erros, *, inteiro):
    minimo, maximo = faixa
    try:
        bruto = texto.strip().replace(",", ".")
        valor = int(bruto) if inteiro else float(bruto)
    except ValueError:
        erros.append(f"{rotulo}: digite um número (ex.: {'20' if inteiro else '10,5'}).")
        return None
    if not minimo <= valor <= maximo:
        erros.append(f"{rotulo}: use um valor entre {_texto(minimo)} e {_texto(maximo)}.")
        return None
    return valor


def _mensagem_pydantic(erro: dict) -> str:
    campo = ".".join(str(p) for p in erro.get("loc", ()))
    mensagem = erro.get("msg", "valor inválido").removeprefix("Value error, ")
    return f"{campo}: {mensagem}" if campo else mensagem
