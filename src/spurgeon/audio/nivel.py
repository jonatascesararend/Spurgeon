"""Etapa: mede o nível do áudio em janelas de 50 ms (RMS) e guarda o resultado.

A medição é usada por duas etapas: a que planeja os blocos da transcrição (para cortar em
pausas) e a que procura as pausas longas. Fazer uma vez só evita repassar o áudio inteiro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_numero
from spurgeon.core.pipeline import ContextoProjeto, Etapa

JANELA_AMOSTRAS = 2400  # 50 ms a 48 kHz (o áudio é sempre reamostrado para 48 kHz na extração)
JANELA_S = 0.05
NIVEL_MUDO_DB = -120.0  # silêncio digital: o astats imprime -inf
_FRACAO_ACIMA_DO_PISO = 0.25
_MARGEM_MINIMA_DB = 4.0
_MARGEM_ATE_A_FALA_DB = 12.0
_CHAVE_RMS = "lavfi.astats.Overall.RMS_level="
_TEMPO = re.compile(r"pts_time:\s*(-?[\d.]+)")

#: (tempo em segundos, nível RMS em dB) de uma janela de 50 ms
Medida = tuple[float, float]


@dataclass(frozen=True)
class NivelAudio:
    piso_db: float  # ruído de fundo: percentil 10 do nível em janelas de 50 ms
    fala_db: float  # fala: percentil 90


def ler_niveis(texto: str) -> list[Medida]:
    """Lê o que o `ametadata` imprimiu: uma linha `frame:` com o tempo e a linha do nível.

    Janelas de silêncio digital (`-inf`) valem `NIVEL_MUDO_DB`, para contarem como silêncio.
    """
    medidas: list[Medida] = []
    tempo: float | None = None
    for linha in texto.splitlines():
        if linha.startswith("frame:"):
            achado = _TEMPO.search(linha)
            tempo = float(achado.group(1)) if achado else None
        elif linha.startswith(_CHAVE_RMS) and tempo is not None:
            try:
                valor = float(linha[len(_CHAVE_RMS) :].strip())  # aceita "-inf"
            except ValueError:
                continue
            if valor == valor:  # descarta nan
                medidas.append((tempo, max(valor, NIVEL_MUDO_DB)))
    return medidas


def calcular_nivel(medidas: list[Medida]) -> NivelAudio:
    """Piso e fala a partir dos percentis 10 e 90 das janelas que têm algum sinal."""
    valores = [nivel for _, nivel in medidas if nivel > NIVEL_MUDO_DB]
    if len(valores) < 20:
        raise ErroSpurgeon("O áudio é curto ou silencioso demais para medir o nível de ruído.")
    ordenados = sorted(valores)
    return NivelAudio(
        piso_db=ordenados[int(0.10 * (len(ordenados) - 1))],
        fala_db=ordenados[int(0.90 * (len(ordenados) - 1))],
    )


def limiar_silencio_db(nivel: NivelAudio) -> float:
    """Um pouco acima do ruído de fundo, e sempre bem abaixo do nível da fala."""
    acima_do_piso = max(_MARGEM_MINIMA_DB, _FRACAO_ACIMA_DO_PISO * (nivel.fala_db - nivel.piso_db))
    return min(nivel.piso_db + acima_do_piso, nivel.fala_db - _MARGEM_ATE_A_FALA_DB)


@dataclass(frozen=True)
class NiveisMedidos:
    duracao_s: float
    nivel: NivelAudio
    limiar_db: float
    medidas: list[Medida]


def carregar_niveis(caminho: Path) -> NiveisMedidos:
    dados = ler_json(caminho)
    return NiveisMedidos(
        duracao_s=dados["duracao_s"],
        nivel=NivelAudio(dados["piso_db"], dados["fala_db"]),
        limiar_db=dados["limiar_db"],
        medidas=list(zip(dados["tempos"], dados["niveis"], strict=True)),
    )


class MedirNivel(Etapa):
    id = "medir_nivel"
    nome = "Medindo o nível do áudio"
    depende_de = ("extrair_audio", "limpar_audio")
    opcoes_relevantes = ("limpar_audio",)
    peso = 2.0

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.niveis]

    def executar(self, ctx: ContextoProjeto) -> None:
        audio = ctx.pastas.audio_base(ctx.opcoes)
        duracao = sondar(audio).duracao_s
        trabalho = ctx.pastas.work

        # O FFmpeg escreve as medições num arquivo, e o caminho é relativo à pasta de
        # trabalho (um "C:" dentro de um filtro precisaria de escape).
        arquivo = trabalho / "niveis.txt"
        ctx.ffmpeg(
            ["-i", audio, "-af",
             f"asetnsamples=n={JANELA_AMOSTRAS}:p=0,astats=metadata=1:reset=1,"
             f"ametadata=mode=print:key={_CHAVE_RMS[:-1]}:file={arquivo.name}",
             "-f", "null", "-"],
            duracao_s=duracao, cwd=trabalho,
        )  # fmt: skip
        medidas = ler_niveis(arquivo.read_text(encoding="utf-8"))
        arquivo.unlink(missing_ok=True)
        nivel = calcular_nivel(medidas)
        limiar = limiar_silencio_db(nivel)
        ctx.log.info(
            "Ruído de fundo: %s dB; fala: %s dB; limiar de silêncio: %s dB.",
            formatar_numero(nivel.piso_db),
            formatar_numero(nivel.fala_db),
            formatar_numero(limiar),
        )
        escrever_json_atomico(
            ctx.pastas.niveis,
            {
                "duracao_s": duracao,
                "piso_db": nivel.piso_db,
                "fala_db": nivel.fala_db,
                "limiar_db": limiar,
                "tempos": [round(t, 3) for t, _ in medidas],
                "niveis": [round(n, 1) for _, n in medidas],
            },
            indent=None,
        )
