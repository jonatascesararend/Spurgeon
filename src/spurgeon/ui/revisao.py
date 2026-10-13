"""Painéis de revisão humana (transcrição e cortes dos Shorts).

Cada etapa que pausa para revisão registra aqui o seu painel, pelo id da etapa. Se uma etapa
não tiver painel, a interface avisa e deixa aprovar.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from spurgeon.core.projeto import PastasProjeto
from spurgeon.ui.revisao_cortes import abrir_painel_cortes
from spurgeon.ui.revisao_transcricao import abrir_painel_transcricao

PainelRevisao = Callable[[ctk.CTk, PastasProjeto], None]

PAINEIS: dict[str, PainelRevisao] = {
    "revisar_transcricao": abrir_painel_transcricao,
    "aprovar_cortes": abrir_painel_cortes,
}


def abrir_revisao(janela: ctk.CTk, id_etapa: str, pastas: PastasProjeto) -> bool:
    """Abre o painel da etapa; devolve False se ela ainda não tem um."""
    painel = PAINEIS.get(id_etapa)
    if painel is None:
        return False
    painel(janela, pastas)
    return True
