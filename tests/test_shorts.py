"""Fase 4: frases, candidatos, notas, seleção, cliente da IA e etapas (com um Ollama de mentira)."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.erros import ErroFerramenta
from spurgeon.core.pipeline import ResultadoEtapa
from spurgeon.shorts import etapas as etapas_mod
from spurgeon.shorts.analise import (
    FOLGA_FIM_S,
    FOLGA_INICIO_S,
    PAUSA_INTERNA_MAXIMA_S,
    ParametrosJanela,
    escolher_sem_sobreposicao,
    gerar_candidatos,
    pontos_de_abertura,
    pontos_de_bordas,
    pontos_de_duracao,
    pontos_de_gancho,
    pontos_de_ritmo,
    pre_nota,
    separar_frases,
    sobreposicao,
)
from spurgeon.shorts.dados import Avaliacao, Candidato, Corte
from spurgeon.shorts.edicao import (
    LimiteInvalido,
    faixa_da_nota,
    indices_das_palavras,
    ler_hashtags,
    mover_limite,
    palavras_das_pontas,
    resumir,
)
from spurgeon.shorts.etapas import (
    AnalisarShorts,
    AprovarCortes,
    AvaliarShorts,
    SelecionarShorts,
    carregar_cortes,
)
from spurgeon.shorts.llm import ClienteLLM, ErroLLM, MetadadosLLM
from spurgeon.shorts.pontuacao import calcular_nota, descrever_detalhe
from spurgeon.shorts.selecao import com_respiro, montar_cortes, nome_de_arquivo
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao

# --- um sermão de mentira ------------------------------------------------------------------

SEGUNDOS_POR_PALAVRA = 0.35
PAUSA_ENTRE_FRASES_S = 0.6


def sermao_falso(frases: int = 40, palavras_por_frase: int = 12) -> list[Palavra]:
    """`frases` frases de 12 palavras (4,2 s cada), com 0,6 s de pausa entre elas."""
    palavras, t = [], 0.0
    for i in range(frases):
        for k in range(palavras_por_frase):
            texto = f"verdade{i}x{k}"
            if k == 0 and i % 5 == 1:
                texto = "Mas"
            if k == palavras_por_frase - 1:
                texto += "?" if i % 7 == 3 else "."
            palavras.append(Palavra(texto, t, t + SEGUNDOS_POR_PALAVRA - 0.05))
            t += SEGUNDOS_POR_PALAVRA
        t += PAUSA_ENTRE_FRASES_S
    return palavras


def gravar_transcricao(caminho, palavras: list[Palavra]) -> None:
    segmento = Segmento(palavras)
    bloco = Bloco(inicio=0.0, fim=palavras[-1].fim, concluido=True, segmentos=[segmento])
    Transcricao(modelo="teste", duracao_s=palavras[-1].fim + 1, blocos=[bloco]).salvar(caminho)


# --- frases --------------------------------------------------------------------------------


def test_frases_terminam_na_pontuacao_e_guardam_os_tempos():
    palavras = sermao_falso(frases=4)
    frases = separar_frases(palavras)
    assert len(frases) == 4
    assert frases[0].primeira == 0 and frases[0].ultima == 11
    assert frases[1].primeira == 12
    assert frases[0].inicio == palavras[0].inicio
    assert frases[0].fim == palavras[11].fim
    assert frases[3].pergunta  # a frase 3 termina em "?"
    assert not frases[0].pergunta


def test_frase_que_abre_com_conectivo_depende_do_que_veio_antes():
    frases = separar_frases(sermao_falso(frases=8))
    assert frases[1].abre_dependente  # "Mas ..."
    assert not frases[2].abre_dependente


def test_sem_palavras_nao_ha_frases_nem_candidatos():
    assert separar_frases([]) == []
    assert gerar_candidatos([], []) == []


def test_frases_com_o_spacy_de_verdade():
    spacy = pytest.importorskip("spacy")
    try:
        nlp = spacy.load("pt_core_news_md", exclude=["ner", "lemmatizer"])
    except OSError:
        pytest.skip("o modelo pt_core_news_md do spaCy não está instalado")
    palavras = sermao_falso(frases=6)
    frases = separar_frases(palavras, nlp)
    assert len(frases) >= 6  # nunca junta frases que a pontuação já separou
    assert frases[0].primeira == 0 and frases[-1].ultima == len(palavras) - 1


# --- candidatos ----------------------------------------------------------------------------


def test_candidatos_tem_frases_inteiras_e_duracao_dentro_dos_limites():
    palavras = sermao_falso()
    frases = separar_frases(palavras)
    parametros = ParametrosJanela(minimo_s=30, maximo_s=60)
    candidatos = gerar_candidatos(palavras, frases, parametros)
    assert candidatos
    for c in candidatos:
        assert 30 <= c.duracao <= 60 - FOLGA_INICIO_S - FOLGA_FIM_S
        assert c.inicio == frases[c.primeira_frase].inicio
        assert c.fim == frases[c.ultima_frase].fim
        assert c.sinais["fecho_completo"]
    assert [c.id for c in candidatos] == list(range(len(candidatos)))


def test_janela_com_pausa_longa_no_meio_e_descartada():
    palavras = sermao_falso(frases=14)
    # abre um buraco de 5 s no meio do sermão
    corte = 12 * 7
    for p in palavras[corte:]:
        p.inicio += 5.0
        p.fim += 5.0
    frases = separar_frases(palavras)
    candidatos = gerar_candidatos(palavras, frases, ParametrosJanela(minimo_s=30, maximo_s=60))
    fim_antes = palavras[corte - 1].fim
    inicio_depois = palavras[corte].inicio
    assert inicio_depois - fim_antes > PAUSA_INTERNA_MAXIMA_S
    for c in candidatos:
        assert not (c.inicio < fim_antes and c.fim > inicio_depois)


def test_candidato_vai_e_volta_do_dicionario():
    palavras = sermao_falso()
    c = gerar_candidatos(palavras, separar_frases(palavras))[0]
    assert Candidato.de_dict(json.loads(json.dumps(c.para_dict()))) == c


# --- pontuação objetiva --------------------------------------------------------------------


def test_pontos_de_duracao():
    assert pontos_de_duracao(55) == 10
    assert pontos_de_duracao(45) == 8
    assert pontos_de_duracao(32) == 5
    assert pontos_de_duracao(20) == 0


def sinais(**mudancas):
    base = {
        "abertura_dependente": False,
        "fecho_completo": True,
        "pausa_antes_s": 0.6,
        "pausa_depois_s": 0.6,
        "pergunta_no_inicio": False,
        "tem_pergunta": False,
        "tem_citacao": False,
        "retomada": False,
        "fecho_conclusivo": False,
        "palavras_por_segundo": 2.8,
        "pausa_interna_max_s": 0.8,
    }
    return {**base, **mudancas}


def test_pontos_de_bordas_abertura_gancho_e_ritmo():
    assert pontos_de_bordas(sinais()) == 15
    assert pontos_de_bordas(sinais(fecho_completo=False, pausa_antes_s=0.0)) == 3.5
    assert pontos_de_abertura(sinais()) == 8
    assert pontos_de_abertura(sinais(abertura_dependente=True)) == 0
    assert pontos_de_abertura(sinais(retomada=True)) == 0
    assert pontos_de_gancho(sinais()) == 0
    assert pontos_de_gancho(sinais(pergunta_no_inicio=True, tem_citacao=True)) == 9
    assert (
        pontos_de_gancho(sinais(pergunta_no_inicio=True, tem_citacao=True, fecho_conclusivo=True))
        == 10
    )
    assert pontos_de_ritmo(sinais()) == 5
    assert pontos_de_ritmo(sinais(palavras_por_segundo=1.0, pausa_interna_max_s=2.5)) == 0


def candidato(id_=0, inicio=0.0, fim=50.0, **mudancas) -> Candidato:
    c = Candidato(
        id=id_,
        primeira_frase=0,
        ultima_frase=1,
        inicio=inicio,
        fim=fim,
        texto=f"texto {id_}",
        pre_nota=0.0,
        sinais=sinais(**mudancas),
    )
    c.pre_nota = pre_nota(c)
    return c


def test_pre_nota_fica_entre_0_e_100_e_premia_bordas_e_abertura():
    boa = candidato()
    ruim = candidato(abertura_dependente=True, fecho_completo=False, pausa_depois_s=0.0)
    assert 0 <= ruim.pre_nota < boa.pre_nota <= 100


def test_sobreposicao_e_a_fracao_do_menor():
    a = candidato(0, 0, 50)
    b = candidato(1, 40, 90)
    assert sobreposicao(a, b) == pytest.approx(10 / 50)
    assert sobreposicao(a, candidato(2, 60, 100)) == 0


def test_escolher_sem_sobreposicao_respeita_o_limite_e_a_quantidade():
    cs = [candidato(i, i * 10.0, i * 10.0 + 50) for i in range(10)]
    escolhidos = escolher_sem_sobreposicao(cs, quantos=3, sobreposicao_maxima=0.2)
    assert len(escolhidos) == 3
    for i, a in enumerate(escolhidos):
        for b in escolhidos[i + 1 :]:
            assert sobreposicao(a, b) <= 0.2


# --- nota final ----------------------------------------------------------------------------


def avaliacao(**mudancas) -> Avaliacao:
    base = {"categoria": "aplicacao_pratica", "nota": 8, "autossuficiente": True}
    return Avaliacao(**{**base, **mudancas})


def test_nota_soma_as_partes_e_nunca_passa_de_100():
    nota, detalhe = calcular_nota(
        candidato(pergunta_no_inicio=True, tem_citacao=True, fecho_conclusivo=True),
        avaliacao(nota=10),
    )
    assert nota == sum(detalhe.values())
    assert nota == 100  # 40 + 20 + 15 + 10 + 10 + 5
    assert detalhe["retencao"] == 40
    assert detalhe["autossuficiencia"] == 20


def test_alerta_de_contexto_desconta_dez_pontos():
    c = candidato()
    sem, _ = calcular_nota(c, avaliacao())
    com, detalhe = calcular_nota(c, avaliacao(alerta_contexto="pode parecer arminiano"))
    assert com == sem - 10
    assert detalhe["alerta_de_contexto"] == -10


def test_trecho_que_nao_se_sustenta_perde_os_pontos_de_autossuficiencia():
    c = candidato()
    a, _ = calcular_nota(c, avaliacao(autossuficiente=True))
    b, detalhe = calcular_nota(c, avaliacao(autossuficiente=False))
    assert a - b == 12
    assert detalhe["autossuficiencia"] == 8


def test_descrever_detalhe_lista_todas_as_partes():
    _, detalhe = calcular_nota(candidato(), avaliacao(alerta_contexto="risco"))
    linhas = descrever_detalhe(detalhe)
    assert linhas[0].startswith("Potencial de retenção (IA): 32 de 40")
    assert len(linhas) == 7
    assert linhas[-1].startswith("Alerta de contexto")


# --- seleção -------------------------------------------------------------------------------


def lote(n: int, avaliacoes_boas: bool = True):
    cs = [candidato(i, i * 60.0, i * 60.0 + 50) for i in range(n)]
    avs = {c.id: avaliacao(nota=10 - (c.id % 5) if avaliacoes_boas else 1) for c in cs}
    return cs, avs


def test_montar_cortes_respeita_o_limite_e_poe_o_resto_como_reserva():
    cs, avs = lote(15)
    cortes = montar_cortes(cs, avs, limite=10, nota_minima=0, reservas=3)
    assert len(cortes) == 13
    assert sum(not c.reserva for c in cortes) == 10
    assert all(c.incluir for c in cortes if not c.reserva)
    assert all(not c.incluir for c in cortes if c.reserva)
    notas = [c.nota for c in cortes]
    assert notas == sorted(notas, reverse=True)


def test_nota_abaixo_do_minimo_vira_reserva():
    cs, avs = lote(6, avaliacoes_boas=False)
    cortes = montar_cortes(cs, avs, limite=20, nota_minima=90, reservas=8)
    assert cortes and all(c.reserva and not c.incluir for c in cortes)


def test_candidato_sem_avaliacao_nao_vira_corte():
    cs, avs = lote(4)
    del avs[2]
    assert 2 not in {c.id for c in montar_cortes(cs, avs, limite=20, nota_minima=0)}


def test_cortes_nao_se_sobrepoem_mais_que_o_limite():
    a, b = candidato(0, 0, 50), candidato(1, 20, 70)
    avs = {0: avaliacao(nota=9), 1: avaliacao(nota=8)}
    assert [c.id for c in montar_cortes([a, b], avs, limite=20, nota_minima=0)] == [0]


def test_respiro_nas_pontas_nunca_passa_de_metade_da_pausa():
    c = candidato(inicio=10.0, fim=60.0, pausa_antes_s=0.2, pausa_depois_s=2.0)
    inicio, fim = com_respiro(c)
    assert inicio == pytest.approx(10.0 - 0.1)
    assert fim == pytest.approx(60.0 + FOLGA_FIM_S)
    assert com_respiro(candidato(inicio=0.05, pausa_antes_s=5.0))[0] == 0.0


def test_corte_dura_menos_de_60s_mesmo_com_respiro():
    c = candidato(inicio=10.0, fim=69.45, pausa_antes_s=5.0, pausa_depois_s=5.0)
    inicio, fim = com_respiro(c)
    assert fim - inicio <= 60.0


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Por que o povo não quer ouvir a verdade?", "por_que_o_povo_nao_quer_ouvir_a_verdade"),
        ("  Ação & Graça!!  ", "acao_graca"),
        ("???", "trecho"),
    ],
)
def test_nome_de_arquivo(texto, esperado):
    assert nome_de_arquivo(texto) == esperado


def test_nome_de_arquivo_nao_repete_nem_estoura_o_tamanho():
    assert nome_de_arquivo("graça", {"graca"}) == "graca_2"
    assert nome_de_arquivo("graça", {"graca", "graca_2"}) == "graca_3"
    longo = nome_de_arquivo("a" * 100, maximo=40)
    assert len(longo) == 40
    assert len(nome_de_arquivo("a" * 100, {longo}, maximo=40)) <= 40


# --- cliente do Ollama ---------------------------------------------------------------------


class OllamaFalso:
    """Imita o `ollama.Client`: respostas prontas, em ordem, e registro das chamadas."""

    def __init__(self, respostas=(), instalados=("qwen3:8b",)):
        self.respostas = list(respostas)
        self.chamadas: list[dict] = []
        self.instalados = list(instalados)
        self.baixados: list[str] = []
        self.titulos: list[str] = []  # títulos a devolver, em ordem, nos pedidos de metadados

    def chat(self, **kwargs):
        self.chamadas.append(kwargs)
        resposta = self.respostas.pop(0) if self.respostas else self.resposta_padrao(kwargs)
        if isinstance(resposta, Exception):
            raise resposta
        conteudo = resposta if isinstance(resposta, str) else json.dumps(resposta)
        return SimpleNamespace(message=SimpleNamespace(content=conteudo))

    def resposta_padrao(self, kwargs):
        if "slug" in json.dumps(kwargs["format"]):
            return {
                "titulo": self.titulos.pop(0)
                if self.titulos
                else "Por que o povo rejeita a verdade?",
                "descricao": "Um trecho sobre a rejeição da verdade.",
                "hashtags": ["#shorts", "fé", "#Miquéias"],
                "slug": "povo-rejeita-verdade",
            }
        return {
            "categoria": "aplicacao_pratica",
            "nota": 7,
            "autossuficiente": True,
            "alerta_contexto": "",
            "motivo": "Boa aplicação.",
        }

    def list(self):
        return SimpleNamespace(models=[SimpleNamespace(model=m) for m in self.instalados])

    def pull(self, modelo, stream=True):
        self.baixados.append(modelo)
        yield SimpleNamespace(total=100, completed=50)
        yield SimpleNamespace(total=100, completed=100)
        self.instalados.append(modelo)


def cliente(falso: OllamaFalso, **kwargs) -> ClienteLLM:
    return ClienteLLM("qwen3:8b", cliente=falso, **kwargs)


def test_avaliar_devolve_a_avaliacao_validada():
    falso = OllamaFalso(
        [
            {
                "categoria": "conclusao",
                "nota": 9,
                "autossuficiente": False,
                "alerta_contexto": " pode soar legalista ",
                "motivo": "Fecha bem.",
            }
        ]
    )
    av = cliente(falso).avaliar("antes", "o trecho", "depois", 45.0)
    assert av == Avaliacao("conclusao", 9, False, "pode soar legalista", "Fecha bem.")
    pergunta = falso.chamadas[0]
    assert "o trecho" in pergunta["messages"][1]["content"]
    assert pergunta["think"] is False
    assert pergunta["format"]["type"] == "object"


def test_sem_gpu_utilizavel_o_ollama_recebe_zero_camadas_na_gpu():
    falso = OllamaFalso()
    cliente(falso, threads=9).avaliar("", "x", "", 40)
    opcoes = falso.chamadas[0]["options"]
    assert opcoes["num_gpu"] == 0
    assert opcoes["num_thread"] == 9
    cliente(falso, usar_gpu=True).avaliar("", "x", "", 40)
    assert "num_gpu" not in falso.chamadas[1]["options"]


def test_resposta_invalida_tenta_de_novo_com_mais_temperatura():
    falso = OllamaFalso(
        ["isto não é json", {"categoria": "outra", "nota": 99, "autossuficiente": 1}]
    )
    av = cliente(falso).avaliar("", "x", "", 40)  # a 3ª resposta é a padrão (válida)
    assert av.nota == 7
    temperaturas = [c["options"]["temperature"] for c in falso.chamadas]
    assert temperaturas == sorted(temperaturas) and temperaturas[0] < temperaturas[-1]


def test_tres_respostas_invalidas_levantam_erro_da_ia():
    falso = OllamaFalso(["lixo", "lixo", "lixo"])
    with pytest.raises(ErroLLM):
        cliente(falso).avaliar("", "x", "", 40)
    assert len(falso.chamadas) == 3


def test_falha_de_conexao_vira_erro_de_ferramenta_sem_tentar_de_novo():
    falso = OllamaFalso([ConnectionError("recusada")])
    with pytest.raises(ErroFerramenta) as erro:
        cliente(falso).avaliar("", "x", "", 40)
    assert not isinstance(erro.value, ErroLLM)
    assert len(falso.chamadas) == 1


def test_metadados_normalizam_as_hashtags():
    meta = cliente(OllamaFalso()).metadados("Miquéias 2", "texto", "outra", 40)
    assert isinstance(meta, MetadadosLLM)
    assert meta.hashtags == ["#shorts", "#fé", "#Miquéias"]
    assert meta.slug == "povo-rejeita-verdade"


def test_modelo_instalado_e_download_com_progresso():
    falso = OllamaFalso(instalados=["llama3.1:8b"])
    c = cliente(falso)
    assert not c.modelo_instalado()
    progresso: list[float] = []
    c.baixar_modelo(progresso.append)
    assert progresso == [0.5, 1.0]
    assert falso.baixados == ["qwen3:8b"] and c.modelo_instalado()
    assert cliente(OllamaFalso(instalados=["qwen3:8b:latest"])).modelo_instalado()


def test_ollama_fora_do_ar_orienta_o_usuario():
    class Quebrado:
        def list(self):
            raise ConnectionError("sem conexão")

    with pytest.raises(ErroFerramenta, match="Abra o aplicativo Ollama"):
        cliente(Quebrado()).modelo_instalado()


# --- etapas --------------------------------------------------------------------------------


@pytest.fixture
def ollama(monkeypatch):
    falso = OllamaFalso()
    monkeypatch.setattr(etapas_mod, "criar_cliente_llm", lambda ctx: cliente(falso))
    monkeypatch.setattr(etapas_mod, "carregar_spacy", lambda: None)
    return falso


@pytest.fixture
def ctx_shorts(contexto):
    contexto.opcoes.gerar_shorts = True
    contexto.opcoes.shorts_candidatos_ia = 6
    gravar_transcricao(contexto.pastas.transcricao, sermao_falso(frases=150))
    return contexto


def rodar(etapa, ctx):
    ctx.etapa_id = etapa.id
    return etapa.executar(ctx)


def test_so_roda_com_shorts_ligados(contexto):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts(), AprovarCortes()):
        assert not etapa.ativa(contexto.opcoes)
    contexto.opcoes.gerar_shorts = True
    assert all(
        e.ativa(contexto.opcoes)
        for e in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts(), AprovarCortes())
    )
    contexto.opcoes.pausar_aprovar_cortes = False
    assert not AprovarCortes().ativa(contexto.opcoes)


def test_analisar_grava_frases_e_os_melhores_candidatos(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    dados = ler_json(ctx_shorts.pastas.candidatos)
    assert len(dados["frases"]) == 150
    assert dados["total_gerados"] > len(dados["candidatos"]) == 6
    assert AnalisarShorts().artefatos(ctx_shorts) == [ctx_shorts.pastas.candidatos]


def test_avaliar_guarda_cada_avaliacao_e_retoma_do_ponto_onde_parou(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    assert len(ollama.chamadas) == 6
    gravado = ler_json(ctx_shorts.pastas.avaliacoes)
    assert gravado["modelo"] == "qwen3:8b" and len(gravado["avaliacoes"]) == 6

    # simula uma queda no meio: só 2 avaliações guardadas
    gravado["avaliacoes"] = dict(list(gravado["avaliacoes"].items())[:2])
    escrever_json_atomico(ctx_shorts.pastas.avaliacoes, gravado)
    ollama.chamadas.clear()
    rodar(AvaliarShorts(), ctx_shorts)
    assert len(ollama.chamadas) == 4
    assert len(ler_json(ctx_shorts.pastas.avaliacoes)["avaliacoes"]) == 6


def test_avaliar_ignora_um_trecho_que_a_ia_nao_consegue_julgar(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    ollama.respostas = ["lixo", "lixo", "lixo"]  # o primeiro trecho falha as 3 tentativas
    rodar(AvaliarShorts(), ctx_shorts)
    assert len(ler_json(ctx_shorts.pastas.avaliacoes)["avaliacoes"]) == 5


def test_avaliar_falha_se_a_ia_nao_avalia_nenhum_trecho(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    ollama.respostas = ["lixo"] * 30
    with pytest.raises(ErroLLM):
        rodar(AvaliarShorts(), ctx_shorts)


def test_avaliar_com_outro_modelo_recomeca(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    ollama.chamadas.clear()
    gravado = ler_json(ctx_shorts.pastas.avaliacoes)
    gravado["modelo"] = "outro-modelo"
    escrever_json_atomico(ctx_shorts.pastas.avaliacoes, gravado)
    rodar(AvaliarShorts(), ctx_shorts)
    assert len(ollama.chamadas) == 6


def test_selecionar_cria_cortes_com_titulo_slug_e_nota(ctx_shorts, ollama):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert cortes
    for c in cortes:
        assert c.titulo and c.descricao and c.hashtags[0] == "#shorts"
        assert 0 <= c.nota <= 100 and c.detalhe
        assert 30 <= c.duracao <= 60
    slugs = [c.slug for c in cortes]
    assert len(set(slugs)) == len(slugs)  # nunca dois arquivos com o mesmo nome
    assert slugs[0] == "povo_rejeita_verdade"


def test_selecionar_de_novo_reaproveita_os_titulos_ja_editados(ctx_shorts, ollama):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    cortes[0].titulo = "Meu título editado"
    etapas_mod.gravar_cortes(ctx_shorts.pastas.cortes_shorts, cortes)
    ollama.chamadas.clear()
    rodar(SelecionarShorts(), ctx_shorts)
    assert ollama.chamadas == []  # não pediu nada à IA de novo
    assert carregar_cortes(ctx_shorts.pastas.cortes_shorts)[0].titulo == "Meu título editado"


def test_aprovar_cortes_pausa_para_revisao(ctx_shorts):
    assert rodar(AprovarCortes(), ctx_shorts) == ResultadoEtapa.AGUARDANDO_REVISAO


def test_corte_vai_e_volta_do_json():
    c = Corte(
        id=3, inicio=1.0, fim=40.0, texto="t", categoria="outra", nota=71.5,
        detalhe={"retencao": 32.0}, hashtags=["#shorts"], slug="x",
    )  # fmt: skip
    assert Corte.de_dict(json.loads(json.dumps(c.para_dict()))) == c


# --- ajustes feitos na tela de aprovação ---------------------------------------------------


def cortes_do_sermao(palavras: list[Palavra]) -> list[Corte]:
    frases = separar_frases(palavras)
    candidatos = escolher_sem_sobreposicao(gerar_candidatos(palavras, frases), 4)
    avs = {c.id: avaliacao(nota=6 + c.id % 4) for c in candidatos}
    return montar_cortes(candidatos, avs, limite=3, nota_minima=0, reservas=1)


def test_mover_o_inicio_uma_palavra_para_tras_inclui_a_palavra_anterior():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    novo = mover_limite(palavras, corte, "inicio", -1)
    assert indices_das_palavras(palavras, novo) == (primeira - 1, ultima)
    assert novo.editado and not corte.editado
    assert novo.nota == corte.nota  # a nota continua a do corte original
    assert novo.texto.startswith(palavras[primeira - 1].texto)
    assert novo.inicio < corte.inicio


def test_mover_o_fim_para_dentro_encurta_o_corte_numa_palavra_inteira():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    novo = mover_limite(palavras, corte, "fim", -3)
    assert indices_das_palavras(palavras, novo) == (primeira, ultima - 3)
    assert novo.duracao < corte.duracao
    assert novo.texto.endswith(palavras[ultima - 3].texto)


def test_nao_deixa_passar_de_60_segundos():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    atual = corte
    with pytest.raises(LimiteInvalido, match="passaria de 60"):
        for _ in range(200):
            atual = mover_limite(palavras, atual, "fim", 1)
    assert atual.duracao <= 60.0 + 1e-6


def test_nao_deixa_ficar_curto_demais_nem_sair_da_transcricao():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    sobram_20_palavras = -(ultima - primeira - 19)  # ≈ 7 s de fala
    with pytest.raises(LimiteInvalido, match="menos de 10"):
        mover_limite(palavras, corte, "fim", sobram_20_palavras)
    primeiro = replace(corte, inicio=palavras[0].inicio, fim=palavras[0].inicio + 30)
    with pytest.raises(LimiteInvalido, match="Não há mais fala"):
        mover_limite(palavras, primeiro, "inicio", -1)
    fora = replace(corte, inicio=9999.0, fim=10030.0)
    with pytest.raises(LimiteInvalido, match="Não há fala"):
        mover_limite(palavras, fora, "fim", 1)


def test_palavras_das_pontas_mostram_o_comeco_e_o_fim_do_corte():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    comeco, fim = palavras_das_pontas(palavras, corte, quantas=3)
    assert comeco.split() == [p.texto for p in palavras[primeira : primeira + 3]]
    assert fim.split() == [p.texto for p in palavras[ultima - 2 : ultima + 1]]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("fé, #Miquéias  shorts", ["#fé", "#Miquéias", "#shorts"]),
        ("#a #a ##b", ["#a", "#b"]),
        ("  ", []),
    ],
)
def test_ler_hashtags(texto, esperado):
    assert ler_hashtags(texto) == esperado


def test_faixas_da_nota_e_resumo_dos_cortes():
    assert [faixa_da_nota(n) for n in (90, 70, 69.9, 50, 49.9, 0)] == [
        "alta", "alta", "media", "media", "baixa", "baixa",
    ]  # fmt: skip
    cortes = cortes_do_sermao(sermao_falso())
    cortes[0].incluir = False
    for c in cortes[1:]:
        c.titulo = "Um título"
    cortes[1].titulo = " "
    r = resumir(cortes)
    assert r.total == len(cortes) and r.marcados == len(cortes) - 1
    assert r.sem_titulo == 1
    assert r.duracao_s == pytest.approx(sum(c.duracao for c in cortes[1:]))


def test_o_pedido_de_titulo_lista_os_ja_criados_e_nao_traz_exemplo_para_copiar(ctx_shorts, ollama):
    ollama.titulos = [f"Título número {i}" for i in range(100)]
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    pedidos = [
        c["messages"][1]["content"] for c in ollama.chamadas if "slug" in json.dumps(c["format"])
    ]
    assert "Títulos já criados" not in pedidos[0]
    assert "Título número 0" in pedidos[1] and "Títulos já criados" in pedidos[1]
    assert "povo não quer ouvir" not in pedidos[0]  # o exemplo antigo era copiado pela IA


def test_titulo_repetido_pede_outro_com_mais_temperatura(ctx_shorts, ollama):
    ollama.titulos = [f"Título número {i // 2}" for i in range(200)]  # cada um vem duas vezes
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    titulos = [c.titulo for c in carregar_cortes(ctx_shorts.pastas.cortes_shorts)]
    assert len(titulos) > 1 and len(set(titulos)) == len(titulos)
    temperaturas = [
        c["options"]["temperature"] for c in ollama.chamadas if "slug" in json.dumps(c["format"])
    ]
    assert max(temperaturas) >= 0.5


def test_titulos_ja_criados_sobrevivem_a_uma_queda_no_meio(ctx_shorts, ollama):
    ctx_shorts.opcoes.shorts_candidatos_ia = 14  # para sobrarem 4 ou mais cortes distintos
    ollama.titulos = [f"Título número {i}" for i in range(100)]
    for etapa in (AnalisarShorts(), AvaliarShorts()):
        rodar(etapa, ctx_shorts)
    pedidos = []
    original = ollama.chat

    def cai_no_quarto_pedido(**kwargs):
        if "slug" in json.dumps(kwargs["format"]):
            pedidos.append(1)
            if len(pedidos) == 4:
                raise ConnectionError("o Ollama caiu")
        return original(**kwargs)

    ollama.chat = cai_no_quarto_pedido
    with pytest.raises(ErroFerramenta):
        rodar(SelecionarShorts(), ctx_shorts)
    assert len(ler_json(ctx_shorts.pastas.metadados)) == 3  # os 3 primeiros ficaram guardados

    ollama.chat = original
    ollama.chamadas.clear()
    rodar(SelecionarShorts(), ctx_shorts)
    refeitos = [c for c in ollama.chamadas if "slug" in json.dumps(c["format"])]
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert len(refeitos) == len(cortes) - 3
    assert [c.titulo for c in cortes][:3] == [f"Título número {i}" for i in range(3)]


def test_titulo_guardado_nao_vale_se_o_texto_do_corte_mudou(ctx_shorts, ollama):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cache = ler_json(ctx_shorts.pastas.metadados)
    for entrada in cache.values():
        entrada["marca"] = "outra-transcricao"
    escrever_json_atomico(ctx_shorts.pastas.metadados, cache)
    ctx_shorts.pastas.cortes_shorts.unlink()
    ollama.chamadas.clear()
    rodar(SelecionarShorts(), ctx_shorts)
    assert any("slug" in json.dumps(c["format"]) for c in ollama.chamadas)
