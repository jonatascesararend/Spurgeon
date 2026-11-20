"""Os vídeos do canal, pela página "Conteúdo → Vídeos" do YouTube Studio (Remoto.md, S6 e R11).

Só a primeira página (os ~30 mais recentes): é onde está o sermão novo. Os sermões são vídeos
enviados (não transmissões), então ficam na aba "Vídeos" (os Shorts têm a aba deles).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from spurgeon.canal.navegador import STUDIO, Studio

MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9,
         "out": 10, "nov": 11, "dez": 12}  # fmt: skip


@dataclass(frozen=True)
class VideoDoCanal:
    id: str
    titulo: str
    duracao_s: int
    publicado: date | None
    situacao: str  # "Publicado", "Enviado", "Programado"…
    visibilidade: str  # "Público", "Não listado", "Privado"…

    def para_json(self) -> dict:
        return {"id": self.id, "titulo": self.titulo, "duracao_s": self.duracao_s,
                "publicado": self.publicado.isoformat() if self.publicado else "",
                "situacao": self.situacao, "visibilidade": self.visibilidade}  # fmt: skip


def ler_duracao(texto: str) -> int:
    """`1:08:31` → 4111; `54:12` → 3252; 0 se não há duração no texto."""
    achado = re.search(r"\b(?:(\d+):)?(\d{1,2}):(\d{2})\b", texto)
    if achado is None:
        return 0
    horas, minutos, segundos = (int(g) if g else 0 for g in achado.groups())
    return horas * 3600 + minutos * 60 + segundos


def ler_data(texto: str) -> date | None:
    """`17 de nov. de 2026` → 2026-11-17 (como o Studio em português mostra)."""
    achado = re.search(r"(\d{1,2}) de (\w{3})\w*\.? de (\d{4})", texto)
    if achado is None or achado.group(2).lower() not in MESES:
        return None
    try:
        return date(int(achado.group(3)), MESES[achado.group(2).lower()], int(achado.group(1)))
    except ValueError:
        return None


def ler_linha(dados: dict) -> VideoDoCanal | None:
    """Uma linha da tabela (o que `_LER_LINHAS` tirou da página) virando um vídeo."""
    achado = re.search(r"/video/([\w-]{11})/", dados.get("href") or "")
    if achado is None:
        return None
    data_e_situacao = " ".join((dados.get("data") or "").split())
    situacao = (ler_data(data_e_situacao) and data_e_situacao.rsplit(" ", 1)[-1]) or ""
    return VideoDoCanal(
        id=achado.group(1),
        titulo=(dados.get("titulo") or "").strip(),
        duracao_s=ler_duracao(dados.get("texto") or ""),
        publicado=ler_data(data_e_situacao),
        situacao=situacao,
        visibilidade=" ".join((dados.get("visibilidade") or "").split()),
    )


_LER_LINHAS = """rs => rs.map(r => ({
    href: (r.querySelector('a[href*="/video/"]') || {getAttribute: () => ''}).getAttribute('href'),
    titulo: (r.querySelector('#video-title') || {}).innerText || '',
    texto: (r.innerText || '').slice(0, 40),
    data: (r.querySelector('.tablecell-date') || {}).innerText || '',
    visibilidade: (r.querySelector('.tablecell-visibility') || {}).innerText || ''
}))"""


def listar_videos(studio: Studio) -> list[VideoDoCanal]:
    pg = studio.pagina
    pg.goto(f"{STUDIO}/channel/{studio.canal}/videos/upload", wait_until="domcontentloaded")
    pg.wait_for_selector("ytcp-video-row", timeout=60_000)
    pg.wait_for_timeout(2500)
    linhas = pg.eval_on_selector_all("ytcp-video-row", _LER_LINHAS)
    return [v for v in (ler_linha(d) for d in linhas) if v is not None]
