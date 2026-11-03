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
    AVISO_ACIMA_DO_LIMITE,
    LimiteInvalido,
    contexto_das_pontas,
    faixa_da_nota,
    indices_das_palavras,
    ler_hashtags,
    mover_limite,
    mover_por_frase,
    palavras_das_pontas,
    passa_do_limite,
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


#: títulos de verdade, todos bem diferentes entre si: nenhum é igual, parecido demais ou genérico
#: para as regras de `shorts/titulos.py`. O Ollama falso devolve um a um, em ordem.
TITULOS_DISTINTOS = [
    "Acaz queimou os próprios filhos no fogo",
    "Quando o profeta incomoda os poderosos",
    "A tempestade que Deus anunciou a Samaria",
    "Ninguém escapa do tribunal de Cristo",
    "O orgulho que derruba reis e nações",
    "Pastores que enganam as ovelhas por dinheiro",
    "A promessa de Belém e o menino rei",
    "Por que a idolatria atrai tanta gente?",
    "Lutero diante da dieta de Worms",
    "Calvino e a soberania sobre cada detalhe",
    "O silêncio de Deus diante da injustiça",
    "Arrependimento verdadeiro muda o rumo da vida",
    "Como orar quando a fé está cansada",
    "Esperança para quem perdeu tudo",
    "Corrupção começa no coração de cada um",
    "A graça que alcança até o pior inimigo",
    "Jonas fugiu mas o Senhor foi atrás",
    "Sábado, descanso e o trabalho redimido",
    "O cálice amargo que Jesus bebeu sozinho",
    "Eleição não é desculpa para a preguiça",
    "Os mártires escoceses e a liberdade de consciência",
    "Davi chorou o pecado e Deus perdoou",
    "A ressurreição muda o que fazemos hoje",
    "Casamento é aliança, não contrato",
    "A ceia aponta para uma festa futura",
    "O batismo e a marca do povo de Deus",
    "Pregar sem medo numa cidade hostil",
    "Um reino que não se parece com Roma",
    "Gratidão no meio da doença prolongada",
    "Os dez mandamentos e a vida em família",
    "O cordeiro que tira o pecado do mundo",
    "Josias redescobre o livro esquecido",
]


class OllamaFalso:
    """Imita o `ollama.Client`: respostas prontas, em ordem, e registro das chamadas."""

    def __init__(self, respostas=(), instalados=("qwen3:8b",)):
        self.respostas = list(respostas)
        self.chamadas: list[dict] = []
        self.instalados = list(instalados)
        self.baixados: list[str] = []
        self.titulos: list[str] = []  # títulos a devolver, em ordem, nos pedidos de título
        self._sorteados = 0  # quantos títulos da lista `TITULOS_DISTINTOS` já saíram

    def chat(self, **kwargs):
        self.chamadas.append(kwargs)
        resposta = self.respostas.pop(0) if self.respostas else self.resposta_padrao(kwargs)
        if isinstance(resposta, Exception):
            raise resposta
        conteudo = resposta if isinstance(resposta, str) else json.dumps(resposta)
        return SimpleNamespace(message=SimpleNamespace(content=conteudo))

    def proximo_titulo(self) -> str:
        if self.titulos:
            return self.titulos.pop(0)
        self._sorteados += 1
        return TITULOS_DISTINTOS[(self._sorteados - 1) % len(TITULOS_DISTINTOS)]

    def resposta_padrao(self, kwargs):
        esquema = json.dumps(kwargs["format"])
        if "slug" in esquema:
            return {
                "titulo": self.proximo_titulo(),
                "descricao": "Um trecho sobre a rejeição da verdade.",
                "hashtags": ["#shorts", "fé", "#Miquéias"],
                "slug": "povo-rejeita-verdade",
            }
        if '"titulo"' in esquema:  # o pedido só do título (refazer ou trocar um recusado)
            return {"titulo": self.proximo_titulo()}
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
        assert 30 <= c.duracao <= 180
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


