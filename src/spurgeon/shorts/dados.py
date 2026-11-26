"""Estruturas da seleção de Shorts: frase, candidato, avaliação da IA e corte."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: As categorias que a IA dá a cada trecho. A ordem é fixa: as quatro primeiras são as da versão
#: antiga (cortes e avaliações já gravados usam essas chaves e continuam valendo), depois vêm as
#: de conteúdo e `outra` por último. As chaves nunca mudam de nome: ficam gravadas nos projetos.
CATEGORIAS = (
    "pergunta_gancho",
    "citacao_biblica",
    "aplicacao_pratica",
    "conclusao",
    "confronto_de_pecados",
    "exortacao",
    "apontando_cristo",
    "necessidade_de_arrependimento",
    "doutrina_biblica",
    "doutrina_reformada",
    "fato_historico",
    "oracao",
    "profecia",
    "promessa",
    "anuncio_de_salvacao",
    "esperanca",
    "outra",
)
ROTULOS_CATEGORIA = {
    "pergunta_gancho": "Pergunta-gancho",
    "citacao_biblica": "Citação bíblica",
    "aplicacao_pratica": "Aplicação prática",
    "conclusao": "Conclusão",
    "confronto_de_pecados": "Confronto de pecados",
    "exortacao": "Exortação",
    "apontando_cristo": "Apontando Cristo",
    "necessidade_de_arrependimento": "Necessidade de arrependimento",
    "doutrina_biblica": "Doutrina bíblica",
    "doutrina_reformada": "Doutrina reformada",
    "fato_historico": "Fato histórico",
    "oracao": "Oração",
    "profecia": "Profecia",
    "promessa": "Promessa",
    "anuncio_de_salvacao": "Anúncio de salvação",
    "esperanca": "Esperança",
    "outra": "Outra",
}
#: Uma cor única por categoria, para a etiqueta (texto branco por cima, num tema escuro). Todas têm
#: contraste de pelo menos 3:1 com o branco. Matizes por afinidade: pecado e arrependimento em
#: vermelhos, rosas e laranjas; Cristo e a salvação em dourado e verdes; doutrina em azuis; oração e
#: profecia em violetas; esperança em ciano. `outra` é o cinza-azulado neutro.
COR_DA_CATEGORIA = {
    "pergunta_gancho": "#0288d1",
    "citacao_biblica": "#00796b",
    "aplicacao_pratica": "#6b8e23",
    "conclusao": "#7b1f3f",
    "confronto_de_pecados": "#c62828",
    "exortacao": "#e65100",
    "apontando_cristo": "#b8860b",
    "necessidade_de_arrependimento": "#d81b78",
    "doutrina_biblica": "#1565c0",
    "doutrina_reformada": "#3949ab",
    "fato_historico": "#8d5524",
    "oracao": "#a21caf",
    "profecia": "#7e57c2",
    "promessa": "#00a06a",
    "anuncio_de_salvacao": "#2e7d32",
    "esperanca": "#0097a7",
    "outra": "#546e7a",
}


def cor_da_categoria(chave: str) -> str:
    """A cor (`#rrggbb`) da etiqueta da categoria; categoria desconhecida usa a de `outra`."""
    return COR_DA_CATEGORIA.get(chave, COR_DA_CATEGORIA["outra"])


def rotulo_da_categoria(chave: str) -> str:
    """O nome da categoria para a tela; categoria desconhecida mostra a própria chave."""
    return ROTULOS_CATEGORIA.get(chave) or chave[:1].upper() + chave[1:]


#: de onde veio um corte: a análise automática ou o "Shorts livre" do Studio
ORIGEM_AUTOMATICA = "automatico"
ORIGEM_MANUAL = "manual"
ROTULOS_ORIGEM = {
    ORIGEM_AUTOMATICA: "gerado automaticamente",
    ORIGEM_MANUAL: "gerado manualmente",
}
#: a opinião da pessoa sobre um Short (👍 / 👎); vazio = nenhuma
OPINIAO_BOA = "bom"
OPINIAO_RUIM = "ruim"
OPINIOES = (OPINIAO_BOA, OPINIAO_RUIM)

#: os cortes manuais começam aqui, longe dos ids dos candidatos (numerados de 0 em diante)
ID_BASE_DOS_MANUAIS = 1_000_000


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
    """Uma janela de frases inteiras de 30 a 180 s que poderia virar um Short."""

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
    #: `automatico` (achado pela análise) ou `manual` (escolhido a dedo no Shorts livre)
    origem: str = ORIGEM_AUTOMATICA
    #: pedaços tirados de dentro do corte, `[[de, ate], …]`, em segundos do vídeo original e em
    #: ordem: o Short vai até `de` e continua em `ate` (cortar uma palavra ou uma frase)
    saltos: list[list[float]] = field(default_factory=list)
    #: encurta as pausas da fala deste Short a partir deste tamanho, em segundos (de 0,1 a 10);
    #: 0 = não corta pausa nenhuma
    pausa_s: float = 0.0
    #: a pessoa já publicou este Short em alguma rede (para se organizar); o arquivo renderizado
    #: vai para `shorts_gerados/postados/`. `postado_em` é quando foi ao ar (`AAAA-MM-DDTHH:MM`;
    #: os projetos de antes têm só a data, `AAAA-MM-DD`: `shorts/agendamento.ler_postado_em`)
    postado: bool = False
    postado_em: str = ""
    #: a previsão de quando o Short vai ao ar (`AAAA-MM-DDTHH:MM`, hora do computador; vazio = sem
    #: agendamento). O arquivo renderizado vai para `shorts_gerados/agendados/`; passada a hora, o
    #: Short vira "já postado" sozinho e segue para `postados/` (`shorts/agendamento.py`)
    agendado_para: str = ""
    #: o que a pessoa achou do Short, para lembrar depois: `bom` (👍), `ruim` (👎) ou vazio. Só
    #: um lembrete: não muda o render nem a nota
    opiniao: str = ""
    #: o parecer do Google Gemini sobre o Short (`shorts/revisao_gemini.RevisaoDoGemini` em
    #: dicionário); vazio = não revisado. Só aconselha: não muda o render nem a nota
    revisao_gemini: dict[str, Any] = field(default_factory=dict)
    #: o ajuste fino da legenda, palavra por palavra (`shorts/ajuste_fino.py`): `{"trechos": [[de,
    #: ate], …], "palavras": [[texto, início, fim], …]}`, em segundos do vídeo original; vazio = as
    #: palavras e os tempos da transcrição
    ajuste_fino: dict[str, Any] = field(default_factory=dict)

    @property
    def janela_s(self) -> float:
        """O trecho lido do vídeo original, de ponta a ponta (com o que os saltos tiram)."""
        return self.fim - self.inicio

    @property
    def duracao(self) -> float:
        """Quanto o Short dura: o trecho lido menos o que os saltos tiram (sem cortar pausas)."""
        return self.fim - self.inicio - sum(max(0.0, ate - de) for de, ate in self.saltos)

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> Corte:
        return cls(**d)
