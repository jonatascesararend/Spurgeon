"""De onde cada Short é cortado: do vídeo longo já pronto ou do vídeo original.

O vídeo longo (`video_longo_otimizado.mp4`) já tem todo o tratamento: pausas longas tiradas, zoom,
cor, HDR→SDR, som limpo e normalizado e marca d'água. Cortar os Shorts DELE, e não do original, faz
o Short sair com a mesma cara e o mesmo som do vídeo final. Por isso o vídeo longo é renderizado
antes dos Shorts, e ao terminar guarda em `_work/video_longo_info.json` (bloco `fonte_dos_shorts`)
o que os Shorts precisam saber dele: os trechos que ficaram (o mapa de tempo original → vídeo
longo), onde o vídeo começa depois da introdução, o zoom e a cor que já estão na imagem e onde a
marca d'água caiu.

Os Shorts só saem do vídeo longo quando ele serve de verdade (`fonte_do_longo`); senão, ou para um
Short que cai na transição da introdução ou da vinheta (`motivo_de_nao_servir`), saem do original,
como sempre saíram. Uma fonte, qualquer que seja, é um `FonteDoShort`: o arquivo da imagem e do
som, o mapa de tempo (só o vídeo longo tem) e o que o filtro do Short ainda precisa aplicar (o zoom
e a cor só no original; no vídeo longo já estão na imagem).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.arquivos import ler_json
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.estado import hash_rapido
from spurgeon.core.ffmpeg import InfoMidia, sondar
from spurgeon.core.projeto import PastasProjeto
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.render import filtros as f
from spurgeon.render.legenda import PalavraDaLegenda
from spurgeon.shorts.dados import Corte

VERSAO_DA_FONTE = 1  # suba se o formato do bloco mudar: o vídeo longo antigo deixa de servir
CHAVE_NO_INFO = "fonte_dos_shorts"
#: o trecho de um Short precisa ter ao menos isto no vídeo longo (senão sumiu nas pausas cortadas)
DURACAO_MINIMA_NO_LONGO_S = 1.0
#: folga além do fade ou da mescla para o Short não pegar nenhum quadro da transição
FOLGA_DA_TRANSICAO_S = 0.1
_TOLERANCIA_S = 1e-6

Conversor = Callable[[float], float]


@dataclass(frozen=True)
class FonteDoShort:
    """De onde sai a imagem e o som de um Short, e o que o filtro ainda tem de aplicar."""

    video: Path
    audio: Path
    info: InfoMidia
    canais: int | None
    fps: Fraction
    #: o zoom e a cor que o filtro do Short aplica; None = já estão na imagem (vídeo longo)
    enquadramento: Enquadramento | None
    tratamento: TratamentoImagem | None
    #: tempo do original → tempo deste vídeo; None = o mesmo tempo (o próprio original)
    mapa: MapaTempo | None = None
    #: entra na assinatura do Short; None no original (os Shorts já prontos continuam valendo)
    identidade: str | None = None
    #: (vídeo longo) onde a imagem está livre da mescla ou do fade da introdução e da vinheta
    inicio_util_s: float = 0.0
    fim_util_s: float = math.inf
    #: (vídeo longo) onde a marca d'água ficou na imagem: (x, y, largura, altura)
    marca_queimada: tuple[int, int, int, int] | None = None

    @property
    def eh_o_longo(self) -> bool:
        return self.mapa is not None

    @property
    def nome(self) -> str:
        return "vídeo longo" if self.eh_o_longo else "vídeo original"


# --- o que o vídeo longo guarda para os Shorts -------------------------------------------------
def _normalizado(valor: Any) -> Any:
    """O que o JSON devolveria (tuplas viram listas): para comparar o gravado com o de agora."""
    return json.loads(json.dumps(valor, sort_keys=True))


def dados_do_longo(
    *,
    hash_do_original: str,
    trechos: Sequence[Trecho],
    inicio_s: float,
    zona_do_inicio_s: float,
    zona_do_fim_s: float,
    tamanho: tuple[int, int],
    enquadramento: Enquadramento,
    tratamento: TratamentoImagem,
    marca: tuple[int, int, int, int] | None,
    assinatura_das_opcoes: str,
    janela: tuple[float, float],
) -> dict[str, Any]:
    """O bloco `fonte_dos_shorts` do `video_longo_info.json`, gravado quando o vídeo longo termina.

    `trechos` são os do original que ficaram (o mapa de tempo); `inicio_s` é onde eles começam no
    vídeo longo (a introdução vem antes, menos a mescla); as zonas são os segundos, no começo e no
    fim desse miolo, que a mescla ou o fade da introdução e da vinheta ainda tocam.
    """
    return {
        "versao": VERSAO_DA_FONTE,
        "video": hash_do_original,
        "opcoes": assinatura_das_opcoes,
        "janela": [round(janela[0], 3), round(janela[1], 3)],
        "trechos": [[t.inicio, t.fim] for t in trechos],
        "inicio_s": inicio_s,
        "zona_do_inicio_s": _com_folga(zona_do_inicio_s),
        "zona_do_fim_s": _com_folga(zona_do_fim_s),
        "tamanho": list(tamanho),
        "zoom": _normalizado(enquadramento.model_dump()),
        "tratamento": _normalizado(tratamento.model_dump()),
        "marca": list(marca) if marca else None,
    }


def _com_folga(zona_s: float) -> float:
    return zona_s + FOLGA_DA_TRANSICAO_S if zona_s > 0 else 0.0


# --- as duas fontes ----------------------------------------------------------------------------
def fonte_do_original(
    video: Path, audio: Path, info: InfoMidia, canais: int | None, opcoes: OpcoesProcessamento
) -> FonteDoShort:
    """O vídeo original com o som já tratado (limpo e normalizado) do projeto."""
    return FonteDoShort(
        video=video,
        audio=audio,
        info=info,
        canais=canais,
        fps=f.fps_constante(info.fps),
        enquadramento=opcoes.enquadramento_nos_curtos,
        tratamento=opcoes.tratamento_nos_curtos,
    )


def fonte_do_longo(
    pastas: PastasProjeto,
    opcoes: OpcoesProcessamento,
    *,
    hash_do_original: str,
    assinatura_do_longo: str,
) -> tuple[FonteDoShort | None, str]:
    """O vídeo longo como fonte dos Shorts, ou (None, o motivo de ele não servir).

    Só serve se foi gerado por esta versão (traz o mapa de tempo), é deste mesmo vídeo e destas
    mesmas opções (`assinatura_do_longo` é a de agora), e se a imagem dele é a que os Shorts querem:
    o zoom e a cor já estão "queimados" nele, então um Short sem zoom ou sem cor não pode sair de um
    vídeo longo que os tem.
    """
    video = pastas.video_longo
    if not video.is_file():
        return None, "o vídeo longo ainda não foi gerado"
    try:
        guardado = ler_json(pastas.video_longo_info)
    except (OSError, ValueError):
        guardado = None
    dados = guardado.get(CHAVE_NO_INFO) if isinstance(guardado, dict) else None
    if not isinstance(dados, dict) or dados.get("versao") != VERSAO_DA_FONTE:
        return None, (
            "o vídeo longo foi gerado por uma versão anterior do programa; renderize-o de novo "
            "para os Shorts saírem dele"
        )
    if dados.get("video") != hash_do_original:
        return None, "o vídeo longo é de outro vídeo original"
    if dados.get("opcoes") != assinatura_do_longo:
        return None, "as opções do vídeo longo mudaram depois de ele ser renderizado"
    janela = [round(opcoes.video_inicio_s, 3), round(opcoes.video_fim_s, 3)]
    if dados.get("janela") != janela:
        return None, "o começo e o fim do vídeo mudaram depois de o vídeo longo ser renderizado"
    if dados.get("zoom") != _normalizado(opcoes.enquadramento_nos_curtos.model_dump()):
        return None, "o zoom do vídeo longo não é o dos Shorts (o zoom está desligado para eles)"
    if dados.get("tratamento") != _normalizado(opcoes.tratamento_nos_curtos.model_dump()):
        return None, "a cor do vídeo longo não é a dos Shorts (o tratamento não vale para eles)"
    try:
        info = sondar(video)
    except (ErroSpurgeon, OSError) as erro:
        return None, f"não consegui ler o vídeo longo ({erro})"
    if not (info.tem_video and info.tem_audio):
        return None, "o vídeo longo está sem imagem ou sem som"
    if [info.largura, info.altura] != dados.get("tamanho"):
        return None, "o tamanho do vídeo longo não é o que foi gravado"
    try:
        mapa = MapaTempo(
            [Trecho(float(a), float(b)) for a, b in dados["trechos"]],
            deslocamento=float(dados["inicio_s"]),
        )
        zona_do_inicio = float(dados.get("zona_do_inicio_s") or 0.0)
        zona_do_fim = float(dados.get("zona_do_fim_s") or 0.0)
        marca = tuple(int(v) for v in dados["marca"]) if dados.get("marca") else None
    except (KeyError, TypeError, ValueError):
        return None, "o mapa de tempo guardado do vídeo longo não pôde ser lido"
    return (
        FonteDoShort(
            video=video,
            audio=video,  # o som já está no vídeo longo, tratado e na mesma linha do tempo
            info=info,
            canais=info.canais_audio,
            fps=f.fps_constante(info.fps),
            enquadramento=None,
            tratamento=None,
            mapa=mapa,
            identidade=f"longo:{hash_rapido(video)}",
            inicio_util_s=mapa.deslocamento + zona_do_inicio,
            fim_util_s=mapa.deslocamento + mapa.duracao_editada - zona_do_fim,
            marca_queimada=marca,  # type: ignore[arg-type]
        ),
        "",
    )


def motivo_de_nao_servir(
    fonte: FonteDoShort,
    corte: Corte,
    deslocamento_pct: float,
    zoom_do_short: Enquadramento | None,
) -> str:
    """Por que ESTE Short não pode sair desta fonte ("" se pode). O original serve a todos.

    No vídeo longo não serve o Short que começa ou termina nos segundos em que a introdução ou a
    vinheta ainda se fundem com a imagem, o que quase só sobrou das pausas cortadas, e o que o
    recorte vertical (deslocado ou com zoom) alcança a marca d'água queimada no vídeo longo.
    """
    if fonte.mapa is None:
        return ""
    de = fonte.mapa.original_para_editado(corte.inicio)
    ate = fonte.mapa.original_para_editado(corte.fim)
    if de < fonte.inicio_util_s - _TOLERANCIA_S:
        return "ele começa na transição da introdução"
    if ate > fonte.fim_util_s + _TOLERANCIA_S:
        return "ele termina na transição da vinheta"
    if ate - de < DURACAO_MINIMA_NO_LONGO_S:
        return "quase todo o trecho dele são pausas que o vídeo longo cortou"
    if fonte.marca_queimada is not None:
        recorte = f.regiao_do_recorte(fonte.info, deslocamento_pct, None, zoom_do_short)
        x, y, largura, altura = recorte
        mx, my, ml, ma = fonte.marca_queimada
        if x < mx + ml and mx < x + largura and y < my + ma and my < y + altura:
            return "o recorte vertical pegaria a marca d'água que está no vídeo longo"
    return ""


# --- o tempo do corte na fonte -----------------------------------------------------------------
def leitura_do_corte(fonte: FonteDoShort, corte: Corte) -> tuple[float, float, Conversor | None]:
    """O que ler da fonte para este corte: (onde começa, quanto dura, conversor de tempo).

    O conversor leva um tempo "desde o começo do corte, no original" para "desde o começo da
    leitura, na fonte"; é None no original, onde os tempos já valem como estão. No vídeo longo as
    pausas cortadas não existem, então o trecho lido é mais curto que `corte.janela_s`.
    """
    if fonte.mapa is None:
        return corte.inicio, corte.janela_s, None
    mapa = fonte.mapa
    de = mapa.original_para_editado(corte.inicio)
    ate = mapa.original_para_editado(corte.fim)

    def converter(desde_o_corte: float) -> float:
        return mapa.original_para_editado(corte.inicio + desde_o_corte) - de

    return de, ate - de, converter


def trechos_na_fonte(trechos: Sequence[Trecho], converter: Conversor) -> list[Trecho]:
    """Os trechos de um Short (desde o começo do corte, no original) na linha do tempo da fonte.

    O que o vídeo longo já cortou deixa de existir: o trecho que o atravessa fica contínuo, e dois
    trechos que passam a se tocar viram um só.
    """
    convertidos: list[Trecho] = []
    for trecho in trechos:
        de, ate = converter(trecho.inicio), converter(trecho.fim)
        if ate - de <= _TOLERANCIA_S:
            continue
        if convertidos and de - convertidos[-1].fim <= _TOLERANCIA_S:
            convertidos[-1] = Trecho(convertidos[-1].inicio, ate)
        else:
            convertidos.append(Trecho(de, ate))
    return convertidos


def palavras_na_fonte(
    palavras: Sequence[PalavraDaLegenda], converter: Conversor
) -> list[PalavraDaLegenda]:
    """As palavras da legenda (desde o começo do corte, no original) na linha do tempo da fonte."""
    return [PalavraDaLegenda(p.texto, converter(p.inicio), converter(p.fim)) for p in palavras]
