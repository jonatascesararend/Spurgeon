"""Etapa: renderiza o vídeo longo (cortes de pausa, áudio tratado, introdução e vinheta).

O vídeo sai em **partes** de cerca de 10 minutos, cada uma codificada à parte em H.264 e
guardada em `_work/render_longo/`. Uma queda de energia, um cancelamento ou um erro só custam a
parte que estava em curso: ao retomar, as partes prontas são reaproveitadas. O áudio é montado
uma única vez, e no fim as partes são juntadas e unidas ao áudio sem recodificar nada.

Para o áudio e o vídeo ficarem sempre em sincronia, toda parte tem um número exato de quadros e
as durações de introdução, vídeo principal e vinheta são múltiplos exatos de um quadro.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.core.tempo import Trecho
from spurgeon.render import filtros as f
from spurgeon.render.marca import (
    MarcaDagua,
    assinatura_da_marca,
    marca_para_o_projeto,
    montar_marca,
    trecho_do_filtro,
)

DURACAO_DA_PARTE_S = 600.0
VERSAO_DO_RENDER = 1
_MARGEM_DE_LEITURA_S = 0.5
_TOLERANCIA_DE_DURACAO_S = 0.5
_MARGEM_DE_DISCO = 1_000_000_000
_SOBRA_NA_JUNCAO_ECONOMICA = 0.15  # fração do arquivo final que ainda ocupa espaço além das partes


@dataclass(frozen=True)
class Parte:
    """Um pedaço do vídeo principal: `quadros` exatos, tirados destes trechos do original."""

    indice: int
    quadros: int
    pedacos: tuple[tuple[float, float], ...]

    @property
    def arquivo(self) -> str:
        return f"parte_{self.indice:03d}.mp4"


# --- planejamento (sem FFmpeg) --------------------------------------------------------------


def pedacos_do_intervalo(
    trechos: list[Trecho], inicio_editado: float, fim_editado: float
) -> tuple[tuple[float, float], ...]:
    """Os trechos do original que formam o intervalo [início, fim) do vídeo já editado."""
    pedacos: list[tuple[float, float]] = []
    acumulado = 0.0
    for trecho in trechos:
        ini_t, fim_t = acumulado, acumulado + trecho.duracao
        acumulado = fim_t
        de, ate = max(inicio_editado, ini_t), min(fim_editado, fim_t)
        if ate - de > 1e-6:
            pedacos.append((trecho.inicio + (de - ini_t), trecho.inicio + (ate - ini_t)))
    return tuple(pedacos)


def planejar_partes(
    trechos: list[Trecho], fps: Fraction, duracao_da_parte_s: float = DURACAO_DA_PARTE_S
) -> list[Parte]:
    """Divide o vídeo editado em partes de número exato de quadros."""
    total_s = sum(t.duracao for t in trechos)
    total = max(1, round(Fraction(total_s).limit_denominator(1_000_000) * fps))
    por_parte = max(1, round(duracao_da_parte_s * float(fps)))
    quantas = max(1, round(total / por_parte))
    limites = [round(total * i / quantas) for i in range(quantas + 1)]
    partes = []
    for i in range(quantas):
        ini_q, fim_q = limites[i], limites[i + 1]
        pedacos = pedacos_do_intervalo(trechos, float(ini_q / fps), float(fim_q / fps))
        partes.append(Parte(i, fim_q - ini_q, pedacos))
    return partes


def arquivos_ja_lidos(
    sequencia: list[tuple[str, float]], tempo_s: float, folga_s: float = 1.0
) -> list[str]:
    """Os arquivos que a junção já terminou de ler quando está em `tempo_s` do vídeo final."""
    lidos, acumulado = [], 0.0
    for nome, duracao in sequencia:
        acumulado += duracao
        if acumulado + folga_s <= tempo_s:
            lidos.append(nome)
    return lidos


def quadros_da_duracao(duracao_s: float, fps: Fraction) -> int:
    return max(1, round(Fraction(duracao_s).limit_denominator(1_000_000) * fps))


def _n(valor: float) -> str:
    return f.numero_ffmpeg(valor)


def recortes_de_quadros(pedacos: list[tuple[float, float]], fps: Fraction) -> list[tuple[int, int]]:
    """(primeiro quadro da grade, quantidade) de cada pedaço, em quadros inteiros.

    A quantidade de cada pedaço sai do arredondamento do tempo ACUMULADO, não do tempo do pedaço:
    assim o erro nunca passa de meio quadro e a imagem não se afasta do áudio a cada corte.
    """
    taxa = float(fps)
    acumulado = 0.0
    recortes = []
    for inicio, fim in pedacos:
        duracao = fim - inicio
        quantos = round((acumulado + duracao) * taxa) - round(acumulado * taxa)
        acumulado += duracao
        if quantos > 0:
            recortes.append((round(inicio * taxa), quantos))
    return recortes


def script_da_parte(
    parte: Parte, inicio_da_leitura: float, info: InfoMidia, fps: Fraction,
    marca: MarcaDagua | None = None,
) -> str:  # fmt: skip
    """O filtro (para `-/filter_complex`) que monta uma parte a partir do vídeo original.

    Os tempos são relativos ao ponto onde a leitura começa (`-ss`). Cada pedaço pega um número
    exato de quadros da grade de `fps`: `trim` escolhe os quadros k com
    `(k - 0.5)/fps <= tempo < (k + n - 0.5)/fps`.
    """
    taxa = f.texto_fps(fps)
    relativos = [(a - inicio_da_leitura, b - inicio_da_leitura) for a, b in parte.pedacos]
    recortes = recortes_de_quadros(relativos, fps)

    def tempos(primeiro: int, quantos: int) -> str:
        inicio = (Fraction(2 * primeiro - 1, 2)) / fps
        fim = (Fraction(2 * (primeiro + quantos) - 1, 2)) / fps
        return f"start={_n(float(inicio))}:end={_n(float(fim))}"

    linhas = []
    if len(recortes) == 1:
        linhas.append(f"[0:v]fps={taxa},trim={tempos(*recortes[0])},setpts=PTS-STARTPTS[bruto]")
    else:
        saidas = "".join(f"[s{i}]" for i in range(len(recortes)))
        linhas.append(f"[0:v]fps={taxa},split={len(recortes)}{saidas}")
        for i, recorte in enumerate(recortes):
            linhas.append(f"[s{i}]trim={tempos(*recorte)},setpts=PTS-STARTPTS[p{i}]")
        entradas = "".join(f"[p{i}]" for i in range(len(recortes)))
        linhas.append(f"{entradas}concat=n={len(recortes)}:v=1:a=0[bruto]")
    # tpad repete o último quadro se faltar algum por arredondamento; `-frames:v` corta o excesso
    sdr = f"[bruto]{f.cadeia_para_sdr(info)}"
    if marca is None:
        linhas.append(f"{sdr},tpad=stop_mode=clone:stop_duration=2[vout]")
    else:  # a marca (entrada 1) entra depois da conversão para SDR, para não ser tonemapada
        linhas.append(f"{sdr}[base]")
        linhas.append(trecho_do_filtro(marca, base="base", entrada="1:v", saida="marcado"))
        linhas.append("[marcado]tpad=stop_mode=clone:stop_duration=2[vout]")
    return ";\n".join(linhas)


def script_de_clipe(info: InfoMidia, largura: int, altura: int, fps: Fraction) -> str:
    """Padroniza a introdução ou a vinheta (tamanho, taxa de quadros, formato e SDR)."""
    return (
        f"[0:v]fps={f.texto_fps(fps)},{f.cadeia_para_sdr(info)},"
        f"scale={largura}:{altura}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={largura}:{altura}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
        "tpad=stop_mode=clone:stop_duration=2[vout]"
    )


def script_do_audio(
    trechos: list[Trecho], *, intro: Clipe | None, vinheta: Clipe | None,
    quadros_principais: int, fps: Fraction, canais: int | None, normalizar: bool, lufs: float,
    compensar_mono: bool = False,
) -> str:  # fmt: skip
    """Junta, em estéreo de 48 kHz, o áudio da introdução, do vídeo (só nos trechos mantidos) e
    da vinheta.

    Os cortes usam `atrim`, exato até a amostra (o demuxer `concat` do FFmpeg erra até dezenas
    de milissegundos por corte, o que desalinharia a imagem).
    """

    def exato(quadros: int) -> str:
        return _n(float(Fraction(quadros) / fps))

    def corte(t: Trecho) -> str:
        return f"atrim=start={_n(t.inicio)}:end={_n(t.fim)},asetpts=PTS-STARTPTS"

    linhas = []
    if len(trechos) == 1:
        principal = f"[0:a]{corte(trechos[0])}"
    else:
        saidas = "".join(f"[x{i}]" for i in range(len(trechos)))
        linhas.append(f"[0:a]asplit={len(trechos)}{saidas}")
        for i, trecho in enumerate(trechos):
            linhas.append(f"[x{i}]{corte(trecho)}[t{i}]")
        principal = "".join(f"[t{i}]" for i in range(len(trechos))) + (
            f"concat=n={len(trechos)}:v=0:a=1"
        )
    estereo = f.filtro_audio_estereo(canais, compensar_mono=compensar_mono)
    linhas.append(
        f"{principal},{estereo},apad=pad_dur=2,"
        f"atrim=duration={exato(quadros_principais)},asetpts=PTS-STARTPTS[am]"
    )
    ordem = []
    for nome, clipe, entrada in (("ai", intro, 1), ("av", vinheta, 2 if intro else 1)):
        if clipe is None:
            continue
        origem = (
            f"[{entrada}:a]{f.filtro_audio_estereo(clipe.info.canais_audio)}"
            if clipe.info.tem_audio
            else f"anullsrc=r={f.TAXA_AUDIO}:cl=stereo"
        )
        if clipe.info.tem_audio and normalizar:
            origem += f",loudnorm=I={lufs:g}:TP=-1.5:LRA=11"
        linhas.append(
            f"{origem},apad=pad_dur=2,atrim=duration={exato(clipe.quadros)},"
            f"asetpts=PTS-STARTPTS[{nome}]"
        )
    if intro:
        ordem.append("[ai]")
    ordem.append("[am]")
    if vinheta:
        ordem.append("[av]")
    linhas.append(f"{''.join(ordem)}concat=n={len(ordem)}:v=0:a=1[aout]")
    return ";\n".join(linhas)


@dataclass(frozen=True)
class Clipe:
    """Introdução ou vinheta: o arquivo, o que o FFmpeg sabe dele e quantos quadros ocupa."""

    caminho: Path
    info: InfoMidia
    quadros: int
    arquivo: str


# --- a etapa ---------------------------------------------------------------------------------


class RenderizarVideoLongo(Etapa):
    id = "renderizar_video_longo"
    nome = "Renderizando o vídeo longo"
    depende_de = (
        "extrair_audio",
        "limpar_audio",
        "normalizar_audio",
        "calcular_cortes",
    )
    opcoes_relevantes = (
        "qualidade",
        "x264_preset_personalizado",
        "crf_personalizado",
        "usar_nvenc",
        "introducao",
        "vinheta_final",
        "limpar_audio",
        "normalizar_volume",
        "lufs_video_longo",
        "marca_dagua",
        "marca_imagem",
        "marca_remover_fundo",
        "marca_canto",
        "marca_tamanho_pct",
        "marca_no_video_longo",
    )
    peso = 123.0  # o do preset padrão (Máxima qualidade); `peso_para` ajusta
    entrega = True

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_video_longo

    def peso_para(self, opcoes) -> float:
        return f.peso_de_encode(opcoes)

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.video_longo]

    def executar(self, ctx: ContextoProjeto) -> None:
        video = Path(ctx.estado.entrada.caminho)
        if not video.exists():
            raise ErroFerramenta(f"O vídeo original não foi encontrado: {video}")
        info = sondar(video)
        fps = f.fps_constante(info.fps)
        largura, altura = (info.largura or 1920) // 2 * 2, (info.altura or 1080) // 2 * 2
        cortes = ler_json(ctx.pastas.cortes)
        trechos = [Trecho(a, b) for a, b in cortes["trechos"]]
        if not trechos:
            raise ErroFerramenta("Não sobrou nenhum trecho de vídeo para renderizar.")
        audio = ctx.pastas.audio_final(ctx.opcoes)
        intro = self._clipe(ctx, ctx.config.pastas.introducao, "intro", fps, ctx.opcoes.introducao)
        vinheta = self._clipe(
            ctx, ctx.config.pastas.vinheta_final, "vinheta", fps, ctx.opcoes.vinheta_final
        )
        marca = self._marca(ctx, largura)
        partes = planejar_partes(trechos, fps, DURACAO_DA_PARTE_S)
        principais = sum(p.quadros for p in partes)
        total_quadros = principais + sum(c.quadros for c in (intro, vinheta) if c)
        duracao_total = float(Fraction(total_quadros) / fps)
        preset = ctx.opcoes.preset_qualidade()

        pasta = ctx.pastas.work / "render_longo"
        self._preparar_pasta(ctx, pasta, info, fps, trechos, audio, intro, vinheta, marca)
        economico = self._conferir_disco(ctx, pasta, duracao_total, preset.crf)
        ctx.log.info(
            "Vídeo de %s em %d partes (%s, preset %s, CRF %d)%s.",
            formatar_duracao(duracao_total),
            len(partes),
            f"{largura}x{altura} a {float(fps):.3f} quadros/s",
            preset.preset,
            preset.crf,
            ", convertendo de HDR para SDR" if info.hdr else "",
        )

        argumentos_codec = self._argumentos_codec(ctx)
        pesos = self._pesos(partes, intro, vinheta, total_quadros)
        feito = 0.0

        feito = self._audio(
            ctx, pasta, audio, trechos, intro, vinheta, principais, fps, pesos, feito
        )
        sequencia: list[tuple[str, float]] = []  # (arquivo, duração em segundos)
        if intro:
            feito = self._clipe_pronto(
                ctx, pasta, intro, largura, altura, fps, argumentos_codec, pesos, feito
            )
            sequencia.append((intro.arquivo, float(Fraction(intro.quadros) / fps)))
        for parte in partes:
            ctx.ponto_seguro()
            feito = self._parte(
                ctx, pasta, parte, video, info, fps, argumentos_codec, pesos, feito, marca
            )
            sequencia.append((parte.arquivo, float(Fraction(parte.quadros) / fps)))
        if vinheta:
            feito = self._clipe_pronto(
                ctx, pasta, vinheta, largura, altura, fps, argumentos_codec, pesos, feito
            )
            sequencia.append((vinheta.arquivo, float(Fraction(vinheta.quadros) / fps)))
        self._unir(ctx, pasta, sequencia, duracao_total, feito, economico=economico)
        shutil.rmtree(pasta, ignore_errors=True)

    def _marca(self, ctx: ContextoProjeto, largura_do_video: int) -> MarcaDagua | None:
        """A marca d'água do vídeo longo, já em PNG com transparência (ou None se não vale)."""
        if not ctx.opcoes.marca_no_longo:
            return None
        if not Path(ctx.opcoes.marca_imagem).is_file():
            raise ErroFerramenta(
                f"A imagem da marca d'água não foi encontrada: {ctx.opcoes.marca_imagem}"
            )
        arquivo = marca_para_o_projeto(ctx.pastas.work, ctx.opcoes)
        return montar_marca(arquivo, ctx.opcoes, largura_do_video)

    # --- preparo ----------------------------------------------------------------------------
    def _clipe(
        self, ctx: ContextoProjeto, caminho: Path, nome: str, fps: Fraction, ligado: bool
    ) -> Clipe | None:
        if not ligado:
            return None
        if not caminho.exists():
            raise ErroFerramenta(
                f"A {'introdução' if nome == 'intro' else 'vinheta final'} está marcada, mas o "
                f"arquivo não existe: {caminho}"
            )
        info = sondar(caminho)
        if not info.tem_video:
            raise ErroFerramenta(f"O arquivo {caminho.name} não tem vídeo.")
        return Clipe(caminho, info, quadros_da_duracao(info.duracao_s, fps), f"parte_{nome}.mp4")

    def _conferir_disco(
        self, ctx: ContextoProjeto, pasta: Path, duracao_s: float, crf: int
    ) -> bool:
        """Confere o espaço livre; True = só dá no modo econômico (apaga as partes ao juntar).

        Normalmente as partes e o arquivo final existem ao mesmo tempo (o dobro do tamanho). Com
        pouco espaço, cada parte é apagada assim que a junção termina de lê-la.
        """
        estimado = f.estimar_tamanho_bytes(duracao_s, crf)
        existente = (
            sum(p.stat().st_size for p in pasta.glob("parte_*.mp4")) if pasta.exists() else 0
        )
        faltam = max(0, estimado - existente)
        normal = faltam + estimado + _MARGEM_DE_DISCO
        economico = faltam + int(estimado * _SOBRA_NA_JUNCAO_ECONOMICA) + _MARGEM_DE_DISCO
        livre = shutil.disk_usage(ctx.pastas.raiz).free
        if livre < economico:
            raise ErroFerramenta(
                f"Falta espaço em disco para renderizar: são necessários cerca de "
                f"{economico / 1e9:.1f} GB livres e há {livre / 1e9:.1f} GB."
            )
        if livre < normal:
            ctx.log.warning(
                "Pouco espaço em disco (%.1f GB livres; o ideal seriam %.1f GB): as partes serão "
                "apagadas conforme a junção as lê. Se a junção falhar, essas partes são refeitas.",
                livre / 1e9,
                normal / 1e9,
            )
            return True
        return False

    def _argumentos_codec(self, ctx: ContextoProjeto) -> list[str]:
        perfil = detectar_hardware(ctx.config.pastas.projetos)
        threads = threads_para_uso(perfil, ctx.opcoes.uso_cpu_percentual)
        nvenc = ctx.opcoes.usar_nvenc and nvenc_funciona()
        return f.argumentos_video(ctx.opcoes, threads=threads, nvenc=nvenc)

    def _pesos(self, partes: list[Parte], intro, vinheta, total_quadros: int) -> dict[str, float]:
        """Fração do progresso da etapa que cada trabalho ocupa (o vídeo é o que demora)."""
        reservado = 0.02 + 0.03  # montar o áudio e juntar tudo no final
        pesos = {"audio": 0.02, "uniao": 0.03}
        if intro:
            pesos[intro.arquivo] = (1 - reservado) * intro.quadros / total_quadros
        for p in partes:
            pesos[p.arquivo] = (1 - reservado) * p.quadros / total_quadros
        if vinheta:
            pesos[vinheta.arquivo] = (1 - reservado) * vinheta.quadros / total_quadros
        return pesos

    def _assinatura(self, ctx, info, fps, trechos, audio, intro, vinheta, marca=None) -> str:
        def marca_do_arquivo(caminho: Path) -> list:
            estatistica = caminho.stat()
            return [caminho.name, estatistica.st_size, int(estatistica.st_mtime)]

        preset = ctx.opcoes.preset_qualidade()
        dados = {
            "versao": VERSAO_DO_RENDER,
            "video": ctx.estado.entrada.hash_rapido,
            "trechos": [[round(t.inicio, 3), round(t.fim, 3)] for t in trechos],
            "fps": f.texto_fps(fps),
            "tamanho": [info.largura, info.altura],
            "hdr": info.transferencia,
            "codec": [preset.preset, preset.crf, ctx.opcoes.usar_nvenc],
            "audio": marca_do_arquivo(audio),
            "normalizar": [ctx.opcoes.normalizar_volume, ctx.opcoes.lufs_video_longo],
            "intro": marca_do_arquivo(intro.caminho) if intro else None,
            "vinheta": marca_do_arquivo(vinheta.caminho) if vinheta else None,
            "duracao_da_parte": DURACAO_DA_PARTE_S,
            "marca": assinatura_da_marca(marca.arquivo, marca) if marca else None,
        }
        return hashlib.sha1(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:16]

    def _preparar_pasta(
        self, ctx, pasta: Path, info, fps, trechos, audio, intro, vinheta, marca=None
    ) -> None:
        """Recomeça do zero se algo que muda o resultado mudou desde a execução anterior."""
        assinatura = self._assinatura(ctx, info, fps, trechos, audio, intro, vinheta, marca)
        marcador = pasta / "assinatura.txt"
        if pasta.exists() and (not marcador.exists() or marcador.read_text() != assinatura):
            ctx.log.info("As opções ou o material mudaram: o render recomeça do início.")
            shutil.rmtree(pasta, ignore_errors=True)
        elif pasta.exists():
            prontas = len(list(pasta.glob("parte_*.mp4")))
            if prontas:
                ctx.log.info("Retomando o render: %d partes já estão prontas.", prontas)
        pasta.mkdir(parents=True, exist_ok=True)
        marcador.write_text(assinatura)
        for sobra in pasta.glob("*.parcial"):  # restos de uma execução interrompida
            sobra.unlink(missing_ok=True)

    # --- trabalhos --------------------------------------------------------------------------
    def _andamento(self, ctx: ContextoProjeto, pesos, nome: str, feito: float):
        """Faixa de progresso da etapa para o trabalho `nome` e o novo total concluído."""
        return (feito, feito + pesos[nome]), feito + pesos[nome]

    def _audio(
        self, ctx, pasta: Path, audio, trechos, intro, vinheta, principais, fps, pesos, feito
    ):
        destino = pasta / "audio.m4a"
        faixa, depois = self._andamento(ctx, pesos, "audio", feito)
        if destino.exists():
            ctx.progresso(depois)
            return depois
        script = pasta / "audio.filter"
        canais = sondar(audio).canais_audio
        script.write_text(
            script_do_audio(
                trechos,
                intro=intro,
                vinheta=vinheta,
                quadros_principais=principais,
                fps=fps,
                canais=canais,
                normalizar=ctx.opcoes.normalizar_volume,
                lufs=ctx.opcoes.lufs_video_longo,
                compensar_mono=ctx.opcoes.normalizar_volume,
            ),
            encoding="utf-8",
        )
        entradas = ["-i", audio]
        for clipe in (intro, vinheta):
            if clipe:
                entradas += ["-i", clipe.caminho]
        parcial = destino.with_name("audio.m4a.parcial")
        total = sum(t.duracao for t in trechos)
        ctx.ffmpeg(
            ["-y", *entradas, "-/filter_complex", script, "-map", "[aout]",
             *f.argumentos_audio(), "-f", "ipod", parcial],
            duracao_s=total, faixa=faixa,
        )  # fmt: skip
        parcial.replace(destino)
        return depois

    def _parte(
        self,
        ctx,
        pasta: Path,
        parte: Parte,
        video: Path,
        info,
        fps,
        codec,
        pesos,
        feito,
        marca=None,
    ):
        destino = pasta / parte.arquivo
        faixa, depois = self._andamento(ctx, pesos, parte.arquivo, feito)
        if destino.exists():
            ctx.progresso(depois)
            return depois
        inicio = max(0.0, parte.pedacos[0][0] - _MARGEM_DE_LEITURA_S)
        fim = parte.pedacos[-1][1] + _MARGEM_DE_LEITURA_S
        script = pasta / f"{destino.stem}.filter"
        script.write_text(script_da_parte(parte, inicio, info, fps, marca), encoding="utf-8")
        parcial = destino.with_name(destino.name + ".parcial")
        ctx.log.info("Renderizando a parte %d (%d quadros)…", parte.indice + 1, parte.quadros)
        ctx.ffmpeg(
            ["-y", "-ss", _n(inicio), "-t", _n(fim - inicio), "-i", video,
             *(["-i", marca.arquivo] if marca else []),
             "-/filter_complex", script, "-map", "[vout]", "-an",
             "-frames:v", str(parte.quadros), *codec, "-f", "mp4", parcial],
            duracao_s=float(Fraction(parte.quadros) / fps), faixa=faixa,
        )  # fmt: skip
        parcial.replace(destino)
        return depois

    def _clipe_pronto(
        self, ctx, pasta: Path, clipe: Clipe, largura, altura, fps, codec, pesos, feito
    ):
        destino = pasta / clipe.arquivo
        faixa, depois = self._andamento(ctx, pesos, clipe.arquivo, feito)
        if destino.exists():
            ctx.progresso(depois)
            return depois
        script = pasta / f"{destino.stem}.filter"
        script.write_text(script_de_clipe(clipe.info, largura, altura, fps), encoding="utf-8")
        parcial = destino.with_name(destino.name + ".parcial")
        ctx.ffmpeg(
            ["-y", "-i", clipe.caminho, "-/filter_complex", script, "-map", "[vout]",
             "-an", "-frames:v", str(clipe.quadros), *codec, "-f", "mp4", parcial],
            duracao_s=float(Fraction(clipe.quadros) / fps), faixa=faixa,
        )  # fmt: skip
        parcial.replace(destino)
        return depois

    def _unir(
        self, ctx, pasta: Path, sequencia: list[tuple[str, float]], duracao_total, feito, *,
        economico: bool = False,
    ) -> None:  # fmt: skip
        """Junta as partes e o áudio, sem recodificar nada."""
        parcial = ctx.pastas.video_longo.with_name(ctx.pastas.video_longo.name + ".parcial")
        parcial.unlink(missing_ok=True)
        lista = pasta / "lista_video.txt"
        lista.write_text("".join(f"file '{n}'\n" for n, _ in sequencia), encoding="utf-8")
        apagados: set[str] = set()

        def apagar_as_ja_lidas(fracao: float) -> None:
            if not economico:
                return
            for nome in arquivos_ja_lidos(sequencia, fracao * duracao_total):
                if nome not in apagados:
                    try:
                        (pasta / nome).unlink()
                        apagados.add(nome)
                    except OSError:  # ainda aberta: tenta de novo no próximo aviso
                        pass

        ctx.ffmpeg(
            ["-y", "-f", "concat", "-safe", "0", "-i", "lista_video.txt",
             "-i", "audio.m4a", "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
             "-movflags", "+faststart", "-f", "mp4", parcial],
            duracao_s=duracao_total, faixa=(feito, 1.0), cwd=pasta, ao_fracao=apagar_as_ja_lidas,
        )  # fmt: skip
        resultado = sondar(parcial)
        diferenca = abs(resultado.duracao_s - duracao_total)
        if diferenca > _TOLERANCIA_DE_DURACAO_S or not resultado.tem_audio:
            parcial.unlink(missing_ok=True)
            raise ErroFerramenta(
                f"O vídeo gerado ficou com {resultado.duracao_s:.1f} s, mas o esperado era "
                f"{duracao_total:.1f} s. Rode de novo; se repetir, veja o processamento.log."
            )
        parcial.replace(ctx.pastas.video_longo)
        preset = ctx.opcoes.preset_qualidade()
        escrever_json_atomico(
            ctx.pastas.video_longo_info,
            {
                "introducao": ctx.opcoes.introducao,
                "vinheta_final": ctx.opcoes.vinheta_final,
                "preset": preset.preset,
                "crf": preset.crf,
                "duracao_s": round(resultado.duracao_s, 3),
            },
        )
        tamanho = ctx.pastas.video_longo.stat().st_size
        ctx.log.info(
            "Vídeo longo pronto: %s, %.2f GB (%.1f Mb/s).",
            formatar_duracao(resultado.duracao_s),
            tamanho / 1e9,
            tamanho * 8 / 1e6 / max(resultado.duracao_s, 1),
        )
