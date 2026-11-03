"""A fila local de todos os projetos: o que o Spurgeon publica sozinho enquanto está aberto."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, Agendador, RegistroDePostagens
from spurgeon.publicacao.modelo import PedidoDeCancelar, Plataforma, Resultado
from spurgeon.publicacao.pacote import pacote_do_corte
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import carregar_cortes


def projetos_com_fila(pasta_dos_projetos: Path) -> list[PastasProjeto]:
    """Os projetos que têm alguma postagem esperando o agendador (sem abrir os outros)."""
    if not pasta_dos_projetos.is_dir():
        return []
    achados = []
    for pasta in sorted(p for p in pasta_dos_projetos.iterdir() if p.is_dir()):
        pastas = PastasProjeto(pasta)
        if (pastas.work / NOME_DO_ARQUIVO).is_file() and RegistroDePostagens(
            pastas.work / NOME_DO_ARQUIVO
        ).na_fila():
            achados.append(pastas)
    return achados


def rodar_fila(
    plataformas: dict[str, Plataforma],
    pasta_dos_projetos: Path,
    *,
    cancelado: PedidoDeCancelar | None = None,
    ao_mudar: Callable[[PastasProjeto, int, Resultado], None] | None = None,
) -> int:
    """Avança a fila de cada projeto. Devolve quantas postagens mudaram de estado."""
    mudaram = 0
    for pastas in projetos_com_fila(pasta_dos_projetos):
        try:
            cortes = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}
        except (OSError, ValueError):
            continue
        registro = RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO)

        def pacote(id_do_corte: int, pastas=pastas, cortes: dict[int, Corte] = cortes):
            corte = cortes.get(id_do_corte)
            return pacote_do_corte(pastas, corte) if corte is not None else None

        for id_do_corte, resultado in Agendador(plataformas, registro, pacote).rodar_pendentes(
            cancelado=cancelado
        ):
            mudaram += 1
            if ao_mudar is not None:
                ao_mudar(pastas, id_do_corte, resultado)
    return mudaram
