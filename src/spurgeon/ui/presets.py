"""Presets de enquadramento e de tratamento de imagem, guardados na configuração do aplicativo."""

from __future__ import annotations

import logging
from collections.abc import Callable

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.config import ConfigApp, salvar_config


class RepositorioDePresets:
    """Lê e grava os presets nomeados (o estilo dos vídeos), fora do repositório do projeto."""

    def __init__(self, config: ConfigApp, salvar: Callable[[ConfigApp], object] = salvar_config):
        self._config = config
        self._salvar = salvar

    def enquadramentos(self) -> dict[str, Enquadramento]:
        return dict(self._config.presets_enquadramento)

    def tratamentos(self) -> dict[str, TratamentoImagem]:
        return dict(self._config.presets_tratamento)

    def salvar_enquadramento(self, nome: str, preset: Enquadramento) -> None:
        self._config.presets_enquadramento[nome.strip()] = preset
        self._gravar()

    def salvar_tratamento(self, nome: str, preset: TratamentoImagem) -> None:
        self._config.presets_tratamento[nome.strip()] = preset
        self._gravar()

    def excluir_enquadramento(self, nome: str) -> None:
        self._config.presets_enquadramento.pop(nome, None)
        self._gravar()

    def excluir_tratamento(self, nome: str) -> None:
        self._config.presets_tratamento.pop(nome, None)
        self._gravar()

    def _gravar(self) -> None:
        try:
            self._salvar(self._config)
        except OSError:
            logging.getLogger(__name__).warning(
                "Não foi possível salvar os presets.", exc_info=True
            )
