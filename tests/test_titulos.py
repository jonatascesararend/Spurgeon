"""Títulos dos Shorts: igual, parecido demais, genérico e abertura repetida.

Os títulos vêm de projetos reais, em que a IA repetiu a mesma fórmula ("Por que …?") e chegou a
criar o mesmo título quatro vezes.
"""

import pytest

from spurgeon.shorts import titulos as t

SERMAO = "07._Quando_quem_deveria_fazer_justiça_pratica_o_mal_(Miquéias_3.1-4)".replace("_", " ")


def test_so_pontuacao_caixa_e_acento_nao_diferenciam_titulos():
    assert t.iguais("Quem deveria julgar pratica o mal", "quem deveria JULGAR, pratica o mal!")
    assert t.iguais("A graça de Deus", "A graca de deus")
    assert t.semelhanca("A graça de Deus", "a graca de deus") == 1.0
    assert not t.iguais("Quem julga pratica o mal", "Quem deveria julgar pratica o mal")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # a mesma ideia com outra palavra no fim
        (
            "Por que precisamos ouvir a palavra de Deus com atenção?",
            "Por que precisamos ouvir a palavra de Deus com sobriedade?",
        ),
        # as mesmas palavras, só sem "deveria"
        ("Quem julga pratica o mal", "Quem deveria julgar pratica o mal"),
        (
            "A autoridade do pregador está na palavra de Deus",
            "Por que a autoridade dos pastores depende da palavra de Deus?",
        ),
        # singular e plural, e a ordem trocada
        ("A graça alcança o pecador", "O pecador alcança as graças"),
    ],
)
def test_titulos_com_as_mesmas_ideias_sao_parecidos(a, b):
    assert t.sao_parecidos(a, b) and t.sao_parecidos(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acaz, o Rei que queimou seus próprios filhos", "Por que Jotão não entrou no templo?"),
        # só dividem palavras amplas ("Deus", "povo"): não é a mesma ideia
        ("Por que o povo tapou os ouvidos para Deus?", "Quando Deus fala ao seu povo"),
        ("Votar em corruptos é coparticipar do mal", "Os líderes prestarão contas diante de Deus"),
        # uma palavra de conteúdo em comum é coincidência
        ("Por que Miquéias condenou a liderança?", "Por que Miquéias profetizou sobre Judá?"),
    ],
)
def test_titulos_de_assuntos_diferentes_nao_sao_parecidos(a, b):
    assert not t.sao_parecidos(a, b)


def test_o_mais_parecido_da_lista():
    usados = ["", "Jotão não entrou no templo", "Acaz queimou os próprios filhos"]
    titulo, nota = t.mais_parecido("Por que Acaz queimou seus filhos?", usados)
    assert titulo == "Acaz queimou os próprios filhos" and nota >= t.LIMITE_DE_SEMELHANCA
    assert t.mais_parecido("qualquer coisa", []) == ("", 0.0)
    assert t.mais_parecido("qualquer coisa", ["", "  "]) == ("", 0.0)


# --- problema_do_titulo --------------------------------------------------------------------


def test_titulo_bom_nao_tem_problema():
    usados = ["Acaz queimou os próprios filhos", "Jotão não entrou no templo"]
    assert t.problema_do_titulo("Quando um rei ímpio governa o povo sofre", usados) is None


def test_titulo_igual_a_outro_e_o_problema_mais_grave():
    p = t.problema_do_titulo("quem julga, pratica o mal!", ["Quem julga pratica o mal"])
    assert p is not None and p.tipo == t.IGUAL and "Quem julga pratica o mal" in p.motivo


def test_titulo_com_as_mesmas_palavras_nao_e_dito_igual_mas_parecido():
    p = t.problema_do_titulo("Quem julga pratica o mal", ["Quem deveria julgar pratica o mal"])
    assert p is not None and p.tipo == t.PARECIDO and p.semelhanca >= t.LIMITE_DE_SEMELHANCA


@pytest.mark.parametrize(
    "titulo",
    [
        "Deus punirá o pecado",
        "Por que ser fiel à palavra de Deus?",
        "Como a palavra de Deus transforma minha vida?",
        "A igreja precisa ser justiça no mundo",
    ],
)
def test_titulo_so_com_palavras_amplas_e_generico(titulo):
    p = t.problema_do_titulo(titulo)
    assert p is not None and p.tipo == t.GENERICO


def test_escrito_nao_e_confundido_com_escritura():
    # "escritura" é ampla; "escrito" não é (o radical de 5 letras é o mesmo)
    assert t.problema_do_titulo("Quando SENHOR é escrito em maiúsculo") is None


def test_titulo_que_so_repete_o_do_sermao_e_generico():
    p = t.problema_do_titulo("Quem deveria fazer justiça pratica o mal", sermao=SERMAO)
    assert p is not None and p.tipo == t.GENERICO and "sermão" in p.motivo
    assert t.problema_do_titulo("Quem deveria governar pratica corrupção", sermao=SERMAO) is None


def test_nome_do_sermao_sem_o_numero_e_o_texto_biblico():
    assert t.nome_do_sermao_sem_numero(SERMAO) == "Quando quem deveria fazer justiça pratica o mal"
    assert t.nome_do_sermao_sem_numero("Sem número (Gn 1)") == "Sem número"


