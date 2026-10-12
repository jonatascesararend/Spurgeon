"""Exceções do aplicativo."""

from __future__ import annotations


class ErroSpurgeon(Exception):
    """Base de todos os erros esperados do aplicativo."""


class Cancelado(ErroSpurgeon):
    """O usuário cancelou o processamento; o projeto continua retomável."""


class ErroConfiguracao(ErroSpurgeon):
    """Configuração ou opções inválidas."""


class ProjetoConflito(ErroSpurgeon):
    """Já existe uma pasta de projeto com esse nome, mas para outro vídeo."""

    def __init__(self, pasta, motivo: str):
        super().__init__(f"{motivo}: {pasta}")
        self.pasta = pasta
        self.motivo = motivo


class ErroFFmpeg(ErroSpurgeon):
    """O FFmpeg (ou o ffprobe) terminou com erro."""

    def __init__(self, comando: list[str], codigo: int | None, saida_erro: str):
        resumo = saida_erro.strip().splitlines()[-1] if saida_erro.strip() else "sem mensagem"
        super().__init__(f"FFmpeg falhou (código {codigo}): {resumo}")
        self.comando = comando
        self.codigo = codigo
        self.saida_erro = saida_erro
