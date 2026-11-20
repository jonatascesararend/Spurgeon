"""Ouvir o Short com a legenda, sem renderizar o vídeo (pedido do autor em 2026-11-20).

Para conferir a legenda basta o som. O PC monta só o áudio do Short, com os MESMOS trechos do
render (`trechos_mantidos`: sem os saltos e com as pausas encurtadas, `atrim` e micro-fades por
`render/shorts.linhas_do_audio_cortado`), num `.m4a` pequeno (AAC mono). O celular toca esse áudio e
acende cada palavra da legenda no seu tempo. A legenda é a mesma que seria queimada no Short:
`palavras_com_a_legenda` (com a correção da pessoa), remapeada para a linha do tempo do Short.

De onde vem o som, nesta ordem (os arquivos pesados de `_work/` somem quando o projeto conclui):
1. o áudio tratado do projeto (`audio_final`), se ainda existe;
2. o vídeo original (`estado.entrada.caminho`), se ainda existe;
3. o próprio Short já renderizado (o som dele já está na linha do tempo do Short). Se o corte
   mudou depois do render, o celular avisa que o som pode não bater com a legenda.

Fica em `_work/remoto/audio_<id>_<assinatura>.m4a` (o de antes do mesmo corte é apagado).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spurgeon.core.estado import carregar_estado
from spurgeon.core.ffmpeg import executar_ffmpeg
from spurgeon.core.projeto import PastasProjeto
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.remoto.dados import ShortNoDisco, shorts_no_disco
from spurgeon.render import filtros as f
from spurgeon.render.legenda import PalavraDaLegenda, agrupar_em_frases, palavras_do_short
from spurgeon.render.shorts import (
    SITUACAO_PRONTO,
    ler_registro,
    linhas_do_audio_cortado,
    situacao_do_short,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import palavras_com_a_legenda
from spurgeon.shorts.pausas import ha_o_que_cortar, remapear_palavras, trechos_mantidos
from spurgeon.transcricao.formato import Palavra, Transcricao

log = logging.getLogger(__name__)

#: sobe quando o jeito de montar o áudio muda (refaz os arquivos guardados)
VERSAO_DO_AUDIO = 1
PASTA = "remoto"
FADE_DAS_PONTAS_S = 0.05
_travas: dict[Path, threading.Lock] = {}
_trava_das_travas = threading.Lock()


class SemFonteDeAudio(LookupError):
    pass


@dataclass(frozen=True)
class FonteDoAudio:
    caminho: Path
    #: `original`: tempo do vídeo original (o áudio é cortado); `short`: o Short já renderizado
    tipo: str
    aviso: str = ""


@dataclass(frozen=True)
class LegendaDoShort:
    duracao: float
    trechos: list[Trecho]  # em segundos desde o início do corte, no tempo original
    frases: list[list[PalavraDaLegenda]]  # tempos desde o começo do Short pronto


def carregar_palavras(pastas: PastasProjeto) -> list[Palavra]:
    return Transcricao.carregar(pastas.transcricao).palavras


def legenda_do_short(palavras: list[Palavra], corte: Corte) -> LegendaDoShort:
    """A legenda que o Short teria, no tempo do Short pronto, e os trechos que o som leva."""
    faladas = palavras_do_short(palavras_com_a_legenda(palavras, corte), corte.inicio, corte.fim)
    mantidos = trechos_mantidos(palavras, corte)
    duracao = corte.janela_s
    if ha_o_que_cortar(mantidos, corte.janela_s):
        mapa = MapaTempo(mantidos)
        faladas = remapear_palavras(faladas, mapa)
        duracao = mapa.duracao_editada
    return LegendaDoShort(duracao, mantidos, agrupar_em_frases(faladas))


def legenda_para_json(legenda: LegendaDoShort) -> dict[str, Any]:
    return {
        "duracao": round(legenda.duracao, 3),
        "frases": [
            [{"t": p.texto, "i": round(p.inicio, 3), "f": round(p.fim, 3)} for p in frase]
            for frase in legenda.frases
        ],
    }


def fonte_do_original(pastas: PastasProjeto) -> FonteDoAudio | None:
    """O áudio tratado do projeto, senão o vídeo original (None se nenhum está mais no PC)."""
    estado = carregar_estado(pastas.raiz)
    if estado is not None:
        tratado = pastas.audio_final(estado.opcoes)
        if tratado.is_file():
            return FonteDoAudio(tratado, "original")
        original = Path(estado.entrada.caminho)
        if original.is_file():
            return FonteDoAudio(original, "original")
    return None


def fonte_do_audio(pastas: PastasProjeto, short: ShortNoDisco) -> FonteDoAudio:
    if (fonte := fonte_do_original(pastas)) is not None:
        return fonte
    if short.arquivo is not None:
        entrada = ler_registro(pastas.shorts_renderizados).get(str(short.corte.id))
        situacao = situacao_do_short(short.corte, entrada, True, com_legenda=False)
        aviso = (
            ""
            if situacao == SITUACAO_PRONTO
            else "O vídeo original não está mais no PC e este Short mudou depois de renderizado: "
            "o som é o do Short antigo e pode não bater com a legenda."
        )
        return FonteDoAudio(short.arquivo, "short", aviso)
    raise SemFonteDeAudio(
        "Não há de onde tirar o som deste Short: o vídeo original não está mais no PC e o Short "
        "não foi renderizado."
    )


def _assinatura(fonte: FonteDoAudio, corte: Corte) -> str:
    try:
        marca = fonte.caminho.stat()
        tamanho, quando = marca.st_size, int(marca.st_mtime)
    except OSError:
        tamanho, quando = 0, 0
    dados = {
        "v": VERSAO_DO_AUDIO, "fonte": str(fonte.caminho), "tamanho": tamanho, "quando": quando,
        "inicio": round(corte.inicio, 3), "fim": round(corte.fim, 3),
        "saltos": corte.saltos, "pausa_s": corte.pausa_s,
    }  # fmt: skip
    return hashlib.sha1(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:12]


def _trava(caminho: Path) -> threading.Lock:
    with _trava_das_travas:
        return _travas.setdefault(caminho, threading.Lock())


def argumentos_do_ffmpeg(
    fonte: FonteDoAudio, corte: Corte, legenda: LegendaDoShort, saida: Path
) -> list[str]:
    fade = min(FADE_DAS_PONTAS_S, legenda.duracao / 4)
    pontas = (
        f"afade=t=in:st=0:d={f.numero_ffmpeg(fade)},"
        f"afade=t=out:st={f.numero_ffmpeg(max(0.0, legenda.duracao - fade))}:"
        f"d={f.numero_ffmpeg(fade)}"
    )
    if fonte.tipo == "short":  # já está na linha do tempo do Short: só tirar o som
        leitura, grafo = [], f"[0:a]aformat=channel_layouts=mono,{pontas}[a]"
    else:
        leitura = ["-ss", f.numero_ffmpeg(corte.inicio), "-t", f.numero_ffmpeg(corte.janela_s)]
        trechos = legenda.trechos or [Trecho(0.0, corte.janela_s)]
        cadeia = f"aformat=channel_layouts=mono,{pontas}"
        grafo = ";".join(linhas_do_audio_cortado(trechos, cadeia, entrada="0:a"))
    return [
        "-y", *leitura, "-i", str(fonte.caminho), "-filter_complex", grafo, "-map", "[a]",
        "-vn", "-c:a", "aac", "-b:a", "64k", "-ar", "44100", "-t", f.numero_ffmpeg(legenda.duracao),
        "-movflags", "+faststart", "-f", "mp4", str(saida),
    ]  # fmt: skip


@dataclass(frozen=True)
class AudioDoShort:
    arquivo: Path
    aviso: str


def audio_do_corte(
    pastas: PastasProjeto, corte: Corte, nome: str, fonte: FonteDoAudio, *, rodar=None
) -> Path:
    """O `.m4a` deste corte, guardado como `_work/remoto/<nome>_<assinatura>.m4a` (o anterior com o
    mesmo nome é apagado). Serve aos Shorts da lista e ao trecho do Shorts livre."""
    pasta = pastas.work / PASTA
    destino = pasta / f"{nome}_{_assinatura(fonte, corte)}.m4a"
    with _trava(destino):
        if not destino.is_file():
            pasta.mkdir(parents=True, exist_ok=True)
            legenda = legenda_do_short(carregar_palavras(pastas), corte)
            parcial = destino.with_name(destino.name + ".parcial")
            (rodar or executar_ffmpeg)(
                argumentos_do_ffmpeg(fonte, corte, legenda, parcial), duracao_s=legenda.duracao
            )
            parcial.replace(destino)
            log.info("Áudio de %s pronto (%s, %.1f s).", nome, fonte.tipo, legenda.duracao)
            for velho in pasta.glob(f"{nome}_*.m4a"):
                if velho != destino:
                    velho.unlink(missing_ok=True)
    return destino


def audio_do_short(pastas: PastasProjeto, id_do_corte: int, *, rodar=None) -> AudioDoShort:
    """O `.m4a` do Short (feito agora ou o que já estava guardado para este mesmo corte)."""
    short = next((s for s in shorts_no_disco(pastas) if s.corte.id == id_do_corte), None)
    if short is None:
        raise LookupError(f"corte {id_do_corte}")
    fonte = fonte_do_audio(pastas, short)
    arquivo = audio_do_corte(pastas, short.corte, f"audio_{id_do_corte}", fonte, rodar=rodar)
    return AudioDoShort(arquivo, fonte.aviso)
