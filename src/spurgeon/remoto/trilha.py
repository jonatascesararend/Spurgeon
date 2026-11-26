"""A trilha de áudio de fora enviada pelo celular, e o áudio pronto do modo "Só o áudio".

O celular manda o arquivo inteiro no corpo do pedido (sem multipart); o PC o grava em
`<projeto>/trilha_externa/` e põe o caminho em `opcoes.audio_externo`, o mesmo campo que a janela do
PC preenche ao escolher um arquivo. Só antes de o processamento começar (como as outras opções).
O celular nunca escreve um caminho do PC: só envia o arquivo ou pede para voltar ao som do vídeo.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from spurgeon.core.config import EXTENSOES_AUDIO
from spurgeon.core.erros import ErroConfiguracao, ErroFFmpeg
from spurgeon.core.estado import carregar_estado, salvar_estado
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.projeto import PastasProjeto, sanitizar_nome
from spurgeon.remoto.configuracao import OpcaoInvalida, configuracao, nao_comecou

#: além do áudio, um vídeo também serve (usa o som dele)
EXTENSOES_DA_TRILHA = (*EXTENSOES_AUDIO, ".mp4", ".mov", ".m4v", ".mkv")
LIMITE_BYTES = 4 * 1024**3  # um WAV de 3 horas em estéreo cabe com folga


def nome_da_trilha(nome_enviado: str) -> str:
    """Um nome seguro para o arquivo, com a extensão de origem (em minúsculas)."""
    original = Path(nome_enviado.replace("\\", "/")).name
    extensao = Path(original).suffix.lower()
    if extensao not in EXTENSOES_DA_TRILHA:
        aceitas = ", ".join(e[1:].upper() for e in EXTENSOES_AUDIO)
        raise OpcaoInvalida(f"Esse arquivo não é de áudio. Envie um destes: {aceitas}.")
    return sanitizar_nome(Path(original).stem, maximo=60) + extensao


def apagar_trilhas(pastas: PastasProjeto, *, exceto: Path | None = None) -> None:
    """Apaga as trilhas enviadas pelo celular (só a pasta delas; nunca um arquivo do PC)."""
    if not pastas.trilhas_externas.is_dir():
        return
    for arquivo in pastas.trilhas_externas.iterdir():
        if arquivo.is_file() and arquivo != exceto:
            with contextlib.suppress(OSError):
                arquivo.unlink()


def enviada_pelo_celular(pastas: PastasProjeto, caminho: str) -> bool:
    if not caminho.strip():
        return False
    try:
        return Path(caminho).resolve().parent == pastas.trilhas_externas.resolve()
    except OSError:
        return False


async def receber_trilha(
    pastas: PastasProjeto, nome_enviado: str, pedacos: AsyncIterator[bytes]
) -> dict:
    """Grava o arquivo que chega em `pedacos`, confere se tem som e o põe nas opções do projeto."""
    if not nao_comecou(pastas):
        raise OpcaoInvalida("O processamento já começou: a trilha de áudio não muda mais.")
    destino = pastas.trilhas_externas / nome_da_trilha(nome_enviado)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    recebidos = 0
    try:
        with parcial.open("wb") as arquivo:
            async for pedaco in pedacos:
                recebidos += len(pedaco)
                if recebidos > LIMITE_BYTES:
                    raise OpcaoInvalida("O arquivo passa de 4 GB: envie em MP3 ou M4A.")
                arquivo.write(pedaco)
        if recebidos == 0:
            raise OpcaoInvalida("O arquivo chegou vazio.")
        parcial.replace(destino)
    finally:
        parcial.unlink(missing_ok=True)
    try:
        info = sondar(destino)
    except (ErroFFmpeg, ErroConfiguracao):
        info = None
    if info is None or not info.tem_audio:
        destino.unlink(missing_ok=True)
        raise OpcaoInvalida("Não deu para ler o som desse arquivo. Ele é mesmo de áudio?")
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        destino.unlink(missing_ok=True)
        raise OpcaoInvalida("O projeto não tem estado.json.")
    estado.opcoes = estado.opcoes.model_copy(update={"audio_externo": str(destino)})
    salvar_estado(pastas.raiz, estado)
    apagar_trilhas(pastas, exceto=destino)  # a de antes não serve mais
    return configuracao(pastas)
