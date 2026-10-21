"""Cliente do Ollama: avalia candidatos a Short e gera os metadados, sempre em JSON validado.

A IA roda 100% no PC (Ollama). A saída é forçada a seguir um esquema JSON (`format=`) e
validada com o pydantic; se mesmo assim vier inválida, tenta de novo. O modo de "raciocínio"
do Qwen3 fica desligado (`think=False`), pois gasta minutos sem melhorar a nota.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator

from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ollama import URL_OLLAMA
from spurgeon.shorts.dados import CATEGORIAS, Avaliacao

_TENTATIVAS = 3
TEMPERATURA_PADRAO = 0.1

SISTEMA = (
    "Você é um editor de vídeos curtos (Shorts) de um canal de sermões da teologia reformada "
    "(calvinista). Seu trabalho é avaliar trechos de sermão e preparar a publicação sem trair o "
    "que o pregador quis dizer. Responda sempre em JSON válido, em português do Brasil."
)

#: Uma definição curta de cada categoria, para a IA escolher a que melhor descreve a ideia central.
#: Sem exemplos de frases ou de títulos (a IA os copiaria). Tem de haver uma para cada categoria
#: de `CATEGORIAS`; o prompt lista todas, na ordem delas.
DEFINICOES_DAS_CATEGORIAS = {
    "pergunta_gancho": "abre com uma pergunta que prende a atenção de quem ouve",
    "citacao_biblica": "gira em torno da leitura ou da explicação de um texto bíblico",
    "aplicacao_pratica": "aplica a verdade à vida de quem ouve, no dia a dia",
    "conclusao": "fecha uma ideia ou o sermão",
    "confronto_de_pecados": "denuncia pecados com franqueza para convencer o ouvinte da sua culpa",
    "exortacao": "chama o ouvinte a agir, obedecer, perseverar ou mudar de conduta",
    "apontando_cristo": "mostra como o texto ou a ideia aponta para Cristo e para a sua obra",
    "necessidade_de_arrependimento": "diz que é preciso se arrepender e voltar-se para Deus",
    "doutrina_biblica": "ensina uma doutrina bíblica a partir da Escritura (Deus, homem, salvação)",
    "doutrina_reformada": "ensina um ponto próprio da teologia reformada (soberania de Deus, "
    "eleição, graça, as solas, confissões)",
    "fato_historico": "narra um acontecimento histórico, bíblico ou da história da igreja",
    "oracao": "é uma oração feita pelo pregador, ou ensina a orar",
    "profecia": "trata de uma profecia: o que Deus anunciou que faria, ou o seu cumprimento",
    "promessa": "declara uma promessa de Deus ao seu povo",
    "anuncio_de_salvacao": "anuncia o evangelho: a salvação em Cristo oferecida a quem crê",
    "esperanca": "consola e anima com a esperança do cristão em meio à dor ou à espera",
    "outra": "use só se nenhuma das anteriores serve",
}
#: o bloco com as definições que entra no prompt (uma categoria por linha)
BLOCO_DE_CATEGORIAS = "\n".join(
    f'  "{chave}": {DEFINICOES_DAS_CATEGORIAS[chave]}' for chave in CATEGORIAS
)

PROMPT_AVALIACAO = """\
CONTEXTO ANTERIOR (só para você entender; não faz parte do corte):
{antes}

TRECHO A AVALIAR (este é o corte, {duracao:.0f} segundos):
{trecho}

CONTEXTO POSTERIOR (só para você entender; não faz parte do corte):
{depois}

