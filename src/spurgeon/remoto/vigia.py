"""O vigia dos avisos (Remoto.md, R6): olha os projetos de tempos em tempos e avisa o celular.

Ele lê o `estado.json` de cada projeto em vez de ouvir o trabalhador. Assim vale para o que o Studio
do PC processa e para o que o celular pedir. Avisa quando um projeto:

* passa a **esperar a revisão** (o pipeline parou numa revisão humana);
* **termina** (com quantos Shorts estão renderizados, para revisar);
* tem **mais Shorts renderizados** sem ter passado por "em andamento" entre duas olhadas;
* **para com erro**.

A primeira olhada só tira a foto: não avisa nada do que já estava assim antes de o servidor abrir.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.core.projeto import PastasProjeto, listar_projetos
from spurgeon.remoto.avisos import Aviso
from spurgeon.remoto.dados import shorts_no_disco

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Foto:
    nome: str
    concluido: bool
    esperando: bool
    erro: bool
    renderizados: int
    marcados: int


def foto_do_projeto(pasta: Path, nome: str) -> Foto | None:
    estado = carregar_estado(pasta)
    if estado is None:
        return None
    status = [e.status for e in estado.etapas]
    shorts = shorts_no_disco(PastasProjeto(pasta))
    return Foto(
        nome=nome,
        concluido=bool(status) and all(s == StatusEtapa.CONCLUIDA for s in status),
        esperando=StatusEtapa.AGUARDANDO_REVISAO in status,
        erro=StatusEtapa.ERRO in status,
        renderizados=sum(1 for s in shorts if s.numero is not None and s.arquivo is not None),
        marcados=sum(1 for s in shorts if s.numero is not None),
    )


def fotos(pasta_projetos: Path) -> dict[str, Foto]:
    achadas: dict[str, Foto] = {}
    for projeto in listar_projetos(pasta_projetos):
        try:
            foto = foto_do_projeto(projeto.pasta, projeto.nome)
        except (OSError, ValueError):
            continue
        if foto is not None:
            achadas[projeto.pasta.name] = foto
    return achadas


def _link(ident: str) -> str:
    return f"/#/p/{quote(ident, safe='')}"


def avisos_das_mudancas(antes: dict[str, Foto], depois: dict[str, Foto]) -> list[Aviso]:
    avisos: list[Aviso] = []
    for ident, agora in depois.items():
        velha = antes.get(ident)
        if velha is None:
            continue  # projeto que apareceu agora: a próxima olhada já compara
        texto = ""
        if agora.erro and not velha.erro:
            texto = "O processamento parou com erro. Toque para ver o log."
        elif agora.esperando and not velha.esperando:
            texto = "Esperando a sua revisão."
        elif agora.concluido and not velha.concluido:
            texto = "Processamento concluído."
            if agora.marcados:
                texto = (
                    f"Pronto: {agora.renderizados} de {agora.marcados} Shorts renderizados. "
                    "Toque para revisar."
                )
        elif agora.concluido and agora.renderizados > velha.renderizados:
            novos = agora.renderizados - velha.renderizados
            texto = (
                f"{novos} Short{'s' if novos > 1 else ''} renderizado{'s' if novos > 1 else ''}."
            )
        if texto:
            avisos.append(Aviso(agora.nome, texto, url=_link(ident), marca=ident))
    return avisos


class Vigia:
    """Roda numa thread do servidor; `pode_avisar()` diz se é hora (acordado, avisos ligados)."""

    def __init__(
        self,
        pasta_projetos: Path,
        enviar: Callable[[Aviso], object],
        *,
        intervalo_s: float,
        pode_avisar: Callable[[], bool] = lambda: True,
        a_cada_olhada: Callable[[], object] = lambda: None,
    ):
        self.pasta_projetos = pasta_projetos
        self.enviar = enviar
        self.intervalo_s = intervalo_s
        self.pode_avisar = pode_avisar
        #: o que mais roda a cada olhada (os agendados que venceram viram "já postado")
        self.a_cada_olhada = a_cada_olhada
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._antes: dict[str, Foto] | None = None

    def olhar(self) -> list[Aviso]:
        """Uma olhada: compara com a anterior e manda o que mudou. Devolve os avisos mandados."""
        depois = fotos(self.pasta_projetos)
        antes, self._antes = self._antes, depois
        if antes is None or not self.pode_avisar():
            return []
        avisos = avisos_das_mudancas(antes, depois)
        for aviso in avisos:
            try:
                self.enviar(aviso)
            except Exception:  # um aviso que falha não derruba o vigia
                log.exception("Falha ao mandar o aviso de '%s'.", aviso.titulo)
        return avisos

    def _rodar(self) -> None:
        while not self._parar.is_set():
            try:
                self.a_cada_olhada()
            except Exception:
                log.exception("A tarefa periódica do vigia tropeçou (tenta de novo depois).")
            try:
                self.olhar()
            except Exception:
                log.exception("O vigia dos avisos tropeçou; tenta de novo na próxima olhada.")
            self._parar.wait(self.intervalo_s)

    def iniciar(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._rodar, name="vigia-dos-avisos", daemon=True
            )
            self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
