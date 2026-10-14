"""Etapa: junta a introdução e a vinheta final a um vídeo longo que já está pronto.

O vídeo principal NÃO é renderizado de novo: só a introdução e a vinheta (segundos de vídeo) são
codificadas, com os mesmos parâmetros do vídeo, e depois tudo é emendado sem recodificar. Leva um
ou dois minutos em vez de horas. O resultado vai para `video_longo_com_vinhetas.mp4` (o vídeo
original do projeto fica como está) e as legendas ganham o deslocamento da introdução.
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

from spurgeon.core.arquivos import ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.render import filtros as f
from spurgeon.render.video_longo import Clipe, RenderizarVideoLongo, script_de_clipe
from spurgeon.transcricao.srt import deslocar_srt

_TOLERANCIA_DE_DURACAO_S = 0.5
_PROBLEMA_NA_EMENDA = re.compile(r"(?i)error|invalid|corrupt|non-monoton|missing|failed")


def script_do_clipe_completo(
    clipe: Clipe, largura: int, altura: int, fps: Fraction, *, normalizar: bool, lufs: float
) -> str:
    """Imagem (`[vout]`) e som (`[aout]`) de uma introdução ou vinheta, prontos para emendar."""
    duracao = f.numero_ffmpeg(float(Fraction(clipe.quadros) / fps))
    if clipe.info.tem_audio:
        origem = f"[0:a]{f.filtro_audio_estereo(clipe.info.canais_audio)}"
        if normalizar:
            origem += f",loudnorm=I={lufs:g}:TP=-1.5:LRA=11"
    else:
        origem = f"anullsrc=r={f.TAXA_AUDIO}:cl=stereo"
    audio = f"{origem},apad=pad_dur=2,atrim=duration={duracao},asetpts=PTS-STARTPTS[aout]"
    return script_de_clipe(clipe.info, largura, altura, fps) + ";\n" + audio


def lista_de_emenda(arquivos: list[Path]) -> str:
    """Arquivo para o demuxer `concat` (caminhos absolutos, aspas escapadas)."""
    linhas = []
    for arquivo in arquivos:
        caminho = arquivo.resolve().as_posix().replace("'", r"'\''")
        linhas.append(f"file '{caminho}'")
    return "\n".join(linhas) + "\n"


class AdicionarVinhetas(Etapa):
    id = "adicionar_vinhetas"
    nome = "Juntando a introdução e a vinheta final ao vídeo pronto"
    depende_de = ()
    opcoes_relevantes = (
        "introducao",
        "vinheta_final",
        "qualidade",
        "x264_preset_personalizado",
        "crf_personalizado",
        "usar_nvenc",
        "normalizar_volume",
        "lufs_video_longo",
    )
    peso = 3.0
    entrega = True
    no_modo_vinhetas = True

    def ativa(self, opcoes) -> bool:
        return opcoes.so_vinhetas

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.video_com_vinhetas]

    def executar(self, ctx: ContextoProjeto) -> None:
        base = ctx.pastas.video_longo
        if not base.exists():
            raise ErroFerramenta(
                "Este projeto ainda não tem o vídeo longo pronto "
                f"({base.name}); gere-o primeiro no modo completo."
            )
        info = sondar(base)
        if info.codec_video != "h264" or not info.tem_audio:
            raise ErroFerramenta(
                f"{base.name} não é um vídeo H.264 com som gerado por este aplicativo; "
                "não dá para emendar sem recodificar."
            )
        fps = f.fps_constante(info.fps)
        largura, altura = (info.largura or 1920), (info.altura or 1080)
        anterior = self._o_que_o_video_ja_tem(ctx)
        etapa_de_render = RenderizarVideoLongo()
        intro = vinheta = None
        if ctx.opcoes.introducao:
            if anterior.get("introducao"):
                raise ErroFerramenta("O vídeo longo deste projeto já tem a introdução.")
            intro = etapa_de_render._clipe(ctx, ctx.config.pastas.introducao, "intro", fps, True)
        if ctx.opcoes.vinheta_final:
            if anterior.get("vinheta_final"):
                raise ErroFerramenta("O vídeo longo deste projeto já tem a vinheta final.")
            vinheta = etapa_de_render._clipe(
                ctx, ctx.config.pastas.vinheta_final, "vinheta", fps, True
            )
        clipes = [c for c in (intro, vinheta) if c is not None]
        if not clipes:
            raise ErroFerramenta("Marque a introdução, a vinheta final ou as duas.")

        pasta = ctx.pastas.work / "vinhetas"
        pasta.mkdir(parents=True, exist_ok=True)
        codec = self._codec_do_video(ctx, anterior)
        prontos: dict[str, Path] = {}
        for i, clipe in enumerate(clipes):
            ctx.ponto_seguro()
            prontos[clipe.arquivo] = self._codificar(
                ctx, pasta, clipe, largura, altura, fps, codec, faixa=(i / 5, (i + 1) / 5)
            )

        sequencia = [prontos[c.arquivo] for c in (intro,) if c] + [base]
        sequencia += [prontos[c.arquivo] for c in (vinheta,) if c]
        self._emendar(ctx, pasta, sequencia, intro, vinheta, fps, info.duracao_s)
        self._legendas(ctx, intro, fps)

    # --- partes ------------------------------------------------------------------------------
    def _o_que_o_video_ja_tem(self, ctx: ContextoProjeto) -> dict:
        try:
            return ler_json(ctx.pastas.video_longo_info)
        except (OSError, ValueError):
            return {}

    def _codec_do_video(self, ctx: ContextoProjeto, anterior: dict) -> list[str]:
        """Os argumentos de codificação: os mesmos do vídeo, para as partes se emendarem."""
        opcoes = ctx.opcoes
        if "preset" in anterior and "crf" in anterior:
            opcoes = opcoes.model_copy(
                update={
                    "qualidade": "personalizado",
                    "x264_preset_personalizado": anterior["preset"],
                    "crf_personalizado": int(anterior["crf"]),
                }
            )
        else:
            ctx.log.warning(
                "Não sei com que qualidade o vídeo foi renderizado; uso a escolhida agora. Se a "
                "emenda não ficar boa, gere o vídeo de novo no modo completo."
            )
        perfil = detectar_hardware(ctx.config.pastas.projetos)
        threads = threads_para_uso(perfil, ctx.opcoes.uso_cpu_percentual)
        return f.argumentos_video(opcoes, threads=threads)

    def _codificar(
        self, ctx, pasta: Path, clipe: Clipe, largura, altura, fps, codec, *, faixa
    ) -> Path:
        destino = pasta / clipe.arquivo
        script = pasta / f"{destino.stem}.filter"
        script.write_text(
            script_do_clipe_completo(
                clipe, largura, altura, fps,
                normalizar=ctx.opcoes.normalizar_volume, lufs=ctx.opcoes.lufs_video_longo,
            ),
            encoding="utf-8",
        )  # fmt: skip
        parcial = destino.with_name(destino.name + ".parcial")
        ctx.log.info("Preparando %s (%d quadros)…", clipe.caminho.name, clipe.quadros)
        ctx.ffmpeg(
            ["-y", "-i", clipe.caminho, "-/filter_complex", script, "-map", "[vout]",
             "-map", "[aout]", "-frames:v", str(clipe.quadros), *codec, *f.argumentos_audio(),
             "-f", "mp4", parcial],
            duracao_s=float(Fraction(clipe.quadros) / fps), faixa=faixa,
        )  # fmt: skip
        parcial.replace(destino)
        return destino

    def _emendar(
        self, ctx, pasta: Path, sequencia: list[Path], intro, vinheta, fps, duracao_base: float
    ) -> None:
        lista = pasta / "lista.txt"
        lista.write_text(lista_de_emenda(sequencia), encoding="utf-8")
        destino = ctx.pastas.video_com_vinhetas
        parcial = destino.with_name(destino.name + ".parcial")
        parcial.unlink(missing_ok=True)
        extras = sum(float(Fraction(c.quadros) / fps) for c in (intro, vinheta) if c)
        esperado = duracao_base + extras
        ctx.ffmpeg(
            ["-y", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy",
             "-movflags", "+faststart", "-f", "mp4", parcial],
            duracao_s=esperado, faixa=(0.6, 1.0),
        )  # fmt: skip
        resultado = sondar(parcial)
        if (
            abs(resultado.duracao_s - esperado) > _TOLERANCIA_DE_DURACAO_S
            or not resultado.tem_audio
        ):
            parcial.unlink(missing_ok=True)
            raise ErroFerramenta(
                f"O vídeo emendado ficou com {resultado.duracao_s:.1f} s, mas o esperado era "
                f"{esperado:.1f} s."
            )
        self._conferir_emendas(ctx, parcial, intro, vinheta, fps, esperado)
        parcial.replace(destino)
        ctx.log.info(
            "Vídeo com introdução/vinheta pronto: %s, %.2f GB (%s).",
            formatar_duracao(resultado.duracao_s),
            destino.stat().st_size / 1e9,
            destino.name,
        )

    def _conferir_emendas(self, ctx, arquivo: Path, intro, vinheta, fps, total_s: float) -> None:
        """Decodifica 2 s ao redor de cada emenda: parâmetros diferentes aparecem como erro."""
        instantes = []
        if intro:
            instantes.append(float(Fraction(intro.quadros) / fps))
        if vinheta:
            instantes.append(total_s - float(Fraction(vinheta.quadros) / fps))
        for instante in instantes:
            saida = ctx.ffmpeg(
                ["-v", "error", "-ss", f.numero_ffmpeg(max(0.0, instante - 1.0)), "-t", "2",
                 "-i", arquivo, "-f", "null", "-"],
            )  # fmt: skip
            if _PROBLEMA_NA_EMENDA.search(saida):
                arquivo.unlink(missing_ok=True)
                raise ErroFerramenta(
                    "A introdução ou a vinheta não combinam com a codificação do vídeo pronto "
                    "(a emenda não decodifica). Gere o vídeo longo de novo, no modo completo, "
                    "com a introdução e a vinheta marcadas."
                )

    def _legendas(self, ctx: ContextoProjeto, intro: Clipe | None, fps: Fraction) -> None:
        if not ctx.pastas.srt.exists():
            return
        deslocamento = float(Fraction(intro.quadros) / fps) if intro else 0.0
        texto = deslocar_srt(ctx.pastas.srt.read_text(encoding="utf-8"), deslocamento)
        ctx.pastas.srt_com_vinhetas.write_text(texto, encoding="utf-8")
        ctx.log.info(
            "Legendas com a introdução (+%.1f s): %s",
            deslocamento,
            ctx.pastas.srt_com_vinhetas.name,
        )
