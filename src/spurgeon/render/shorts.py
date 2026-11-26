"""Etapa: renderiza os Shorts aprovados (9:16, 1080×1920, legenda dinâmica em ASS).

Um Short por vez, cada um num arquivo próprio e gravado só quando termina. Se o processamento
for interrompido, os já prontos são reaproveitados (conferidos por uma assinatura do corte e das
opções), e só o que falta é refeito.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from spurgeon.audio.normalizar import filtro_aplicar, filtro_medir, ler_medicao
from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.arquivos import escrever_json_atomico, escrever_texto_atomico, ler_json
from spurgeon.core.config import DURACAO_MAXIMA_SHORT_S
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.core.projeto import PastasProjeto
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.render import filtros as f
from spurgeon.render.fonte_do_short import (
    FonteDoShort,
    fonte_do_longo,
    fonte_do_original,
    leitura_do_corte,
    motivo_de_nao_servir,
    palavras_na_fonte,
    trechos_na_fonte,
)
from spurgeon.render.imagem import filtro_tratamento
from spurgeon.render.legenda import EstiloLegenda, montar_ass, palavras_do_short
from spurgeon.render.marca import (
    MarcaDagua,
    assinatura_da_marca,
    marca_para_o_projeto,
    montar_marca,
    trecho_do_filtro,
)
from spurgeon.render.video_longo import RenderizarVideoLongo, recortes_de_quadros
from spurgeon.shorts.agendamento import ler_horario, rotulo_do_horario, rotulo_do_postado
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import palavras_com_a_legenda, passa_do_limite
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.shorts.fila import limpar_render, marcar_render
from spurgeon.shorts.pausas import ha_o_que_cortar, remapear_palavras, trechos_mantidos
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo

VERSAO_DO_RENDER = 1  # não mude à toa: refaria todos os Shorts já prontos
#: o limite de um Short mais a folga de arredondamento do último quadro
DURACAO_MAXIMA_S = DURACAO_MAXIMA_SHORT_S + 0.5
FADE_S = 0.1
FADE_MINIMO_S = 0.02
#: fade em cada emenda do som onde uma pausa foi cortada (o corte cai no silêncio: é contra estalo)
MICRO_FADE_S = 0.01
_TOLERANCIA_DE_DURACAO_S = 0.6


#: a subpasta de `shorts_gerados/` onde ficam os Shorts já postados (marcados à mão ou porque a
#: hora agendada passou); o arquivo é movido para lá no Studio Shorts, e volta ao desmarcar
PASTA_DOS_POSTADOS = "postados"
#: a subpasta dos Shorts com horário agendado (o arquivo vai para lá ao agendar, e daí para
#: `postados/` quando a hora passa ou o Short é marcado como postado; volta se o horário sai)
PASTA_DOS_AGENDADOS = "agendados"
#: a subpasta dos Shorts tirados da seleção (volta quando forem marcados de novo)
PASTA_DOS_DESMARCADOS = "desmarcados"
#: as subpastas que um Short ocupa por causa do seu estado, não da seleção
_PASTAS_DE_ESTADO = (PASTA_DOS_POSTADOS, PASTA_DOS_AGENDADOS)


def pasta_do_estado(corte: Corte) -> str:
    """A subpasta de `shorts_gerados/` que o estado do corte pede: postado, agendado ou "" (a
    pasta principal). Postado vale mais que agendado."""
    if corte.postado:
        return PASTA_DOS_POSTADOS
    if corte.agendado_para:
        return PASTA_DOS_AGENDADOS
    return ""


@dataclass(frozen=True)
class ShortPlanejado:
    numero: int
    corte: Corte

    @property
    def nome(self) -> str:
        return f"short_{self.numero:02d}_{self.corte.slug}"

    @property
    def _prefixo(self) -> str:
        """O caminho dentro de `shorts_gerados/`: um Short já postado mora em `postados/` e um
        agendado em `agendados/`."""
        pasta = pasta_do_estado(self.corte)
        return f"{pasta}/" if pasta else ""

    @property
    def video(self) -> str:
        return f"{self._prefixo}{self.nome}.mp4"

    @property
    def texto(self) -> str:
        return f"{self._prefixo}{self.nome}.txt"


def planejar_shorts(cortes: list[Corte]) -> list[ShortPlanejado]:
    """Os cortes marcados, numerados na ordem em que aparecem no sermão."""
    marcados = sorted((c for c in cortes if c.incluir), key=lambda c: c.inicio)
    return [ShortPlanejado(i, c) for i, c in enumerate(marcados, start=1)]


def texto_de_publicacao(corte: Corte) -> str:
    """O .txt que acompanha cada Short: o que colar na hora de publicar."""
    linhas = []
    marcado_para = ler_horario(corte.agendado_para)
    if corte.postado:
        linhas = [f"JÁ POSTADO{_quando_foi_postado(corte)}", ""]
    elif marcado_para is not None:
        linhas = [f"AGENDADO PARA {rotulo_do_horario(marcado_para)}", ""]
    linhas += [
        f"Título: {corte.titulo}",
        "",
        "Descrição:",
        corte.descricao,
        "",
        "Hashtags: " + " ".join(corte.hashtags),
        "",
        f"Trecho no vídeo original: {segundos_para_rotulo(corte.inicio)} a "
        f"{segundos_para_rotulo(corte.fim)} ({corte.duracao:.0f} s)",
        f"Nota de qualidade: {corte.nota:.0f} de 100",
    ]
    if corte.alerta_contexto:
        linhas.append(f"Atenção (contexto): {corte.alerta_contexto}")
    return "\n".join(linhas) + "\n"


def _quando_foi_postado(corte: Corte) -> str:
    """` (marcado em 02/11/2026 às 18:30)`, ou nada se o dia não se sabe."""
    quando = rotulo_do_postado(corte.postado_em)
    return f" (marcado em {quando})" if quando else ""


def linhas_do_video_cortado(
    trechos: Sequence[Trecho], fps: Fraction, *, entrada: str = "0:v", saida: str = "bruto"
) -> list[str]:
    """As linhas do filtro que montam o vídeo só com os `trechos` (Short sem as pausas longas).

    Os tempos dos trechos contam desde o `-ss` da leitura. O vídeo vai para a grade de `fps` e
    cada trecho pega um número EXATO de quadros (`recortes_de_quadros`: o erro nunca passa de meio
    quadro e não acumula de um corte para o outro): `trim` escolhe os quadros k com
    `(k - 0.5)/fps <= tempo < (k + n - 0.5)/fps`, o mesmo critério do vídeo longo.

    O `fps` leva `start_time=0`: depois do `-ss` o primeiro quadro costuma chegar uns centésimos
    depois de 0 e cairia na posição 1 da grade; sem a posição 0 o primeiro trecho perderia um
    quadro e o vídeo inteiro ficaria um quadro adiantado em relação ao som.
    """
    taxa = f.texto_fps(fps)
    grade = f"fps={taxa}:start_time=0"
    recortes = recortes_de_quadros([(t.inicio, t.fim) for t in trechos], fps)

    def tempos(primeiro: int, quantos: int) -> str:
        inicio = Fraction(2 * primeiro - 1, 2) / fps
        fim = Fraction(2 * (primeiro + quantos) - 1, 2) / fps
        return f"start={f.numero_ffmpeg(float(inicio))}:end={f.numero_ffmpeg(float(fim))}"

    if len(recortes) == 1:
        return [f"[{entrada}]{grade},trim={tempos(*recortes[0])},setpts=PTS-STARTPTS[{saida}]"]
    saidas = "".join(f"[s{i}]" for i in range(len(recortes)))
    linhas = [f"[{entrada}]{grade},split={len(recortes)}{saidas}"]
    for i, recorte in enumerate(recortes):
        linhas.append(f"[s{i}]trim={tempos(*recorte)},setpts=PTS-STARTPTS[p{i}]")
    pedacos = "".join(f"[p{i}]" for i in range(len(recortes)))
    linhas.append(f"{pedacos}concat=n={len(recortes)}:v=1:a=0[{saida}]")
    return linhas


def linhas_do_audio_cortado(
    trechos: Sequence[Trecho], cadeia: str, *, entrada: str = "1:a", saida: str = "a",
    micro_fade_s: float = MICRO_FADE_S,
) -> list[str]:  # fmt: skip
    """As linhas do filtro que montam o som só com os `trechos`, e depois aplicam `cadeia`.

    O corte usa `atrim` (exato até a amostra). Cada emenda leva um micro-fade (saída no trecho que
    termina nela, entrada no que começa), contra o estalo; as pontas do Short não, que têm o fade
    de `cadeia`. `cadeia` vazia = o som sai direto da junção.
    """
    depois = f",{cadeia}" if cadeia else ""
    ultimo = len(trechos) - 1

    def corte(i: int, t: Trecho) -> str:
        passos = [
            f"atrim=start={f.numero_ffmpeg(t.inicio)}:end={f.numero_ffmpeg(t.fim)}",
            "asetpts=PTS-STARTPTS",
        ]
        fade = min(micro_fade_s, t.duracao / 2)
        if i > 0:
            passos.append(f"afade=t=in:st=0:d={f.numero_ffmpeg(fade)}")
        if i < ultimo:
            passos.append(
                f"afade=t=out:st={f.numero_ffmpeg(t.duracao - fade)}:d={f.numero_ffmpeg(fade)}"
            )
        return ",".join(passos)

    if len(trechos) == 1:
        return [f"[{entrada}]{corte(0, trechos[0])}{depois}[{saida}]"]
    saidas = "".join(f"[x{i}]" for i in range(len(trechos)))
    linhas = [f"[{entrada}]asplit={len(trechos)}{saidas}"]
    for i, trecho in enumerate(trechos):
        linhas.append(f"[x{i}]{corte(i, trecho)}[t{i}]")
    juntos = "".join(f"[t{i}]" for i in range(len(trechos)))
    linhas.append(f"{juntos}concat=n={len(trechos)}:v=0:a=1{depois}[{saida}]")
    return linhas


def filtro_de_video(
    info: InfoMidia,
    fps: Fraction,
    *,
    deslocamento_pct: float,
    legenda: str | None,
    marca: MarcaDagua | None = None,
    enquadramento: Enquadramento | None = None,
    tratamento: TratamentoImagem | None = None,
    zoom_do_short: Enquadramento | None = None,
    trechos: Sequence[Trecho] | None = None,
) -> str:
    """O grafo do vídeo: da entrada `0:v` até `[v]`.

    Recorte vertical (dentro do zoom, se houver, e com o zoom do Short), conversão para SDR,
    tratamento de imagem, ampliação para 1080×1920, marca d'água (entrada `2:v`) e legenda
    queimada, nesta ordem: a marca fica sob a legenda se as duas se encontrarem.

    Com `trechos` (Short sem as pausas longas) o vídeo é montado antes de tudo isso, só com esses
    trechos, em quadros exatos (`linhas_do_video_cortado`); sem eles o grafo é o de sempre.
    """
    cor = filtro_tratamento(tratamento) if tratamento is not None else None
    adiante = [
        f.recorte_vertical(info, deslocamento_pct, enquadramento, zoom_do_short),
        f.cadeia_para_sdr(info),
        *([cor] if cor else []),
        f"scale={f.LARGURA_SHORT}:{f.ALTURA_SHORT}:flags=lanczos",
        "setsar=1",
    ]
    if trechos:
        cortes = ";\n".join(linhas_do_video_cortado(trechos, fps))
        entrada, ate_a_ampliacao = f"{cortes};\n[bruto]", ",".join(adiante)
    else:
        entrada, ate_a_ampliacao = "[0:v]", ",".join([f"fps={f.texto_fps(fps)}", *adiante])
    final = ([f"ass={legenda}:fontsdir=fontes"] if legenda else []) + ["format=yuv420p"]
    if marca is None:
        return f"{entrada}{ate_a_ampliacao},{','.join(final)}[v]"
    sobreposicao = trecho_do_filtro(marca, base="base", entrada="2:v", saida="marcado")
    return f"{entrada}{ate_a_ampliacao}[base];{sobreposicao};[marcado]{','.join(final)}[v]"


def filtro_de_audio(
    canais: int | None, duracao_s: float, medido: dict[str, float] | None, alvo_lufs: float,
    *, fade_entrada_s: float = FADE_S, fade_saida_s: float = FADE_S,
) -> str:  # fmt: skip
    """Estéreo, volume do Short (se medido) e fade curto nas pontas contra estalos.

    A medição do volume precisa ter passado pelo MESMO estéreo (`_medir_volume`): dois canais
    iguais medem 3 dB acima do mono.
    """
    cadeia = [f.filtro_audio_estereo(canais)]
    if medido is not None:
        cadeia += [filtro_aplicar(alvo_lufs, medido), f"aresample={f.TAXA_AUDIO}"]
    fade_fora = max(0.0, duracao_s - fade_saida_s)
    cadeia += [
        f"afade=t=in:st=0:d={fade_entrada_s:.3f}",
        f"afade=t=out:st={f.numero_ffmpeg(fade_fora)}:d={fade_saida_s:.3f}",
    ]
    return ",".join(cadeia)


def fades_do_short(faladas, duracao_s: float) -> tuple[float, float]:
    """Duração do fade de entrada e de saída: 100 ms, menos se a fala começa ou termina colada.

    O fade protege contra estalos, mas não pode apagar o começo da primeira palavra nem o fim da
    última quando o corte caiu bem perto delas.
    """
    if not faladas:
        return FADE_S, FADE_S
    folga_inicio = faladas[0].inicio - 0.01
    folga_fim = duracao_s - faladas[-1].fim - 0.01
    return (
        min(FADE_S, max(FADE_MINIMO_S, folga_inicio)),
        min(FADE_S, max(FADE_MINIMO_S, folga_fim)),
    )


# --- registro dos Shorts já renderizados ------------------------------------------------------
# `shorts_renderizados.json`, por id do corte: o arquivo gerado (relativo a `shorts_gerados/`) e a
# assinatura do que o produziu. Com isso, mudar a seleção só renderiza o que é novo ou mudou; os
# outros só mudam de número no nome ou vão para `desmarcados/` (nada é apagado).

Registro = dict[str, dict[str, Any]]


def ler_registro(caminho: Path) -> Registro:
    try:
        dados = ler_json(caminho)
    except (OSError, ValueError):
        return {}
    if not isinstance(dados, dict):
        return {}
    return {
        k: v
        for k, v in dados.items()
        if isinstance(v, dict) and {"arquivo", "assinatura"} <= set(v)
    }


def arquivo_do_short_pronto(
    pastas: PastasProjeto, curto: ShortPlanejado, registro: Registro
) -> Path | None:
    """Onde está o `.mp4` deste Short: o que o registro diz (foi lá que a renderização o pôs) ou,
    se ele não está lá, o lugar que o corte tem agora. None se nenhum dos dois existe.

    Quem confere os Shorts DEPOIS de renderizar não pode confiar só no corte de agora: o Studio
    Shorts continua à mão durante a renderização, e marcar um Short como postado, agendá-lo ou
    mudar o título troca o lugar (ou o nome) que o corte pede, sem que o arquivo tenha se mexido.
    """
    entrada = registro.get(str(curto.corte.id))
    candidatos = [curto.video] if entrada is None else [entrada["arquivo"], curto.video]
    return next((c for c in (pastas.shorts / n for n in candidatos) if c.is_file()), None)


SITUACAO_NOVO = "novo"  # nunca foi renderizado
SITUACAO_PRONTO = "pronto"
SITUACAO_ALTERADO = "alterado"  # o corte ou a legenda mudou depois de renderizado


def assinatura_visual(opcoes) -> str:
    """A aparência do Short que não depende do corte: zoom, cor, recorte e marca d'água.

    Serve à tela (o selo "renderizado" some se a aparência mudou); quem decide de fato o que
    renderiza de novo é a `assinatura_do_short`.
    """
    marca = None
    if opcoes.marca_nos_curtos:
        arquivo = Path(opcoes.marca_imagem)
        estatistica = arquivo.stat() if arquivo.is_file() else None
        marca = [
            opcoes.marca_imagem, estatistica.st_size if estatistica else 0,
            int(estatistica.st_mtime) if estatistica else 0, opcoes.marca_remover_fundo,
            opcoes.marca_canto, opcoes.marca_tamanho_pct,
        ]  # fmt: skip
    dados = {
        "zoom": opcoes.enquadramento_nos_curtos.model_dump(),
        "tratamento": opcoes.tratamento_nos_curtos.model_dump(),
        "recorte": opcoes.recorte_deslocamento_x_pct,
        "marca": marca,
    }
    if opcoes.enquadramento_do_short.ativo:  # só entra quando existe: o que saiu continua valendo
        dados["zoom_do_short"] = opcoes.enquadramento_do_short.model_dump()
    return hashlib.sha1(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:10]


def _resumo_do_ajuste_fino(corte: Corte) -> str:
    return hashlib.sha1(json.dumps(corte.ajuste_fino, sort_keys=True).encode()).hexdigest()[:10]


def detalhes_do_registro(corte: Corte, visual: str | None = None) -> dict[str, Any]:
    """O que o registro guarda além da assinatura, para a tela saber se o Short ainda vale."""
    detalhes: dict[str, Any] = {
        "corte": [round(corte.inicio, 3), round(corte.fim, 3)],
        "legenda": hashlib.sha1(corte.legenda.encode()).hexdigest()[:10],
    }
    if corte.saltos:  # só entram quando existem: um registro antigo, sem eles, continua valendo
        detalhes["saltos"] = [[round(de, 3), round(ate, 3)] for de, ate in corte.saltos]
    if corte.pausa_s:
        detalhes["pausa_s"] = round(corte.pausa_s, 2)
    if corte.ajuste_fino:
        detalhes["ajuste_fino"] = _resumo_do_ajuste_fino(corte)
    if visual is not None:
        detalhes["visual"] = visual
    return detalhes


def situacao_do_short(
    corte: Corte, entrada: dict[str, Any] | None, arquivo_existe: bool, *, com_legenda: bool,
    visual: str | None = None,
) -> str:  # fmt: skip
    """`novo`, `pronto` ou `alterado`, conforme o que o registro lembra do último render.

    É um aviso para a tela: quem decide de fato o que renderiza de novo é a assinatura.
    Registros antigos, sem os detalhes, valem como prontos.
    """
    if entrada is None or not arquivo_existe:
        return SITUACAO_NOVO
    atual = detalhes_do_registro(corte)
    if "corte" in entrada and entrada["corte"] != atual["corte"]:
        return SITUACAO_ALTERADO
    if entrada.get("saltos") != atual.get("saltos") or entrada.get("pausa_s") != atual.get(
        "pausa_s"
    ):
        return SITUACAO_ALTERADO
    if com_legenda and "legenda" in entrada and entrada["legenda"] != atual["legenda"]:
        return SITUACAO_ALTERADO
    if com_legenda and entrada.get("ajuste_fino") != atual.get("ajuste_fino"):
        return SITUACAO_ALTERADO
    if visual is not None and "visual" in entrada and entrada["visual"] != visual:
        return SITUACAO_ALTERADO
    return SITUACAO_PRONTO


def assinatura_do_short(
    hash_do_video: str,
    opcoes,
    corte: Corte,
    transferencia: str | None,
    com_legenda: bool,
    marca: MarcaDagua | None,
    *,
    fonte_id: str | None = None,
) -> str:
    """O que decide se um Short pronto ainda vale: o corte e tudo que muda a imagem ou o som.

    `fonte_id` é a identidade do vídeo longo quando o Short sai dele (`FonteDoShort.identidade`);
    só entra quando existe, então um Short cortado do original continua valendo como sempre.
    """
    preset = opcoes.preset_qualidade()
    dados = {
        "versao": VERSAO_DO_RENDER,
        "video": hash_do_video,
        "corte": [round(corte.inicio, 3), round(corte.fim, 3)],
        "legenda_corrigida": hashlib.sha1(corte.legenda.encode()).hexdigest()[:10]
        if com_legenda
        else "",
        "hdr": transferencia,
        "codec": [preset.preset, preset.crf, opcoes.usar_nvenc],
        "legenda": [com_legenda, opcoes.legenda_posicao_vertical],
        "recorte": opcoes.recorte_deslocamento_x_pct,
        "volume": [opcoes.limpar_audio, opcoes.normalizar_volume, opcoes.lufs_shorts],
        "marca": assinatura_da_marca(marca.arquivo, marca) if marca else None,
    }
    # o estilo só entra quando existe: um Short renderizado sem zoom nem cor continua valendo
    if opcoes.enquadramento_nos_curtos.ativo:
        dados["zoom"] = opcoes.enquadramento_nos_curtos.model_dump()
    if opcoes.enquadramento_do_short.ativo:
        dados["zoom_do_short"] = opcoes.enquadramento_do_short.model_dump()
    if not opcoes.tratamento_nos_curtos.neutro:
        dados["tratamento"] = opcoes.tratamento_nos_curtos.model_dump()
    # o que a pessoa tirou do meio e as pausas encurtadas só contam quando existem
    if corte.saltos:
        dados["saltos"] = [[round(de, 3), round(ate, 3)] for de, ate in corte.saltos]
    if corte.pausa_s:
        dados["pausa_s"] = round(corte.pausa_s, 2)
    if com_legenda and corte.ajuste_fino:  # os tempos das palavras acertados à mão
        dados["ajuste_fino"] = _resumo_do_ajuste_fino(corte)
    if fonte_id:  # cortado do vídeo longo: se ele for refeito, o Short também é
        dados["fonte"] = fonte_id
    return hashlib.sha1(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:16]


def marca_dos_shorts(ctx: ContextoProjeto) -> MarcaDagua | None:
    """A marca d'água dos Shorts (PNG pronto, tamanho para 1080 de largura), ou None."""
    if not ctx.opcoes.marca_nos_curtos:
        return None
    if not Path(ctx.opcoes.marca_imagem).is_file():
        raise ErroFerramenta(
            f"A imagem da marca d'água não foi encontrada: {ctx.opcoes.marca_imagem}"
        )
    arquivo = marca_para_o_projeto(ctx.pastas.work, ctx.opcoes)
    return montar_marca(arquivo, ctx.opcoes, f.LARGURA_SHORT)


