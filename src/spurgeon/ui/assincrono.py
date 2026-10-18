"""Trabalho em segundo plano para a interface: roda numa thread e entrega o resultado na janela.

As threads de trabalho não mexem em widgets: quem entrega o resultado é a função `na_interface`
(`JanelaPrincipal._na_interface`), que agenda a chamada na thread da interface.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class Tarefas:
    def __init__(self, na_interface: Callable[[Callable[[], None]], None]):
        self._na_interface = na_interface
        self._pedidos: dict[str, int] = {}
        self._trava = threading.Lock()

    def rodar(
        self,
        funcao: Callable[[], Any],
        ao_terminar: Callable[[Any], None] | None = None,
        ao_falhar: Callable[[Exception], None] | None = None,
        *,
        chave: str | None = None,
    ) -> None:
        """Executa `funcao` fora da interface. Com `chave`, só o pedido mais novo de cada chave
        entrega o resultado (um slider que se mexe várias vezes só mostra a última prévia)."""
        with self._trava:
            numero = self._pedidos.get(chave or "", 0) + 1
            if chave is not None:
                self._pedidos[chave] = numero

        def vigente() -> bool:
            with self._trava:
                return chave is None or self._pedidos.get(chave) == numero

        def trabalho() -> None:
            try:
                resultado = funcao()
            except Exception as erro:  # a interface decide o que mostrar
                excecao = erro
                self._na_interface(lambda: ao_falhar(excecao) if ao_falhar and vigente() else None)
                return
            self._na_interface(
                lambda: ao_terminar(resultado) if ao_terminar and vigente() else None
            )

        threading.Thread(target=trabalho, daemon=True).start()
