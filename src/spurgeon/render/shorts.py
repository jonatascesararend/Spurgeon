"""Etapa: renderiza os Shorts aprovados (9:16, 1080×1920, legenda dinâmica em ASS).

Um Short por vez, cada um num arquivo próprio e gravado só quando termina. Se o processamento
for interrompido, os já prontos são reaproveitados (conferidos por uma assinatura do corte e das
opções), e só o que falta é refeito.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from spurgeon.audio.normalizar import filtro_aplicar, filtro_medir, ler_medicao
from spurgeon.core.arquivos import escrever_json_atomico, escrever_texto_atomico, ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.render import filtros as f
from spurgeon.render.legenda import EstiloLegenda, montar_ass, palavras_do_short
from spurgeon.render.marca import (
    MarcaDagua,
    assinatura_da_marca,
    marca_para_o_projeto,
    montar_marca,
    trecho_do_filtro,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import palavras_com_a_legenda
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo

VERSAO_DO_RENDER = 1
DURACAO_MAXIMA_S = 60.5  # 60 s do pedido mais a folga de arredondamento do último quadro
FADE_S = 0.1
FADE_MINIMO_S = 0.02
_TOLERANCIA_DE_DURACAO_S = 0.6


@dataclass(frozen=True)
class ShortPlanejado:
    numero: int
    corte: Corte

    @property
    def nome(self) -> str:
        return f"short_{self.numero:02d}_{self.corte.slug}"

    @property
    def video(self) -> str:
        return f"{self.nome}.mp4"

    @property
    def texto(self) -> str:
        return f"{self.nome}.txt"


def planejar_shorts(cortes: list[Corte]) -> list[ShortPlanejado]:
    """Os cortes marcados, numerados na ordem em que aparecem no sermão."""
    marcados = sorted((c for c in cortes if c.incluir), key=lambda c: c.inicio)
    return [ShortPlanejado(i, c) for i, c in enumerate(marcados, start=1)]


def texto_de_publicacao(corte: Corte) -> str:
    """O .txt que acompanha cada Short: o que colar na hora de publicar."""
    linhas = [
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


def filtro_de_video(
    info: InfoMidia,
    fps: Fraction,
    *,
    deslocamento_pct: float,
    legenda: str | None,
    marca: MarcaDagua | None = None,
) -> str:
    """O grafo do vídeo: da entrada `0:v` até `[v]`.

    Recorte vertical, conversão para SDR, ampliação para 1080×1920, marca d'água (entrada `2:v`)
    e legenda queimada, nesta ordem: a marca fica sob a legenda se as duas se encontrarem.
    """
    ate_a_ampliacao = ",".join(
        [
            f"fps={f.texto_fps(fps)}",
            f.recorte_vertical(info, deslocamento_pct),
            f.cadeia_para_sdr(info),
            f"scale={f.LARGURA_SHORT}:{f.ALTURA_SHORT}:flags=lanczos",
            "setsar=1",
        ]
    )
    final = ([f"ass={legenda}:fontsdir=fontes"] if legenda else []) + ["format=yuv420p"]
    if marca is None:
        return f"[0:v]{ate_a_ampliacao},{','.join(final)}[v]"
    sobreposicao = trecho_do_filtro(marca, base="base", entrada="2:v", saida="marcado")
    return f"[0:v]{ate_a_ampliacao}[base];{sobreposicao};[marcado]{','.join(final)}[v]"


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

PASTA_DOS_DESMARCADOS = "desmarcados"
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


SITUACAO_NOVO = "novo"  # nunca foi renderizado
SITUACAO_PRONTO = "pronto"
SITUACAO_ALTERADO = "alterado"  # o corte ou a legenda mudou depois de renderizado


def detalhes_do_registro(corte: Corte) -> dict[str, Any]:
    """O que o registro guarda além da assinatura, para a tela saber se o Short ainda vale."""
    return {
        "corte": [round(corte.inicio, 3), round(corte.fim, 3)],
        "legenda": hashlib.sha1(corte.legenda.encode()).hexdigest()[:10],
    }


def situacao_do_short(
    corte: Corte, entrada: dict[str, Any] | None, arquivo_existe: bool, *, com_legenda: bool
) -> str:
    """`novo`, `pronto` ou `alterado`, conforme o que o registro lembra do último render.

    É um aviso para a tela: quem decide de fato o que renderiza de novo é a assinatura.
    Registros antigos, sem os detalhes, valem como prontos.
    """
    if entrada is None or not arquivo_existe:
        return SITUACAO_NOVO
    atual = detalhes_do_registro(corte)
    if "corte" in entrada and entrada["corte"] != atual["corte"]:
        return SITUACAO_ALTERADO
    if com_legenda and "legenda" in entrada and entrada["legenda"] != atual["legenda"]:
        return SITUACAO_ALTERADO
    return SITUACAO_PRONTO


def assinatura_do_short(
    hash_do_video: str,
    opcoes,
    corte: Corte,
    transferencia: str | None,
    com_legenda: bool,
    marca: MarcaDagua | None,
) -> str:
    """O que decide se um Short pronto ainda vale: o corte e tudo que muda a imagem ou o som."""
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


def organizar_pasta(
    ctx: ContextoProjeto, pasta: Path, planejados: list[ShortPlanejado], registro: Registro
) -> None:
    """Põe os Shorts prontos no nome certo e tira de vista os que saíram da seleção.

    Um Short pronto cujo número mudou (a seleção mudou) só é renomeado; um que foi desmarcado vai
    para `desmarcados/` e volta quando for marcado de novo. Restos `.parcial` são apagados.
    """
    for resto in pasta.glob("*.parcial"):
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

    # em duas fases, porque os números trocam de lugar (o 2 vira 1 e o 3 vira 2)
    renomes = []
    for id_do_corte, curto in marcados.items():
        entrada = registro.get(id_do_corte)
        if entrada and entrada["arquivo"] != curto.video and (pasta / entrada["arquivo"]).exists():
            renomes.append((pasta / entrada["arquivo"], curto, entrada))
    provisorios = []
    for atual, curto, entrada in renomes:
        provisorio = pasta / f"{curto.video}.mover"
        _mover_par(atual, provisorio)
        provisorios.append((provisorio, curto, entrada))
    for provisorio, curto, entrada in provisorios:
        destino = pasta / curto.video
        provisorio.replace(destino)
        texto = provisorio.with_suffix(".txt")
        if texto.exists():
            texto.replace(destino.with_suffix(".txt"))
        entrada["arquivo"] = curto.video
    if renomes:
        ctx.log.info("%d Shorts já prontos mudaram de número no nome.", len(renomes))
    escrever_json_atomico(ctx.pastas.shorts_renderizados, registro)


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
    )
    opcoes_relevantes = (
        "qualidade",
        "x264_preset_personalizado",
        "crf_personalizado",
        "usar_nvenc",
        "legendas_dinamicas",
        "legenda_posicao_vertical",
        "recorte_deslocamento_x_pct",
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
        planejados = planejar_shorts(carregar_cortes(ctx.pastas.cortes_shorts))
        if not planejados:
            ctx.log.warning("Nenhum corte foi aprovado: nenhum Short será gerado.")
            return
        video = Path(ctx.estado.entrada.caminho)
        if not video.exists():
            raise ErroFerramenta(f"O vídeo original não foi encontrado: {video}")
        info = sondar(video)
        fps = f.fps_constante(info.fps)
        audio = ctx.pastas.audio_final(ctx.opcoes)
        canais = sondar(audio).canais_audio
        palavras = Transcricao.carregar(ctx.pastas.transcricao).palavras
        preset = ctx.opcoes.preset_qualidade()
        pasta = ctx.pastas.shorts
        pasta.mkdir(parents=True, exist_ok=True)

        fontes = self._preparar_fontes(ctx) if ctx.opcoes.legendas_dinamicas else None
        marca = marca_dos_shorts(ctx)
        registro = ler_registro(ctx.pastas.shorts_renderizados)
        organizar_pasta(ctx, pasta, planejados, registro)
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

        total = len(planejados)
        for i, curto in enumerate(planejados):
            ctx.ponto_seguro()
            assinatura = assinatura_do_short(
                ctx.estado.entrada.hash_rapido, ctx.opcoes, curto.corte, info.transferencia,
                fontes is not None, marca,
            )  # fmt: skip
            destino = pasta / curto.video
            escrever_texto_atomico(pasta / curto.texto, texto_de_publicacao(curto.corte))
            pronto = registro.get(str(curto.corte.id))
            if destino.exists() and pronto and pronto["assinatura"] == assinatura:
                ctx.log.info("Short %d de %d já está pronto: %s", i + 1, total, curto.video)
                detalhes = detalhes_do_registro(curto.corte)
                if any(pronto.get(k) != v for k, v in detalhes.items()):  # registro antigo
                    pronto.update(detalhes)
                    escrever_json_atomico(ctx.pastas.shorts_renderizados, registro)
                ctx.progresso((i + 1) / total)
                continue
            ctx.log.info(
                "Short %d de %d: %s (%d s, nota %.0f)…",
                i + 1,
                total,
                curto.corte.titulo,
                curto.corte.duracao,
                curto.corte.nota,
            )
            self._renderizar(
                ctx, curto, video, info, fps, audio, canais, palavras, codec, fontes, marca,
                faixa=(i / total, (i + 1) / total),
            )  # fmt: skip
            registro[str(curto.corte.id)] = {
                "arquivo": curto.video,
                "assinatura": assinatura,
                **detalhes_do_registro(curto.corte),
            }
            escrever_json_atomico(ctx.pastas.shorts_renderizados, registro)
            ctx.progresso((i + 1) / total)

    # --- preparo ----------------------------------------------------------------------------
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
        self, ctx, curto: ShortPlanejado, video: Path, info, fps, audio: Path, canais,
        palavras: list[Palavra], codec, fontes, marca, *, faixa,
    ) -> None:  # fmt: skip
        corte = curto.corte
        trabalho = ctx.pastas.work
        faladas = palavras_do_short(
            palavras_com_a_legenda(palavras, corte), corte.inicio, corte.fim
        )
        fade_entrada, fade_saida = fades_do_short(faladas, corte.duracao)
        legenda = None
        if fontes is not None:
            legenda = f"legendas/{curto.nome}.ass"
            estilo = EstiloLegenda(posicao_vertical=ctx.opcoes.legenda_posicao_vertical)
            ass = montar_ass(faladas, estilo)
            escrever_texto_atomico(trabalho / legenda, ass)

        medido = None
        if ctx.opcoes.normalizar_volume:
            medido = self._medir_volume(ctx, audio, canais, corte, ctx.opcoes.lufs_shorts)

        duracao = corte.duracao
        script = trabalho / "legendas" / f"{curto.nome}.filter"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            filtro_de_video(
                info,
                fps,
                deslocamento_pct=ctx.opcoes.recorte_deslocamento_x_pct,
                legenda=legenda,
                marca=marca,
            )
            + ";\n[1:a]"
            + filtro_de_audio(
                canais,
                duracao,
                medido,
                ctx.opcoes.lufs_shorts,
                fade_entrada_s=fade_entrada,
                fade_saida_s=fade_saida,
            )
            + "[a]",
            encoding="utf-8",
        )
        destino = ctx.pastas.shorts / curto.video
        parcial = destino.with_name(destino.name + ".parcial")
        leitura = ["-ss", f.numero_ffmpeg(corte.inicio), "-t", f.numero_ffmpeg(duracao)]
        ctx.ffmpeg(
            ["-y", *leitura, "-i", video, *leitura, "-i", audio,
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
        self, ctx, audio: Path, canais: int | None, corte: Corte, alvo_lufs: float
    ) -> dict[str, float] | None:
        """Primeira passagem do loudnorm, só no trecho do Short e já em estéreo."""
        saida = ctx.ffmpeg(
            ["-y", "-ss", f.numero_ffmpeg(corte.inicio), "-t", f.numero_ffmpeg(corte.duracao),
             "-i", audio, "-af", f"{f.filtro_audio_estereo(canais)},{filtro_medir(alvo_lufs)}",
             "-f", "null", "-"],
        )  # fmt: skip
        medido = ler_medicao(saida)
        if medido.get("input_i", -120.0) <= -70.0:  # trecho mudo: não há o que normalizar
            return None
        return medido
