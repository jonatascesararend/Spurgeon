"""Etapas da Fase 3: transcrição, revisão, pausas conferidas pela fala e legendas .srt."""

from __future__ import annotations

import hashlib
from pathlib import Path

from spurgeon.audio.nivel import carregar_niveis
from spurgeon.audio.pausas import DURACAO_MINIMA_S, encontrar_pausas, subtrair_intervalos
from spurgeon.core.arquivos import escrever_json_atomico, escrever_texto_atomico, ler_json
from spurgeon.core.ffmpeg import sondar
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, recomendar, threads_para_uso
from spurgeon.core.pipeline import ContextoProjeto, Etapa, ResultadoEtapa
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.transcricao.alucinacao import remover_alucinacoes
from spurgeon.transcricao.blocos import PAUSA_MINIMA_S, planejar_blocos
from spurgeon.transcricao.formato import Bloco, Transcricao, segundos_para_rotulo
from spurgeon.transcricao.modelo import (
    TAMANHO_MB,
    baixar_modelo,
    carregar_modelo,
    modelo_baixado,
)
from spurgeon.transcricao.motor import (
    TAXA_HZ,
    VERSAO_DOS_PARAMETROS,
    transcrever_trecho,
)
from spurgeon.transcricao.srt import formatar_srt, montar_legendas, remapear_palavras
from spurgeon.transcricao.vocabulario import carregar_vocabulario, corrigir_termos, montar_dicas

_TOLERANCIA_PLANO_S = 1.0


class Transcrever(Etapa):
    id = "transcrever"
    nome = "Transcrevendo o áudio (Whisper)"
    depende_de = ("extrair_audio", "limpar_audio", "normalizar_audio", "medir_nivel")
    opcoes_relevantes = ("limpar_audio", "normalizar_volume", "lufs_video_longo", "whisper_modelo")
    peso = 25.0

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.transcricao]

    def executar(self, ctx: ContextoProjeto) -> None:
        niveis = carregar_niveis(ctx.pastas.niveis)
        duracao = niveis.duracao_s
        pausas = encontrar_pausas(niveis.medidas, niveis.limiar_db, PAUSA_MINIMA_S, duracao)
        plano = planejar_blocos(duracao, pausas)

        termos = carregar_vocabulario(ctx.config.pastas.vocabulario)
        dicas = montar_dicas(termos)
        assinatura = _assinatura(ctx, dicas)
        transcricao = _retomar_ou_criar(ctx, plano, duracao, assinatura)

        pendentes = [b for b in transcricao.blocos if not b.concluido]
        if pendentes:
            feitos = len(transcricao.blocos) - len(pendentes)
            if feitos:
                ctx.log.info("Retomando: %d de %d blocos já transcritos.", feitos, len(plano))
            modelo = _preparar_modelo(ctx)
            from faster_whisper.audio import decode_audio

            audio = decode_audio(str(ctx.pastas.audio_final(ctx.opcoes)), sampling_rate=TAXA_HZ)
            ctx.log.info("Termos de apoio ao Whisper: %s", dicas or "(nenhum)")
            segundos_feitos = sum(b.fim - b.inicio for b in transcricao.blocos if b.concluido)
            for numero, bloco in enumerate(transcricao.blocos, start=1):
                if bloco.concluido:
                    continue
                _transcrever_bloco(
                    ctx, modelo, audio, bloco, numero, len(plano), dicas, segundos_feitos, duracao
                )
                segundos_feitos += bloco.fim - bloco.inicio
                transcricao.salvar(ctx.pastas.transcricao)
            del modelo

        for r in remover_alucinacoes(transcricao):
            ctx.log.warning(
                "Removido o trecho inventado pelo Whisper em %s: “%s” (%s).",
                segundos_para_rotulo(r.inicio),
                r.texto,
                r.motivo,
            )
        correcoes = corrigir_termos(transcricao.palavras, termos)
        for c in correcoes[:10]:
            ctx.log.info(
                "Termo corrigido em %s: %s → %s", segundos_para_rotulo(c.inicio), c.antes, c.depois
            )
        if len(correcoes) > 10:
            ctx.log.info("... e mais %d correções de termos.", len(correcoes) - 10)
        transcricao.salvar(ctx.pastas.transcricao)
        palavras = len(transcricao.palavras)
        ctx.log.info(
            "Transcrição pronta: %d segmentos, %d palavras (%.0f palavras por minuto).",
            len(transcricao.segmentos),
            palavras,
            palavras / (duracao / 60) if duracao else 0,
        )


def _assinatura(ctx: ContextoProjeto, dicas: str) -> str:
    """Identifica o que muda o resultado; se mudar, a transcrição gravada não vale mais."""
    base = f"{ctx.opcoes.assinatura(Transcrever.opcoes_relevantes)}|{VERSAO_DOS_PARAMETROS}|{dicas}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _retomar_ou_criar(
    ctx: ContextoProjeto, plano: list[tuple[float, float]], duracao: float, assinatura: str
) -> Transcricao:
    """Aproveita os blocos de uma execução interrompida, se nada relevante mudou."""
    caminho = ctx.pastas.transcricao
    if caminho.exists():
        try:
            antiga = Transcricao.carregar(caminho)
        except (OSError, ValueError, KeyError):
            antiga = None
        if (
            antiga is not None
            and antiga.assinatura == assinatura
            and len(antiga.blocos) == len(plano)
            and all(
                abs(b.inicio - ini) < _TOLERANCIA_PLANO_S and abs(b.fim - fim) < _TOLERANCIA_PLANO_S
                for b, (ini, fim) in zip(antiga.blocos, plano, strict=True)
            )
        ):
            return antiga
    return Transcricao(
        modelo=ctx.opcoes.whisper_modelo,
        duracao_s=duracao,
        assinatura=assinatura,
        blocos=[Bloco(ini, fim) for ini, fim in plano],
    )


