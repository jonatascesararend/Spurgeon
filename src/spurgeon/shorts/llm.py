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
from spurgeon.shorts.dados import Avaliacao

URL_OLLAMA = "http://127.0.0.1:11434"
_TENTATIVAS = 3
TEMPERATURA_PADRAO = 0.1

SISTEMA = (
    "Você é um editor de vídeos curtos (Shorts) de um canal de sermões da teologia reformada "
    "(calvinista). Seu trabalho é avaliar trechos de sermão e preparar a publicação sem trair o "
    "que o pregador quis dizer. Responda sempre em JSON válido, em português do Brasil."
)

PROMPT_AVALIACAO = """\
CONTEXTO ANTERIOR (só para você entender; não faz parte do corte):
{antes}

TRECHO A AVALIAR (este é o corte, {duracao:.0f} segundos):
{trecho}

CONTEXTO POSTERIOR (só para você entender; não faz parte do corte):
{depois}

Avalie o TRECHO como um possível Short de até 60 segundos:
- categoria: "pergunta_gancho" (abre com uma pergunta que prende a atenção), "citacao_biblica" \
(gira em torno de um texto bíblico), "aplicacao_pratica" (aplica a verdade à vida de quem ouve), \
"conclusao" (fecha uma ideia ou o sermão) ou "outra".
- nota: de 0 a 10, o potencial de prender quem assiste. Tem gancho? Desenvolve uma ideia central \
completa? Termina com sentido? Seja exigente: 10 é raro.
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


class AvaliacaoLLM(BaseModel):
    categoria: Literal[
        "pergunta_gancho", "citacao_biblica", "aplicacao_pratica", "conclusao", "outra"
    ]
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


class MetadadosLLM(BaseModel):
    titulo: str = Field(min_length=3)
    descricao: str = Field(min_length=3)
    hashtags: list[str] = Field(min_length=1)
    slug: str = Field(min_length=2)

    @field_validator("hashtags")
    @classmethod
    def _com_cerquilha(cls, valores: list[str]) -> list[str]:
        limpas = []
        for v in valores:
            texto = "".join(v.split())
            if texto and not texto.startswith("#"):
                texto = "#" + texto
            if len(texto) > 1 and texto not in limpas:
                limpas.append(texto)
        if not limpas:
            raise ValueError("sem hashtags válidas")
        return limpas[:8]


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
        contexto_tokens: int = 4096,
        usar_gpu: bool = False,
        cliente: Any | None = None,
    ):
        self.modelo = modelo
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
                    keep_alive="30m",
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