def test_abertura_repetida_em_muitos_titulos_pede_outra():
    usados = [
        "Por que Ezequias foi levantado pelo Senhor?",
        "Por que Acaz buscou um falso deus?",
        "Por que Jotão não entrou no templo?",
        "Acaz, o Rei que queimou seus próprios filhos",
        "Quando um Rei ímpio governa o povo sofre",
        "A vida sem Cristo não vale a pena ser vivida",
    ]
    assert t.abertura("Por que Miquéias condenou a liderança?") == "por que"
    p = t.problema_do_titulo("Por que Miquéias condenou a liderança?", usados)
    assert p is not None and p.tipo == t.ABERTURA and "por que" in p.motivo
    # com outra abertura, e com poucas ocorrências da mesma, o título serve
    assert t.problema_do_titulo("Miquéias condenou a liderança de Judá", usados) is None
    assert t.problema_do_titulo("Por que Miquéias condenou a liderança?", usados[:2]) is None


def test_titulo_curto_nao_tem_abertura():
    assert t.abertura("Deus é soberano") == ""


def test_sequencia_real_de_titulos_repetidos_e_acusada():
    gerados = [
        "Quem deveria fazer justiça pratica o mal",
        "Quem deveria fazer justiça pratica o mal",
        "Quem deveria julgar pratica o mal",
        "Corrupção envolve pessoas e não só números",
        "Quem deveria julgar pratica o mal",
        "Corrupção envolve pessoas e não só números",
    ]
    aceitos: list[str] = []
    problemas = []
    for titulo in gerados:
        problema = t.problema_do_titulo(titulo, aceitos)
        problemas.append(problema.tipo if problema else None)
        if problema is None:
            aceitos.append(titulo)
    # o 3º e o 5º nunca entram na lista de aceitos, então são "parecidos" com o 1º (e não "iguais")
    assert problemas == [None, t.IGUAL, t.PARECIDO, None, t.PARECIDO, t.IGUAL]


# --- escolher_titulo -----------------------------------------------------------------------


def test_titulo_que_serve_nao_pede_outro():
    def pedir(recusas, temperatura):
        raise AssertionError("não devia pedir")

    escolha = t.escolher_titulo("Jotão não entrou no templo", pedir, ["Acaz queimou os filhos"])
    assert escolha.titulo == "Jotão não entrou no templo"
    assert escolha.problema is None and escolha.pedidos == 0


def test_titulo_recusado_pede_outro_dizendo_o_motivo_e_soltando_a_temperatura():
    pedidos = []
    novos = iter(["Acaz queimou os próprios filhos", "Quando um rei ímpio governa o povo sofre"])
    recusados = []

    def pedir(recusas, temperatura):
        pedidos.append((recusas, temperatura))
        return next(novos)

    escolha = t.escolher_titulo(
        "Acaz queimou os filhos no fogo",
        pedir,
        ["Acaz queimou os próprios filhos"],
        ao_recusar=lambda titulo, problema: recusados.append((titulo, problema.tipo)),
    )
    assert escolha.titulo == "Quando um rei ímpio governa o povo sofre"
    assert escolha.problema is None and escolha.pedidos == 2
    # cada pedido leva TODAS as recusas até ali, com o motivo
    assert [len(r) for r, _ in pedidos] == [1, 2]
    assert pedidos[0][0][0][0] == "Acaz queimou os filhos no fogo"
    assert "parecido" in pedidos[0][0][0][1] or "igual" in pedidos[0][0][0][1]
    temperaturas = [temp for _, temp in pedidos]
    assert temperaturas == sorted(temperaturas) and temperaturas[0] < temperaturas[-1]
    assert [tipo for _, tipo in recusados] == [t.PARECIDO, t.IGUAL]


def test_se_nenhum_servir_fica_o_de_menor_problema_nunca_o_igual():
    usados = ["Acaz queimou os próprios filhos"]
    novos = iter(
        [
            "Acaz queimou os próprios filhos",  # igual: o pior
            "Acaz queimou os filhos",  # parecido
            "Deus punirá o pecado",  # genérico: o menos grave dos três
        ]
    )
    escolha = t.escolher_titulo(
        "Acaz queimou os próprios filhos.", lambda recusas, temp: next(novos), usados
    )
    assert escolha.titulo == "Deus punirá o pecado"
    assert escolha.problema is not None and escolha.problema.tipo == t.GENERICO
    assert escolha.pedidos == t.TENTATIVAS_EXTRAS


def test_o_numero_de_pedidos_tem_limite():
    chamadas = []

    def pedir(recusas, temperatura):
        chamadas.append(1)
        return "Acaz queimou os próprios filhos"

    t.escolher_titulo("Acaz queimou os próprios filhos", pedir, ["Acaz queimou os próprios filhos"])
    assert len(chamadas) == t.TENTATIVAS_EXTRAS
    chamadas.clear()
    t.escolher_titulo(
        "Acaz queimou os próprios filhos",
        pedir,
        ["Acaz queimou os próprios filhos"],
        tentativas=1,
    )
    assert len(chamadas) == 1