def _preparar_modelo(ctx: ContextoProjeto):
    nome = ctx.opcoes.whisper_modelo
    if not modelo_baixado(ctx.config, nome):
        ctx.log.info(
            "Baixando o modelo Whisper %s (cerca de %d MB, só na primeira vez)…",
            nome,
            TAMANHO_MB.get(nome, 0),
        )
        baixar_modelo(ctx.config, nome, cancelar=ctx.cancelar)
        ctx.log.info("Modelo baixado.")
    perfil = detectar_hardware(ctx.config.pastas.projetos)
    recomendacao = recomendar(perfil)
    threads = threads_para_uso(perfil, ctx.opcoes.uso_cpu_percentual)
    ctx.log.info(
        "Carregando o modelo %s (%s, %s, %d threads).",
        nome,
        recomendacao.whisper_dispositivo,
        recomendacao.whisper_compute_type,
        threads,
    )
    return carregar_modelo(
        ctx.config,
        nome,
        dispositivo=recomendacao.whisper_dispositivo,
        compute_type=recomendacao.whisper_compute_type,
        threads=threads,
    )


def _transcrever_bloco(
    ctx: ContextoProjeto,
    modelo,
    audio,
    bloco: Bloco,
    numero: int,
    total: int,
    dicas: str,
    segundos_feitos: float,
    duracao: float,
) -> None:
    ctx.log.info(
        "Transcrevendo o bloco %d de %d (%s a %s)…",
        numero,
        total,
        segundos_para_rotulo(bloco.inicio),
        segundos_para_rotulo(bloco.fim),
    )

    def ao_segmento(fim_absoluto: float) -> None:
        ctx.ponto_seguro()  # pausa e cancelamento valem a cada segmento pronto
        ctx.progresso((segundos_feitos + (fim_absoluto - bloco.inicio)) / duracao)

    bloco.segmentos = transcrever_trecho(modelo, audio, bloco.inicio, bloco.fim, dicas, ao_segmento)
    bloco.concluido = True


class RevisarTranscricao(Etapa):
    id = "revisar_transcricao"
    nome = "Revisão da transcrição"
    depende_de = ("transcrever",)
    peso = 0.0

    def ativa(self, opcoes) -> bool:
        return opcoes.pausar_revisar_transcricao

    def executar(self, ctx: ContextoProjeto) -> ResultadoEtapa:
        ctx.log.info(
            "Revise a transcrição na janela e clique em Aprovar e continuar (arquivo: %s).",
            ctx.pastas.transcricao.name,
        )
        return ResultadoEtapa.AGUARDANDO_REVISAO


class RefinarPausas(Etapa):
    id = "refinar_pausas"
    nome = "Conferindo as pausas com a transcrição"
    depende_de = ("detectar_pausas", "transcrever")
    peso = 0.2

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.pausas]

    def executar(self, ctx: ContextoProjeto) -> None:
        dados = ler_json(ctx.pastas.pausas)
        energia = [(a, b) for a, b in dados["pausas"]]
        transcricao = Transcricao.carregar(ctx.pastas.transcricao)
        fala = [(p.inicio, p.fim) for p in transcricao.palavras]
        refinadas = subtrair_intervalos(energia, fala, duracao_minima_s=DURACAO_MINIMA_S)
        dados["pausas_refinadas"] = [[round(a, 3), round(b, 3)] for a, b in refinadas]
        escrever_json_atomico(ctx.pastas.pausas, dados)
        perdido = sum(b - a for a, b in energia) - sum(b - a for a, b in refinadas)
        ctx.log.info(
            "%d pausas por energia; %d depois de tirar onde o Whisper ouviu fala (%s a menos).",
            len(energia),
            len(refinadas),
            formatar_duracao(perdido) if perdido >= 1 else "menos de 1 s",
        )


class GerarSrt(Etapa):
    id = "gerar_srt"
    nome = "Gerando as legendas (.srt)"
    depende_de = ("transcrever", "revisar_transcricao", "calcular_cortes")
    opcoes_relevantes = ("introducao", "srt_max_linhas", "srt_max_caracteres_por_linha")
    peso = 0.3

    def ativa(self, opcoes) -> bool:
        return opcoes.gerar_video_longo

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.srt]

    def executar(self, ctx: ContextoProjeto) -> None:
        cortes = ler_json(ctx.pastas.cortes)
        deslocamento = 0.0
        if ctx.opcoes.introducao:
            deslocamento = sondar(ctx.config.pastas.introducao).duracao_s
            ctx.log.info(
                "Introdução de %s: os tempos das legendas avançam esse tanto.",
                formatar_duracao(deslocamento),
            )
        mapa = MapaTempo([Trecho(a, b) for a, b in cortes["trechos"]], deslocamento)
        transcricao = Transcricao.carregar(ctx.pastas.transcricao)
        palavras = remapear_palavras(transcricao.palavras, mapa)
        legendas = montar_legendas(
            palavras,
            max_linhas=ctx.opcoes.srt_max_linhas,
            max_caracteres=ctx.opcoes.srt_max_caracteres_por_linha,
        )
        escrever_texto_atomico(ctx.pastas.srt, formatar_srt(legendas))
        ctx.log.info(
            "%d legendas em %s (até %d linhas de %d caracteres).",
            len(legendas),
            ctx.pastas.srt.name,
            ctx.opcoes.srt_max_linhas,
            ctx.opcoes.srt_max_caracteres_por_linha,
        )
