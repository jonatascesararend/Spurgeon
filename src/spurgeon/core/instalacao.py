"""Download dos modelos de IA na instalação (`spurgeon baixar-modelos`, chamado pelo instalar.bat).

O aplicativo também baixa cada modelo sozinho na primeira vez que precisa dele, mas isso acontece
no meio de um processamento de horas. Baixando na instalação, o primeiro vídeo já roda direto (e
sem internet). Cada download é independente: se um falhar, os outros seguem.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from spurgeon.core.config import ConfigApp
from spurgeon.core.erros import ErroSpurgeon

PASSO_DO_PROGRESSO = 0.1  # o texto avisa a cada 10%


@dataclass(frozen=True)
class ResultadoDoDownload:
    nome: str
    ok: bool
    detalhe: str


def _com_progresso(nome: str, emitir: Callable[[str], None]) -> Callable[[float], None]:
    """Uma função de progresso que escreve uma linha a cada 10% (cabe em qualquer terminal)."""
    ultimo = {"faixa": -1}

    def avisar(fracao: float) -> None:
        faixa = int(min(max(fracao, 0.0), 1.0) / PASSO_DO_PROGRESSO)
        if faixa != ultimo["faixa"]:
            ultimo["faixa"] = faixa
            emitir(f"  {nome}: {faixa * 10}%")

    return avisar


def baixar_modelos(
    config: ConfigApp,
    *,
    limpeza_de_audio: bool = True,
    whisper: str | None = "",
    ollama: str | None = "",
    emitir: Callable[[str], None] = print,
) -> list[ResultadoDoDownload]:
    """Baixa o filtro de limpeza de áudio, o modelo do Whisper e o modelo do Ollama.

    `whisper` e `ollama`: "" usa o modelo das configurações; `None` não baixa; outro texto é o
    nome do modelo. Devolve um resultado por item (sem levantar erro: a instalação segue).
    """
    resultados: list[ResultadoDoDownload] = []

    def tentar(nome: str, trabalho: Callable[[], str]) -> None:
        emitir(f"Baixando: {nome}")
        try:
            detalhe = trabalho()
        except (ErroSpurgeon, OSError, ImportError) as erro:
            emitir(f"  não foi possível: {erro}")
            resultados.append(ResultadoDoDownload(nome, False, str(erro)))
        else:
            emitir(f"  pronto: {detalhe}")
            resultados.append(ResultadoDoDownload(nome, True, detalhe))

    if limpeza_de_audio:
        from spurgeon.audio.deep_filter import baixar_deep_filter

        tentar(
            "filtro de limpeza de áudio (deep-filter)",
            lambda: str(
                baixar_deep_filter(config, ao_progresso=_com_progresso("deep-filter", emitir))
            ),
        )
    if whisper is not None:
        from spurgeon.transcricao.modelo import baixar_modelo

        nome = whisper or config.padroes.whisper_modelo
        tentar(
            f"modelo de transcrição Whisper ({nome})",
            lambda: str(
                baixar_modelo(config, nome, ao_progresso=_com_progresso("Whisper", emitir))
            ),
        )
    if ollama is not None:
        nome = ollama or config.padroes.ollama_modelo
        tentar(f"modelo da IA dos Shorts, Ollama ({nome})", lambda: _baixar_do_ollama(nome, emitir))
    return resultados


def _baixar_do_ollama(nome: str, emitir: Callable[[str], None]) -> str:
    from spurgeon.core.erros import ErroFerramenta
    from spurgeon.core.ollama import garantir_servidor
    from spurgeon.shorts.llm import ClienteLLM

    if not garantir_servidor():
        raise ErroFerramenta(
            "o Ollama não está instalado ou não abriu. Instale em https://ollama.com/download "
            "e rode de novo: uv run spurgeon baixar-modelos"
        )
    cliente = ClienteLLM(nome)
    if cliente.modelo_instalado():
        return f"{nome} já estava instalado"
    cliente.baixar_modelo(_com_progresso(nome, emitir))
    return f"{nome} instalado"
