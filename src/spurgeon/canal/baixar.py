"""Baixar um vídeo do canal pelo menu "Opções → Baixar" do YouTube Studio (Remoto.md, R11 e R15).

O Studio entrega o MP4 em 720p (H.264, AAC): é a fonte que o autor escolheu. Provado na R0.2: um
sermão de 1h08 deu 503 MB em 54 s. O arquivo vai para a pasta de entrada do Studio com o nome que
o YouTube sugere (o título); se já está lá com conteúdo, não baixa de novo.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from spurgeon.canal.navegador import STUDIO, Studio
from spurgeon.core.projeto import sanitizar_nome


class FalhaNoDownload(RuntimeError):
    pass


def nome_do_arquivo(sugerido: str, titulo: str) -> str:
    """O nome sugerido pelo YouTube (o título + `.mp4`), seguro para o Windows."""
    base = Path(sugerido).stem if sugerido else titulo
    return f"{sanitizar_nome(base) or 'video'}.mp4"


def baixar_video(
    studio: Studio,
    video_id: str,
    titulo: str,
    pasta: Path,
    *,
    andamento: Callable[[str], None] = lambda _t: None,
) -> Path:
    pg = studio.pagina
    pronto = pasta / nome_do_arquivo("", titulo)
    if pronto.is_file() and pronto.stat().st_size > 0:
        andamento("O vídeo já estava baixado no PC.")
        return pronto
    andamento("Procurando o vídeo no YouTube Studio…")
    pg.goto(f"{STUDIO}/channel/{studio.canal}/videos/upload", wait_until="domcontentloaded")
    pg.wait_for_selector("ytcp-video-row", timeout=60_000)
    pg.wait_for_timeout(2500)
    linha = pg.locator("ytcp-video-row").filter(has=pg.locator(f'a[href*="/video/{video_id}/"]'))
    if not linha.count():
        raise FalhaNoDownload("O vídeo não está entre os mais recentes do canal no Studio.")
    linha = linha.first
    linha.hover()
    pg.wait_for_timeout(600)
    linha.locator(
        "ytcp-icon-button[aria-label='Opções'], button[aria-label='Opções']"
    ).first.click()
    pg.wait_for_timeout(800)
    andamento("Baixando o vídeo (o Studio entrega em 720p)…")
    with pg.expect_download(timeout=120_000) as pedido:
        pg.get_by_text("Baixar", exact=True).first.click()
    download = pedido.value
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome_do_arquivo(download.suggested_filename, titulo)
    parcial = destino.with_name(destino.name + ".parcial")
    download.save_as(str(parcial))  # espera o download terminar
    if download.failure():
        parcial.unlink(missing_ok=True)
        raise FalhaNoDownload(f"O download falhou: {download.failure()}")
    parcial.replace(destino)
    tamanho = destino.stat().st_size
    if tamanho < 1_000_000:
        raise FalhaNoDownload("O arquivo baixado está vazio ou incompleto.")
    andamento(f"Baixado: {destino.name} ({tamanho / 1e6:.0f} MB).")
    return destino
