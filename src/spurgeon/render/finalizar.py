"""Etapa final: confere os arquivos entregues e registra o resumo no log.

Só depois desta conferência o processamento conta como concluído e a pasta `_work/` pode ser
apagada. Um problema grave (arquivo ausente, vídeo com a duração errada, Short fora do formato)
interrompe com erro e deixa os intermediários para a pessoa conseguir retomar.
"""

from __future__ import annotations

import re

from spurgeon.core.arquivos import ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.pipeline import ContextoProjeto, Etapa
from spurgeon.render.filtros import ALTURA_SHORT, LARGURA_SHORT
from spurgeon.render.shorts import DURACAO_MAXIMA_S, planejar_shorts
from spurgeon.shorts.etapas import carregar_cortes

TOLERANCIA_VIDEO_S = 1.0
TOLERANCIA_LEGENDA_S = 0.5
_TEMPO_SRT = re.compile(r"-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def fim_da_ultima_legenda(texto_srt: str) -> tuple[int, float]:
    """(quantidade de legendas, fim da última em segundos) de um arquivo .srt."""
    fins = [
        int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        for h, m, s, ms in _TEMPO_SRT.findall(texto_srt)
    ]
    return len(fins), max(fins, default=0.0)


class Finalizar(Etapa):
    id = "finalizar"
    nome = "Conferindo o resultado"
    depende_de = ("renderizar_shorts", "renderizar_video_longo", "gerar_srt")
    peso = 0.5

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo or opcoes.gerar_shorts

    def executar(self, ctx: ContextoProjeto) -> None:
        problemas: list[str] = []
        avisos: list[str] = []
        resumo: list[str] = []
        if ctx.opcoes.gerar_video_longo:
            self._video_longo(ctx, problemas, avisos, resumo)
        if ctx.opcoes.gerar_shorts:
            self._shorts(ctx, problemas, avisos, resumo)

        ctx.log.info("Resultado em %s:", ctx.pastas.raiz)
        for linha in resumo:
            ctx.log.info("  %s", linha)
        for aviso in avisos:
            ctx.log.warning("Atenção: %s", aviso)
        decorrido = self._tempo_total(ctx)
        if decorrido:
            ctx.log.info("Tempo total de processamento: %s.", formatar_duracao(decorrido))
        if problemas:
            for problema in problemas:
                ctx.log.error("Problema: %s", problema)
            raise ErroFerramenta(
                "A conferência final encontrou problemas: " + " | ".join(problemas)
            )

    # --- vídeo longo ------------------------------------------------------------------------
    def _video_longo(self, ctx, problemas: list[str], avisos: list[str], resumo: list[str]) -> None:
        video = ctx.pastas.video_longo
        if not video.exists():
            problemas.append(f"O vídeo longo não foi gerado ({video.name}).")
            return
        info = sondar(video)
        esperado = self._duracao_esperada(ctx)
        if not (info.tem_video and info.tem_audio):
            problemas.append(f"{video.name} está sem imagem ou sem som.")
        if esperado and abs(info.duracao_s - esperado) > TOLERANCIA_VIDEO_S:
            problemas.append(
                f"{video.name} tem {formatar_duracao(info.duracao_s)}, mas o esperado era "
                f"{formatar_duracao(esperado)}."
            )
        resumo.append(
            f"{video.name}: {formatar_duracao(info.duracao_s)}, "
            f"{info.largura}x{info.altura}, {video.stat().st_size / 1e9:.2f} GB"
        )
        self._legendas(ctx, info.duracao_s, problemas, avisos, resumo)

    def _duracao_esperada(self, ctx: ContextoProjeto) -> float | None:
        try:
            esperado = float(ler_json(ctx.pastas.cortes)["duracao_editada_s"])
        except (OSError, KeyError, ValueError):
            return None
        for ligado, caminho in (
            (ctx.opcoes.introducao, ctx.config.pastas.introducao),
            (ctx.opcoes.vinheta_final, ctx.config.pastas.vinheta_final),
        ):
            if ligado and caminho.exists():
                esperado += sondar(caminho).duracao_s
        return esperado

    def _legendas(self, ctx, duracao_video: float, problemas, avisos, resumo) -> None:
        srt = ctx.pastas.srt
        if not srt.exists():
            problemas.append(f"As legendas não foram geradas ({srt.name}).")
            return
        quantidade, fim = fim_da_ultima_legenda(srt.read_text(encoding="utf-8"))
        if quantidade == 0:
            avisos.append(f"{srt.name} está vazio: a transcrição não tem fala?")
        elif fim > duracao_video + TOLERANCIA_LEGENDA_S:
            problemas.append(
                f"A última legenda termina em {formatar_duracao(fim)}, depois do fim do vídeo "
                f"({formatar_duracao(duracao_video)})."
            )
        resumo.append(f"{srt.name}: {quantidade} legendas")

    # --- Shorts -----------------------------------------------------------------------------
    def _shorts(self, ctx, problemas: list[str], avisos: list[str], resumo: list[str]) -> None:
        if not ctx.pastas.cortes_shorts.exists():
            problemas.append("Os cortes dos Shorts não foram escolhidos.")
            return
        planejados = planejar_shorts(carregar_cortes(ctx.pastas.cortes_shorts))
        if not planejados:
            avisos.append("Nenhum corte foi aprovado: nenhum Short foi gerado.")
            return
        total_s = total_bytes = 0.0
        for curto in planejados:
            arquivo = ctx.pastas.shorts / curto.video
            if not arquivo.exists():
                problemas.append(f"O Short {curto.video} não foi gerado.")
                continue
            info = sondar(arquivo)
            if (info.largura, info.altura) != (LARGURA_SHORT, ALTURA_SHORT) or not info.tem_audio:
                problemas.append(f"{curto.video} não está em 1080x1920 com áudio.")
            if info.duracao_s > DURACAO_MAXIMA_S:
                problemas.append(f"{curto.video} tem {info.duracao_s:.1f} s (o limite é 60 s).")
            if not (ctx.pastas.shorts / curto.texto).exists():
                avisos.append(f"Falta o texto de publicação {curto.texto}.")
            total_s += info.duracao_s
            total_bytes += arquivo.stat().st_size
            resumo.append(f"{curto.video}: {info.duracao_s:.0f} s, nota {curto.corte.nota:.0f}")
        resumo.append(
            f"{len(planejados)} Shorts em {ctx.pastas.shorts.name}/ "
            f"({formatar_duracao(total_s)} no total, {total_bytes / 1e9:.2f} GB)"
        )

    def _tempo_total(self, ctx: ContextoProjeto) -> float:
        """Soma do tempo das etapas já concluídas (não conta as esperas por revisão humana)."""
        return sum(
            (e.fim - e.inicio).total_seconds()
            for e in ctx.estado.etapas
            if e.inicio is not None and e.fim is not None
        )
