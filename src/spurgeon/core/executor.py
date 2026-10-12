"""Controla o subprocesso do trabalhador: inicia, manda comandos e entrega os eventos."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.config import ConfigApp
from spurgeon.core.projeto import PastasProjeto

_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COMANDOS = ("pausar", "retomar", "cancelar")


class ExecutorProjeto:
    """Um processamento por vez. Os eventos chegam por `eventos()` (sem bloquear)."""

    def __init__(self) -> None:
        self._processo: subprocess.Popen | None = None
        self._fila: queue.Queue[dict[str, Any]] = queue.Queue()

    @property
    def ativo(self) -> bool:
        return self._processo is not None and self._processo.poll() is None

    def iniciar(
        self,
        pasta_projeto: Path,
        config: ConfigApp,
        *,
        aprovar: str | None = None,
        ambiente: dict[str, str] | None = None,
    ) -> None:
        if self.ativo:
            raise RuntimeError("Já existe um processamento em andamento.")
        pastas = PastasProjeto(Path(pasta_projeto))
        pastas.work.mkdir(parents=True, exist_ok=True)
        arquivo_config = pastas.work / "config_execucao.json"
        escrever_json_atomico(arquivo_config, config.model_dump(mode="json"))

        comando = [
            sys.executable, "-X", "utf8", "-m", "spurgeon.core.trabalhador",
            "--projeto", str(pastas.raiz), "--config", str(arquivo_config),
        ]  # fmt: skip
        if aprovar:
            comando += ["--aprovar", aprovar]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **(ambiente or {})}
        self._fila = queue.Queue()
        self._processo = subprocess.Popen(
            comando,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=_SEM_JANELA,
        )
        erros: deque[str] = deque(maxlen=40)
        leitor_erros = threading.Thread(
            target=self._ler_erros, args=(self._processo, erros), daemon=True
        )
        leitor_erros.start()
        threading.Thread(
            target=self._ler_eventos,
            args=(self._processo, self._fila, leitor_erros, erros),
            daemon=True,
        ).start()

    @staticmethod
    def _ler_erros(processo, erros: deque[str]) -> None:
        for linha in processo.stderr:
            erros.append(linha.rstrip())

    @staticmethod
    def _ler_eventos(processo, fila, leitor_erros, erros) -> None:
        for linha in processo.stdout:
            linha = linha.rstrip("\n")
            if not linha:
                continue
            try:
                evento = json.loads(linha)
                if not isinstance(evento, dict) or "tipo" not in evento:
                    raise ValueError
            except ValueError:
                evento = {"tipo": "log", "texto": linha, "nivel": 20}
            fila.put(evento)
        codigo = processo.wait()
        leitor_erros.join(timeout=2)
        fila.put({"tipo": "processo_encerrado", "codigo": codigo, "stderr": list(erros)})

    def enviar(self, comando: str) -> None:
        if comando not in COMANDOS:
            raise ValueError(f"Comando desconhecido: {comando}")
        if not self.ativo:
            return
        try:
            self._processo.stdin.write(comando + "\n")
            self._processo.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # o processo acabou de terminar

    def eventos(self) -> list[dict[str, Any]]:
        """Devolve, sem esperar, os eventos que chegaram desde a última chamada."""
        recebidos = []
        while True:
            try:
                recebidos.append(self._fila.get_nowait())
            except queue.Empty:
                return recebidos

    def encerrar(self, espera_s: float = 8.0) -> None:
        """Pede o cancelamento e, se o processo não sair a tempo, o encerra à força."""
        if self._processo is None:
            return
        self.enviar("cancelar")
        try:
            self._processo.wait(timeout=espera_s)
        except subprocess.TimeoutExpired:
            self._processo.kill()
            self._processo.wait()
