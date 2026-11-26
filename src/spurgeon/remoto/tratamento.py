"""A prévia do tratamento de imagem no celular, como o cartão "Tratamento de imagem" do PC.

Um quadro do vídeo do projeto (o PC precisa ter o vídeo: um projeto criado do canal pode baixá-lo
antes de começar) sai do FFmpeg com os mesmos filtros do render (`render/previa.py`), então o que
o celular mostra é o que vai para o arquivo. A sugestão automática mede alguns quadros do trecho
usado (`render/imagem.sugerir_tratamento`) e grava o resultado nas opções do projeto.

Os quadros ficam em `_work/previa_tratamento/` (o `.nut` de cada instante é reaproveitado; o PNG
tratado é feito a cada pedido e não fica no disco).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from spurgeon.core.aparencia import AJUSTES_DO_TRATAMENTO, TratamentoImagem
from spurgeon.core.estado import carregar_estado
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.projeto import PastasProjeto
from spurgeon.remoto import configuracao
from spurgeon.render import previa

#: quantos quadros de exemplo ("Outro quadro" dá a volta neles) e quantos a sugestão mede
QUADROS_DE_EXEMPLO = 5
PASTA_DA_PREVIA = "previa_tratamento"


class SemVideo(RuntimeError):
    """O vídeo do projeto ainda não está no PC (ou sumiu)."""


def _video(pastas: PastasProjeto) -> tuple[Path, Any]:
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        raise SemVideo("O projeto não tem estado.json.")
    video = Path(estado.entrada.caminho)
    if estado.entrada.tamanho <= 0 or not video.is_file():
        raise SemVideo("O vídeo deste projeto ainda não está no PC.")
    return video, estado.opcoes


def instantes(duracao_s: float, opcoes) -> list[float]:
    """Instantes espalhados pelo trecho usado do vídeo (o começo e o fim escolhidos)."""
    inicio = max(0.0, float(opcoes.video_inicio_s or 0.0))
    fim = float(opcoes.video_fim_s or 0.0) or duracao_s
    fim = min(fim, duracao_s) if duracao_s > 0 else fim
    if fim <= inicio:
        inicio, fim = 0.0, duracao_s
    return [inicio + t for t in previa.instantes_de_exemplo(fim - inicio, QUADROS_DE_EXEMPLO)]


def _quadro_nut(pastas: PastasProjeto, indice: int) -> Path:
    video, opcoes = _video(pastas)
    info = sondar(video)
    lista = instantes(info.duracao_s, opcoes)
    instante = lista[indice % len(lista)]
    pasta = pastas.work / PASTA_DA_PREVIA
    nut = pasta / f"quadro_{round(instante * 100):08d}.nut"
    if not nut.exists():
        previa.extrair_quadro(video, info, instante, nut)
    return nut


def tratamento_do_pedido(valores: dict[str, str]) -> TratamentoImagem:
    """O tratamento que vem na consulta (`?brilho=10&contraste=-5…`); o que falta vale 0."""
    dados = {}
    for nome in AJUSTES_DO_TRATAMENTO:
        if nome in valores:
            dados[nome] = float(valores[nome])
    return TratamentoImagem(**dados)


def quadro_png(
    pastas: PastasProjeto, indice: int, tratamento: TratamentoImagem | None = None
) -> bytes:
    """O quadro de exemplo `indice` em PNG, com o tratamento (None ou neutro = o original)."""
    nut = _quadro_nut(pastas, indice)
    usar = tratamento if tratamento is not None and not tratamento.neutro else None
    with tempfile.TemporaryDirectory(prefix="spurgeon_previa_") as pasta:
        destino = Path(pasta) / "quadro.png"
        previa.quadro_para_png(nut, destino, tratamento=usar)
        return destino.read_bytes()


def sugerir(pastas: PastasProjeto) -> dict[str, Any]:
    """Mede alguns quadros, grava a sugestão nas opções do projeto (ligando o tratamento, se
    ela muda algo) e devolve a tela de opções nova, com `sugestao_neutra`."""
    imagens = []
    with tempfile.TemporaryDirectory(prefix="spurgeon_sugestao_") as pasta:
        for i in range(QUADROS_DE_EXEMPLO):
            destino = Path(pasta) / f"q{i}.png"
            previa.quadro_para_png(_quadro_nut(pastas, i), destino)
            with Image.open(destino) as aberta:
                imagens.append(aberta.convert("RGB"))
    from spurgeon.render.imagem import sugerir_tratamento

    sugerido = sugerir_tratamento(imagens)
    mudancas: dict[str, Any] = {
        f"tratamento_{nome}": round(valor, 1) for nome, valor in sugerido.model_dump().items()
    }
    if not sugerido.neutro:
        mudancas["tratamento_ativo"] = True
    resposta = configuracao.mudar(pastas, mudancas)
    resposta["sugestao_neutra"] = sugerido.neutro
    return resposta