def test_deixa_passar_do_limite_mas_o_corte_deixa_de_poder_ser_short():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    assert not passa_do_limite(corte)
    atual = corte
    for _ in range(500):  # o corte cresce palavra a palavra, sem erro, e passa dos 180 s
        atual = mover_limite(palavras, atual, "fim", 1)
    assert atual.duracao > 180.0
    assert passa_do_limite(atual)
    assert "180" in AVISO_ACIMA_DO_LIMITE


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
    ollama.titulos = list(TITULOS_DISTINTOS)
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    pedidos = [
        c["messages"][1]["content"] for c in ollama.chamadas if "slug" in json.dumps(c["format"])
    ]
    assert "Títulos já criados" not in pedidos[0]
    assert TITULOS_DISTINTOS[0] in pedidos[1] and "Títulos já criados" in pedidos[1]
    assert "povo não quer ouvir" not in pedidos[0]  # o exemplo antigo era copiado pela IA


def test_titulo_repetido_pede_outro_com_mais_temperatura(ctx_shorts, ollama):
    ollama.titulos = [TITULOS_DISTINTOS[i // 2] for i in range(60)]  # cada um vem duas vezes
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    titulos = [c.titulo for c in carregar_cortes(ctx_shorts.pastas.cortes_shorts)]
    assert len(titulos) > 1 and len(set(titulos)) == len(titulos)
    temperaturas = [
        c["options"]["temperature"]
        for c in ollama.chamadas
        if '"titulo"' in json.dumps(c["format"])
    ]
    assert max(temperaturas) >= 0.5


def test_titulo_igual_ou_parecido_pede_outro_dizendo_o_motivo(ctx_shorts, ollama):
    # a IA insiste no mesmo título e depois num quase igual; só então traz um diferente
    ollama.titulos = [
        TITULOS_DISTINTOS[0],  # 1º corte
        TITULOS_DISTINTOS[0],  # 2º corte, no pedido completo: igual
        "Acaz queimou os filhos no fogo",  # a nova tentativa: parecido
        TITULOS_DISTINTOS[1],  # a seguinte serve
    ]
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    titulos = [c.titulo for c in cortes]
    assert titulos[:2] == [TITULOS_DISTINTOS[0], TITULOS_DISTINTOS[1]]
    assert len(set(titulos)) == len(titulos)
    assert cortes[1].slug.startswith("quando_o_profeta")  # o arquivo acompanha o título que ficou
    so_titulo = [
        c["messages"][1]["content"]
        for c in ollama.chamadas
        if '"titulo"' in json.dumps(c["format"]) and "slug" not in json.dumps(c["format"])
    ]
    assert "já foram recusados" not in so_titulo[0].split("TRECHO")[0]
    assert "é igual ao título" in so_titulo[0]  # a IA é avisada do porquê
    assert "é parecido demais" in so_titulo[1] and "Acaz queimou os filhos no fogo" in so_titulo[1]


def test_titulo_generico_tambem_e_recusado_e_o_log_conta(ctx_shorts, ollama, caplog):
    caplog.set_level("INFO")
    ollama.titulos = ["Deus punirá o pecado", TITULOS_DISTINTOS[2]]
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert cortes[0].titulo == TITULOS_DISTINTOS[2]
    assert "Título recusado (generico): “Deus punirá o pecado”" in caplog.text


def test_selecionar_de_novo_troca_o_titulo_repetido_que_a_ia_criou(ctx_shorts, ollama):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert len(cortes) >= 3
    # a IA de uma versão antiga repetiu o título do 1º corte no 2º e no 3º; a pessoa editou o 3º
    cache = ler_json(ctx_shorts.pastas.metadados)
    repetido = cortes[0].titulo
    cache[str(cortes[1].id)]["titulo"] = repetido
    cortes[1].titulo = repetido
    cortes[2].titulo = repetido  # só nos cortes: o do cache continua o da IA, então foi edição
    escrever_json_atomico(ctx_shorts.pastas.metadados, cache)
    etapas_mod.gravar_cortes(ctx_shorts.pastas.cortes_shorts, cortes)
    ollama.chamadas.clear()
    rodar(SelecionarShorts(), ctx_shorts)
    refeitos = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert refeitos[0].titulo == repetido
    assert refeitos[1].titulo != repetido  # o repetido da IA ganhou outro
    assert refeitos[2].titulo == repetido  # o que a pessoa escreveu fica como está


def test_refazer_titulo_nao_volta_igual_nem_parecido_com_o_de_outro_short():
    falso = OllamaFalso()
    falso.titulos = ["Acaz queimou os filhos no fogo", TITULOS_DISTINTOS[1]]
    novo = cliente(falso).refazer(
        "titulo", "Sermão", "o texto do trecho", "outra", 40,
        titulos_usados=["Acaz queimou os próprios filhos"],
    )  # fmt: skip
    assert novo == TITULOS_DISTINTOS[1]
    assert len(falso.chamadas) == 2
    segundo = falso.chamadas[1]["messages"][1]["content"]
    assert "já foram recusados" in segundo and "Acaz queimou os filhos no fogo" in segundo
    assert "Títulos já criados" in segundo and "Acaz queimou os próprios filhos" in segundo


def test_os_pedidos_de_titulo_pedem_especifico_e_nao_trazem_exemplo_para_copiar():
    falso = OllamaFalso()
    c = cliente(falso)
    c.metadados("Sermão", "texto", "outra", 40)
    c.refazer("titulo", "Sermão", "texto", "outra", 40)
    for chamada in falso.chamadas:
        pedido = chamada["messages"][1]["content"]
        assert "ESPECÍFICO" in pedido and "genérico" in pedido and "Varie a forma" in pedido
        assert not any(titulo in pedido for titulo in TITULOS_DISTINTOS)


def test_selecionar_de_novo_nao_perde_o_que_ja_foi_postado(ctx_shorts, ollama):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    cortes[1].postado, cortes[1].postado_em = True, "2026-11-02"
    etapas_mod.gravar_cortes(ctx_shorts.pastas.cortes_shorts, cortes)
    rodar(SelecionarShorts(), ctx_shorts)
    refeitos = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert [c.postado for c in refeitos] == [i == 1 for i in range(len(refeitos))]
    assert refeitos[1].postado_em == "2026-11-02"


def test_titulos_ja_criados_sobrevivem_a_uma_queda_no_meio(ctx_shorts, ollama):
    ctx_shorts.opcoes.shorts_candidatos_ia = 14  # para sobrarem 4 ou mais cortes distintos
    ollama.titulos = list(TITULOS_DISTINTOS)
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
    assert [c.titulo for c in cortes][:3] == TITULOS_DISTINTOS[:3]


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


def test_ao_terminar_a_selecao_o_modelo_sai_da_memoria(ctx_shorts, ollama):
    descarregados = []
    ollama.generate = lambda **kwargs: descarregados.append(kwargs)
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    assert descarregados == [{"model": "qwen3:8b", "keep_alive": 0}]


def test_falha_ao_descarregar_o_modelo_nao_derruba_nada():
    class SemGenerate:
        def generate(self, **_kwargs):
            raise ConnectionError("fora do ar")

    cliente(SemGenerate()).descarregar()  # não levanta


def test_nome_de_arquivo_corta_entre_palavras_e_nao_no_meio_de_uma():
    nome = nome_de_arquivo("Será que o que você quer ouvir é o que precisa ouvir?")
    assert nome == "sera_que_o_que_voce_quer_ouvir_e_o_que"
    assert not nome.endswith("_p") and len(nome) <= 40
    # palavra única e comprida: não há onde cortar, então corta no limite
    assert nome_de_arquivo("a" * 60, maximo=40) == "a" * 40
    # corte que cai certinho no fim de uma palavra
    assert nome_de_arquivo("um dois tres", maximo=8) == "um_dois"


def test_selecao_com_niveis_do_audio_comeca_e_termina_em_silencio(ctx_shorts, ollama):
    """Com a medição de nível, início e fim de cada corte caem em janelas silenciosas."""
    palavras = sermao_falso(frases=150)
    total = palavras[-1].fim + 2.0
    n = int(total / 0.05)
    tempos = [round(i * 0.05, 3) for i in range(n)]
    niveis = [
        -20.0 if any(p.inicio - 0.05 <= t < p.fim + 0.05 for p in palavras) else -80.0
        for t in tempos
    ]
    escrever_json_atomico(
        ctx_shorts.pastas.niveis,
        {"duracao_s": total, "piso_db": -80.0, "fala_db": -20.0, "limiar_db": -55.0,
         "tempos": tempos, "niveis": niveis},
    )  # fmt: skip
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert cortes

    def nivel_em(t: float) -> float:
        return niveis[min(int(t / 0.05), n - 1)]

    for c in cortes:
        assert nivel_em(c.inicio) < -55.0, f"o corte {c.id} começa dentro da voz"
        assert nivel_em(c.fim - 0.05) < -55.0, f"o corte {c.id} termina dentro da voz"
        assert c.duracao <= 180.0


def test_sem_a_medicao_de_nivel_a_selecao_avisa_e_usa_o_respiro_fixo(ctx_shorts, ollama, caplog):
    for etapa in (AnalisarShorts(), AvaliarShorts()):
        rodar(etapa, ctx_shorts)
    with caplog.at_level("WARNING"):
        rodar(SelecionarShorts(), ctx_shorts)
    assert "respiro fixo" in caplog.text


# --- ajuste do início e do fim por frase, e o contexto das pontas -----------------------------


def corte_entre(palavras: list[Palavra], primeira: int, ultima: int) -> Corte:
    base = cortes_do_sermao(palavras)[0]
    return replace(base, inicio=palavras[primeira].inicio, fim=palavras[ultima].fim)


def test_frase_anterior_entra_pelo_inicio_e_a_de_abertura_sai():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    mais = mover_por_frase(palavras, corte, "inicio", -1)
    assert indices_das_palavras(palavras, mais) == (primeira - 12, ultima)
    menos = mover_por_frase(palavras, corte, "inicio", +1)
    assert indices_das_palavras(palavras, menos) == (primeira + 12, ultima)


def test_no_meio_da_frase_o_ajuste_vai_ao_comeco_ou_ao_fim_dela():
    palavras = sermao_falso()
    corte = cortes_do_sermao(palavras)[0]
    primeira, ultima = indices_das_palavras(palavras, corte)
    no_meio = mover_limite(palavras, corte, "inicio", 4)  # abre 4 palavras dentro de uma frase
    assert indices_das_palavras(palavras, mover_por_frase(palavras, no_meio, "inicio", -1))[0] == (
        primeira
    )
    assert indices_das_palavras(palavras, mover_por_frase(palavras, no_meio, "inicio", +1))[0] == (
        primeira + 12
    )
    corta_fim = mover_limite(palavras, corte, "fim", -5)  # termina 5 palavras antes do ponto
    assert indices_das_palavras(palavras, mover_por_frase(palavras, corta_fim, "fim", +1))[1] == (
        ultima
    )
    assert indices_das_palavras(palavras, mover_por_frase(palavras, corta_fim, "fim", -1))[1] == (
        ultima - 12
    )


def test_ajuste_por_frase_respeita_os_limites_do_corte_e_da_transcricao():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    for _ in range(40):  # empurra o fim: passa dos 180 s sem erro
        corte = mover_por_frase(palavras, corte, "fim", +1)
    assert passa_do_limite(corte)
    with pytest.raises(LimiteInvalido, match="Não há mais fala"):
        for _ in range(100):  # até o fim da transcrição
            corte = mover_por_frase(palavras, corte, "fim", +1)
    inicial = corte_entre(palavras, 0, 40)
    with pytest.raises(LimiteInvalido, match="Não há mais fala"):
        mover_por_frase(palavras, inicial, "inicio", -1)
    with pytest.raises(ValueError, match="sentido"):
        mover_por_frase(palavras, inicial, "fim", 0)


def test_contexto_mostra_o_que_fica_de_fora_nas_duas_pontas():
    palavras = sermao_falso()
    corte = corte_entre(palavras, 24, 59)
    contexto = contexto_das_pontas(palavras, corte, fora=5, dentro=3)
    assert contexto.antes.split() == [p.texto for p in palavras[19:24]]
    assert contexto.comeco.split() == [p.texto for p in palavras[24:27]]
    assert contexto.fim.split() == [p.texto for p in palavras[57:60]]
    assert contexto.depois.split() == [p.texto for p in palavras[60:65]]


def test_avisa_de_inicio_e_fim_no_meio_da_frase():
    palavras = sermao_falso()
    contexto = contexto_das_pontas(palavras, corte_entre(palavras, 28, 55))
    assert any("começa no meio de uma frase" in a.lower() for a in contexto.avisos)
    assert any("termina no meio de uma frase" in a.lower() for a in contexto.avisos)


def test_avisa_de_abertura_dependente_e_de_fim_em_pergunta():
    palavras = sermao_falso()
    # a frase 1 abre com "Mas" e a frase 3 termina em "?"
    contexto = contexto_das_pontas(palavras, corte_entre(palavras, 12, 47))
    assert palavras[12].texto == "Mas"
    assert any("depende do que foi dito antes" in a for a in contexto.avisos)
    assert any("termina numa pergunta" in a.lower() for a in contexto.avisos)


def test_corte_bem_fechado_nao_recebe_avisos():
    palavras = sermao_falso()
    contexto = contexto_das_pontas(palavras, corte_entre(palavras, 24, 59))
    assert contexto.avisos == ()


# --- Shorts de até 180 s, com preferência por ~60 s ------------------------------------------


def test_pontos_de_duracao_favorecem_60s_mas_nao_zeram_os_longos():
    assert pontos_de_duracao(60) == pontos_de_duracao(50) == pontos_de_duracao(75) == 10.0
    assert (
        pontos_de_duracao(30) == 5.0 and pontos_de_duracao(45) == 8.0 and pontos_de_duracao(20) == 0
    )
    assert 5 < pontos_de_duracao(120) < pontos_de_duracao(90) < 10
    assert pontos_de_duracao(180) == pontos_de_duracao(200) == 2.0  # ainda vale alguma coisa
    sequencia = [pontos_de_duracao(d) for d in range(75, 181, 5)]
    assert sequencia == sorted(sequencia, reverse=True)  # só cai, sem degraus para cima


def test_o_limite_padrao_dos_candidatos_e_180s():
    assert ParametrosJanela().maximo_s == 180.0
    palavras = sermao_falso(frases=100)
    frases = separar_frases(palavras)
    candidatos = gerar_candidatos(palavras, frases)
    maior = max(c.duracao for c in candidatos)
    assert 100 < maior <= 180.0
    ate_60 = gerar_candidatos(palavras, frases, ParametrosJanela(maximo_s=60.0))
    assert max(c.duracao for c in ate_60) <= 60.0
    assert len(candidatos) > len(ate_60)


def test_cota_de_longos_leva_alguns_a_avaliacao_da_ia():
    from spurgeon.shorts.analise import DURACAO_LONGA_S, escolher_para_ia

    palavras = sermao_falso(frases=200)
    candidatos = gerar_candidatos(palavras, separar_frases(palavras))
    sem_cota = escolher_sem_sobreposicao(candidatos, 20, 0.6)
    assert not any(c.duracao >= DURACAO_LONGA_S for c in sem_cota)  # os longos ficariam de fora
    com_cota = escolher_para_ia(candidatos, 20, 0.6)
    longos = [c for c in com_cota if c.duracao >= DURACAO_LONGA_S]
    assert len(com_cota) == 20 and 4 <= len(longos) <= 6  # 25% das vagas
    # e os de ~60 s continuam sendo a maioria
    assert sum(1 for c in com_cota if 45 <= c.duracao <= 75) >= 8
    assert all(sobreposicao(a, b) <= 0.6 for i, a in enumerate(com_cota) for b in com_cota[i + 1 :])


def test_cota_de_longos_nao_trava_se_nao_ha_longos():
    from spurgeon.shorts.analise import escolher_para_ia

    palavras = sermao_falso(frases=100)
    candidatos = gerar_candidatos(palavras, separar_frases(palavras), ParametrosJanela(maximo_s=60))
    assert len(escolher_para_ia(candidatos, 10, 0.6)) == 10  # as vagas voltam para os demais


def test_analisar_inclui_candidatos_longos_e_o_limite_de_180s(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    dados = ler_json(ctx_shorts.pastas.candidatos)
    duracoes = [c["fim"] - c["inicio"] for c in dados["candidatos"]]
    assert max(duracoes) > 90 and max(duracoes) <= 180
    assert min(duracoes) >= 30


def test_analisar_so_usa_a_fala_do_trecho_escolhido_do_video(ctx_shorts, ollama):
    ctx_shorts.opcoes.video_inicio_s = 100.0
    ctx_shorts.opcoes.video_fim_s = 400.0
    rodar(AnalisarShorts(), ctx_shorts)
    dados = ler_json(ctx_shorts.pastas.candidatos)
    assert dados["candidatos"]
    assert all(c["inicio"] >= 100.0 and c["fim"] <= 400.0 for c in dados["candidatos"])
    assert all(f["inicio"] >= 100.0 and f["fim"] <= 400.0 for f in dados["frases"])
    assert "video_inicio_s" in AnalisarShorts.opcoes_relevantes


def test_palavras_da_janela_corta_as_pontas():
    from spurgeon.shorts.analise import palavras_da_janela

    palavras = sermao_falso(frases=10)
    meio = palavras_da_janela(palavras, 10.0, 30.0)
    assert meio and meio[0].inicio >= 10.0 and meio[-1].fim <= 30.0
    assert palavras_da_janela(palavras, 0.0, None) == palavras


# --- as avaliações da IA valem por trecho, não por número -----------------------------------


def test_refazer_a_analise_aproveita_as_avaliacoes_dos_mesmos_trechos(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    assert len(ollama.chamadas) == 6
    ollama.chamadas.clear()
    rodar(AnalisarShorts(), ctx_shorts)  # a mesma análise: os candidatos são os mesmos
    rodar(AvaliarShorts(), ctx_shorts)
    assert ollama.chamadas == []  # nada foi pedido de novo


def test_avaliacao_nao_vai_para_outro_candidato_quando_os_numeros_mudam(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    # outra duração máxima: os candidatos são outros (e numerados de novo)
    ctx_shorts.opcoes.shorts_duracao_max_s = 90.0
    ollama.chamadas.clear()
    rodar(AnalisarShorts(), ctx_shorts)
    _, candidatos = etapas_mod.carregar_candidatos(ctx_shorts.pastas.candidatos)
    feitas = etapas_mod._ler_avaliacoes(ctx_shorts.pastas.avaliacoes, "qwen3:8b", candidatos)
    guardadas = {etapas_mod.marca_do_candidato(c) for c in candidatos}
    for id_, _avaliacao in feitas.items():  # só vale para quem tem o mesmo trecho e texto
        assert (
            etapas_mod.marca_do_candidato(next(c for c in candidatos if c.id == id_)) in guardadas
        )
    rodar(AvaliarShorts(), ctx_shorts)
    assert len(ollama.chamadas) == len(candidatos) - len(feitas)


def test_projeto_antigo_com_avaliacoes_por_id_e_migrado_sem_perder_nada(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    _, candidatos = etapas_mod.carregar_candidatos(ctx_shorts.pastas.candidatos)
    novo = ler_json(ctx_shorts.pastas.avaliacoes)
    antigo = {  # o formato de antes da 1.2: chaves por id, sem "versao"
        "modelo": novo["modelo"],
        "avaliacoes": {
            str(c.id): novo["avaliacoes"][etapas_mod.marca_do_candidato(c)] for c in candidatos
        },
    }
    escrever_json_atomico(ctx_shorts.pastas.avaliacoes, antigo)
    # ler direto: o arquivo antigo ainda vale para os candidatos que existem
    lidas = etapas_mod._ler_avaliacoes(ctx_shorts.pastas.avaliacoes, "qwen3:8b", candidatos)
    assert set(lidas) == {c.id for c in candidatos}
    # refazer a análise migra o arquivo (por trecho) antes de trocar os candidatos
    ollama.chamadas.clear()
    rodar(AnalisarShorts(), ctx_shorts)
    migrado = ler_json(ctx_shorts.pastas.avaliacoes)
    assert migrado["versao"] == 2 and len(migrado["avaliacoes"]) == len(candidatos)
    rodar(AvaliarShorts(), ctx_shorts)
    assert ollama.chamadas == []


def test_avaliacao_de_outro_modelo_continua_sendo_descartada(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    _, candidatos = etapas_mod.carregar_candidatos(ctx_shorts.pastas.candidatos)
    assert etapas_mod._ler_avaliacoes(ctx_shorts.pastas.avaliacoes, "outro", candidatos) == {}