Avalie o TRECHO como um possível Short de YouTube (de 30 segundos a 3 minutos; o ideal é por \
volta de 1 minuto, mas uma ideia que precisa de mais tempo pode valer um trecho longo):
- categoria: escolha UMA, a que MELHOR descreve a ideia central do trecho. As opções:
{categorias}
- nota: de 0 a 10, o potencial de prender quem assiste. Tem gancho? Desenvolve uma ideia central \
completa? Termina com sentido? Seja exigente: 10 é raro. Num trecho com mais de 90 segundos a \
ideia precisa se sustentar o tempo todo, sem enrolação nem desvios; se perde o fio, dê nota baixa.
- autossuficiente: true se quem nunca ouviu o resto do sermão entende o trecho; false se ele \
depende de algo dito antes (por exemplo, começa com "isso", "ele" ou "como eu dizia").
- alerta_contexto: deixe "" se o trecho isolado é seguro. Se, fora do sermão, ele pudesse ser \
mal interpretado teologicamente (parecer que o pregador diz o contrário do que quis dizer), \
descreva o risco em uma frase curta.
- motivo: uma frase curta (até 20 palavras) justificando a nota."""

PROMPT_METADADOS = """\
Sermão: {sermao}

TRECHO ({duracao:.0f} segundos, categoria "{categoria}"):
{trecho}

{usados}Crie os dados para publicar este trecho como YouTube Short:
- titulo: até 60 caracteres, em português do Brasil, uma frase natural e completa (uma pergunta \
ou uma afirmação direta) que desperte curiosidade sobre o ASSUNTO ESPECÍFICO deste trecho, com \
palavras que aparecem nele. Fiel ao que o trecho diz, sem exagero, sem CAIXA ALTA e sem aspas. \
Não escreva um título genérico que serviria para qualquer parte do sermão.
- descricao: 1 ou 2 frases (até 220 caracteres) resumindo o trecho, sem inventar nada que não \
esteja nele.
- hashtags: de 4 a 6, em português, cada uma começando com # e sem espaços; inclua #shorts.
- slug: de 2 a 5 palavras minúsculas, sem acentos, separadas por hífen, para o nome do arquivo."""

_BLOCO_USADOS = (
    "Títulos já criados para outros trechos deste sermão (o novo deve ser diferente deles):\n"
    "{titulos}\n\n"
)
_MAXIMO_DE_TITULOS_NO_PROMPT = 12

#: o que a pessoa pode pedir de novo, um por vez, a partir do texto que o Short tem agora
CAMPOS_REFAZIVEIS = ("titulo", "descricao", "hashtags")

PROMPT_CAMPO = """\
Sermão: {sermao}

TRECHO ({duracao:.0f} segundos, categoria "{categoria}"):
{trecho}

