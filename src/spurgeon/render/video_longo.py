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

from spurgeon.core.arquivos import ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.core.tempo import Trecho
from spurgeon.render import filtros as f

DURACAO_DA_PARTE_S = 600.0
VERSAO_DO_RENDER = 1
_MARGEM_DE_LEITURA_S = 0.5
_TOLERANCIA_DE_DURACAO_S = 0.5
_MBPS_EM_CRF16 = 8.0  # bitrate típico de uma pessoa falando, em 1080p, com x264 e CRF 16


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


def quadros_da_duracao(duracao_s: float, fps: Fraction) -> int:
    return max(1, round(Fraction(duracao_s).limit_denominator(1_000_000) * fps))


def _n(valor: float) -> str:
    return f.numero_ffmpeg(valor)


def script_da_parte(parte: Parte, inicio_da_leitura: float, info: InfoMidia, fps: Fraction) -> str:
    """O filtro (para `-/filter_complex`) que monta uma parte a partir do vídeo original.

    Os tempos são relativos ao ponto onde a leitura começa (`-ss`).
    """
    taxa = f.texto_fps(fps)
    relativos = [(a - inicio_da_leitura, b - inicio_da_leitura) for a, b in parte.pedacos]
    linhas = []
    if len(relativos) == 1:
        a, b = relativos[0]
        linhas.append(f"[0:v]fps={taxa},trim=start={_n(a)}:end={_n(b)},setpts=PTS-STARTPTS[bruto]")
    else:
        saidas = "".join(f"[s{i}]" for i in range(len(relativos)))
        linhas.append(f"[0:v]fps={taxa},split={len(relativos)}{saidas}")
        for i, (a, b) in enumerate(relativos):
            linhas.append(f"[s{i}]trim=start={_n(a)}:end={_n(b)},setpts=PTS-STARTPTS[p{i}]")
        entradas = "".join(f"[p{i}]" for i in range(len(relativos)))
        linhas.append(f"{entradas}concat=n={len(relativos)}:v=1:a=0[bruto]")
    # tpad repete o último quadro se faltar algum por arredondamento; `-frames:v` corta o excesso
    linhas.append(f"[bruto]{f.cadeia_para_sdr(info)},tpad=stop_mode=clone:stop_duration=2[vout]")
    return ";\n".join(linhas)


def script_de_clipe(info: InfoMidia, largura: int, altura: int, fps: Fraction) -> str:
    """Padroniza a introdução ou a vinheta (tamanho, taxa de quadros, formato e SDR)."""
    return (
        f"[0:v]fps={f.texto_fps(fps)},{f.cadeia_para_sdr(info)},"
        f"scale={largura}:{altura}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={largura}:{altura}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
        "tpad=stop_mode=clone:stop_duration=2[vout]"
    )


def lista_do_audio(audio: Path, trechos: list[Trecho]) -> str:
    """Arquivo para o demuxer `concat`: o áudio inteiro, só nos trechos mantidos."""
    caminho = audio.resolve().as_posix().replace("'", r"'\''")
    linhas = []
    for t in trechos:
        linhas += [f"file '{caminho}'", f"inpoint {t.inicio:.6f}", f"outpoint {t.fim:.6f}"]
    return "\n".join(linhas) + "\n"


def script_do_audio(
    *, intro: _Clipe | None, vinheta: _Clipe | None, quadros_principais: int, fps: Fraction,
    canais: int | None, normalizar: bool, lufs: float,
) -> str:  # fmt: skip
    """Junta, em estéreo de 48 kHz, o áudio da introdução, do vídeo e da vinheta."""

    def exato(quadros: int) -> str:
        return _n(float(Fraction(quadros) / fps))

    linhas = [
        f"[0:a]{f.filtro_audio_estereo(canais)},apad=pad_dur=2,"
        f"atrim=duration={exato(quadros_principais)},asetpts=PTS-STARTPTS[am]"
    ]
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
class _Clipe:
    """Introdução ou vinheta: o arquivo, o que o FFmpeg sabe dele e quantos quadros ocupa."""

    caminho: Path
    info: InfoMidia
    quadros: int
    arquivo: str


