"""O que as rotas compartilham: configuração, pasta de projetos, avisos e o "encerrar"."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto, salvar_config_remoto
from spurgeon.remoto.fila import Fila
from spurgeon.remoto.livre import Criacoes


@dataclass
class Contexto:
    config: ConfigRemoto
    pasta_projetos: Path
    avisos: Avisos
    versao: str
    #: grava o `remoto.json` (os testes trocam por nada ou por um caminho em `tmp_path`)
    gravar_config: Callable[[ConfigRemoto], object] = salvar_config_remoto
    #: pede ao servidor para sair (o `servir` liga isto ao uvicorn)
    encerrar: Callable[[], None] = lambda: None
    #: o processamento que o celular pede (um por vez no PC)
    fila: Fila = field(default_factory=Fila)
    #: o Short livre que a IA está criando (um por vez)
    criacoes: Criacoes = field(default_factory=Criacoes)
    _trava: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mudar_config(self, **campos) -> ConfigRemoto:
        with self._trava:
            self.config = self.config.model_copy(update=campos)
            self.gravar_config(self.config)
            return self.config
