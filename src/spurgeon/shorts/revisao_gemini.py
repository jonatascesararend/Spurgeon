"""O parecer do Google Gemini sobre um Short: se o corte ficou bom e se título e descrição combinam.

A revisão é pedida Short a Short (no Studio do PC ou no celular) e fica gravada no próprio corte
(`Corte.revisao_gemini`), para a lista mostrar o selo "revisado pelo Gemini". Ela só aconselha:
nada muda no Short até a pessoa usar o título ou a descrição sugeridos.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from spurgeon.shorts import gemini
from spurgeon.shorts.dados import Corte, rotulo_da_categoria
from spurgeon.shorts.etapas import gravar_campos_do_corte

VEREDITOS = ("bom", "ajustar", "ruim")
ROTULO_DO_VEREDITO = {
    "bom": "Bom para postar",
    "ajustar": "Bom, com ajustes",
    "ruim": "Não vale postar",
}
LIMITE_DO_TITULO = 100
_MINIMO_DE_PALAVRAS = 8

SISTEMA = (
    "Você é um editor experiente de vídeos curtos (Shorts, Reels) de um canal de sermões da "
    "teologia reformada (calvinista). Você revisa o trabalho de outra IA, que cortou o trecho do "
    "sermão e escreveu o título e a descrição. Seja franco e específico, sem elogios vazios. "
    "Nunca proponha nada que traia o que o pregador quis dizer ou que soe sensacionalista. "
    "Responda em português do Brasil, só com o JSON pedido."
)

ESQUEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "veredito": {"type": "STRING", "enum": list(VEREDITOS)},
        "nota": {"type": "INTEGER", "description": "0 a 10: quanto o Short vale ser postado"},
        "comentario": {
            "type": "STRING",
            "description": "2 a 4 frases: o que funciona e o que atrapalha no corte",
        },
        "corte_completo": {
            "type": "BOOLEAN",
            "description": "a ideia começa e termina dentro do trecho, sem depender do resto",
        },
        "titulo_combina": {"type": "BOOLEAN"},
        "descricao_combina": {"type": "BOOLEAN"},
        "titulo_sugerido": {
            "type": "STRING",
            "description": "um título melhor; vazio se o atual já está bom",
        },
        "descricao_sugerida": {
            "type": "STRING",
            "description": "uma descrição melhor; vazia se a atual já está boa",
        },
    },
    "required": [
        "veredito",
        "nota",
        "comentario",
        "corte_completo",
        "titulo_combina",
        "descricao_combina",
        "titulo_sugerido",
        "descricao_sugerida",
    ],
    "propertyOrdering": [
        "comentario",
        "corte_completo",
        "titulo_combina",
        "descricao_combina",
        "veredito",
        "nota",
        "titulo_sugerido",
        "descricao_sugerida",
    ],
}


class TextoCurto(ValueError):
    """O Short não tem texto bastante para o Gemini avaliar."""


@dataclass(frozen=True)
class RevisaoDoGemini:
    veredito: str
    nota: int
    comentario: str
    corte_completo: bool
    titulo_combina: bool
    descricao_combina: bool
    titulo_sugerido: str
    descricao_sugerida: str
    modelo: str
    quando: str  # AAAA-MM-DDTHH:MM, hora do computador
    #: o título e a descrição que o Gemini leu (se a pessoa mudar depois, a tela avisa)
    titulo_revisado: str = ""
    descricao_revisada: str = ""

    @property
    def rotulo(self) -> str:
        return ROTULO_DO_VEREDITO.get(self.veredito, self.veredito)

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> RevisaoDoGemini | None:
        """`None` para o corte sem revisão (ou com uma que não se lê)."""
        if not isinstance(dados, dict) or not dados.get("veredito"):
            return None
        nomes = cls.__dataclass_fields__
        try:
            return cls(**{k: v for k, v in dados.items() if k in nomes})
        except TypeError:
            return None

    def desatualizada(self, corte: Corte) -> bool:
        """O título ou a descrição mudaram depois da revisão (usar a sugestão não conta)."""
        titulo, descricao = corte.titulo.strip(), corte.descricao.strip()
        return titulo not in {self.titulo_revisado.strip(), self.titulo_sugerido.strip()} or (
            descricao not in {self.descricao_revisada.strip(), self.descricao_sugerida.strip()}
        )


def montar_pedido(corte: Corte, texto: str, sermao: str) -> str:
    duracao = round(corte.duracao)
    return "\n".join([
        f"Sermão: {sermao or '(sem nome)'}",
        f"Duração do Short: {duracao} segundos",
        f"Categoria que a primeira IA deu: {rotulo_da_categoria(corte.categoria)}",
        f"Título atual: {corte.titulo or '(sem título)'}",
        f"Descrição atual: {corte.descricao or '(sem descrição)'}",
        "",
        "O que o pregador diz no Short (a legenda inteira):",
        f'"""{texto}"""',
        "",
        "Avalie:",
        "1. O corte: prende a atenção no começo, a ideia fica completa e compreensível sozinha "
        "(sem frase cortada no meio ou referência a algo de fora do trecho) e termina bem?",
        "2. O título combina com o que é dito, é fiel ao pregador e dá vontade de assistir? "
        f"Até {LIMITE_DO_TITULO} caracteres, sem caixa alta gritando e sem clickbait.",
        "3. A descrição resume com fidelidade o que o Short diz?",
        "veredito: bom (pode postar como está), ajustar (vale postar depois de mudar algo) ou "
        "ruim (o trecho não se sustenta como Short).",
        "Só sugira título ou descrição novos se forem claramente melhores; senão deixe vazio.",
    ])  # fmt: skip


def _texto(valor: Any, limite: int | None = None) -> str:
    texto = " ".join(str(valor or "").split())
    return texto[:limite].rstrip() if limite else texto


def interpretar(bruto: str, corte: Corte, *, modelo: str, quando: datetime) -> RevisaoDoGemini:
    try:
        dados = json.loads(bruto)
    except ValueError as erro:
        raise gemini.ErroDoGemini("O Gemini respondeu fora do formato combinado.") from erro
    if not isinstance(dados, dict):
        raise gemini.ErroDoGemini("O Gemini respondeu fora do formato combinado.")
    veredito = str(dados.get("veredito", "")).strip().lower()
    if veredito not in VEREDITOS:
        raise gemini.ErroDoGemini(f"O Gemini deu um veredito que não existe: “{veredito}”.")
    try:
        nota = max(0, min(10, round(float(dados.get("nota", 0)))))
    except (TypeError, ValueError):
        nota = 0
    titulo = _texto(dados.get("titulo_sugerido"), LIMITE_DO_TITULO)
    descricao = str(dados.get("descricao_sugerida") or "").strip()
    return RevisaoDoGemini(
        veredito=veredito,
        nota=nota,
        comentario=str(dados.get("comentario") or "").strip(),
        corte_completo=bool(dados.get("corte_completo")),
        titulo_combina=bool(dados.get("titulo_combina")),
        descricao_combina=bool(dados.get("descricao_combina")),
        titulo_sugerido="" if titulo == corte.titulo.strip() else titulo,
        descricao_sugerida="" if descricao == corte.descricao.strip() else descricao,
        modelo=modelo,
        quando=quando.strftime("%Y-%m-%dT%H:%M"),
        titulo_revisado=corte.titulo,
        descricao_revisada=corte.descricao,
    )


def revisar(
    config: gemini.Configuracao,
    corte: Corte,
    texto: str,
    *,
    sermao: str = "",
    agora: datetime | None = None,
) -> RevisaoDoGemini:
    """Pede o parecer do Gemini sobre o Short (`texto` é a legenda que ele tem agora)."""
    texto = " ".join(texto.split())
    if len(texto.split()) < _MINIMO_DE_PALAVRAS:
        raise TextoCurto("O Short tem pouco texto para o Gemini avaliar.")
    bruto, modelo = gemini.responder(
        config, montar_pedido(corte, texto, sermao), sistema=SISTEMA, esquema=ESQUEMA
    )
    return interpretar(bruto, corte, modelo=modelo, quando=agora or datetime.now())


def gravar(caminho: Path, id_do_corte: int, revisao: RevisaoDoGemini | None) -> bool:
    """Grava só a revisão no corte (na hora, sem levar o resto da edição). `None` apaga."""
    return gravar_campos_do_corte(
        caminho, id_do_corte, revisao_gemini=revisao.para_dict() if revisao else {}
    )


def quando_legivel(quando: str) -> str:
    """`2026-11-23T10:30` → `23/11 às 10:30` (o texto como veio, se não se lê)."""
    try:
        return f"{datetime.fromisoformat(quando):%d/%m às %H:%M}"
    except ValueError:
        return quando


def da_revisao(corte: Corte) -> RevisaoDoGemini | None:
    return RevisaoDoGemini.de_dict(corte.revisao_gemini)