def _mover_par(origem_video: Path, destino_video: Path) -> None:
    """Move o `.mp4` e o `.txt` que o acompanha."""
    destino_video.parent.mkdir(parents=True, exist_ok=True)
    origem_video.replace(destino_video)
    texto = origem_video.with_suffix(".txt")
    if texto.exists():
        texto.replace(destino_video.with_suffix(".txt"))


@dataclass
class ResultadoDasMarcas:
    """O que `aplicar_marcas_de_postado` fez: os arquivos movidos e os que não deu."""

    movidos: list[str]
    falhas: list[str]


def aplicar_marcas_de_postado(pastas: PastasProjeto, cortes: Sequence[Corte]) -> ResultadoDasMarcas:
    """Move os Shorts já renderizados para `postados/` ou `agendados/` (ou os tira de lá),
    conforme o `postado` e o `agendado_para` de cada corte (o `.mp4` e o `.txt`), e acerta o
    registro.

    Marcar como postado leva o arquivo de onde estiver para `shorts_gerados/postados/`; agendar o
    leva para `shorts_gerados/agendados/` (quando a hora passa, o corte vira postado e ele segue
    para `postados/`). Tirar a marca o devolve a `shorts_gerados/` (ou a `desmarcados/`, se o
    corte está desmarcado). Postado vale mais que agendado, e agendado mais que desmarcado. Quem
    ainda não tem arquivo não muda nada: ao ser renderizado ele já vai para o lugar certo
    (`ShortPlanejado.video`).

    Um arquivo aberto em outro programa não pode ser movido no Windows: vai para `falhas`, sem
    interromper os outros (o lugar certo é acertado na próxima vez).
    """
    registro = ler_registro(pastas.shorts_renderizados)
    resultado = ResultadoDasMarcas([], [])
    mudou = False
    for corte in cortes:
        entrada = registro.get(str(corte.id))
        if entrada is None:
            continue
        relativo = Path(entrada["arquivo"])
        atual = pastas.shorts / relativo
        if not atual.is_file():
            continue
        pasta_do_corte = pasta_do_estado(corte)
        if pasta_do_corte:
            pasta_nova = Path(pasta_do_corte)
        elif relativo.parent.as_posix() in _PASTAS_DE_ESTADO:
            pasta_nova = Path() if corte.incluir else Path(PASTA_DOS_DESMARCADOS)
        else:
            continue
        if pasta_nova == relativo.parent:
            continue
        novo = pastas.shorts / pasta_nova / atual.name
        if novo.exists():
            resultado.falhas.append(f"{atual.name} (já existe um igual em {pasta_nova.as_posix()})")
            continue
        try:
            _mover_par(atual, novo)
        except OSError as erro:
            resultado.falhas.append(f"{atual.name} ({erro.strerror or erro})")
            continue
        entrada["arquivo"] = (pasta_nova / atual.name).as_posix()
        texto = novo.with_suffix(".txt")
        if texto.exists():  # o .txt de publicação diz se já foi postado ou quando vai ao ar
            escrever_texto_atomico(texto, texto_de_publicacao(corte))
        resultado.movidos.append(atual.name)
        mudou = True
    if mudou:
        escrever_json_atomico(pastas.shorts_renderizados, registro)
    return resultado


