"""Do Short do projeto ao pacote que vai para as redes."""

from __future__ import annotations

from pathlib import Path

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, RegistroDePostagens
from spurgeon.publicacao.modelo import Pacote
from spurgeon.render.shorts import ler_registro
from spurgeon.shorts.dados import Corte


def registro_do_projeto(pastas: PastasProjeto) -> RegistroDePostagens:
    return RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO)


def arquivo_do_short(pastas: PastasProjeto, corte: Corte) -> Path | None:
    """O `.mp4` renderizado do Short (em `shorts_gerados/` ou em `postados/`), ou None."""
    entrada = ler_registro(pastas.shorts_renderizados).get(str(corte.id))
    if not entrada:
        return None
    caminho = pastas.shorts / entrada["arquivo"]
    return caminho if caminho.is_file() else None


def pacote_do_corte(pastas: PastasProjeto, corte: Corte) -> Pacote | None:
    """O vídeo e os textos do Short, ou None se ele ainda não foi renderizado."""
    arquivo = arquivo_do_short(pastas, corte)
    if arquivo is None:
        return None
    return Pacote(arquivo, corte.titulo, corte.descricao, tuple(corte.hashtags), corte.duracao)
