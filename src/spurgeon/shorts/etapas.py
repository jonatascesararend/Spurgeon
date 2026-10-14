"""Etapas da Fase 4: achar trechos, avaliá-los com a IA, escolher os cortes e pedir aprovação."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from spurgeon.audio.nivel import carregar_niveis
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.ollama import garantir_servidor
from spurgeon.core.pipeline import ContextoProjeto, Etapa, ResultadoEtapa
from spurgeon.shorts.analise import (
    ParametrosJanela,
    escolher_sem_sobreposicao,
    gerar_candidatos,
    separar_frases,
)
from spurgeon.shorts.dados import ROTULOS_CATEGORIA, Avaliacao, Candidato, Corte
from spurgeon.shorts.llm import ClienteLLM, ErroLLM
from spurgeon.shorts.selecao import RESERVAS_PADRAO, montar_cortes, nome_de_arquivo
from spurgeon.transcricao.formato import Transcricao, segundos_para_rotulo

#: na pré-seleção os candidatos podem se sobrepor mais, para a IA comparar variações do mesmo trecho
SOBREPOSICAO_NA_PRE_SELECAO = 0.6
_CONTEXTO_ANTES = 300
_CONTEXTO_DEPOIS = 200
MODELO_SPACY = "pt_core_news_md"
_TEMPERATURAS_DE_VARIACAO = (0.5, 0.9)


def carregar_spacy():
    import spacy

    try:
        return spacy.load(MODELO_SPACY, exclude=["ner", "lemmatizer"])
    except OSError as erro:
        raise ErroFerramenta(
            f"Falta o modelo de português do spaCy ({MODELO_SPACY}). "
            "Rode o instalar.bat (ou: uv sync --extra shorts)."
        ) from erro


def criar_cliente_llm(ctx: ContextoProjeto) -> ClienteLLM:
    """O cliente do Ollama configurado para esta máquina; baixa o modelo se faltar."""
    if not garantir_servidor():  # tenta abrir o servidor; se não der, o erro explica o que fazer
        ctx.log.warning("O Ollama não está respondendo e não foi possível abri-lo sozinho.")
    perfil = detectar_hardware(ctx.config.pastas.projetos)
    cliente = ClienteLLM(
        ctx.opcoes.ollama_modelo,
        threads=threads_para_uso(perfil, ctx.opcoes.uso_cpu_percentual),
        usar_gpu=perfil.cuda_utilizavel,
    )
    if not cliente.modelo_instalado():
        ctx.log.info(
            "Baixando o modelo %s do Ollama (vários GB, só na primeira vez)…", cliente.modelo
        )
        ultimo = {"pct": -10}

        def progresso(fracao: float) -> None:
            ctx.ponto_seguro()
            pct = int(fracao * 100) // 10 * 10
            if pct != ultimo["pct"]:
                ultimo["pct"] = pct
                ctx.log.info("Modelo %s: %d%% baixado.", cliente.modelo, pct)

        cliente.baixar_modelo(progresso)
    return cliente


def _nome_do_sermao(ctx: ContextoProjeto) -> str:
    return re.sub(r"^\d{4}-\d{2}-\d{2}_", "", ctx.pastas.raiz.name).replace("_", " ")


class AnalisarShorts(Etapa):
    id = "analisar_shorts"
    nome = "Procurando trechos para os Shorts"
    depende_de = ("transcrever", "revisar_transcricao")
    opcoes_relevantes = ("shorts_duracao_min_s", "shorts_duracao_max_s", "shorts_candidatos_ia")
    peso = 1.0

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_shorts

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.candidatos]

    def executar(self, ctx: ContextoProjeto) -> None:
        palavras = Transcricao.carregar(ctx.pastas.transcricao).palavras
        frases = separar_frases(palavras, carregar_spacy())
        parametros = ParametrosJanela(
            minimo_s=ctx.opcoes.shorts_duracao_min_s, maximo_s=ctx.opcoes.shorts_duracao_max_s
        )
        todos = gerar_candidatos(palavras, frases, parametros)
        escolhidos = escolher_sem_sobreposicao(
            todos, ctx.opcoes.shorts_candidatos_ia, SOBREPOSICAO_NA_PRE_SELECAO
        )
        escrever_json_atomico(
            ctx.pastas.candidatos,
            {
                "frases": [{"inicio": f.inicio, "fim": f.fim, "texto": f.texto} for f in frases],
                "total_gerados": len(todos),
                "candidatos": [c.para_dict() for c in escolhidos],
            },
        )
        ctx.log.info(
            "%d frases e %d janelas de %g a %g s; os %d melhores pela pontuação objetiva vão "
            "para a avaliação da IA.",
            len(frases),
            len(todos),
            parametros.minimo_s,
            parametros.maximo_s,
            len(escolhidos),
        )
        if not escolhidos:
            ctx.log.warning(
                "Nenhuma janela de %g a %g s serve de Short.",
                parametros.minimo_s,
                parametros.maximo_s,
            )


def carregar_candidatos(caminho: Path) -> tuple[list[dict[str, Any]], list[Candidato]]:
    dados = ler_json(caminho)
    return dados["frases"], [Candidato.de_dict(d) for d in dados["candidatos"]]


def _ler_avaliacoes(caminho: Path, modelo: str) -> dict[int, Avaliacao]:
    if not caminho.exists():
        return {}
    dados = ler_json(caminho)
    if dados.get("modelo") != modelo:  # outro modelo, outra opinião: recomeça
        return {}
    return {int(k): Avaliacao.de_dict(v) for k, v in dados["avaliacoes"].items()}


def _gravar_avaliacoes(caminho: Path, modelo: str, avaliacoes: dict[int, Avaliacao]) -> None:
    escrever_json_atomico(
        caminho,
        {"modelo": modelo, "avaliacoes": {str(k): v.para_dict() for k, v in avaliacoes.items()}},
        indent=None,
    )


class AvaliarShorts(Etapa):
    id = "avaliar_shorts"
    nome = "Avaliando os trechos com a IA"
    depende_de = ("analisar_shorts",)
    opcoes_relevantes = ("ollama_modelo",)
    peso = 16.0  # ~35 min com o qwen3:8b na CPU

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_shorts

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.avaliacoes]

    def executar(self, ctx: ContextoProjeto) -> None:
        frases, candidatos = carregar_candidatos(ctx.pastas.candidatos)
        cliente = criar_cliente_llm(ctx)
        feitas = _ler_avaliacoes(ctx.pastas.avaliacoes, cliente.modelo)
        if feitas:
            ctx.log.info("Retomando: %d de %d trechos já avaliados.", len(feitas), len(candidatos))
        for numero, c in enumerate(candidatos, start=1):
            if c.id in feitas:
                continue
            ctx.ponto_seguro()
            antes = " ".join(
                f["texto"] for f in frases[max(0, c.primeira_frase - 2) : c.primeira_frase]
            )
            depois = " ".join(f["texto"] for f in frases[c.ultima_frase + 1 : c.ultima_frase + 2])
            try:
                avaliacao = cliente.avaliar(
                    antes[-_CONTEXTO_ANTES:], c.texto, depois[:_CONTEXTO_DEPOIS], c.duracao
                )
            except ErroLLM as erro:  # um trecho que a IA não consegue avaliar não derruba os outros
                ctx.log.warning("Trecho em %s ignorado: %s", segundos_para_rotulo(c.inicio), erro)
                continue
            feitas[c.id] = avaliacao
            _gravar_avaliacoes(ctx.pastas.avaliacoes, cliente.modelo, feitas)
            ctx.progresso(numero / len(candidatos))
            ctx.log.info(
                "Trecho %d de %d (%s): nota %d, %s%s.",
                numero,
                len(candidatos),
                segundos_para_rotulo(c.inicio),
                avaliacao.nota,
                ROTULOS_CATEGORIA.get(avaliacao.categoria, avaliacao.categoria),
                " (com alerta de contexto)" if avaliacao.alerta_contexto else "",
            )
        _gravar_avaliacoes(ctx.pastas.avaliacoes, cliente.modelo, feitas)
        if not feitas:
            raise ErroLLM("A IA não conseguiu avaliar nenhum trecho.")


def carregar_cortes(caminho: Path) -> list[Corte]:
    return [Corte.de_dict(d) for d in ler_json(caminho)]


def gravar_cortes(caminho: Path, cortes: list[Corte]) -> None:
    escrever_json_atomico(caminho, [c.para_dict() for c in cortes])


class SelecionarShorts(Etapa):
    id = "selecionar_shorts"
    nome = "Escolhendo os cortes e criando títulos"
    depende_de = ("avaliar_shorts", "medir_nivel")
    opcoes_relevantes = ("shorts_limite_seguranca", "shorts_nota_minima", "ollama_modelo")
    peso = 8.0  # título, descrição e hashtags de cada corte (~15 min)

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_shorts

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.cortes_shorts]

    def executar(self, ctx: ContextoProjeto) -> None:
        _, candidatos = carregar_candidatos(ctx.pastas.candidatos)
        avaliacoes = _ler_avaliacoes(ctx.pastas.avaliacoes, ctx.opcoes.ollama_modelo)
        cortes = montar_cortes(
            candidatos,
            avaliacoes,
            limite=ctx.opcoes.shorts_limite_seguranca,
            nota_minima=ctx.opcoes.shorts_nota_minima,
            reservas=RESERVAS_PADRAO,
            niveis=self._niveis(ctx),
        )
        principais = [c for c in cortes if not c.reserva]
        ctx.log.info(
            "%d cortes escolhidos (notas de %s a %s) e %d de reserva.",
            len(principais),
            f"{min((c.nota for c in principais), default=0):g}",
            f"{max((c.nota for c in principais), default=0):g}",
            len(cortes) - len(principais),
        )
        if not principais:
            ctx.log.warning(
                "Nenhum corte passou da nota mínima (%g). Veja as reservas na aprovação.",
                ctx.opcoes.shorts_nota_minima,
            )
        cliente = criar_cliente_llm(ctx)
        try:
            self._criar_metadados(ctx, cortes, cliente)
        finally:
            cliente.descarregar()  # a renderização que vem a seguir precisa da memória
        gravar_cortes(ctx.pastas.cortes_shorts, cortes)

    def _niveis(self, ctx: ContextoProjeto):
        """As medidas de 50 ms do áudio e o limiar de silêncio, para ajustar início e fim."""
        if not ctx.pastas.niveis.exists():
            ctx.log.warning("Sem a medição de nível do áudio: os cortes usam um respiro fixo.")
            return None
        medido = carregar_niveis(ctx.pastas.niveis)
        return medido.medidas, medido.limiar_db

    def _criar_metadados(
        self, ctx: ContextoProjeto, cortes: list[Corte], cliente: ClienteLLM
    ) -> None:
        # Cada título leva cerca de 1 min na CPU: o que já foi feito não se perde numa queda.
        # Prioridade: o que a pessoa editou na aprovação, depois o que a IA já tinha criado.
        anteriores: dict[int, Corte] = {}
        if ctx.pastas.cortes_shorts.exists():
            anteriores = {c.id: c for c in carregar_cortes(ctx.pastas.cortes_shorts) if c.titulo}
        cache = _ler_cache_de_metadados(ctx.pastas.metadados)
        sermao = _nome_do_sermao(ctx)
        usados: set[str] = set()
        titulos: list[str] = []
        for numero, corte in enumerate(cortes, start=1):
            ctx.ponto_seguro()
            marca = _marca_do_texto(corte.texto)
            antigo = anteriores.get(corte.id)
            guardado = cache.get(str(corte.id))
            if antigo is not None:
                corte.titulo, corte.descricao = antigo.titulo, antigo.descricao
                corte.hashtags = antigo.hashtags
                corte.slug = nome_de_arquivo(antigo.slug or antigo.titulo, usados)
            elif guardado is not None and guardado["marca"] == marca:
                corte.titulo, corte.descricao = guardado["titulo"], guardado["descricao"]
                corte.hashtags = guardado["hashtags"]
                corte.slug = nome_de_arquivo(guardado["slug"] or guardado["titulo"], usados)
            else:
                meta = self._metadados_sem_repetir(cliente, sermao, corte, titulos)
                corte.titulo = meta.titulo.strip()
                corte.descricao = meta.descricao.strip()
                corte.hashtags = meta.hashtags
                corte.slug = nome_de_arquivo(meta.slug or meta.titulo, usados)
                cache[str(corte.id)] = {
                    "marca": marca,
                    "titulo": corte.titulo,
                    "descricao": corte.descricao,
                    "hashtags": corte.hashtags,
                    "slug": corte.slug,
                }
                escrever_json_atomico(ctx.pastas.metadados, cache, indent=None)
                ctx.log.info(
                    "Título %d de %d (%s): %s",
                    numero,
                    len(cortes),
                    formatar_duracao(corte.duracao),
                    corte.titulo,
                )
            usados.add(corte.slug)
            titulos.append(corte.titulo)
            ctx.progresso(numero / len(cortes))

    def _metadados_sem_repetir(self, cliente, sermao: str, corte: Corte, titulos: list[str]):
        """Pede os metadados e, se o título repetir um já criado, tenta de novo mais solto."""
        meta = cliente.metadados(
            sermao, corte.texto, corte.categoria, corte.duracao, titulos_usados=titulos
        )
        conhecidos = {_chave_do_titulo(t) for t in titulos}
        for temperatura in _TEMPERATURAS_DE_VARIACAO:
            if _chave_do_titulo(meta.titulo) not in conhecidos:
                break
            meta = cliente.metadados(
                sermao,
                corte.texto,
                corte.categoria,
                corte.duracao,
                titulos_usados=titulos,
                temperatura=temperatura,
            )
        return meta


def _chave_do_titulo(titulo: str) -> str:
    return re.sub(r"\W+", " ", titulo.casefold()).strip()


def _marca_do_texto(texto: str) -> str:
    """Identifica o texto de um corte: se a transcrição mudar, o título guardado não vale mais."""
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def _ler_cache_de_metadados(caminho: Path) -> dict[str, dict[str, Any]]:
    try:
        dados = ler_json(caminho)
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


class AprovarCortes(Etapa):
    id = "aprovar_cortes"
    nome = "Aprovação dos cortes dos Shorts"
    depende_de = ("selecionar_shorts",)
    peso = 0.0

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_shorts and opcoes.pausar_aprovar_cortes

    def executar(self, ctx: ContextoProjeto) -> ResultadoEtapa:
        ctx.log.info(
            "Confira os cortes na janela e clique em Aprovar e continuar (arquivo: %s).",
            ctx.pastas.cortes_shorts.name,
        )
        return ResultadoEtapa.AGUARDANDO_REVISAO
