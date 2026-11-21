"""Etapas da Fase 4: achar trechos, avaliá-los com a IA, escolher os cortes e pedir aprovação."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from spurgeon.audio.nivel import carregar_niveis
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.diagnostico import situacao_da_maquina
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.formatacao import formatar_duracao
from spurgeon.core.hardware import detectar_hardware, threads_para_uso
from spurgeon.core.ollama import garantir_servidor
from spurgeon.core.pipeline import ContextoProjeto, Etapa, ResultadoEtapa
from spurgeon.shorts.analise import (
    ParametrosJanela,
    escolher_para_ia,
    gerar_candidatos,
    palavras_da_janela,
    separar_frases,
)
from spurgeon.shorts.dados import (
    ORIGEM_MANUAL,
    ROTULOS_CATEGORIA,
    Avaliacao,
    Candidato,
    Corte,
)
from spurgeon.shorts.llm import ClienteLLM, ErroLLM
from spurgeon.shorts.selecao import RESERVAS_PADRAO, montar_cortes, nome_de_arquivo
from spurgeon.shorts.titulos import LIMITE_DE_SEMELHANCA, Problema, mais_parecido
from spurgeon.transcricao.formato import Transcricao, segundos_para_rotulo

#: na pré-seleção os candidatos podem se sobrepor mais, para a IA comparar variações do mesmo trecho
SOBREPOSICAO_NA_PRE_SELECAO = 0.6
_CONTEXTO_ANTES = 300
_CONTEXTO_DEPOIS = 200
MODELO_SPACY = "pt_core_news_md"


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
    ctx.log.info("Conferindo se o Ollama está respondendo (abre o servidor se precisar)…")
    if not garantir_servidor():  # tenta abrir o servidor; se não der, o erro explica o que fazer
        ctx.log.warning("O Ollama não está respondendo e não foi possível abri-lo sozinho.")
    perfil = detectar_hardware(ctx.config.pastas.projetos)
    ctx.log.info(
        "IA: modelo %s do Ollama, %s.",
        ctx.opcoes.ollama_modelo,
        "com a GPU" if perfil.cuda_utilizavel else "só no processador (sem GPU utilizável)",
    )
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


def nome_do_sermao(raiz: Path) -> str:
    """O nome do sermão pela pasta do projeto (`2026-10-04_Miqueias_2` → `Miqueias 2`)."""
    return re.sub(r"^\d{4}-\d{2}-\d{2}_", "", raiz.name).replace("_", " ")


def _nome_do_sermao(ctx: ContextoProjeto) -> str:
    return nome_do_sermao(ctx.pastas.raiz)


class AnalisarShorts(Etapa):
    id = "analisar_shorts"
    nome = "Procurando trechos para os Shorts"
    depende_de = ("transcrever", "revisar_transcricao")
    opcoes_relevantes = (
        "shorts_duracao_min_s",
        "shorts_duracao_max_s",
        "shorts_candidatos_ia",
        "video_inicio_s",
        "video_fim_s",
    )
    peso = 1.0

    def ativa(self, opcoes) -> bool:
        return opcoes.processa_shorts

    def artefatos(self, ctx: ContextoProjeto) -> list[Path]:
        return [ctx.pastas.candidatos]

    def executar(self, ctx: ContextoProjeto) -> None:
        palavras = Transcricao.carregar(ctx.pastas.transcricao).palavras
        if ctx.opcoes.tem_janela:  # só o que está no trecho do vídeo que o projeto usa
            fim = ctx.opcoes.video_fim_s or None
            palavras = palavras_da_janela(palavras, ctx.opcoes.video_inicio_s, fim)
            ctx.log.info(
                "Só as %d palavras do trecho escolhido do vídeo entram nos Shorts.", len(palavras)
            )
        migrar_avaliacoes(ctx.pastas.avaliacoes, ctx.pastas.candidatos)
        ctx.log.info("Carregando o modelo de português do spaCy…")
        nlp = ctx.executar_vigiado(
            "Carregando o modelo de português do spaCy", carregar_spacy, detalhe=situacao_da_maquina
        )
        ctx.log.info("Separando as %d palavras em frases…", len(palavras))
        frases = ctx.executar_vigiado(
            "Separando as frases",
            lambda: separar_frases(palavras, nlp),
            detalhe=situacao_da_maquina,
        )
        parametros = ParametrosJanela(
            minimo_s=ctx.opcoes.shorts_duracao_min_s, maximo_s=ctx.opcoes.shorts_duracao_max_s
        )
        ctx.log.info(
            "%d frases; montando as janelas de %g a %g s…",
            len(frases),
            parametros.minimo_s,
            parametros.maximo_s,
        )
        todos = ctx.executar_vigiado(
            "Montando as janelas candidatas",
            lambda: gerar_candidatos(palavras, frases, parametros),
            detalhe=situacao_da_maquina,
        )
        escolhidos = escolher_para_ia(
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


def marca_do_candidato(c: Candidato) -> str:
    """Identifica um candidato pelo trecho e pelo texto (o id muda quando a análise é refeita)."""
    bruto = f"{c.inicio:.2f}|{c.fim:.2f}|{c.texto}"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:12]


def migrar_avaliacoes(avaliacoes: Path, candidatos: Path) -> None:
    """Passa as avaliações de um projeto antigo (guardadas por id) para a chave por trecho.

    A análise refeita (outra duração máxima, outro começo do vídeo…) numera os candidatos de novo;
    por isso as avaliações da IA, que levam ~1 min cada, passaram a valer por trecho e texto, e as
    que continuam valendo são reaproveitadas. Precisa rodar ANTES de `candidatos.json` ser trocado.
    """
    if not avaliacoes.exists() or not candidatos.exists():
        return
    try:
        dados = ler_json(avaliacoes)
        if dados.get("versao", 1) >= 2:
            return
        antigos = {c.id: c for c in carregar_candidatos(candidatos)[1]}
        convertidas = {
            marca_do_candidato(antigos[int(k)]): v
            for k, v in dados["avaliacoes"].items()
            if int(k) in antigos
        }
    except (OSError, ValueError, KeyError):
        return
    escrever_json_atomico(
        avaliacoes,
        {"versao": 2, "modelo": dados.get("modelo"), "avaliacoes": convertidas},
        indent=None,
    )


def _ler_avaliacoes(
    caminho: Path, modelo: str, candidatos: Sequence[Candidato]
) -> dict[int, Avaliacao]:
    """As avaliações já feitas dos candidatos de agora (id de agora → avaliação)."""
    if not caminho.exists():
        return {}
    dados = ler_json(caminho)
    if dados.get("modelo") != modelo:  # outro modelo, outra opinião: recomeça
        return {}
    guardadas = dados["avaliacoes"]
    if dados.get("versao", 1) < 2:  # projeto antigo, por id: vale para os candidatos que há
        return {int(k): Avaliacao.de_dict(v) for k, v in guardadas.items()}
    return {
        c.id: Avaliacao.de_dict(guardadas[marca])
        for c in candidatos
        if (marca := marca_do_candidato(c)) in guardadas
    }


def _gravar_avaliacoes(
    caminho: Path, modelo: str, avaliacoes: dict[int, Avaliacao], candidatos: Sequence[Candidato]
) -> None:
    marcas = {c.id: marca_do_candidato(c) for c in candidatos}
    guardadas: dict = {}
    try:  # o que já estava guardado para outros trechos (de uma análise anterior) não se perde
        antigo = ler_json(caminho) if caminho.exists() else {}
        if antigo.get("versao", 1) >= 2 and antigo.get("modelo") == modelo:
            guardadas = dict(antigo["avaliacoes"])
    except (OSError, ValueError, KeyError, AttributeError):
        guardadas = {}
    guardadas.update({marcas[k]: v.para_dict() for k, v in avaliacoes.items() if k in marcas})
    escrever_json_atomico(
        caminho, {"versao": 2, "modelo": modelo, "avaliacoes": guardadas}, indent=None
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
        feitas = _ler_avaliacoes(ctx.pastas.avaliacoes, cliente.modelo, candidatos)
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
                with ctx.batimento(
                    f"Avaliando o trecho {numero} de {len(candidatos)} "
                    f"({segundos_para_rotulo(c.inicio)}, {c.duracao:.0f} s) na IA",
                    detalhe=situacao_da_maquina,
                    intervalo_s=30.0,
                    quando_pausa="quando a IA responder sobre este trecho (cerca de 1 min)",
                ):
                    avaliacao = cliente.avaliar(
                        antes[-_CONTEXTO_ANTES:], c.texto, depois[:_CONTEXTO_DEPOIS], c.duracao
                    )
            except ErroLLM as erro:  # um trecho que a IA não consegue avaliar não derruba os outros
                ctx.log.warning("Trecho em %s ignorado: %s", segundos_para_rotulo(c.inicio), erro)
                continue
            feitas[c.id] = avaliacao
            _gravar_avaliacoes(ctx.pastas.avaliacoes, cliente.modelo, feitas, candidatos)
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
        _gravar_avaliacoes(ctx.pastas.avaliacoes, cliente.modelo, feitas, candidatos)
        if not feitas:
            raise ErroLLM("A IA não conseguiu avaliar nenhum trecho.")


def carregar_cortes(caminho: Path) -> list[Corte]:
    return [Corte.de_dict(d) for d in ler_json(caminho)]


def gravar_cortes(caminho: Path, cortes: list[Corte]) -> None:
    escrever_json_atomico(caminho, [c.para_dict() for c in cortes])


def gravar_campos_do_corte(caminho: Path, id_do_corte: int, **campos) -> bool:
    """Grava só estes campos de um corte no arquivo, sem levar junto o resto do que a tela tem
    em edição (o "já postado" e o "agendado para" valem na hora). Devolve False se o corte não
    está no arquivo (ainda não foi salvo)."""
    if not caminho.exists():
        return False
    cortes = carregar_cortes(caminho)
    for corte in cortes:
        if corte.id == id_do_corte:
            for nome, valor in campos.items():
                setattr(corte, nome, valor)
            gravar_cortes(caminho, cortes)
            return True
    return False


def gravar_postado(caminho: Path, id_do_corte: int, postado: bool, quando: str = "") -> bool:
    """Grava só o "já postado" de um corte (e a data), sem o resto da edição."""
    return gravar_campos_do_corte(
        caminho, id_do_corte, postado=postado, postado_em=quando if postado else ""
    )


def cortes_manuais_guardados(caminho: Path) -> list[Corte]:
    """Os cortes criados à mão (Shorts livre) que já estão no arquivo, para não se perderem.

    Quem refaz a seleção automática reescreve `cortes_shorts.json`; o que a pessoa escolheu a dedo
    não vem da análise, então não pode sumir junto com ela.
    """
    if not caminho.exists():
        return []
    try:
        return [c for c in carregar_cortes(caminho) if c.origem == ORIGEM_MANUAL]
    except (OSError, ValueError, TypeError, KeyError):
        return []


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
        avaliacoes = _ler_avaliacoes(ctx.pastas.avaliacoes, ctx.opcoes.ollama_modelo, candidatos)
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
                "Nenhum corte passou da nota mínima (%g). Veja as reservas no Studio Shorts.",
                ctx.opcoes.shorts_nota_minima,
            )
        cliente = criar_cliente_llm(ctx)
        try:
            self._criar_metadados(ctx, cortes, cliente)
        finally:
            cliente.descarregar()  # a renderização que vem a seguir precisa da memória
        manuais = cortes_manuais_guardados(ctx.pastas.cortes_shorts)
        if manuais:
            ctx.log.info(
                "Mantendo os %d Short(s) criados à mão no Studio (Shorts livre).", len(manuais)
            )
        gravar_cortes(ctx.pastas.cortes_shorts, [*cortes, *manuais])

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
            if antigo is not None and not _mesmo_trecho(antigo, corte):
                antigo = None  # a análise foi refeita: o número é o mesmo, mas o trecho é outro
            if antigo is not None and antigo.postado:
                corte.postado, corte.postado_em = True, antigo.postado_em  # não se perde ao refazer
            if antigo is not None and antigo.agendado_para:
                corte.agendado_para = antigo.agendado_para
            guardado = cache.get(str(corte.id))
            if antigo is not None and _repete_um_ja_criado(antigo.titulo, titulos, guardado, marca):
                ctx.log.info("O título do corte %d repete um já criado; criando outro.", numero)
                antigo = guardado = None
            if antigo is not None:
                corte.titulo, corte.descricao = antigo.titulo, antigo.descricao
                corte.hashtags = antigo.hashtags
                corte.slug = nome_de_arquivo(antigo.slug or antigo.titulo, usados)
            elif (
                guardado is not None
                and guardado["marca"] == marca
                and not _repete_um_ja_criado(guardado["titulo"], titulos, guardado, marca)
            ):
                corte.titulo, corte.descricao = guardado["titulo"], guardado["descricao"]
                corte.hashtags = guardado["hashtags"]
                corte.slug = nome_de_arquivo(guardado["slug"] or guardado["titulo"], usados)
            else:
                with ctx.batimento(
                    f"Criando título, descrição e hashtags do corte {numero} de {len(cortes)}",
                    detalhe=situacao_da_maquina,
                    intervalo_s=30.0,
                    quando_pausa="quando a IA responder sobre este corte (cerca de 1 min)",
                ):
                    meta = self._metadados_sem_repetir(cliente, sermao, corte, titulos, ctx)
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

    def _metadados_sem_repetir(
        self, cliente, sermao: str, corte: Corte, titulos: list[str], ctx: ContextoProjeto
    ):
        def recusado(titulo: str, problema: Problema) -> None:
            ctx.log.info("Título recusado (%s): “%s” %s.", problema.tipo, titulo, problema.motivo)

        return metadados_sem_repetir(
            cliente, sermao, corte.texto, corte.categoria, corte.duracao, titulos, recusado
        )


def metadados_sem_repetir(
    cliente: ClienteLLM,
    sermao: str,
    texto: str,
    categoria: str,
    duracao_s: float,
    titulos: Sequence[str],
    ao_recusar: Callable[[str, Problema], None] | None = None,
):
    """Pede os metadados e confere o título: se for igual, parecido demais com um já criado ou
    genérico, pede só o título de novo, dizendo à IA o que estava errado (até algumas vezes; se
    nenhum servir, fica o melhor). O nome do arquivo acompanha o título que ficou."""
    meta = cliente.metadados(sermao, texto, categoria, duracao_s, titulos_usados=titulos)
    escolha = cliente.melhorar_titulo(
        meta.titulo, sermao, texto, categoria, duracao_s, titulos, ao_recusar
    )
    if escolha.problema is not None and ao_recusar is not None:
        ao_recusar(escolha.titulo, escolha.problema)  # ficou assim mesmo: o log mostra
    if escolha.titulo != meta.titulo:
        meta = meta.model_copy(update={"titulo": escolha.titulo, "slug": escolha.titulo})
    return meta


def _repete_um_ja_criado(
    titulo: str, titulos: Sequence[str], guardado: dict[str, Any] | None, marca: str
) -> bool:
    """O título que a IA criou antes (e que a pessoa não mexeu) é igual ou parecido demais com um
    já criado neste sermão? Títulos que a pessoa escreveu ou editou nunca são trocados."""
    veio_da_ia = (
        guardado is not None and guardado.get("marca") == marca and guardado.get("titulo") == titulo
    )
    return veio_da_ia and mais_parecido(titulo, titulos)[1] >= LIMITE_DE_SEMELHANCA


def _mesmo_trecho(a: Corte, b: Corte, minimo: float = 0.8) -> bool:
    """Os dois cortes cobrem (quase) o mesmo trecho do sermão? Um corte editado ainda conta."""
    comum = min(a.fim, b.fim) - max(a.inicio, b.inicio)
    return comum > 0 and comum / max(min(a.duracao, b.duracao), 0.1) >= minimo


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
    """A antiga pausa para aprovar os cortes. Não roda mais: os Shorts saem todos desmarcados e a
    pessoa marca e ajusta no Studio Shorts (nenhum sai pronto da IA). Fica registrada só para um
    projeto antigo que parou nela poder ser aprovado e seguir."""

    id = "aprovar_cortes"
    nome = "Aprovação dos cortes dos Shorts"
    depende_de = ("selecionar_shorts",)
    peso = 0.0

    def ativa(self, opcoes) -> bool:
        return False

    def executar(self, ctx: ContextoProjeto) -> ResultadoEtapa:
        ctx.log.info(
            "Confira os cortes na janela e clique em Aprovar e continuar (arquivo: %s).",
            ctx.pastas.cortes_shorts.name,
        )
        return ResultadoEtapa.AGUARDANDO_REVISAO
