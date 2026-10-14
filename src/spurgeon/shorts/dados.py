"""Estruturas da seleção de Shorts: frase, candidato, avaliação da IA e corte."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORIAS = ("pergunta_gancho", "citacao_biblica", "aplicacao_pratica", "conclusao", "outra")
ROTULOS_CATEGORIA = {
    "pergunta_gancho": "Pergunta-gancho",
    "citacao_biblica": "Citação bíblica",
    "aplicacao_pratica": "Aplicação prática",
    "conclusao": "Conclusão",
    "outra": "Outra",
}


@dataclass(frozen=True)
class Frase:
    """Uma frase do sermão, com os índices (inclusivos) das palavras que a formam."""

    primeira: int
    ultima: int
    inicio: float
    fim: float
    texto: str
    pergunta: bool = False
    abre_dependente: bool = False  # começa com "mas", "então", "isso"…: precisa do que veio antes


@dataclass
class Candidato:
    """Uma janela de frases inteiras de 30 a 60 s que poderia virar um Short."""

    id: int
    primeira_frase: int
    ultima_frase: int
    inicio: float  # da primeira palavra
    fim: float  # da última palavra
    texto: str
    pre_nota: float
    sinais: dict[str, Any] = field(default_factory=dict)

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> Candidato:
        return cls(**d)


@dataclass
class Avaliacao:
    """O que o LLM achou de um candidato."""

    categoria: str
    nota: int  # 0 a 10: potencial de retenção
    autossuficiente: bool
    alerta_contexto: str = ""  # vazio se o trecho não oferece risco de ser mal interpretado
    motivo: str = ""

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> Avaliacao:
        return cls(**d)


@dataclass
class Corte:
    """Um Short escolhido, com a nota de qualidade e os metadados para publicar."""

    id: int  # id do candidato de origem
    inicio: float  # já com a folga de respiro antes da primeira palavra
    fim: float  # já com a folga depois da última palavra
    texto: str
    categoria: str
    nota: float  # 0 a 100
    detalhe: dict[str, float]  # de onde veio cada ponto da nota
    alerta_contexto: str = ""
    motivo: str = ""
    titulo: str = ""
    descricao: str = ""
    hashtags: list[str] = field(default_factory=list)
    slug: str = ""
    incluir: bool = True
    reserva: bool = False  # sugerido além do limite; só entra se a pessoa marcar
    editado: bool = False  # a pessoa mexeu no início ou no fim; a nota é a do corte original
    #: a legenda corrigida pela pessoa (todas as palavras do corte); vazia = a da transcrição
    legenda: str = ""

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> Corte:
        return cls(**d)
