"""Short livre: o corte que a pessoa escolhe a dedo no texto do sermão.

A análise automática propõe janelas de frases inteiras; aqui quem manda é a pessoa, que marca da
palavra A à palavra B (em qualquer lugar, até no meio de uma frase) ou pinta só os pedaços que quer
(a frase 1 e a frase 3, sem a 2: o que fica no meio vira um salto, como no Studio). Depois o
sistema faz o mesmo que faz com os cortes automáticos: a IA avalia o trecho (categoria, retenção,
alerta de contexto), a nota sai da mesma conta (`calcular_nota`), as pontas se ajustam ao
silêncio de verdade e a IA cria título, descrição e hashtags. O corte entra no Studio marcado
como `gerado manualmente`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

from spurgeon.core.config import DURACAO_MAXIMA_SHORT_S
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ollama import garantir_servidor
from spurgeon.shorts.analise import RESERVA_DE_FOLGA_S, candidato_do_trecho
from spurgeon.shorts.bordas import Medida, refinar_bordas
from spurgeon.shorts.dados import (
    ID_BASE_DOS_MANUAIS,
    ORIGEM_MANUAL,
    Candidato,
    Corte,
    Frase,
)
from spurgeon.shorts.edicao import DURACAO_MINIMA_S, corte_com_indices, faixas_de
from spurgeon.shorts.llm import ClienteLLM
from spurgeon.shorts.pontuacao import calcular_nota
from spurgeon.shorts.selecao import com_respiro, nome_de_arquivo
from spurgeon.transcricao.formato import Palavra

DURACAO_MAXIMA_LIVRE_S = DURACAO_MAXIMA_SHORT_S - RESERVA_DE_FOLGA_S  # cabe com o respiro
_CONTEXTO_ANTES = 300
_CONTEXTO_DEPOIS = 200
_PALAVRAS_DE_CONTEXTO = 90


@dataclass(frozen=True)
class ContextoDaIA:
    """Como falar com a IA local: o modelo do Ollama e quanto da máquina usar."""

    modelo: str
    threads: int
    usar_gpu: bool


@dataclass(frozen=True)
class Trecho:
    """O que a pessoa marcou: da palavra `primeira` à `ultima` (índices, inclusive).

    `fora` são as palavras de dentro desse intervalo que ela deixou de fora (a frase do meio que
    não quer): o Short pula esses pedaços e continua logo depois.
    """

    primeira: int
    ultima: int
    fora: frozenset[int] = frozenset()

    @classmethod
    def de_indices(cls, indices: Iterable[int]) -> Trecho | None:
        """O trecho que tem exatamente estas palavras (None se não há nenhuma)."""
        conjunto = set(indices)
        if not conjunto:
            return None
        primeira, ultima = min(conjunto), max(conjunto)
        return cls(primeira, ultima, frozenset(set(range(primeira, ultima + 1)) - conjunto))

    @property
    def indices(self) -> list[int]:
        """As palavras que o Short junta, em ordem."""
        return [i for i in range(self.primeira, self.ultima + 1) if i not in self.fora]

    @property
    def pedacos(self) -> list[tuple[int, int]]:
        """As sequências de palavras seguidas (uma só, se nada ficou de fora)."""
        return faixas_de(self.indices)


def duracao_do_trecho(palavras: Sequence[Palavra], trecho: Trecho) -> float:
    """Quanto o Short dura: da primeira à última palavra, sem os pedaços que ficaram de fora."""
    if not trecho.fora:
        return palavras[trecho.ultima].fim - palavras[trecho.primeira].inicio
    return sum(palavras[b].fim - palavras[a].inicio for a, b in trecho.pedacos)


def problema_do_trecho(palavras: Sequence[Palavra], trecho: Trecho | None) -> str | None:
    """Por que este trecho ainda não serve de Short (None se serve)."""
    if trecho is None:
        return "Escolha o começo e o fim do trecho no texto."
    duracao = duracao_do_trecho(palavras, trecho)
    if duracao < DURACAO_MINIMA_S:
        return (
            f"O trecho tem {duracao:.0f} s; um Short precisa de pelo menos {DURACAO_MINIMA_S:g} s."
        )
    if duracao > DURACAO_MAXIMA_LIVRE_S:
        return (
            f"O trecho tem {duracao:.0f} s; o máximo de um Short é {DURACAO_MAXIMA_SHORT_S:g} s "
            "(já contando o respiro nas pontas)."
        )
    return None


def mover_ponta(
    palavras: Sequence[Palavra],
    frases: Sequence[Frase],
    trecho: Trecho,
    ponta: str,
    sentido: int,
    *,
    por_frase: bool,
) -> Trecho:
    """Anda com o começo (`inicio`) ou o fim (`fim`) do trecho: `sentido` +1 vai para a frente no
    tempo e −1 para trás, de palavra em palavra ou de frase em frase. Nunca sai do sermão nem
    deixa o trecho sem palavras."""
    if ponta not in ("inicio", "fim") or sentido not in (-1, 1):
        raise ValueError("ponta deve ser inicio ou fim; sentido, -1 ou +1")
    atual = trecho.primeira if ponta == "inicio" else trecho.ultima
    if por_frase:
        pontos = sorted(
            {f.primeira for f in frases} if ponta == "inicio" else {f.ultima for f in frases}
        )
        alvos = [p for p in pontos if (p > atual if sentido > 0 else p < atual)]
        if not alvos:
            return trecho
        novo = alvos[0] if sentido > 0 else alvos[-1]
    else:
        novo = atual + sentido
    novo = max(0, min(len(palavras) - 1, novo))
    while novo in trecho.fora:  # a ponta cai numa palavra que ficou de fora: vai à próxima
        novo += sentido
    primeira, ultima = (novo, trecho.ultima) if ponta == "inicio" else (trecho.primeira, novo)
    if primeira > ultima or not 0 <= novo < len(palavras):
        return trecho
    return Trecho(primeira, ultima, frozenset(i for i in trecho.fora if primeira < i < ultima))


def proximo_id(cortes: Sequence[Corte]) -> int:
    ids = [c.id for c in cortes if c.id >= ID_BASE_DOS_MANUAIS]
    return max(ids, default=ID_BASE_DOS_MANUAIS - 1) + 1


def preparar_cliente(
    modelo: str, *, threads: int, usar_gpu: bool, manter_na_memoria: str = "30m"
) -> ClienteLLM:
    """O cliente do Ollama, conferindo antes que o servidor responde e que o modelo existe.

    Aqui não se baixa o modelo sozinho (são vários GB): quem não o tem é avisado do que fazer.
    `manter_na_memoria` é quanto o Ollama guarda o modelo carregado depois do último pedido.
    """
    if not garantir_servidor():
        raise ErroFerramenta(
            "O Ollama não está respondendo e não foi possível abri-lo sozinho. Abra o aplicativo "
            "Ollama e tente de novo."
        )
    cliente = ClienteLLM(
        modelo, threads=threads, usar_gpu=usar_gpu, manter_na_memoria=manter_na_memoria
    )
    if not cliente.modelo_instalado():
        raise ErroFerramenta(
            f"O modelo {modelo} ainda não está instalado no Ollama. Ele é baixado na primeira vez "
            f"que um processamento usa a IA, ou rode: ollama pull {modelo}"
        )
    return cliente


def _contexto(palavras: Sequence[Palavra], trecho: Trecho) -> tuple[str, str]:
    antes = " ".join(
        p.texto for p in palavras[max(0, trecho.primeira - _PALAVRAS_DE_CONTEXTO) : trecho.primeira]
    )
    depois = " ".join(
        p.texto for p in palavras[trecho.ultima + 1 : trecho.ultima + 1 + _PALAVRAS_DE_CONTEXTO]
    )
    return antes[-_CONTEXTO_ANTES:], depois[:_CONTEXTO_DEPOIS]


def gerar_corte_livre(
    cliente: ClienteLLM,
    *,
    palavras: Sequence[Palavra],
    frases: Sequence[Frase],
    trecho: Trecho,
    id_: int,
    sermao: str,
    titulos_usados: Sequence[str] = (),
    nomes_usados: Sequence[str] = (),
    niveis: tuple[Sequence[Medida], float] | None = None,
    ao_andamento: Callable[[str], None] | None = None,
    criar_metadados: Callable[..., object] | None = None,
) -> Corte:
    """Faz o corte do trecho escolhido, igual aos automáticos (leva uns 2 minutos na CPU).

    Levanta `ValueError` se o trecho não serve (curto ou longo demais) e `ErroLLM`/`ErroFerramenta`
    se a IA não responde. `criar_metadados` existe para os testes.
    """
    if (problema := problema_do_trecho(palavras, trecho)) is not None:
        raise ValueError(problema)
    if criar_metadados is None:
        from spurgeon.shorts.etapas import metadados_sem_repetir as criar_metadados

    def andamento(texto: str) -> None:
        if ao_andamento is not None:
            ao_andamento(texto)

    candidato: Candidato = candidato_do_trecho(
        id_, palavras, frases, trecho.primeira, trecho.ultima
    )
    sem_os_pulados = candidato  # o que a IA lê e o que dá a nota: só o que o Short fala
    if trecho.fora:
        sem_os_pulados = replace(
            candidato,
            texto=" ".join(palavras[i].texto for i in trecho.indices),
            fim=candidato.inicio + duracao_do_trecho(palavras, trecho),
        )
    antes, depois = _contexto(palavras, trecho)
    andamento("A IA está avaliando o trecho (categoria, retenção e risco de contexto)…")
    avaliacao = cliente.avaliar(antes, sem_os_pulados.texto, depois, sem_os_pulados.duracao)
    nota, detalhe = calcular_nota(sem_os_pulados, avaliacao)
    if niveis is None:
        inicio, fim = com_respiro(candidato)
    else:
        inicio, fim = refinar_bordas(candidato, *niveis, maximo_s=DURACAO_MAXIMA_SHORT_S)
    andamento("A IA está criando o título, a descrição e as hashtags…")
    meta = criar_metadados(
        cliente,
        sermao,
        sem_os_pulados.texto,
        avaliacao.categoria,
        sem_os_pulados.duracao,
        titulos_usados,
    )
    titulo = meta.titulo.strip()
    corte = Corte(
        id=id_,
        inicio=inicio,
        fim=fim,
        texto=sem_os_pulados.texto,
        categoria=avaliacao.categoria,
        nota=nota,
        detalhe=detalhe,
        alerta_contexto=avaliacao.alerta_contexto,
        motivo=avaliacao.motivo,
        titulo=titulo,
        descricao=meta.descricao.strip(),
        hashtags=list(meta.hashtags),
        slug=nome_de_arquivo(meta.slug or titulo, nomes_usados),
        incluir=True,
        reserva=False,
        editado=False,
        origem=ORIGEM_MANUAL,
    )
    if trecho.fora:  # os pedaços de fora viram saltos (as pontas continuam as do silêncio)
        corte = replace(
            corte_com_indices(palavras, corte, trecho.indices),
            inicio=inicio,
            fim=fim,
            editado=False,
        )
    return corte


def resumo_das_pontas(palavras: Sequence[Palavra], trecho: Trecho, quantas: int = 7) -> str:
    """`começa em “…” e termina em “…”`, para a pessoa conferir onde o trecho abre e fecha."""
    comeco = " ".join(p.texto for p in palavras[trecho.primeira : trecho.primeira + quantas])
    fim = " ".join(
        p.texto
        for p in palavras[max(trecho.primeira, trecho.ultima - quantas + 1) : trecho.ultima + 1]
    )
    return re.sub(r"\s+", " ", f"Começa em “{comeco}…” e termina em “…{fim}”.")
