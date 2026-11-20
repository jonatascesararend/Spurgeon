"""A trava do celular (Remoto.md, R8): o celular abriu o projeto, o PC salva o que tinha e trava.

Três arquivos pequenos fazem a conversa entre o servidor e a janela do PC (processos diferentes):

* `_work/celular.json` (o servidor grava): `{"sessao": id, "desde": t, "batimento": t}`. Vale
  enquanto o batimento tem menos de `VALIDADE_S` (o app do celular renova a cada ~30 s); sair do
  projeto apaga o arquivo.
* `_work/celular_pc.json` (a janela grava): `{"sessao": id}`, o recibo de que o PC salvou o que
  tinha em edição para aquela sessão e travou o Studio. O servidor espera esse recibo antes de
  deixar o celular editar, mas só se a janela está com aquele projeto aberto.
* `janela_aberta.json`, ao lado do `config.toml` (a janela grava a cada ~2 s): `{"projeto": pasta,
  "batimento": t}`. Diz ao servidor se a janela está aberta e em qual projeto.

Tudo com escrita atômica. Este módulo não importa a interface nem o servidor: a janela também o usa.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from pathlib import Path

import platformdirs

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import NOME_APP
from spurgeon.core.projeto import PastasProjeto

ARQUIVO_DA_TRAVA = "celular.json"
ARQUIVO_DO_RECIBO = "celular_pc.json"
ARQUIVO_DA_JANELA = "janela_aberta.json"
#: sem batimento por este tempo, a trava some sozinha (o celular perdeu o sinal ou fechou o app)
VALIDADE_S = 90.0
#: a janela renova o batimento a cada ~2 s; sem ele por este tempo, a janela é dada como fechada
VALIDADE_DA_JANELA_S = 8.0
#: quanto o servidor espera o PC salvar antes de liberar o celular assim mesmo
ESPERA_DO_RECIBO_S = 6.0

Relogio = Callable[[], float]


def _ler(caminho: Path) -> dict:
    try:
        dados = ler_json(caminho)
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


# --- a trava (servidor) ---------------------------------------------------------------------


def trava(pastas: PastasProjeto, relogio: Relogio = time.time) -> dict | None:
    """A trava valendo agora, ou None."""
    dados = _ler(pastas.work / ARQUIVO_DA_TRAVA)
    batimento = dados.get("batimento")
    if not isinstance(batimento, (int, float)) or relogio() - batimento > VALIDADE_S:
        return None
    return dados


def ativa(pastas: PastasProjeto, relogio: Relogio = time.time) -> bool:
    return trava(pastas, relogio) is not None


def tomar(pastas: PastasProjeto, relogio: Relogio = time.time) -> str:
    """Toma (ou renova) a trava; devolve o id da sessão (o mesmo enquanto ela não venceu)."""
    agora = relogio()
    atual = trava(pastas, relogio)
    dados = (
        {**atual, "batimento": agora}
        if atual
        else {"sessao": uuid.uuid4().hex, "desde": agora, "batimento": agora}
    )
    pastas.work.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(pastas.work / ARQUIVO_DA_TRAVA, dados)
    return dados["sessao"]


def soltar(pastas: PastasProjeto) -> None:
    (pastas.work / ARQUIVO_DA_TRAVA).unlink(missing_ok=True)


# --- o recibo (janela) ----------------------------------------------------------------------


def recibo(pastas: PastasProjeto) -> str:
    return str(_ler(pastas.work / ARQUIVO_DO_RECIBO).get("sessao", ""))


def dar_recibo(pastas: PastasProjeto, sessao: str) -> None:
    pastas.work.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(pastas.work / ARQUIVO_DO_RECIBO, {"sessao": sessao})


# --- a janela aberta (janela) ---------------------------------------------------------------


def caminho_da_janela() -> Path:
    return platformdirs.user_config_path(NOME_APP, appauthor=False) / ARQUIVO_DA_JANELA


def anunciar_janela(projeto: Path | None, relogio: Relogio = time.time) -> None:
    caminho = caminho_da_janela()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(
        caminho, {"projeto": str(projeto) if projeto else "", "batimento": relogio()}
    )


def fechar_janela() -> None:
    caminho_da_janela().unlink(missing_ok=True)


def janela_com_o_projeto(pastas: PastasProjeto, relogio: Relogio = time.time) -> bool:
    """A janela do PC está aberta, com este projeto?"""
    dados = _ler(caminho_da_janela())
    batimento = dados.get("batimento")
    if not isinstance(batimento, (int, float)) or relogio() - batimento > VALIDADE_DA_JANELA_S:
        return False
    projeto = dados.get("projeto") or ""
    try:
        return bool(projeto) and Path(projeto).resolve() == pastas.raiz.resolve()
    except OSError:
        return False


def esperar_o_pc(
    pastas: PastasProjeto,
    sessao: str,
    *,
    espera_s: float = ESPERA_DO_RECIBO_S,
    dormir: Callable[[float], None] = time.sleep,
    relogio: Relogio = time.time,
) -> str:
    """Depois de tomar a trava: `"sem_janela"` (ninguém a avisar), `"salvou"` (o PC deu o recibo) ou
    `"sem_resposta"` (a janela está no projeto, mas não respondeu a tempo)."""
    if not janela_com_o_projeto(pastas, relogio):
        return "sem_janela"
    limite = relogio() + espera_s
    while True:
        if recibo(pastas) == sessao:
            return "salvou"
        if relogio() >= limite:
            return "sem_resposta"
        dormir(0.25)
