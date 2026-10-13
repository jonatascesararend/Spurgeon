"""Etapa: renderiza os Shorts aprovados (9:16, 1080×1920, legenda dinâmica em ASS).

Um Short por vez, cada um num arquivo próprio e gravado só quando termina. Se o processamento
for interrompido, os já prontos são reaproveitados (conferidos por uma assinatura do corte e das
opções), e só o que falta é refeito.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from spurgeon.audio.normalizar import filtro_aplicar, filtro_medir, ler_medicao
from spurgeon.core.arquivos import escrever_json_atomico, escrever_texto_atomico, ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.render import filtros as f
from spurgeon.render.legenda import EstiloLegenda, montar_ass, palavras_do_short
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import carregar_cortes
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo

VERSAO_DO_RENDER = 1
DURACAO_MAXIMA_S = 60.5  # 60 s do pedido mais a folga de arredondamento do último quadro
FADE_S = 0.1
_PADRAO_DE_ARQUIVO = re.compile(r"^short_\d{2,3}_.+\.(mp4|txt)(\.parcial)?$")
_MBPS_EM_CRF16_VERTICAL = 14.0
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
    info: InfoMidia, fps: Fraction, *, deslocamento_pct: float, legenda: str | None
) -> str:
    """Recorte vertical, conversão para SDR, ampliação para 1080×1920 e legenda queimada."""
    partes = [
        f"fps={f.texto_fps(fps)}",
        f.recorte_vertical(info, deslocamento_pct),
        f.cadeia_para_sdr(info),
        f"scale={f.LARGURA_SHORT}:{f.ALTURA_SHORT}:flags=lanczos",
        "setsar=1",
    ]
    if legenda:
        partes.append(f"ass={legenda}:fontsdir=fontes")
    partes.append("format=yuv420p")
    return ",".join(partes)


def filtro_de_audio(canais: int | None, duracao_s: float, medido: dict[str, float] | None,
                    alvo_lufs: float) -> str:  # fmt: skip
    """Estéreo, volume do Short (se medido) e fade de 100 ms nas pontas contra estalos."""
    cadeia = [f.filtro_audio_estereo(canais)]
    if medido is not None:
        cadeia += [filtro_aplicar(alvo_lufs, medido), f"aresample={f.TAXA_AUDIO}"]
    fade_fora = max(0.0, duracao_s - FADE_S)
    cadeia += [
        f"afade=t=in:st=0:d={FADE_S:g}",
        f"afade=t=out:st={f.numero_ffmpeg(fade_fora)}:d={FADE_S:g}",
    ]
    return ",".join(cadeia)


def estimar_tamanho_bytes(duracao_s: float, crf: int) -> int:
    mbps = _MBPS_EM_CRF16_VERTICAL * 2 ** ((16 - crf) / 6)
    return int(duracao_s * mbps * 1e6 / 8)


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
    )
    peso = 37.0  # 30% do vídeo longo: os Shorts somam cerca de 20 min de vídeo
    entrega = True

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_shorts

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

        self._conferir_disco(ctx, planejados, preset.crf)
        self._remover_sobras(ctx, pasta, planejados)
        fontes = self._preparar_fontes(ctx) if ctx.opcoes.legendas_dinamicas else None
        registro_caminho = ctx.pastas.work / "shorts_renderizados.json"
        registro: dict[str, str] = ler_json(registro_caminho) if registro_caminho.exists() else {}
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
            assinatura = self._assinatura(ctx, curto, info, audio, fontes)
            destino = pasta / curto.video
            escrever_texto_atomico(pasta / curto.texto, texto_de_publicacao(curto.corte))
            if destino.exists() and registro.get(curto.video) == assinatura:
                ctx.log.info("Short %d de %d já está pronto: %s", i + 1, total, curto.video)
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
                ctx, curto, video, info, fps, audio, canais, palavras, codec, fontes,
                faixa=(i / total, (i + 1) / total),
            )  # fmt: skip
            registro[curto.video] = assinatura
            escrever_json_atomico(registro_caminho, registro)
            ctx.progresso((i + 1) / total)

    # --- preparo ----------------------------------------------------------------------------
    def _conferir_disco(self, ctx: ContextoProjeto, planejados, crf: int) -> None:
        livre = shutil.disk_usage(ctx.pastas.raiz).free
        total_s = sum(s.corte.duracao for s in planejados)
        necessario = estimar_tamanho_bytes(total_s, crf) + 1_000_000_000
        if livre < necessario:
            raise ErroFerramenta(
                f"Falta espaço em disco para os Shorts: são necessários cerca de "
                f"{necessario / 1e9:.1f} GB livres e há {livre / 1e9:.1f} GB."
            )

    def _remover_sobras(self, ctx: ContextoProjeto, pasta: Path, planejados) -> None:
        """Apaga Shorts de uma aprovação anterior que não valem mais (desmarcados/renomeados)."""
        validos = {s.video for s in planejados} | {s.texto for s in planejados}
        for arquivo in pasta.iterdir():
            if _PADRAO_DE_ARQUIVO.match(arquivo.name) and arquivo.name not in validos:
                ctx.log.info("Removendo %s: não faz parte da aprovação atual.", arquivo.name)
                arquivo.unlink(missing_ok=True)

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

    def _assinatura(self, ctx, curto: ShortPlanejado, info, audio: Path, fontes) -> str:
        o = ctx.opcoes
        preset = o.preset_qualidade()
        dados = {
            "versao": VERSAO_DO_RENDER,
            "video": ctx.estado.entrada.hash_rapido,
            "corte": [round(curto.corte.inicio, 3), round(curto.corte.fim, 3)],
            "hdr": info.transferencia,
            "codec": [preset.preset, preset.crf, o.usar_nvenc],
            "legenda": [fontes is not None, o.legenda_posicao_vertical],
            "recorte": o.recorte_deslocamento_x_pct,
            "audio": [audio.name, audio.stat().st_size],
            "volume": [o.normalizar_volume, o.lufs_shorts],
        }
        return hashlib.sha1(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:16]

    # --- um Short ---------------------------------------------------------------------------
    def _renderizar(
        self, ctx, curto: ShortPlanejado, video: Path, info, fps, audio: Path, canais,
        palavras: list[Palavra], codec, fontes, *, faixa,
    ) -> None:  # fmt: skip
        corte = curto.corte
        trabalho = ctx.pastas.work
        legenda = None
        if fontes is not None:
            legenda = f"legendas/{curto.nome}.ass"
            estilo = EstiloLegenda(posicao_vertical=ctx.opcoes.legenda_posicao_vertical)
            ass = montar_ass(palavras_do_short(palavras, corte.inicio, corte.fim), estilo)
            escrever_texto_atomico(trabalho / legenda, ass)

        medido = None
        if ctx.opcoes.normalizar_volume:
            medido = self._medir_volume(ctx, audio, corte, ctx.opcoes.lufs_shorts)

        duracao = corte.duracao
        script = trabalho / "legendas" / f"{curto.nome}.filter"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "[0:v]"
            + filtro_de_video(
                info, fps, deslocamento_pct=ctx.opcoes.recorte_deslocamento_x_pct, legenda=legenda
            )
            + "[v];\n[1:a]"
            + filtro_de_audio(canais, duracao, medido, ctx.opcoes.lufs_shorts)
            + "[a]",
            encoding="utf-8",
        )
        destino = ctx.pastas.shorts / curto.video
        parcial = destino.with_name(destino.name + ".parcial")
        leitura = ["-ss", f.numero_ffmpeg(corte.inicio), "-t", f.numero_ffmpeg(duracao)]
        ctx.ffmpeg(
            ["-y", *leitura, "-i", video, *leitura, "-i", audio,
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
        self, ctx, audio: Path, corte: Corte, alvo_lufs: float
    ) -> dict[str, float] | None:
        """Primeira passagem do loudnorm, só no trecho do Short."""
        saida = ctx.ffmpeg(
            ["-y", "-ss", f.numero_ffmpeg(corte.inicio), "-t", f.numero_ffmpeg(corte.duracao),
             "-i", audio, "-af", filtro_medir(alvo_lufs), "-f", "null", "-"],
        )  # fmt: skip
        medido = ler_medicao(saida)
        if medido.get("input_i", -120.0) <= -70.0:  # trecho mudo: não há o que normalizar
            return None
        return medido
