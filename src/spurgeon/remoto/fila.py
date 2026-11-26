"""Um processamento por vez no PC (Remoto.md, S7), mesmo com o celular e a janela pedindo.

O servidor roda o trabalhador do mesmo jeito que a janela (`core/executor.ExecutorProjeto`, com a
configuração da última execução do projeto, `_work/config_execucao.json`). Antes de começar, procura
qualquer trabalhador rodando no PC (`trabalhadores_rodando`, pelo `psutil`): pode ser o da janela. A
janela faz a mesma conferência antes de começar (`ui/app.py`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

import psutil

from spurgeon.core.config import ConfigApp, carregar_config
from spurgeon.core.estado import carregar_estado, salvar_estado
from spurgeon.core.executor import ExecutorProjeto
from spurgeon.core.projeto import PastasProjeto

log = logging.getLogger(__name__)

MODULO_DO_TRABALHADOR = "spurgeon.core.trabalhador"
#: o que o celular manda mudar nas opções do projeto para renderizar só os Shorts (como o
#: "Renderizar" do Studio: `ui/app.py`, `_renderizar_shorts`)
AJUSTES_DA_RENDERIZACAO = {
    "modo": "so_shorts",
    "pausar_aprovar_cortes": False,
    "pausar_revisar_transcricao": False,
}


class FilaOcupada(RuntimeError):
    pass


def trabalhadores_rodando(*, exceto: int | None = None) -> list[int]:
    """Os pids dos trabalhadores do Studio rodando neste PC (de qualquer projeto)."""
    achados = []
    for processo in psutil.process_iter(["pid", "cmdline"]):
        try:
            linha = processo.info.get("cmdline") or []
        except (psutil.Error, OSError):
            continue
        if MODULO_DO_TRABALHADOR in linha and processo.info["pid"] not in (exceto, os.getpid()):
            achados.append(processo.info["pid"])
    return achados


def config_da_execucao(pastas: PastasProjeto) -> ConfigApp:
    """A configuração da última execução do projeto (pastas, modelos, qualidade); senão a atual."""
    arquivo = pastas.work / "config_execucao.json"
    try:
        return ConfigApp.model_validate_json(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return carregar_config()


class Fila:
    def __init__(
        self,
        *,
        criar_executor: Callable[[], ExecutorProjeto] = ExecutorProjeto,
        procurar: Callable[[], list[int]] = trabalhadores_rodando,
    ):
        self._criar_executor = criar_executor
        self._procurar = procurar
        self._executor: ExecutorProjeto | None = None
        self._trava = threading.Lock()
        self.projeto: str = ""
        self.pausado = False
        self.etapa = ""
        self.fracao = 0.0
        self.ultimo_resultado = ""
        self.ultimo_erro = ""

    @property
    def rodando(self) -> bool:
        return self._executor is not None and self._executor.ativo

    def estado(self) -> dict[str, Any]:
        return {
            "rodando": self.rodando,
            "projeto": self.projeto if self.rodando else "",
            "pausado": self.pausado and self.rodando,
            "etapa": self.etapa if self.rodando else "",
            "fracao": round(self.fracao, 3),
            "ultimo_resultado": self.ultimo_resultado,
            "ultimo_erro": self.ultimo_erro,
        }

    def ocupada(self) -> str:
        """Por que não dá para começar agora ("" = dá)."""
        if self.rodando:
            return "O PC já está processando um projeto pelo celular."
        if self._procurar():
            return "O PC já está processando (pela janela do Studio). Espere terminar."
        return ""

    def iniciar(
        self,
        pastas: PastasProjeto,
        *,
        aprovar: str | None = None,
        ajustes: dict[str, Any] | None = None,
        so_os_shorts: list[int] | None = None,
        refazer: bool = False,
    ) -> None:
        """Começa o processamento do projeto. `so_os_shorts`: a renderização dos Shorts fica só
        nestes cortes (e, com `refazer`, renderiza de novo mesmo os que ainda valem)."""
        from spurgeon.render.shorts import esquecer_pedido_de_render, pedir_render_de

        with self._trava:
            motivo = self.ocupada()
            if motivo:
                raise FilaOcupada(motivo)
            estado = carregar_estado(pastas.raiz)
            if estado is None:
                raise FilaOcupada("O projeto não tem estado.json.")
            if ajustes:
                estado.opcoes = estado.opcoes.model_copy(update=ajustes)
                salvar_estado(pastas.raiz, estado)
            if so_os_shorts:
                pedir_render_de(pastas, so_os_shorts, refazer=refazer)
            else:
                esquecer_pedido_de_render(pastas)  # resto de um pedido que não chegou a valer
            executor = self._criar_executor()
            try:
                executor.iniciar(pastas.raiz, config_da_execucao(pastas), aprovar=aprovar)
            except BaseException:
                esquecer_pedido_de_render(pastas)
                raise
            self._executor = executor
            self.projeto, self.pausado, self.etapa, self.fracao = pastas.raiz.name, False, "", 0.0
            self.ultimo_resultado = self.ultimo_erro = ""
            threading.Thread(target=self._acompanhar, args=(executor, pastas), daemon=True).start()
            log.info("Processamento iniciado pelo celular: %s%s", pastas.raiz.name,
                     f" (aprovando {aprovar})" if aprovar else "")  # fmt: skip

    def enviar(self, comando: str) -> None:
        if not self.rodando:
            raise FilaOcupada("Nenhum processamento do celular está rodando.")
        assert self._executor is not None
        self._executor.enviar(comando)

    def _acompanhar(self, executor: ExecutorProjeto, pastas: PastasProjeto | None = None) -> None:
        """Lê os eventos do trabalhador (o pipe não pode encher) até ele terminar."""
        while True:
            for evento in executor.eventos():
                self._tratar(evento)
            if not executor.ativo:
                for evento in executor.eventos():
                    self._tratar(evento)
                if pastas is not None:  # um "só este Short" que não chegou à etapa não fica valendo
                    from spurgeon.render.shorts import esquecer_pedido_de_render

                    esquecer_pedido_de_render(pastas)
                return
            time.sleep(0.5)

    def _tratar(self, evento: dict[str, Any]) -> None:
        tipo = evento.get("tipo")
        if tipo == "progresso":
            self.etapa, self.fracao = evento.get("etapa", ""), evento.get("total", 0.0)
        elif tipo == "pausa":
            self.pausado = bool(evento.get("ativa"))
        elif tipo == "fim":
            self.ultimo_resultado = evento.get("resultado", "")
            self.ultimo_erro = evento.get("erro") or ""
            log.info("Processamento do celular terminou: %s %s", self.ultimo_resultado,
                     self.ultimo_erro)  # fmt: skip
        elif tipo == "processo_encerrado" and not self.ultimo_resultado:
            self.ultimo_resultado = "erro"
            self.ultimo_erro = "O processamento terminou de forma inesperada."
