"""Ligar e fechar o servidor do controle remoto por dois atalhos na pasta do Studio.

* `spurgeon remoto ligar` sobe o servidor sem janela (o mesmo `pythonw -m spurgeon remoto iniciar`
  do atalho da inicialização), se ele ainda não está rodando.
* `spurgeon remoto reiniciar` fecha e liga de novo (o "Reiniciar o servidor" do menu do celular
  faz o mesmo por dentro: `ctx.reiniciar`, recusado no meio de um trabalho).
* `spurgeon remoto parar` fecha o servidor. Se ele está no meio de um trabalho (um processamento
  pedido pelo celular, uma postagem no YouTube Studio: processos filhos do servidor), pergunta
  antes e, com o sim, fecha também esse trabalho.

Os dois respondem numa caixa do Windows (os atalhos chamam o `pythonw.exe`, sem console).
`criar_atalhos` põe "Controle remoto - ligar" e "Controle remoto - fechar" na pasta do Studio.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import psutil

from spurgeon.core.config import raiz_repositorio
from spurgeon.remoto.inicializacao import _SEM_JANELA, python_sem_console, script_do_atalho

ATALHO_LIGAR = "Controle remoto - ligar.lnk"
ATALHO_FECHAR = "Controle remoto - fechar.lnk"
ATALHO_REINICIAR = "Controle remoto - reiniciar.lnk"
TITULO = "Studio Spurgeon - controle remoto"
_ESPERA_S = 10


def e_o_servidor(linha_de_comando: list[str]) -> bool:
    """A linha de comando é a do servidor (`… -m spurgeon remoto iniciar` ou `spurgeon remoto`)?"""
    partes = [p.lower() for p in linha_de_comando]
    for i, parte in enumerate(partes):
        if parte == "spurgeon" or Path(parte).name in ("spurgeon.exe", "spurgeon-script.py"):
            resto = partes[i + 1 :]
            return resto[:1] == ["remoto"] and (len(resto) == 1 or resto[1] == "iniciar")
    return False


def servidores(procurar: Callable[[], list] = psutil.process_iter) -> list[psutil.Process]:
    """Os processos do servidor que estão rodando. No Windows o `pythonw` do `.venv` é um
    lançador que abre o Python de verdade como filho: os dois entram (fechar um só não basta)."""
    achados = []
    for processo in procurar():
        if processo.pid == os.getpid():
            continue
        try:
            if e_o_servidor(processo.cmdline()):
                achados.append(processo)
        except (psutil.Error, OSError):
            continue
    return achados


def trabalhando(processos: list[psutil.Process]) -> list[psutil.Process]:
    """Os filhos dos servidores que não são outro servidor: o trabalhador de um processamento,
    o navegador de uma postagem, o FFmpeg de um áudio…"""
    proprios = {p.pid for p in processos}
    filhos = []
    for processo in processos:
        try:
            filhos.extend(f for f in processo.children(recursive=True) if f.pid not in proprios)
        except (psutil.Error, OSError):
            continue
    return filhos


def _caixa(texto: str, *, pergunta: bool = False) -> bool:
    """Uma caixa do Windows (a resposta de uma pergunta: True = Sim)."""
    if sys.platform != "win32":
        print(texto)
        return not pergunta
    import ctypes

    estilo = 0x04 | 0x30 if pergunta else 0x40  # Sim/Não com aviso; OK com informação
    resposta = ctypes.windll.user32.MessageBoxW(None, texto, TITULO, estilo | 0x40000)
    return resposta == 6 if pergunta else True


def abrir_servidor() -> object:
    """Sobe um servidor novo, sem janela e solto de quem o abriu (o atalho ou o servidor velho,
    no "Reiniciar" do celular).

    O `pythonw` do `.venv` é um lançador que põe o Python num "job" do Windows que morre junto com
    ele: o servidor novo pede para sair do job (`CREATE_BREAKAWAY_FROM_JOB`) e, se o Windows não
    deixa (o job já solta os filhos sozinho), abre sem pedir."""
    base = (
        _SEM_JANELA
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    comando = [str(python_sem_console()), "-m", "spurgeon", "remoto", "iniciar"]
    fora_do_job = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    if fora_do_job:
        try:
            return subprocess.Popen(
                comando, cwd=raiz_repositorio(), close_fds=True, creationflags=base | fora_do_job
            )
        except OSError:
            pass
    return subprocess.Popen(comando, cwd=raiz_repositorio(), close_fds=True, creationflags=base)


def ligar(
    *, avisar: Callable[..., bool] = _caixa, abrir: Callable[[], object] = abrir_servidor
) -> int:
    if servidores():
        avisar("O servidor do controle remoto já está ligado.")
        return 0
    abrir()
    avisar("Servidor do controle remoto ligado: em alguns segundos o celular já conecta.")
    return 0


def parar(*, avisar: Callable[..., bool] = _caixa, reiniciando: bool = False) -> int:
    rodando = servidores()
    if not rodando:
        if not reiniciando:
            avisar("O servidor do controle remoto já está fechado.")
        return 0
    filhos = trabalhando(rodando)
    if filhos and not avisar(
        "O servidor está no meio de um trabalho (um processamento pedido pelo celular ou uma "
        "postagem no YouTube). Fechar agora interrompe esse trabalho.\n\nFechar assim mesmo?",
        pergunta=True,
    ):
        return 1
    rodando = [*filhos, *rodando]  # o trabalho não fica rodando sozinho, sem o servidor
    for processo in rodando:
        try:
            processo.terminate()
        except (psutil.Error, OSError):
            continue
    _, vivos = psutil.wait_procs(rodando, timeout=_ESPERA_S)
    for processo in vivos:
        try:
            processo.kill()
        except (psutil.Error, OSError):
            continue
    if not reiniciando:
        avisar('Servidor do controle remoto fechado. Para ligar de novo, use o atalho "ligar".')
    return 0


def reiniciar(
    *, avisar: Callable[..., bool] = _caixa, abrir: Callable[[], object] = abrir_servidor
) -> int:
    """Fecha o servidor (perguntando, se ele está no meio de um trabalho) e liga de novo."""
    if parar(avisar=avisar, reiniciando=True):
        return 1  # a pessoa preferiu não interromper o trabalho
    abrir()
    avisar("Servidor do controle remoto reiniciado: em alguns segundos o celular já conecta.")
    return 0


def motivo_para_nao_reiniciar(ctx) -> str:
    """Por que o servidor não pode reiniciar agora pelo celular ("" = pode): reiniciar no meio
    de um trabalho o interromperia."""
    if ctx.fila.rodando:
        return "O PC está processando um projeto pedido pelo celular. Espere terminar."
    if ctx.postagens.ocupada():
        return "O PC está postando um Short no YouTube. Espere terminar."
    if ctx.criacoes.ocupada():
        return "A IA está criando um Short livre. Espere terminar."
    if ctx.novos is not None and ctx.novos.rodando():
        return "O PC está baixando um vídeo do canal. Espere terminar."
    return ""


def criar_atalhos(pasta: Path | None = None) -> list[Path]:
    """Os atalhos "ligar", "fechar" e "reiniciar" na pasta do Studio (ou em `pasta`)."""
    pasta = pasta or raiz_repositorio()
    icone = raiz_repositorio() / "recursos" / "icone_studio_spurgeon.ico"
    criados = []
    for nome, acao, descricao in (
        (ATALHO_LIGAR, "ligar", "Liga o servidor do controle remoto do Studio Spurgeon"),
        (ATALHO_FECHAR, "parar", "Fecha o servidor do controle remoto do Studio Spurgeon"),
        (
            ATALHO_REINICIAR,
            "reiniciar",
            "Reinicia o servidor do controle remoto do Studio Spurgeon",
        ),
    ):
        atalho = pasta / nome
        script = script_do_atalho(
            atalho, python_sem_console(), f"-m spurgeon remoto {acao}", raiz_repositorio(),
            icone=icone if icone.is_file() else None, descricao=descricao,
        )  # fmt: skip
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, timeout=60, creationflags=_SEM_JANELA,
        )  # fmt: skip
        criados.append(atalho)
    return criados
