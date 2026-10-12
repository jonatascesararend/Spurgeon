"""Linha de comando: `python -m spurgeon` ou `spurgeon`."""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from spurgeon import __version__
from spurgeon.core.config import caminho_config, carregar_config
from spurgeon.core.erros import ErroSpurgeon
from spurgeon.core.hardware import detectar_hardware, recomendar, threads_para_uso
from spurgeon.core.preflight import Nivel, executar_preflight, tem_erro

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


def _gui(_: argparse.Namespace) -> int:
    print("A interface gráfica será criada na Fase 1. Por enquanto use: spurgeon doctor")
    return 0


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