def renomear_prontos(
    pasta: Path, planejados: list[ShortPlanejado], registro: Registro
) -> ResultadoDasMarcas:
    """Põe cada Short já renderizado no nome e na pasta que o corte tem agora (título, número
    e `postados/` ou `agendados/`) e acerta o registro. Em duas fases, porque os números trocam
    de lugar (o 2 vira 1 e o 3 vira 2).

    Um arquivo aberto em outro programa não se move no Windows, e um nome que já está ocupado não
    é sobrescrito: vão para `falhas` (o arquivo fica como está), sem parar os outros.
    """
    resultado = ResultadoDasMarcas([], [])
    renomes = []
    for curto in planejados:
        entrada = registro.get(str(curto.corte.id))
        if entrada and entrada["arquivo"] != curto.video and (pasta / entrada["arquivo"]).exists():
            renomes.append((pasta / entrada["arquivo"], curto, entrada))
    provisorios = []
    for atual, curto, entrada in renomes:
        provisorio = pasta / f"{curto.video}.mover"
        try:
            _mover_par(atual, provisorio)
        except OSError as erro:
            resultado.falhas.append(f"{atual.name} ({erro.strerror or erro})")
            continue
        provisorios.append((provisorio, atual, curto, entrada))
    for provisorio, atual, curto, entrada in provisorios:
        destino = pasta / curto.video
        if destino.exists():  # outro arquivo no lugar: desfaz, não sobrescreve
            _mover_par(provisorio, atual)
            resultado.falhas.append(f"{atual.name} (já existe {destino.name})")
            continue
        provisorio.replace(destino)
        texto = provisorio.with_suffix(".txt")
        if texto.exists():
            texto.replace(destino.with_suffix(".txt"))
        entrada["arquivo"] = curto.video
        resultado.movidos.append(destino.name)
    return resultado


