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
from spurgeon.remoto.novo import NovosProjetos
from spurgeon.remoto.postagem import Postagens


def listar_o_canal() -> list:
    from spurgeon.canal.listar import listar_videos
    from spurgeon.canal.navegador import abrir_studio

    with abrir_studio() as studio:
        return listar_videos(studio)


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
    #: a postagem no YouTube pelo navegador (uma por vez)
    postagens: Postagens = field(default_factory=Postagens)
    #: os vídeos do canal (abre o YouTube Studio; os testes trocam por uma lista)
    listar_canal: Callable[[], list] = field(default=None)  # type: ignore[assignment]
    #: o projeto novo a partir de um vídeo do canal (um por vez); None = o padrão, ligado à fila
    novos: NovosProjetos | None = None
    _trava: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.listar_canal is None:
            self.listar_canal = listar_o_canal
        if self.novos is None:
            self.novos = NovosProjetos(
                iniciar_processamento=lambda pastas: self.fila.iniciar(pastas),
                ocupada=self.fila.ocupada,
            )

    def mudar_config(self, **campos) -> ConfigRemoto:
        with self._trava:
            self.config = self.config.model_copy(update=campos)
            self.gravar_config(self.config)
            return self.config
