"""O cronograma do canal: o que está programado e o que já saiu, Shorts e vídeos longos.

As listas "Conteúdo → Vídeos" e "Conteúdo → Shorts" do YouTube Studio não mostram a hora do que
está programado, mas a própria página as carrega como JSON
(`youtubei/v1/creator/list_creator_videos`) com tudo: `scheduledPublishingDetails` (a hora
marcada), `timePublishedSeconds`, `contentType` (Short ou não), título e duração. A automação só
abre as duas listas e lê essas respostas; nada é mudado no canal. As páginas seguem (30 por vez)
até passar do dia pedido (`desde`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from spurgeon.canal.navegador import STUDIO, Studio

PROGRAMADO = "programado"
PUBLICADO = "publicado"
SHORT = "short"
VIDEO = "video"
CANAL = "canal"
SPURGEON = "spurgeon"

#: as abas do Studio (`/videos/<aba>`) e o tipo que cada uma tem
ABAS = {"short": SHORT, "upload": VIDEO}
#: páginas de 30 por aba, no máximo (o mês passado inteiro cabe com folga num canal como este)
MAXIMO_DE_PAGINAS = 8
#: sermões relacionados mais antigos que as páginas lidas, buscados um a um pelo título
MAXIMO_DE_TITULOS_A_BUSCAR = 30


@dataclass(frozen=True)
class ItemDoCronograma:
    id: str
    titulo: str
    tipo: str  # SHORT ou VIDEO
    situacao: str  # PROGRAMADO ou PUBLICADO
    quando: datetime  # hora do computador
    duracao_s: int
    #: o vídeo relacionado do Short (o sermão): o id no YouTube e o título, quando se sabe
    relacionado_id: str = ""
    relacionado: str = ""
    #: CANAL (lido do YouTube Studio) ou SPURGEON (o que os projetos dizem, ainda não lido do canal)
    origem: str = "canal"

    def para_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "titulo": self.titulo, "tipo": self.tipo, "situacao": self.situacao,
            "quando": self.quando.strftime("%Y-%m-%dT%H:%M"), "duracao_s": self.duracao_s,
            "relacionado_id": self.relacionado_id, "relacionado": self.relacionado,
            "origem": self.origem,
        }  # fmt: skip

    @classmethod
    def de_json(cls, dados: dict[str, Any]) -> ItemDoCronograma:
        return cls(
            str(dados["id"]), str(dados["titulo"]), str(dados["tipo"]), str(dados["situacao"]),
            datetime.strptime(dados["quando"], "%Y-%m-%dT%H:%M"), int(dados.get("duracao_s") or 0),
            str(dados.get("relacionado_id") or ""), str(dados.get("relacionado") or ""),
            str(dados.get("origem") or CANAL),
        )  # fmt: skip


def _segundos(valor: Any) -> int:
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def id_do_relacionado(video: dict[str, Any]) -> str:
    """O id do vídeo relacionado do Short (`videoLinkageShortsAttribution`), ou ""."""
    ligacao = video.get("videoLinkageShortsAttribution") or {}
    for link in ligacao.get("links") or []:
        if alvo := str(link.get("targetExternalVideoId") or ""):
            return alvo
    return ""


def _titulo(video: dict[str, Any]) -> str:
    return " ".join(str(video.get("title") or "").split())


def hora_programada(video: dict[str, Any]) -> int:
    """Os segundos (época) da publicação programada que ainda vale (0 = nenhuma)."""
    detalhes = video.get("scheduledPublishingDetails") or {}
    for marcada in detalhes.get("scheduledPublishings") or []:
        if marcada.get("status") == "SCHEDULED_PUBLISHING_STATUS_SCHEDULED":
            return _segundos(marcada.get("scheduledTimeSeconds"))
    return 0


def ler_item(
    video: dict[str, Any],
    tipo_da_aba: str = VIDEO,
    *,
    hora_local: Callable[[int], datetime] = datetime.fromtimestamp,
) -> ItemDoCronograma | None:
    """Um vídeo do JSON do Studio virando item do cronograma, ou None se não entra: só o que está
    programado e o que saiu como público (não listados, privados e rascunhos ficam de fora)."""
    video_id = str(video.get("videoId") or "")
    if not video_id:
        return None
    tipo = SHORT if video.get("contentType") == "CREATOR_CONTENT_TYPE_SHORTS" else tipo_da_aba
    publicado = _segundos(video.get("timePublishedSeconds"))
    if programada := hora_programada(video):
        situacao, segundos = PROGRAMADO, programada
    elif video.get("privacy") == "VIDEO_PRIVACY_PUBLIC" and publicado:
        situacao, segundos = PUBLICADO, publicado
    else:
        return None
    return ItemDoCronograma(
        id=video_id,
        titulo=_titulo(video) or "(sem título)",
        tipo=tipo,
        situacao=situacao,
        quando=hora_local(segundos),
        duracao_s=_segundos(video.get("lengthSeconds")),
        relacionado_id=id_do_relacionado(video),
    )


def momento_na_lista(video: dict[str, Any]) -> int:
    """A hora pela qual o Studio ordena a lista (a programada, a de publicação ou a de envio)."""
    return (
        hora_programada(video)
        or _segundos(video.get("timePublishedSeconds"))
        or _segundos(video.get("timeCreatedSeconds"))
    )


def juntar(
    paginas: dict[str, list[dict[str, Any]]],
    desde: date,
    *,
    hora_local: Callable[[int], datetime] = datetime.fromtimestamp,
    titulos: dict[str, str] | None = None,
) -> list[ItemDoCronograma]:
    """As respostas das abas (`{"short": [vídeos…], "upload": [...]}`) num cronograma só, de `desde`
    em diante, em ordem de hora, sem repetir vídeo. O título do vídeo relacionado de cada Short sai
    de qualquer vídeo lido (mesmo os que não entram: não listados, mais antigos) ou de `titulos`."""
    conhecidos = dict(titulos or {})
    for videos in paginas.values():
        for video in videos:
            if video.get("videoId") and _titulo(video):
                conhecidos.setdefault(str(video["videoId"]), _titulo(video))
    itens: dict[str, ItemDoCronograma] = {}
    for aba, videos in paginas.items():
        for video in videos:
            item = ler_item(video, ABAS.get(aba, VIDEO), hora_local=hora_local)
            if item is not None and item.quando.date() >= desde:
                if item.relacionado_id:
                    item = replace(item, relacionado=conhecidos.get(item.relacionado_id, ""))
                itens.setdefault(item.id, item)
    return sorted(itens.values(), key=lambda i: (i.quando, i.titulo))


def relacionados_sem_titulo(paginas: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Os ids de vídeos relacionados que nenhuma página lida traz (o sermão é mais antigo)."""
    lidos = {str(v.get("videoId")) for videos in paginas.values() for v in videos}
    faltam: list[str] = []
    for videos in paginas.values():
        for video in videos:
            alvo = id_do_relacionado(video)
            if alvo and alvo not in lidos and alvo not in faltam:
                faltam.append(alvo)
    return faltam