def sincronizar_arquivos(pastas: PastasProjeto, cortes: Sequence[Corte]) -> ResultadoDasMarcas:
    """Depois de salvar no Studio: os Shorts já renderizados passam a ter o nome do título de
    agora e a pasta certa (`postados/` ou `agendados/`), e o `.txt` ao lado diz o título, a
    descrição e as hashtags de agora. O que não deu para mover vai para `falhas`.
    """
    registro = ler_registro(pastas.shorts_renderizados)
    planejados = planejar_shorts(list(cortes))
    resultado = renomear_prontos(pastas.shorts, planejados, registro)
    if resultado.movidos:
        escrever_json_atomico(pastas.shorts_renderizados, registro)
    for curto in planejados:
        entrada = registro.get(str(curto.corte.id))
        if not entrada or not (pastas.shorts / entrada["arquivo"]).is_file():
            continue
        texto = (pastas.shorts / entrada["arquivo"]).with_suffix(".txt")
        novo = texto_de_publicacao(curto.corte)
        if not texto.exists() or texto.read_text(encoding="utf-8") != novo:
            escrever_texto_atomico(texto, novo)
    marcas = aplicar_marcas_de_postado(pastas, cortes)  # postados, agendados e desmarcados
    resultado.movidos += marcas.movidos
    resultado.falhas += marcas.falhas
    return resultado


