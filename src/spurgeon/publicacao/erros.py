"""Erros da publicação nas redes."""

from __future__ import annotations

from spurgeon.core.erros import ErroSpurgeon


class ErroDePublicacao(ErroSpurgeon):
    """Não deu para publicar ou agendar (a mensagem já é para a pessoa ler)."""


class FaltaConfigurar(ErroDePublicacao):
    """A conta ainda não foi conectada, ou falta um dado dela."""


class ReconectarNecessario(ErroDePublicacao):
    """A rede recusou o acesso guardado (revogado ou vencido): é preciso conectar de novo."""


class ErroDeRede(ErroDePublicacao):
    """A rede respondeu com erro (ou não respondeu). `codigo` é o status HTTP, 0 sem resposta."""

    def __init__(self, mensagem: str, *, codigo: int = 0, corpo: str = ""):
        super().__init__(mensagem)
        self.codigo = codigo
        self.corpo = corpo

    @property
    def passageiro(self) -> bool:
        """Vale tentar de novo: sem conexão, limite momentâneo ou falha do lado da rede."""
        return self.codigo in (0, 408, 429) or self.codigo >= 500
