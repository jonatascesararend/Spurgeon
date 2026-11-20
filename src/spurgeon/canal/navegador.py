"""O navegador da automação (Remoto.md, S5): o Chrome instalado com um perfil só dela.

A pessoa faz o login na conta (e escolhe o canal da igreja) uma vez, à mão, nesse perfil; depois a
automação o abre sozinha. Antes de qualquer ação ela confere o canal pelo nome (`#entity-name`):
nunca age no canal errado (uma conta pode ter vários canais). O nome do canal fica na configuração
do PC (`remoto.json`, campo `canal`; `spurgeon remoto configurar --canal "Nome"`), nunca no código.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STUDIO = "https://studio.youtube.com"


#: o perfil da automação só abre uma vez: listar, baixar e postar esperam a vez um do outro
_NAVEGADOR = threading.Lock()


class CanalErrado(RuntimeError):
    pass


class SessaoCaiu(RuntimeError):
    pass


def pasta_do_perfil() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Spurgeon" / "navegador"


@dataclass(frozen=True)
class Studio:
    pagina: Any  # playwright Page
    canal: str  # id do canal (UC…)


def nome_do_canal() -> str:
    """O canal em que a automação pode agir, como a configuração do PC diz ("" = não definido)."""
    from spurgeon.remoto.config import carregar_config_remoto

    return carregar_config_remoto().canal.strip()


def conferir_canal(pagina, nome_esperado: str) -> str:
    """Abre o Studio e devolve o id do canal; levanta se não está logado ou se é outro canal."""
    if not nome_esperado.strip():
        raise CanalErrado(
            "O canal do YouTube não foi configurado neste PC: rode "
            '`spurgeon remoto configurar --canal "Nome do canal"`.'
        )
    pagina.goto(f"{STUDIO}/", wait_until="domcontentloaded", timeout=60_000)
    pagina.wait_for_timeout(5000)
    achado = re.search(r"/channel/([\w-]+)", pagina.url)
    if achado is None:
        raise SessaoCaiu(
            "O YouTube Studio não abriu logado: entre de novo na conta no navegador da automação."
        )
    nome = pagina.locator("#entity-name").first.inner_text().strip()
    if nome.casefold() != nome_esperado.casefold():
        raise CanalErrado(f"O Studio abriu no canal “{nome}”, não em “{nome_esperado}”.")
    return achado.group(1)


@contextmanager
def abrir_studio(*, visivel: bool = True, canal: str | None = None) -> Iterator[Studio]:
    """O Studio aberto no perfil da automação e no canal certo (fecha ao sair). `canal` None = o da
    configuração do PC."""
    esperado = nome_do_canal() if canal is None else canal
    from playwright.sync_api import sync_playwright

    with _NAVEGADOR, sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(
            str(pasta_do_perfil()), channel="chrome", headless=not visivel,
            viewport={"width": 1400, "height": 950}, locale="pt-BR",
        )  # fmt: skip
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            yield Studio(pagina, conferir_canal(pagina, esperado))
        finally:
            contexto.close()
