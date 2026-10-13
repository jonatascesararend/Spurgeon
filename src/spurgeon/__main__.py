"""Linha de comando: `python -m spurgeon` ou `spurgeon`."""

from __future__ import annotations

import argparse
import contextlib
import sys
import threading
from pathlib import Path

from spurgeon import __version__
from spurgeon.core.config import caminho_config, carregar_config
from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.formatacao import formatar_duracao, resumir_linha_log
from spurgeon.core.hardware import detectar_hardware, recomendar, threads_para_uso
from spurgeon.core.pipeline import ResultadoPipeline
from spurgeon.core.preflight import Nivel, executar_preflight, tem_erro
from spurgeon.core.projeto import criar_ou_retomar_projeto, data_gravacao
from spurgeon.core.trabalhador import executar as executar_projeto
from spurgeon.ui.formulario import (
    ErroFormulario,
    ValoresFormulario,
    ler_data,
    para_opcoes,
    sugerir_nome,
)

_ROTULOS = {Nivel.OK: "[OK]   ", Nivel.AVISO: "[AVISO]", Nivel.ERRO: "[ERRO] "}


def _doctor(args: argparse.Namespace) -> int:
    config = carregar_config()
    verificacoes = executar_preflight(config, video=args.video)
    for v in verificacoes:
        print(f"{_ROTULOS[v.nivel]} {v.nome}: {v.detalhe}")
    if tem_erro(verificacoes):
        print("\nHá problemas a corrigir antes de processar.")
        return 1
    print("\nAmbiente pronto.")
    return 0


def _hardware(_: argparse.Namespace) -> int:
    config = carregar_config()
    perfil = detectar_hardware(config.pastas.projetos)
    rec = recomendar(perfil)
    print(f"CPU:    {perfil.cpu_nome}")
    print(f"        {perfil.nucleos_fisicos} núcleos, {perfil.threads_logicos} threads")
    print(f"RAM:    {perfil.ram_total_gb} GB ({perfil.ram_livre_gb} GB livres)")
    print(f"Disco:  {perfil.disco_livre_gb} GB livres")
    for g in perfil.gpus:
        vram = f"{g.vram_mb} MB" if g.vram_mb else "VRAM desconhecida"
        print(f"GPU:    {g.nome} ({g.fabricante}, {vram}, driver {g.driver})")
    print(f"CUDA:   {'utilizável' if perfil.cuda_utilizavel else 'não'} - {perfil.motivo_cuda}")
    print("\nUso de processamento:")
    for pct in (75, 100):
        print(f"  {pct}% = {threads_para_uso(perfil, pct)} threads")
    print("\nRecomendação:")
    print(
        f"  Whisper: {rec.whisper_modelo} em {rec.whisper_dispositivo} ({rec.whisper_compute_type})"
    )
    print(f"  LLM:     {rec.llm_modelo}")
    for aviso in rec.avisos:
        print(f"  ! {aviso}")
    return 0


def _config(_: argparse.Namespace) -> int:
    caminho = caminho_config()
    print(
        f"Arquivo de configuração: {caminho}" + ("" if caminho.exists() else " (ainda não existe)")
    )
    print(carregar_config().model_dump_json(indent=2))
    return 0


def _processar(args: argparse.Namespace) -> int:
    """Processa um vídeo sem a interface: o mesmo caminho que a janela usa."""
    config = carregar_config()
    valores = ValoresFormulario.de_opcoes(config.padroes)
    valores.limpar_audio = args.limpar_audio
    valores.normalizar_volume = args.normalizar
    valores.gerar_video_longo = not args.sem_video_longo
    valores.revisar_transcricao = False  # sem janela, ninguém para revisar
    valores.aprovar_cortes = False
    if args.limiar_pausa:
        valores.limiar_pausa = args.limiar_pausa
    if args.pausa_residual:
        valores.pausa_residual = args.pausa_residual
    try:
        opcoes = para_opcoes(valores, config.padroes)
        data = ler_data(args.data) if args.data else data_gravacao(args.video)
    except ErroFormulario as erro:
        print("Confira as opções:\n  " + "\n  ".join(erro.mensagens), file=sys.stderr)
        return 2
    if not args.video.is_file():
        print(f"O vídeo não existe: {args.video}", file=sys.stderr)
        return 2
    config.padroes = opcoes
    if args.projetos:
        config.pastas.projetos = args.projetos

    verificacoes = executar_preflight(config, video=args.video, opcoes=opcoes)
    for v in verificacoes:
        if v.nivel != Nivel.OK:
            print(f"{_ROTULOS[v.nivel]} {v.nome}: {v.detalhe}")
    if tem_erro(verificacoes):
        return 1

    projeto = criar_ou_retomar_projeto(
        config.pastas.projetos, args.video, args.nome or sugerir_nome(args.video), data, opcoes
    )
    print(("Retomando" if projeto.retomado else "Projeto criado") + f": {projeto.pastas.raiz}")

    ultimo = {"pct": -1}

    def emitir(evento: dict) -> None:
        if evento["tipo"] == "log":
            print(resumir_linha_log(evento["texto"]), flush=True)
        elif evento["tipo"] == "progresso":
            pct = int(evento["total"] * 100) // 5 * 5
            if pct != ultimo["pct"]:
                ultimo["pct"] = pct
                print(f"          [{pct:3d}% do total]", flush=True)

    cancelar, pausar = threading.Event(), threading.Event()
    try:
        resultado = executar_projeto(
            projeto.pastas.raiz, config, emitir=emitir, cancelar=cancelar, pausar=pausar
        )
    except KeyboardInterrupt:
        print("\nInterrompido. O projeto pode ser retomado rodando o mesmo comando.")
        return 130
    print(f"\nResultado: {resultado.value}. Pasta do projeto: {projeto.pastas.raiz}")
    return 0 if resultado == ResultadoPipeline.CONCLUIDO else 1


