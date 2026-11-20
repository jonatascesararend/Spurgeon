"""`spurgeon remoto …`: o servidor, o acesso pelo Tailscale, a inicialização e o aviso de teste.

Este módulo é importado ao montar a linha de comando: nada pesado no topo (o Starlette e o uvicorn
só entram quando o servidor sobe).
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import platformdirs

from spurgeon.core.config import NOME_APP

log = logging.getLogger("spurgeon.remoto")

NOME_DO_LOG = "remoto.log"


def configurar_log() -> Path:
    """Log em `%LOCALAPPDATA%\\Spurgeon\\Logs\\remoto.log`; sob o `pythonw` (sem console), a saída
    também vai para lá (`sys.stdout`/`sys.stderr` são None e quebrariam um `print`)."""
    pasta = platformdirs.user_log_path(NOME_APP, appauthor=False)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / NOME_DO_LOG
    raiz = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in raiz.handlers):
        handler = RotatingFileHandler(caminho, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        raiz.addHandler(handler)
    raiz.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:
        arquivo = caminho.open("a", encoding="utf-8", buffering=1)  # vive até o fim do processo
        sys.stdout = sys.stdout or arquivo
        sys.stderr = sys.stderr or arquivo
    return caminho


def _dizer(texto: str) -> None:
    log.info(texto)
    if sys.stdout is not None:
        print(texto)


def preparar_acesso(config, *, publicar: bool = True):
    """Acha a conta e o endereço no Tailscale, autoriza a conta se a lista está vazia e publica a
    porta na rede privada. Devolve a configuração (já gravada se mudou). Sem Tailscale, só avisa."""
    from spurgeon.remoto import tailscale
    from spurgeon.remoto.config import salvar_config_remoto

    try:
        dados = tailscale.status()
    except (tailscale.ErroTailscale, ValueError) as erro:
        _dizer(f"Tailscale: {erro}")
        return config
    mudancas = {}
    conta = tailscale.conta_do_pc(dados)
    if conta and not config.usuarios:
        mudancas["usuarios"] = [conta]
        _dizer(f"Conta autorizada: {conta}")
    endereco = tailscale.endereco_do_pc(dados)
    if endereco and endereco != config.endereco:
        mudancas["endereco"] = endereco
    if mudancas:
        config = config.model_copy(update=mudancas)
        salvar_config_remoto(config)
    if not tailscale.https_ligado(dados):
        _dizer("Atenção: o HTTPS do Tailscale está desligado (painel do Tailscale → DNS → HTTPS).")
    if publicar and not tailscale.porta_publicada(config.porta):
        try:
            tailscale.publicar(config.porta)
            _dizer(f"Publicado na rede privada: {config.endereco}")
        except tailscale.ErroTailscale as erro:
            _dizer(f"Não consegui publicar a porta no Tailscale: {erro}")
    return config


def _iniciar(args: argparse.Namespace) -> int:
    from spurgeon import __version__
    from spurgeon.core.config import carregar_config
    from spurgeon.remoto.avisos import Avisos
    from spurgeon.remoto.config import INTERVALO_DO_VIGIA_S, carregar_config_remoto
    from spurgeon.remoto.contexto import Contexto
    from spurgeon.remoto.livre import Criacoes
    from spurgeon.remoto.servidor import servir
    from spurgeon.remoto.vigia import Vigia

    caminho_do_log = configurar_log()
    config = preparar_acesso(carregar_config_remoto(), publicar=not args.sem_tailscale)
    if not config.usuarios:
        _dizer("Nenhuma conta autorizada: rode `spurgeon remoto configurar` (Tailscale ligado).")
    pasta_projetos = carregar_config().pastas.projetos
    ctx = Contexto(config, pasta_projetos, Avisos(), __version__)
    ctx.criacoes = Criacoes(ao_terminar=lambda c: avisar_da_criacao(ctx, c))
    vigia = Vigia(
        pasta_projetos, ctx.avisos.enviar, intervalo_s=INTERVALO_DO_VIGIA_S,
        pode_avisar=lambda: ctx.config.avisos_ligados and not ctx.config.dormindo,
    )  # fmt: skip
    _dizer(
        f"Controle remoto: {config.endereco or 'sem endereço do Tailscale'} (log: {caminho_do_log})"
    )
    try:
        servir(ctx, config.porta, ao_iniciar=vigia.iniciar, ao_terminar=vigia.parar)
    except OSError as erro:
        _dizer(f"A porta {config.porta} está ocupada (o servidor já está rodando?): {erro}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


def avisar_da_criacao(ctx, criacao) -> None:
    """O Short livre ficou pronto (ou falhou): avisa o celular, que pode ter saído do app."""
    from urllib.parse import quote

    from spurgeon.remoto.avisos import Aviso

    if ctx.config.dormindo or not ctx.config.avisos_ligados:
        return
    projeto = quote(criacao.projeto, safe="")
    if criacao.corte_id is not None:
        url, texto = f"/#/p/{projeto}/s/{criacao.corte_id}", criacao.texto
    else:
        url, texto = f"/#/p/{projeto}/livre", f"O Short livre não foi criado: {criacao.erro}"
    ctx.avisos.enviar(Aviso("Shorts livre", texto, url=url, marca=f"livre-{criacao.projeto}"))


def _configurar(args: argparse.Namespace) -> int:
    from spurgeon.remoto import inicializacao
    from spurgeon.remoto.config import carregar_config_remoto

    config = preparar_acesso(carregar_config_remoto())
    _dizer(f"Contas autorizadas: {', '.join(config.usuarios) or 'nenhuma'}")
    _dizer(f"Endereço do app: {config.endereco or 'desconhecido'}")
    if args.inicializacao:
        _dizer(f"Abre com o Windows: {inicializacao.ligar()}")
    return 0


def _inicializacao(args: argparse.Namespace) -> int:
    from spurgeon.remoto import inicializacao

    if args.acao == "ligar":
        _dizer(f"Atalho criado: {inicializacao.ligar()}")
    elif inicializacao.desligar():
        _dizer("O servidor não abre mais com o Windows.")
    else:
        _dizer("O servidor já não abria com o Windows.")
    return 0


def _aviso_teste(args: argparse.Namespace) -> int:
    from spurgeon.remoto.avisos import Aviso, Avisos

    entregas = Avisos().enviar(Aviso("Studio Spurgeon", args.texto, marca="teste"))
    if not entregas:
        _dizer("Nenhum aparelho inscrito: abra o app no celular e ative os avisos.")
        return 1
    for e in entregas:
        _dizer(f"{'entregue' if e.ok else 'falhou'} ({e.codigo}) {e.detalhe}".strip())
    return 0 if all(e.ok for e in entregas) else 1


_ACOES = {
    "iniciar": _iniciar,
    "configurar": _configurar,
    "inicializacao": _inicializacao,
    "aviso-teste": _aviso_teste,
}


def _despachar(args: argparse.Namespace) -> int:
    """`spurgeon remoto` sozinho = `iniciar`."""
    acao = getattr(args, "acao_remoto", None) or "iniciar"
    if acao == "iniciar" and not hasattr(args, "sem_tailscale"):
        args.sem_tailscale = False
    return _ACOES[acao](args)


def adicionar_comando(sub) -> None:
    """Põe `remoto` nos comandos do `spurgeon` (chamado pelo `__main__`)."""
    remoto = sub.add_parser("remoto", help="controle remoto pelo celular (branch controle-remoto)")
    acoes = remoto.add_subparsers(dest="acao_remoto")
    iniciar = acoes.add_parser("iniciar", help="sobe o servidor (o atalho do Windows chama este)")
    iniciar.add_argument("--sem-tailscale", action="store_true", help="não mexe no tailscale serve")
    configurar = acoes.add_parser("configurar", help="autoriza a conta e publica no Tailscale")
    configurar.add_argument("--inicializacao", action="store_true", help="e abre com o Windows")
    inic = acoes.add_parser("inicializacao", help="abrir (ou não) o servidor com o Windows")
    inic.add_argument("acao", choices=("ligar", "desligar"))
    teste = acoes.add_parser("aviso-teste", help="manda um aviso de teste ao celular")
    teste.add_argument("texto", nargs="?", default="Teste: os avisos do PC chegam neste aparelho.")
    remoto.set_defaults(funcao=_despachar)