{usados}{pedido}"""

_PEDIDOS = {
    "titulo": (
        "Crie o título para publicar este trecho como YouTube Short: até 60 caracteres, em "
        "português do Brasil, uma frase natural e completa (uma pergunta ou uma afirmação direta) "
        "que desperte curiosidade sobre o ASSUNTO ESPECÍFICO deste trecho, com palavras que "
        "aparecem nele. Fiel ao que o trecho diz, sem exagero, sem CAIXA ALTA e sem aspas. Não "
        "escreva um título genérico que serviria para qualquer parte do sermão."
    ),
    "descricao": (
        "Crie a descrição para publicar este trecho como YouTube Short: 1 ou 2 frases (até 220 "
        "caracteres) em português do Brasil resumindo o trecho, sem inventar nada que não esteja "
        "nele."
    ),
    "hashtags": (
        "Crie as hashtags para publicar este trecho como YouTube Short: de 4 a 6, em português, "
        "cada uma começando com # e sem espaços, sobre os assuntos deste trecho; inclua #shorts."
    ),
}
TEMPERATURA_DE_REFAZER = 0.3  # um pouco solta: quem pede de novo quer outra opção


class AvaliacaoLLM(BaseModel):
    categoria: Literal[*CATEGORIAS]  # o esquema enviado ao Ollama lista todas como enum
    nota: int = Field(ge=0, le=10)
    autossuficiente: bool
    alerta_contexto: str = ""
    motivo: str = ""

    def para_avaliacao(self) -> Avaliacao:
        return Avaliacao(
            categoria=self.categoria,
            nota=self.nota,
            autossuficiente=self.autossuficiente,
            alerta_contexto=self.alerta_contexto.strip(),
            motivo=self.motivo.strip(),
        )


def _hashtags_limpas(valores: list[str]) -> list[str]:
    limpas: list[str] = []
    for v in valores:
        texto = "".join(v.split())
        if texto and not texto.startswith("#"):
            texto = "#" + texto
        if len(texto) > 1 and texto not in limpas:
            limpas.append(texto)
    if not limpas:
        raise ValueError("sem hashtags válidas")
    return limpas[:8]


class MetadadosLLM(BaseModel):
    titulo: str = Field(min_length=3)
    descricao: str = Field(min_length=3)
    hashtags: list[str] = Field(min_length=1)
    slug: str = Field(min_length=2)

    @field_validator("hashtags")
    @classmethod
    def _com_cerquilha(cls, valores: list[str]) -> list[str]:
        return _hashtags_limpas(valores)


class TituloLLM(BaseModel):
    titulo: str = Field(min_length=3)


class DescricaoLLM(BaseModel):
    descricao: str = Field(min_length=3)


class HashtagsLLM(BaseModel):
    hashtags: list[str] = Field(min_length=1)

    @field_validator("hashtags")
    @classmethod
    def _com_cerquilha(cls, valores: list[str]) -> list[str]:
        return _hashtags_limpas(valores)


_ESQUEMA_DO_CAMPO: dict[str, type[BaseModel]] = {
    "titulo": TituloLLM,
    "descricao": DescricaoLLM,
    "hashtags": HashtagsLLM,
}


class ErroLLM(ErroFerramenta):
    """A IA não respondeu de forma utilizável."""


Esquema = TypeVar("Esquema", bound=BaseModel)


class ClienteLLM:
    def __init__(
        self,
        modelo: str,
        *,
        host: str = URL_OLLAMA,
        threads: int = 0,
        contexto_tokens: int = 6144,  # a lista de 17 categorias aumentou o prompt de avaliação
        usar_gpu: bool = False,
        cliente: Any | None = None,
        manter_na_memoria: str = "30m",
    ):
        self.modelo = modelo
        self._manter = manter_na_memoria  # quanto o Ollama guarda o modelo carregado, parado
        self._opcoes: dict[str, Any] = {
            "temperature": TEMPERATURA_PADRAO,
            "num_ctx": contexto_tokens,
        }
        if threads:
            self._opcoes["num_thread"] = threads
        if not usar_gpu:
            # O Ollama novo tenta a GPU por Vulkan e uma placa antiga (como a GTX 760) derruba o
            # processo ("device lost"). Sem GPU utilizável, zero camadas vão para ela.
            self._opcoes["num_gpu"] = 0
        if cliente is None:
            from ollama import Client

            cliente = Client(host=host, timeout=900)
        self._cliente = cliente

    # --- disponibilidade ----------------------------------------------------------------------
    def modelos_instalados(self) -> list[str]:
        try:
            resposta = self._cliente.list()
        except Exception as erro:
            raise ErroFerramenta(
                f"O Ollama não respondeu ({erro}). Abra o aplicativo Ollama e tente de novo."
            ) from erro
        return [m.model for m in resposta.models]

    def modelo_instalado(self) -> bool:
        instalados = self.modelos_instalados()
        return self.modelo in instalados or f"{self.modelo}:latest" in instalados

    def baixar_modelo(self, ao_progresso: Callable[[float], None] | None = None) -> None:
        """Baixa o modelo (vários GB; precisa de internet), informando o progresso 0..1."""
        try:
            for parte in self._cliente.pull(self.modelo, stream=True):
                total = getattr(parte, "total", None)
                completo = getattr(parte, "completed", None)
                if ao_progresso and total and completo is not None:
                    ao_progresso(min(1.0, completo / total))
        except Exception as erro:
            raise ErroFerramenta(
                f"Não foi possível baixar o modelo {self.modelo} ({erro}). "
                f"Confira a internet ou rode: ollama pull {self.modelo}"
            ) from erro

    def descarregar(self) -> None:
        """Tira o modelo da memória (~5 GB), para sobrar RAM para a renderização.

        Falhas são ignoradas: o Ollama também o descarrega sozinho depois de um tempo.
        """
        with contextlib.suppress(Exception):
            self._cliente.generate(model=self.modelo, keep_alive=0)

    # --- perguntas ----------------------------------------------------------------------------
    def avaliar(self, antes: str, trecho: str, depois: str, duracao_s: float) -> Avaliacao:
        prompt = PROMPT_AVALIACAO.format(
            antes=antes or "(início do sermão)",
            trecho=trecho,
            depois=depois or "(fim do sermão)",
            duracao=duracao_s,
            categorias=BLOCO_DE_CATEGORIAS,
        )
        return self._perguntar(prompt, AvaliacaoLLM).para_avaliacao()

    def metadados(
        self,
        sermao: str,
        trecho: str,
        categoria: str,
        duracao_s: float,
        titulos_usados: Sequence[str] = (),
        temperatura: float = TEMPERATURA_PADRAO,
    ) -> MetadadosLLM:
        usados = ""
        if titulos_usados:
            recentes = list(titulos_usados)[-_MAXIMO_DE_TITULOS_NO_PROMPT:]
            usados = _BLOCO_USADOS.format(titulos="\n".join(f"- {t}" for t in recentes))
        prompt = PROMPT_METADADOS.format(
            sermao=sermao, trecho=trecho, categoria=categoria, duracao=duracao_s, usados=usados
        )
        return self._perguntar(prompt, MetadadosLLM, temperatura)

    def refazer(
        self,
        campo: str,
        sermao: str,
        trecho: str,
        categoria: str,
        duracao_s: float,
        titulos_usados: Sequence[str] = (),
        temperatura: float = TEMPERATURA_DE_REFAZER,
    ) -> str | list[str]:
        """Cria de novo só um dado do Short (`titulo`, `descricao` ou `hashtags`), a partir do texto
        que ele tem agora. Devolve o texto, ou a lista de hashtags."""
        if campo not in CAMPOS_REFAZIVEIS:
            raise ValueError(f"campo desconhecido: {campo}")
        usados = ""
        if campo == "titulo" and titulos_usados:
            recentes = list(titulos_usados)[-_MAXIMO_DE_TITULOS_NO_PROMPT:]
            usados = _BLOCO_USADOS.format(titulos="\n".join(f"- {t}" for t in recentes))
        prompt = PROMPT_CAMPO.format(
            sermao=sermao, trecho=trecho, categoria=categoria, duracao=duracao_s,
            usados=usados, pedido=_PEDIDOS[campo],
        )  # fmt: skip
        resposta = self._perguntar(prompt, _ESQUEMA_DO_CAMPO[campo], temperatura)
        valor = getattr(resposta, campo)
        return list(valor) if campo == "hashtags" else str(valor).strip()

    def _perguntar(
        self, prompt: str, esquema: type[Esquema], temperatura: float = TEMPERATURA_PADRAO
    ) -> Esquema:
        ultimo_erro: Exception | None = None
        for tentativa in range(_TENTATIVAS):
            opcoes = {**self._opcoes, "temperature": temperatura + 0.2 * tentativa}
            try:
                resposta = self._cliente.chat(
                    model=self.modelo,
                    messages=[
                        {"role": "system", "content": SISTEMA},
                        {"role": "user", "content": prompt},
                    ],
                    format=esquema.model_json_schema(),
                    options=opcoes,
                    think=False,
                    keep_alive=self._manter,
                )
                return esquema.model_validate_json(resposta.message.content)
            except ValidationError as erro:  # JSON fora do esquema: tenta de novo
                ultimo_erro = erro
            except Exception as erro:
                raise ErroFerramenta(
                    f"Falha ao falar com o Ollama ({type(erro).__name__}: {erro}). "
                    "Confira se o aplicativo está aberto e o modelo instalado."
                ) from erro
        raise ErroLLM(f"A IA devolveu uma resposta inválida {_TENTATIVAS} vezes: {ultimo_erro}")