def _benchmark(args: argparse.Namespace) -> int:
    """Mede a velocidade do Whisper com um trecho do vídeo e estima o tempo de um sermão."""
    from spurgeon.transcricao.benchmark import medir_velocidade

    config = carregar_config()
    if not args.midia.is_file():
        print(f"O arquivo não existe: {args.midia}", file=sys.stderr)
        return 2
    print(f"Medindo o modelo {args.modelo} com {args.segundos:g} s de {args.midia.name}…")
    r = medir_velocidade(config, args.modelo, args.midia, trecho_s=args.segundos)
    print(f"  carga do modelo: {r.carga_s:.1f} s ({r.dispositivo}, {r.threads} threads)")
    print(f"  transcrição:     {r.transcricao_s:.1f} s para {r.trecho_s:.0f} s de áudio")
    print(f"  velocidade:      {r.fator:.2f}x o tempo real ({r.palavras} palavras)")
    print("  estimativa para sermões inteiros:")
    for minutos in (45, 60, 90):
        estimativa = formatar_duracao(r.segundos_para(minutos * 60))
        print(f"    {minutos} min de áudio  ->  cerca de {estimativa}")
    return 0


def _acordado(args: argparse.Namespace) -> int:
    """Mantém o PC acordado e sem bloquear por um tempo (útil para deixar algo rodando)."""
    import time

    from spurgeon.core.energia import impedir_suspensao

    limite = time.monotonic() + args.horas * 3600
    print(
        f"O PC não vai suspender, apagar a tela nem bloquear por até {args.horas:g} h. "
        "Ctrl+C encerra e devolve tudo ao normal.",
        flush=True,
    )
    try:
        with impedir_suspensao(sem_bloqueio=True):
            while time.monotonic() < limite:
                time.sleep(5)
    except KeyboardInterrupt:
        pass
    print("Encerrado: o PC voltou a seguir as configurações normais de energia.")
    return 0


def _gui(_: argparse.Namespace) -> int:
    try:
        from spurgeon.ui.app import executar
    except ImportError:
        print(
            "A interface precisa do customtkinter. Instale com: uv sync --extra ui", file=sys.stderr
        )
        return 1
    return executar()


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            fluxo.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="spurgeon", description="Studio Spurgeon")
    parser.add_argument("--version", action="version", version=f"spurgeon {__version__}")
    sub = parser.add_subparsers(dest="comando")

    doctor = sub.add_parser("doctor", help="verifica o ambiente (pré-verificação)")
    doctor.add_argument("--video", type=Path, help="vídeo a processar (checa o espaço em disco)")
    doctor.set_defaults(funcao=_doctor)
    sub.add_parser("hardware", help="mostra o hardware e a recomendação").set_defaults(
        funcao=_hardware
    )
    sub.add_parser("config", help="mostra a configuração atual").set_defaults(funcao=_config)
    sub.add_parser("gui", help="abre a interface gráfica").set_defaults(funcao=_gui)

    acordado = sub.add_parser("acordado", help="impede o PC de suspender, apagar a tela e bloquear")
    acordado.add_argument("--horas", type=float, default=12.0, help="por quanto tempo (padrão: 12)")
    acordado.set_defaults(funcao=_acordado)

    benchmark = sub.add_parser("benchmark", help="mede a velocidade do Whisper nesta máquina")
    benchmark.add_argument("midia", type=Path, help="vídeo ou áudio de onde tirar o trecho")
    benchmark.add_argument("--modelo", default="large-v3-turbo", help="tamanho do modelo Whisper")
    benchmark.add_argument("--segundos", type=float, default=60.0, help="duração do trecho medido")
    benchmark.set_defaults(funcao=_benchmark)

    processar = sub.add_parser("processar", help="processa um vídeo sem a interface")
    processar.add_argument("video", type=Path)
    processar.add_argument("--nome", help="nome do sermão (padrão: o nome do arquivo)")
    processar.add_argument("--data", help="data da gravação, dd/mm/aaaa (padrão: a do vídeo)")
    processar.add_argument("--projetos", type=Path, help="pasta dos projetos")
    processar.add_argument("--limpar-audio", action="store_true", help="limpa o ruído do áudio")
    processar.add_argument("--normalizar", action="store_true", help="normaliza o volume")
    processar.add_argument("--sem-video-longo", action="store_true", help="não gera o vídeo longo")
    processar.add_argument("--limiar-pausa", help="tamanho mínimo da pausa a cortar, em s (10,0)")
    processar.add_argument("--pausa-residual", help="pausa mantida em cada corte, em s (1,0)")
    processar.set_defaults(funcao=_processar)

    args = parser.parse_args(argv)
    if not getattr(args, "funcao", None):
        parser.print_help()
        return 0
    try:
        return args.funcao(args)
    except ErroSpurgeon as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