def organizar_pasta(
    ctx: ContextoProjeto, pasta: Path, planejados: list[ShortPlanejado], registro: Registro
) -> None:
    """Põe os Shorts prontos no nome certo e tira de vista os que saíram da seleção.

    Um Short pronto cujo número mudou (a seleção mudou) só é renomeado; um que foi desmarcado vai
    para `desmarcados/` e volta quando for marcado de novo. Restos `.parcial` são apagados.
    """
    for resto in (
        *pasta.glob("*.parcial"),
        *(p for sub in _PASTAS_DE_ESTADO for p in pasta.glob(f"{sub}/*.parcial")),
    ):
        resto.unlink(missing_ok=True)
    marcados = {str(s.corte.id): s for s in planejados}

    for id_do_corte, entrada in registro.items():
        atual = pasta / entrada["arquivo"]
        if id_do_corte not in marcados and atual.exists() and atual.parent == pasta:
            ctx.log.info(
                "Short fora da seleção: %s vai para %s/.", atual.name, PASTA_DOS_DESMARCADOS
            )
            novo = pasta / PASTA_DOS_DESMARCADOS / atual.name
            _mover_par(atual, novo)
            entrada["arquivo"] = f"{PASTA_DOS_DESMARCADOS}/{atual.name}"

    renomeados = renomear_prontos(pasta, planejados, registro)
    for aviso in renomeados.falhas:
        ctx.log.warning("Não consegui renomear um Short pronto: %s", aviso)
    if renomeados.movidos:
        ctx.log.info("%d Shorts já prontos mudaram de nome ou de número.", len(renomeados.movidos))
    escrever_json_atomico(ctx.pastas.shorts_renderizados, registro)