def titulo_pelo_oembed(pg, video_id: str) -> str:
    """O título de um vídeo pelo oEmbed do YouTube (público ou não listado), pelo próprio navegador
    da automação; "" se não deu (privado, removido, sem rede)."""
    url = (
        f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={video_id}"
    )
    try:
        resposta = pg.request.get(url, timeout=15_000)
        return " ".join(str(resposta.json().get("title") or "").split()) if resposta.ok else ""
    except Exception:  # um título que falta não estraga o cronograma
        return ""


def _e_a_lista(resposta) -> bool:
    return "list_creator_videos" in resposta.url and resposta.request.method == "POST"


def ler_cronograma(studio: Studio, desde: date) -> list[ItemDoCronograma]:
    """Abre as listas de Shorts e de vídeos do Studio e lê o JSON delas até passar de `desde`."""
    pg = studio.pagina
    limite = datetime.combine(desde, datetime.min.time()).timestamp()
    paginas: dict[str, list[dict[str, Any]]] = {}
    for aba in ABAS:
        with pg.expect_response(_e_a_lista, timeout=90_000) as chegou:
            pg.goto(f"{STUDIO}/channel/{studio.canal}/videos/{aba}", wait_until="domcontentloaded")
        corpo = chegou.value.json()
        videos = list(corpo.get("videos") or [])
        for _ in range(MAXIMO_DE_PAGINAS - 1):
            momentos = [momento_na_lista(v) for v in corpo.get("videos") or []]
            if not corpo.get("nextPageToken") or not momentos or min(momentos) < limite:
                break
            seguinte = pg.locator("ytcp-table-footer #navigate-after").first
            seguinte.wait_for(timeout=30_000)
            with pg.expect_response(_e_a_lista, timeout=60_000) as chegou:
                seguinte.click()
            corpo = chegou.value.json()
            videos += list(corpo.get("videos") or [])
        paginas[aba] = videos
    titulos = {
        alvo: titulo
        for alvo in relacionados_sem_titulo(paginas)[:MAXIMO_DE_TITULOS_A_BUSCAR]
        if (titulo := titulo_pelo_oembed(pg, alvo))
    }
    return juntar(paginas, desde, titulos=titulos)


def achar_na_lista(
    videos: list[dict[str, Any]], *, video_id: str = "", titulo: str = ""
) -> dict[str, Any] | None:
    """O vídeo da lista do Studio com este id, ou (sem id) o mais recente com este título."""
    if video_id:
        return next((v for v in videos if v.get("videoId") == video_id), None)
    procurado = " ".join(titulo.split()).casefold()
    if not procurado:
        return None
    return next(
        (v for v in videos if " ".join(str(v.get("title") or "").split()).casefold() == procurado),
        None,
    )


def procurar_short(
    studio: Studio, *, video_id: str = "", titulo: str = ""
) -> ItemDoCronograma | None:
    """Como o Short está no canal, pela 1ª página da lista de Shorts (os mais recentes): o item se
    está programado ou já saiu público; None se não está lá ou ainda é rascunho/privado. Serve para
    conferir uma postagem que deu erro no fim (o Studio pode ter programado assim mesmo)."""
    pg = studio.pagina
    with pg.expect_response(_e_a_lista, timeout=90_000) as chegou:
        pg.goto(f"{STUDIO}/channel/{studio.canal}/videos/short", wait_until="domcontentloaded")
    videos = list(chegou.value.json().get("videos") or [])
    video = achar_na_lista(videos, video_id=video_id, titulo=titulo)
    return ler_item(video, SHORT) if video is not None else None


def comeco_do_mes_passado(hoje: date) -> date:
    return date(hoje.year - 1, 12, 1) if hoje.month == 1 else date(hoje.year, hoje.month - 1, 1)