def estimar_tamanho_bytes(duracao_s: float, crf: int) -> int:
    mbps = _MBPS_EM_CRF16 * 2 ** ((16 - crf) / 6)
    return int(duracao_s * mbps * 1e6 / 8)


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
    )
    peso = 123.0  # o do preset padrão (Máxima qualidade); `peso_para` ajusta
    entrega = True

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo

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
        audio = ctx.pastas.audio_final(ctx.opcoes)
        intro = self._clipe(ctx, ctx.config.pastas.introducao, "intro", fps, ctx.opcoes.introducao)
        vinheta = self._clipe(
            ctx, ctx.config.pastas.vinheta_final, "vinheta", fps, ctx.opcoes.vinheta_final
        )
        partes = planejar_partes(trechos, fps, DURACAO_DA_PARTE_S)
        principais = sum(p.quadros for p in partes)
        total_quadros = principais + sum(c.quadros for c in (intro, vinheta) if c)
        duracao_total = float(Fraction(total_quadros) / fps)
        preset = ctx.opcoes.preset_qualidade()
        self._conferir_disco(ctx, duracao_total, preset.crf)

        pasta = ctx.pastas.work / "render_longo"
        self._preparar_pasta(ctx, pasta, info, fps, trechos, audio, intro, vinheta)
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
        sequencia: list[str] = []
        if intro:
            feito = self._clipe_pronto(
                ctx, pasta, intro, largura, altura, fps, argumentos_codec, pesos, feito
            )
            sequencia.append(intro.arquivo)
        for parte in partes:
            ctx.ponto_seguro()
            feito = self._parte(ctx, pasta, parte, video, info, fps, argumentos_codec, pesos, feito)
            sequencia.append(parte.arquivo)
        if vinheta:
            feito = self._clipe_pronto(
                ctx, pasta, vinheta, largura, altura, fps, argumentos_codec, pesos, feito
            )
            sequencia.append(vinheta.arquivo)
        self._unir(ctx, pasta, sequencia, fps, duracao_total, feito)
        shutil.rmtree(pasta, ignore_errors=True)

    # --- preparo ----------------------------------------------------------------------------
    def _clipe(
        self, ctx: ContextoProjeto, caminho: Path, nome: str, fps: Fraction, ligado: bool
    ) -> _Clipe | None:
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
        return _Clipe(caminho, info, quadros_da_duracao(info.duracao_s, fps), f"parte_{nome}.mp4")

    def _conferir_disco(self, ctx: ContextoProjeto, duracao_s: float, crf: int) -> None:
        livre = shutil.disk_usage(ctx.pastas.raiz).free
        # as partes e o arquivo final existem ao mesmo tempo antes de as partes serem apagadas
        necessario = int(estimar_tamanho_bytes(duracao_s, crf) * 2.2) + 1_000_000_000
        if livre < necessario:
            raise ErroFerramenta(
                f"Falta espaço em disco para renderizar: são necessários cerca de "
                f"{necessario / 1e9:.1f} GB livres e há {livre / 1e9:.1f} GB."
            )

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

    def _assinatura(self, ctx, info, fps, trechos, audio, intro, vinheta) -> str:
        def marca(caminho: Path) -> list:
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
            "audio": marca(audio),
            "normalizar": [ctx.opcoes.normalizar_volume, ctx.opcoes.lufs_video_longo],
            "intro": marca(intro.caminho) if intro else None,
            "vinheta": marca(vinheta.caminho) if vinheta else None,
            "duracao_da_parte": DURACAO_DA_PARTE_S,
        }
        return hashlib.sha1(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:16]

    def _preparar_pasta(self, ctx, pasta: Path, info, fps, trechos, audio, intro, vinheta) -> None:
        """Recomeça do zero se algo que muda o resultado mudou desde a execução anterior."""
        assinatura = self._assinatura(ctx, info, fps, trechos, audio, intro, vinheta)
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
        lista = pasta / "lista_audio.txt"
        lista.write_text(lista_do_audio(audio, trechos), encoding="utf-8")
        script = pasta / "audio.filter"
        canais = sondar(audio).canais_audio
        script.write_text(
            script_do_audio(
                intro=intro,
                vinheta=vinheta,
                quadros_principais=principais,
                fps=fps,
                canais=canais,
                normalizar=ctx.opcoes.normalizar_volume,
                lufs=ctx.opcoes.lufs_video_longo,
            ),
            encoding="utf-8",
        )
        entradas = ["-f", "concat", "-safe", "0", "-i", lista]
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

    def _parte(self, ctx, pasta: Path, parte: Parte, video: Path, info, fps, codec, pesos, feito):
        destino = pasta / parte.arquivo
        faixa, depois = self._andamento(ctx, pesos, parte.arquivo, feito)
        if destino.exists():
            ctx.progresso(depois)
            return depois
        inicio = max(0.0, parte.pedacos[0][0] - _MARGEM_DE_LEITURA_S)
        fim = parte.pedacos[-1][1] + _MARGEM_DE_LEITURA_S
        script = pasta / f"{destino.stem}.filter"
        script.write_text(script_da_parte(parte, inicio, info, fps), encoding="utf-8")
        parcial = destino.with_name(destino.name + ".parcial")
        ctx.log.info("Renderizando a parte %d (%d quadros)…", parte.indice + 1, parte.quadros)
        ctx.ffmpeg(
            ["-y", "-ss", _n(inicio), "-t", _n(fim - inicio), "-i", video,
             "-/filter_complex", script, "-map", "[vout]", "-an",
             "-frames:v", str(parte.quadros), *codec, "-f", "mp4", parcial],
            duracao_s=float(Fraction(parte.quadros) / fps), faixa=faixa,
        )  # fmt: skip
        parcial.replace(destino)
        return depois

    def _clipe_pronto(
        self, ctx, pasta: Path, clipe: _Clipe, largura, altura, fps, codec, pesos, feito
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

    def _unir(self, ctx, pasta: Path, sequencia: list[str], fps, duracao_total, feito) -> None:
        """Junta as partes e o áudio, sem recodificar nada."""
        parcial = ctx.pastas.video_longo.with_name(ctx.pastas.video_longo.name + ".parcial")
        parcial.unlink(missing_ok=True)
        lista = pasta / "lista_video.txt"
        lista.write_text("".join(f"file '{nome}'\n" for nome in sequencia), encoding="utf-8")
        ctx.ffmpeg(
            ["-y", "-f", "concat", "-safe", "0", "-i", "lista_video.txt",
             "-i", "audio.m4a", "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
             "-movflags", "+faststart", "-f", "mp4", parcial],
            duracao_s=duracao_total, faixa=(feito, 1.0), cwd=pasta,
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
        tamanho = ctx.pastas.video_longo.stat().st_size
        ctx.log.info(
            "Vídeo longo pronto: %s, %.2f GB (%.1f Mb/s).",
            formatar_duracao(resultado.duracao_s),
            tamanho / 1e9,
            tamanho * 8 / 1e6 / max(resultado.duracao_s, 1),
        )