#: o pedido de renderizar só alguns Shorts (o "Renderizar só este" / "Refazer" do celular), em
#: `_work/`: a etapa lê e apaga no começo, então vale para uma renderização só
ARQUIVO_DO_PEDIDO = "renderizar_so.json"
#: um pedido mais velho que isto é resto de uma renderização que não chegou a esta etapa
VALIDADE_DO_PEDIDO_S = 30 * 60


@dataclass(frozen=True)
class PedidoDeRender:
    ids: frozenset[int]
    refazer: bool = False  # renderiza de novo mesmo se o Short pronto ainda vale


def pedir_render_de(pastas: PastasProjeto, ids: Sequence[int], *, refazer: bool = False) -> None:
    """Na próxima renderização dos Shorts, só estes cortes (e, com `refazer`, do zero)."""
    pastas.work.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(
        pastas.work / ARQUIVO_DO_PEDIDO,
        {"ids": [int(i) for i in ids], "refazer": bool(refazer), "quando": time.time()},
    )


def esquecer_pedido_de_render(pastas: PastasProjeto) -> None:
    (pastas.work / ARQUIVO_DO_PEDIDO).unlink(missing_ok=True)


def consumir_pedido_de_render(pastas: PastasProjeto) -> PedidoDeRender | None:
    """O pedido de renderizar só alguns Shorts, se há um recente; o arquivo é apagado."""
    caminho = pastas.work / ARQUIVO_DO_PEDIDO
    if not caminho.exists():
        return None
    try:
        dados = ler_json(caminho)
        ids = frozenset(int(i) for i in dados["ids"])
        recente = time.time() - float(dados.get("quando", 0)) <= VALIDADE_DO_PEDIDO_S
        pedido = PedidoDeRender(ids, bool(dados.get("refazer"))) if ids and recente else None
    except (OSError, ValueError, TypeError, KeyError):
        pedido = None
    esquecer_pedido_de_render(pastas)
    return pedido


