"""Quem pode usar o controle remoto (Remoto.md, S8).

O servidor só escuta em `127.0.0.1`: de fora do PC, só se chega a ele pelo `tailscale serve`, que
atende o HTTPS na rede privada (só os aparelhos da conta chegam lá) e acrescenta o cabeçalho
`Tailscale-User-Login` com a conta de quem pediu. Por isso basta conferir esse cabeçalho contra a
lista de contas permitidas.

Limite conhecido: um programa rodando NESTE PC pode falar direto com `127.0.0.1` e inventar o
cabeçalho. Um programa local hostil já teria acesso aos projetos pelo disco, então o servidor não
protege contra ele.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

CABECALHO_DO_USUARIO = "tailscale-user-login"


def usuario_do_pedido(cabecalhos: Mapping[str, str]) -> str:
    """A conta do Tailscale do pedido, em minúsculas; "" se o pedido não veio pelo Tailscale."""
    for nome, valor in cabecalhos.items():
        if nome.lower() == CABECALHO_DO_USUARIO:
            return valor.strip().lower()
    return ""


def autorizado(cabecalhos: Mapping[str, str], usuarios: Iterable[str]) -> bool:
    quem = usuario_do_pedido(cabecalhos)
    return bool(quem) and quem in {u.strip().lower() for u in usuarios if u.strip()}