class RenderizarShorts(Etapa):
    id = "renderizar_shorts"
    nome = "Renderizando os Shorts"
    depende_de = (
        "extrair_audio",
        "limpar_audio",
        "normalizar_audio",
        "revisar_transcricao",
        "selecionar_shorts",
        "aprovar_cortes",
        "renderizar_video_longo",  # os Shorts saem dele: vem antes (`fonte_do_short`)
    )
    opcoes_relevantes = (
        "qualidade",
        "x264_preset_personalizado",
        "crf_personalizado",
        "usar_nvenc",
        "legendas_dinamicas",
        "legenda_posicao_vertical",
        "recorte_deslocamento_x_pct",
        "zoom_pct",
        "zoom_centro_x_pct",
        "zoom_centro_y_pct",
        "zoom_nos_shorts",
        "shorts_zoom_pct",
        "shorts_zoom_centro_x_pct",
        "shorts_zoom_centro_y_pct",
        "tratamento_ativo",
        "tratamento_brilho",
        "tratamento_contraste",
        "tratamento_saturacao",
        "tratamento_temperatura",
        "tratamento_matiz",
        "tratamento_brancos",
        "tratamento_sombras",
        "tratamento_nos_shorts",
        "limpar_audio",
        "normalizar_volume",
        "lufs_shorts",
        "marca_dagua",
        "marca_imagem",
        "marca_remover_fundo",
        "marca_canto",
        "marca_tamanho_pct",
        "marca_nos_shorts",
    )
    peso = 37.0  # 30% do vídeo longo: os Shorts somam cerca de 20 min de vídeo
    entrega = True

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_shorts

    def peso_para(self, opcoes) -> float:
        return 0.3 * f.peso_de_encode(opcoes)

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        if not ctx.pastas.cortes_shorts.exists():
            return []
        planejados = planejar_shorts(carregar_cortes(ctx.pastas.cortes_shorts))
        return [ctx.pastas.shorts / s.video for s in planejados]

    def executar(self, ctx: ContextoProjeto) -> None:
        pedido = consumir_pedido_de_render(ctx.pastas)
        todos = planejar_shorts(carregar_cortes(ctx.pastas.cortes_shorts))
        planejados = [s for s in todos if pedido is None or s.corte.id in pedido.ids]
        if pedido is not None:
            ctx.log.info(
                "%s só %s (pedido pelo celular).",
                "Refazendo" if pedido.refazer else "Renderizando",
                ", ".join(f"o Short {s.numero}" for s in planejados) or "nenhum Short",
            )
        if not planejados:
            ctx.log.info(
                "Nenhum Short aprovado: os cortes sugeridos ficam no Studio Shorts para você "
                "aprovar, ajustar e renderizar."
            )
            return
        longos = [s for s in planejados if passa_do_limite(s.corte)]
        if longos:
            lista = "; ".join(f"“{s.corte.titulo}” ({s.corte.duracao:.0f} s)" for s in longos)
            raise ErroFerramenta(
                f"Com mais de {DURACAO_MAXIMA_SHORT_S:g} segundos o vídeo não pode ser um Short: "
                f"{lista}. Encurte o corte "
                "no Studio Shorts ou desmarque-o."
            )
        video = Path(ctx.estado.entrada.caminho)
        if not video.exists():
            raise ErroFerramenta(f"O vídeo original não foi encontrado: {video}")
        info = sondar(video)
        audio = ctx.pastas.audio_final(ctx.opcoes)
        original = fonte_do_original(video, audio, info, sondar(audio).canais_audio, ctx.opcoes)
        longo = self._o_longo_como_fonte(ctx)
        palavras = Transcricao.carregar(ctx.pastas.transcricao).palavras
        preset = ctx.opcoes.preset_qualidade()
        pasta = ctx.pastas.shorts
        pasta.mkdir(parents=True, exist_ok=True)

        fontes = self._preparar_fontes(ctx) if ctx.opcoes.legendas_dinamicas else None
        marca = marca_dos_shorts(ctx)
        registro = ler_registro(ctx.pastas.shorts_renderizados)
        organizar_pasta(ctx, pasta, todos, registro)
        self._conferir_disco(ctx, planejados, registro, preset.crf)
        perfil = detectar_hardware(ctx.config.pastas.projetos)
        codec = f.argumentos_video(
            ctx.opcoes,
            threads=threads_para_uso(perfil, ctx.opcoes.uso_cpu_percentual),
            nvenc=ctx.opcoes.usar_nvenc and nvenc_funciona(),
        )
        ctx.log.info(
            "%d Shorts de 1080x1920 (preset %s, CRF %d)%s.",
            len(planejados),
            preset.preset,
            preset.crf,
            ", com legenda dinâmica" if fontes is not None else ", sem legenda queimada",
        )

        visual = assinatura_visual(ctx.opcoes)
        ids = [s.corte.id for s in planejados]
        feitos: list[int] = []
        # o "renderizando agora" e o "na fila" do Studio e do celular
        marcar_render(ctx.pastas, ids)
        try:
            self._renderizar_todos(
                ctx, planejados, pedido, ids, feitos, original, longo, info, fontes, marca,
                registro, palavras, codec, pasta, visual,
            )  # fmt: skip
        finally:
            limpar_render(ctx.pastas)

    def _renderizar_todos(
        self, ctx, planejados, pedido, ids, feitos, original, longo, info, fontes, marca,
        registro, palavras, codec, pasta, visual,
    ) -> None:  # fmt: skip
        total = len(planejados)
        for i, curto in enumerate(planejados):
            ctx.ponto_seguro()
            fonte, recusa = original, ""
            if longo is not None:  # do vídeo longo, a não ser que este Short não caiba nele
                recusa = motivo_de_nao_servir(
                    longo, curto.corte, ctx.opcoes.recorte_deslocamento_x_pct,
                    ctx.opcoes.enquadramento_do_short,
                )  # fmt: skip
                if not recusa:
                    fonte = longo
            assinatura = assinatura_do_short(
                ctx.estado.entrada.hash_rapido, ctx.opcoes, curto.corte, info.transferencia,
                fontes is not None, marca, fonte_id=fonte.identidade,
            )  # fmt: skip
            destino = pasta / curto.video
            destino.parent.mkdir(parents=True, exist_ok=True)  # as subpastas podem não existir
            escrever_texto_atomico(pasta / curto.texto, texto_de_publicacao(curto.corte))
            pronto = registro.get(str(curto.corte.id))
            refazer = pedido is not None and pedido.refazer
            if not refazer and destino.exists() and pronto and pronto["assinatura"] == assinatura:
                ctx.log.info("Short %d de %d já está pronto: %s", i + 1, total, curto.video)
                detalhes = detalhes_do_registro(curto.corte, visual)
                if any(pronto.get(k) != v for k, v in detalhes.items()):  # registro antigo
                    pronto.update(detalhes)
                    escrever_json_atomico(ctx.pastas.shorts_renderizados, registro)
                feitos.append(curto.corte.id)
                marcar_render(ctx.pastas, ids, feitos=feitos)
                ctx.progresso((i + 1) / total)
                continue
            ctx.log.info(
                "Short %d de %d: %s (%d s, nota %.0f, do %s)…",
                i + 1,
                total,
                curto.corte.titulo,
                curto.corte.duracao,
                curto.corte.nota,
                fonte.nome,
            )
            if recusa:
                ctx.log.info("Este Short sai do vídeo original, não do longo: %s.", recusa)
            marcar_render(ctx.pastas, ids, atual=curto.corte.id, feitos=feitos)
            self._renderizar(
                ctx, curto, fonte, palavras, codec, fontes, marca,
                faixa=(i / total, (i + 1) / total),
            )  # fmt: skip
            registro[str(curto.corte.id)] = {
                "arquivo": curto.video,
                "assinatura": assinatura,
                **detalhes_do_registro(curto.corte, visual),
                **({"fonte": "longo"} if fonte.eh_o_longo else {}),
            }
            escrever_json_atomico(ctx.pastas.shorts_renderizados, registro)
            feitos.append(curto.corte.id)
            marcar_render(ctx.pastas, ids, feitos=feitos)
            ctx.progresso((i + 1) / total)

    # --- preparo ----------------------------------------------------------------------------
    def _o_longo_como_fonte(self, ctx: ContextoProjeto) -> FonteDoShort | None:
        """O vídeo longo pronto como fonte dos Shorts se ele serve; senão None (o log diz por quê).

        Vale em qualquer modo: com o vídeo longo gerado por esta versão e as opções de agora, os
        Shorts do Studio ("Renderizar") saem dele também, iguais aos do processamento completo.
        """
        longo, motivo = fonte_do_longo(
            ctx.pastas,
            ctx.opcoes,
            hash_do_original=ctx.estado.entrada.hash_rapido,
            assinatura_do_longo=ctx.opcoes.assinatura(RenderizarVideoLongo.opcoes_relevantes),
        )
        if longo is None:
            ctx.log.info("Os Shorts saem do vídeo original: %s.", motivo)
        else:
            ctx.log.info(
                "Os Shorts saem do vídeo longo pronto (%s): a imagem e o som já estão tratados.",
                longo.video.name,
            )
        return longo

    def _conferir_disco(
        self, ctx: ContextoProjeto, planejados, registro: Registro, crf: int
    ) -> None:
        livre = shutil.disk_usage(ctx.pastas.raiz).free
        faltam = [
            s for s in planejados
            if not (ctx.pastas.shorts / s.video).exists() or str(s.corte.id) not in registro
        ]  # fmt: skip
        total_s = sum(s.corte.duracao for s in faltam)
        necessario = f.estimar_tamanho_bytes(total_s, crf, vertical=True) + 1_000_000_000
        if livre < necessario:
            raise ErroFerramenta(
                f"Falta espaço em disco para os Shorts: são necessários cerca de "
                f"{necessario / 1e9:.1f} GB livres e há {livre / 1e9:.1f} GB."
            )

    def _preparar_fontes(self, ctx: ContextoProjeto) -> Path:
        """Copia as fontes para dentro de `_work`, de onde o filtro `ass` as lê (caminho curto)."""
        destino = ctx.pastas.work / "fontes"
        destino.mkdir(parents=True, exist_ok=True)
        encontradas = [
            p for p in ctx.config.pastas.fontes.glob("*") if p.suffix.lower() in {".ttf", ".otf"}
        ]
        if not encontradas:
            ctx.log.warning(
                "Nenhuma fonte em %s: a legenda usará a fonte padrão do sistema.",
                ctx.config.pastas.fontes,
            )
        for fonte in encontradas:
            shutil.copy2(fonte, destino / fonte.name)
        return destino

    # --- um Short ---------------------------------------------------------------------------
    def _renderizar(
        self, ctx, curto: ShortPlanejado, fonte: FonteDoShort, palavras: list[Palavra],
        codec, fontes, marca, *, faixa,
    ) -> None:  # fmt: skip
        corte = curto.corte
        trabalho = ctx.pastas.work
        # o trecho lido da fonte: no original é o corte; no vídeo longo, o mesmo trecho já sem as
        # pausas que ele cortou (`converter` leva os tempos de uma linha do tempo para a outra)
        inicio_lido, janela, converter = leitura_do_corte(fonte, corte)
        faladas = palavras_do_short(
            palavras_com_a_legenda(palavras, corte), corte.inicio, corte.fim
        )
        mantidos = trechos_mantidos(palavras, corte)
        if converter is not None:
            faladas = palavras_na_fonte(faladas, converter)
            mantidos = trechos_na_fonte(mantidos, converter)
        duracao = janela  # a do Short pronto; o trecho lido só é lido (`leitura`)
        trechos = (
            None  # o que fica do corte sem os saltos e as pausas (segundos desde o início dele)
        )
        if ha_o_que_cortar(mantidos, janela):
            mapa = MapaTempo(mantidos)
            trechos, duracao = mantidos, mapa.duracao_editada
            faladas = remapear_palavras(faladas, mapa)
            ctx.log.info(
                "%d cortes no Short (pedaços tirados e pausas): ele fica com %.1f s (o trecho "
                "lido tem %.1f s).",
                len(mantidos) - 1, duracao, janela,
            )  # fmt: skip
        fade_entrada, fade_saida = fades_do_short(faladas, duracao)
        legenda = None
        if fontes is not None:
            legenda = f"legendas/{curto.nome}.ass"
            estilo = EstiloLegenda(posicao_vertical=ctx.opcoes.legenda_posicao_vertical)
            ass = montar_ass(faladas, estilo)
            escrever_texto_atomico(trabalho / legenda, ass)

        medido = None
        if ctx.opcoes.normalizar_volume:
            medido = self._medir_volume(
                ctx, fonte.audio, fonte.canais, inicio_lido, janela, ctx.opcoes.lufs_shorts
            )

        cadeia_do_audio = filtro_de_audio(
            fonte.canais,
            duracao,
            medido,
            ctx.opcoes.lufs_shorts,
            fade_entrada_s=fade_entrada,
            fade_saida_s=fade_saida,
        )
        if trechos:
            audio_do_short = ";\n".join(linhas_do_audio_cortado(trechos, cadeia_do_audio))
        else:
            audio_do_short = f"[1:a]{cadeia_do_audio}[a]"
        script = trabalho / "legendas" / f"{curto.nome}.filter"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            filtro_de_video(
                fonte.info,
                fonte.fps,
                deslocamento_pct=ctx.opcoes.recorte_deslocamento_x_pct,
                legenda=legenda,
                marca=marca,
                enquadramento=fonte.enquadramento,  # None no vídeo longo: o zoom já está nele
                tratamento=fonte.tratamento,  # idem a cor
                zoom_do_short=ctx.opcoes.enquadramento_do_short,
                trechos=trechos,
            )
            + ";\n"
            + audio_do_short,
            encoding="utf-8",
        )
        destino = ctx.pastas.shorts / curto.video
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_name(destino.name + ".parcial")
        # a leitura é sempre do trecho inteiro; só a saída (`-t` final) tem a duração já cortada
        leitura = ["-ss", f.numero_ffmpeg(inicio_lido), "-t", f.numero_ffmpeg(janela)]
        ctx.ffmpeg(
            ["-y", *leitura, "-i", fonte.video, *leitura, "-i", fonte.audio,
             *(["-i", marca.arquivo] if marca else []),
             "-/filter_complex", script, "-map", "[v]", "-map", "[a]", *codec,
             *f.argumentos_audio(), "-t", f.numero_ffmpeg(duracao),
             "-movflags", "+faststart", "-f", "mp4", parcial],
            duracao_s=duracao, faixa=faixa, cwd=trabalho,
        )  # fmt: skip
        resultado = sondar(parcial)
        if (
            not resultado.tem_audio
            or abs(resultado.duracao_s - duracao) > _TOLERANCIA_DE_DURACAO_S
            or resultado.duracao_s > DURACAO_MAXIMA_S
        ):
            parcial.unlink(missing_ok=True)
            raise ErroFerramenta(
                f"O Short {curto.nome} saiu com {resultado.duracao_s:.1f} s (esperado "
                f"{duracao:.1f} s). Rode de novo; se repetir, veja o processamento.log."
            )
        parcial.replace(destino)
        ctx.log.info(
            "Pronto: %s (%s, %.1f MB).",
            curto.video,
            formatar_duracao(resultado.duracao_s),
            destino.stat().st_size / 1e6,
        )

    def _medir_volume(
        self, ctx, audio: Path, canais: int | None, inicio_s: float, janela_s: float,
        alvo_lufs: float,
    ) -> dict[str, float] | None:  # fmt: skip
        """Primeira passagem do loudnorm, só no trecho lido do Short e já em estéreo."""
        saida = ctx.ffmpeg(
            ["-y", "-ss", f.numero_ffmpeg(inicio_s), "-t", f.numero_ffmpeg(janela_s),
             "-i", audio, "-af", f"{f.filtro_audio_estereo(canais)},{filtro_medir(alvo_lufs)}",
             "-f", "null", "-"],
        )  # fmt: skip
        medido = ler_medicao(saida)
        if medido.get("input_i", -120.0) <= -70.0:  # trecho mudo: não há o que normalizar
            return None
        return medido
