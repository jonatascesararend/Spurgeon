"""Peças puras da Fase 3: formato, edição, legendas, vocabulário, blocos e pausas."""

from itertools import pairwise

import pytest

from spurgeon.audio.pausas import subtrair_intervalos
from spurgeon.core.tempo import MapaTempo, Trecho
from spurgeon.transcricao.blocos import planejar_blocos
from spurgeon.transcricao.edicao import (
    aplicar_edicao,
    aplicar_edicoes,
    segmento_de_texto,
    separar_pontuacao,
    trocar_em_tudo,
)
from spurgeon.transcricao.formato import (
    Bloco,
    Palavra,
    Segmento,
    Transcricao,
    segundos_para_rotulo,
)
from spurgeon.transcricao.srt import (
    Legenda,
    dividir_em_linhas,
    formatar_srt,
    formatar_tempo,
    montar_legendas,
    remapear_palavras,
)
from spurgeon.transcricao.vocabulario import (
    carregar_vocabulario,
    corrigir_termos,
    montar_dicas,
)


def palavras_de(texto: str, inicio: float = 0.0, passo: float = 0.4, prob: float = 0.95):
    """Palavras com 0,4 s cada, uma depois da outra."""
    return [
        Palavra(t, inicio + i * passo, inicio + (i + 1) * passo - 0.05, prob)
        for i, t in enumerate(texto.split())
    ]


# --- formato -------------------------------------------------------------------------------


def test_transcricao_vai_e_volta_do_disco(tmp_path):
    original = Transcricao(
        modelo="large-v3-turbo",
        duracao_s=30.0,
        assinatura="abc",
        revisada=True,
        blocos=[
            Bloco(0.0, 15.0, [Segmento(palavras_de("Deus é bom."))], concluido=True),
            Bloco(15.0, 30.0),
        ],
    )
    original.blocos[0].segmentos[0].palavras[1].original = "e"
    original.salvar(tmp_path / "t.json")
    lida = Transcricao.carregar(tmp_path / "t.json")
    assert lida.para_dict() == original.para_dict()
    assert lida.blocos[0].segmentos[0].palavras[1].original == "e"
    assert lida.blocos[1].concluido is False
    assert [p.texto for p in lida.palavras] == ["Deus", "é", "bom."]


def test_segmento_tem_inicio_fim_e_texto():
    seg = Segmento(palavras_de("A graça de Deus", inicio=10.0))
    assert seg.inicio == 10.0
    assert seg.fim == pytest.approx(11.55)
    assert seg.texto == "A graça de Deus"


@pytest.mark.parametrize(
    ("segundos", "esperado"), [(0, "0:00"), (125, "2:05"), (3725.4, "1:02:05")]
)
def test_rotulo_de_tempo(segundos, esperado):
    assert segundos_para_rotulo(segundos) == esperado


# --- edição --------------------------------------------------------------------------------


def test_texto_igual_nao_muda_nada():
    palavras = palavras_de("Deus é bom")
    assert aplicar_edicao(palavras, "Deus é bom") is palavras


def test_trocar_uma_palavra_mantem_o_tempo_e_guarda_o_original():
    palavras = palavras_de("A graça do Espurgeon")
    novas = aplicar_edicao(palavras, "A graça do Spurgeon")
    assert [p.texto for p in novas] == ["A", "graça", "do", "Spurgeon"]
    assert novas[3].inicio == palavras[3].inicio
    assert novas[3].fim == palavras[3].fim
    assert novas[3].original == "Espurgeon"
    assert novas[3].prob == 1.0  # uma pessoa corrigiu


def test_palavras_que_nao_mudaram_ficam_identicas():
    palavras = palavras_de("A graça do Senhor")
    novas = aplicar_edicao(palavras, "A graça do Deus")
    assert novas[0] is palavras[0]
    assert novas[2] is palavras[2]


def test_apagar_palavra_remove_so_ela():
    novas = aplicar_edicao(palavras_de("Deus é muito bom"), "Deus é bom")
    assert [p.texto for p in novas] == ["Deus", "é", "bom"]


def test_inserir_palavra_ocupa_a_folga_entre_as_vizinhas():
    palavras = [Palavra("Deus", 0.0, 0.5), Palavra("bom", 2.0, 2.5)]
    novas = aplicar_edicao(palavras, "Deus é bom")
    meio = novas[1]
    assert meio.texto == "é"
    assert 0.5 <= meio.inicio <= meio.fim <= 2.0


def test_trocar_por_mais_palavras_divide_o_intervalo_em_proporcao():
    palavras = [Palavra("Deus", 0.0, 1.0), Palavra("aleluia", 1.0, 3.0)]
    novas = aplicar_edicao(palavras, "Deus Glória a Deus")
    textos = [p.texto for p in novas]
    assert textos == ["Deus", "Glória", "a", "Deus"]
    assert novas[1].inicio == pytest.approx(1.0)  # começa onde a antiga começava
    assert novas[-1].fim == pytest.approx(3.0)  # e termina onde a antiga terminava
    assert all(a.fim <= b.inicio + 1e-9 for a, b in pairwise(novas[1:]))


def test_apagar_o_texto_todo_devolve_vazio():
    assert aplicar_edicao(palavras_de("Deus é bom"), "   ") == []


def test_editar_transcricao_inteira_por_segmento():
    t = Transcricao(
        "tiny",
        20.0,
        blocos=[
            Bloco(0.0, 20.0, [Segmento(palavras_de("Primeiro erro aqui")),
                              Segmento(palavras_de("Segundo ok", inicio=5.0))], True),
        ],
    )  # fmt: skip
    mudados = aplicar_edicoes(t, ["Primeiro acerto aqui", "Segundo ok"])
    assert mudados == 1
    assert t.revisada
    assert t.segmentos[0].texto == "Primeiro acerto aqui"


def test_segmento_apagado_some_da_transcricao():
    t = Transcricao(
        "tiny",
        10.0,
        blocos=[
            Bloco(0.0, 10.0, [Segmento(palavras_de("Lixo")), Segmento(palavras_de("Bom"))], True)
        ],
    )
    aplicar_edicoes(t, ["", "Bom"])
    assert [s.texto for s in t.segmentos] == ["Bom"]


def test_quantidade_de_textos_errada_e_recusada():
    t = Transcricao("tiny", 10.0, blocos=[Bloco(0.0, 10.0, [Segmento(palavras_de("A"))], True)])
    with pytest.raises(ValueError, match="1 segmentos"):
        aplicar_edicoes(t, ["A", "B"])


def test_trocar_em_tudo_preserva_pontuacao_e_inicial_maiuscula():
    t = Transcricao(
        "tiny",
        10.0,
        blocos=[Bloco(0.0, 10.0, [Segmento(palavras_de("Calvino, calvino e (Calvino)."))], True)],
    )
    assert trocar_em_tudo(t, "calvino", "Calvino") == 1  # as outras duas já estavam certas
    assert [p.texto for p in t.palavras] == ["Calvino,", "Calvino", "e", "(Calvino)."]
    assert t.palavras[1].original == "calvino"
    assert t.palavras[0].original is None


def test_trocar_em_tudo_so_aceita_uma_palavra():
    t = Transcricao("tiny", 1.0)
    with pytest.raises(ValueError):
        trocar_em_tudo(t, "duas palavras", "x")


@pytest.mark.parametrize(
    ("token", "esperado"),
    [("Deus,", ("", "Deus", ",")), ("(graça)", ("(", "graça", ")")), ("...", ("...", "", ""))],
)
def test_separar_pontuacao(token, esperado):
    assert separar_pontuacao(token) == esperado


def test_segmento_de_texto_reparte_o_tempo():
    seg = segmento_de_texto("um dois", 0.0, 2.0)
    assert [p.texto for p in seg.palavras] == ["um", "dois"]
    assert seg.fim == pytest.approx(2.0)


# --- legendas ------------------------------------------------------------------------------


def test_texto_curto_fica_em_uma_linha():
    assert dividir_em_linhas(["Deus", "é", "bom"], 42, 2) == ["Deus é bom"]


def test_texto_longo_vira_duas_linhas_equilibradas():
    texto = "A graça de Deus não depende do mérito do homem quando ele chama"
    linhas = dividir_em_linhas(texto.split(), 42, 2)
    assert linhas is not None and len(linhas) == 2
    assert all(len(linha) <= 42 for linha in linhas)
    assert abs(len(linhas[0]) - len(linhas[1])) <= 12


def test_quebra_de_linha_prefere_depois_da_virgula():
    palavras = ["Se", "Deus", "é", "por", "nós,", "quem", "será", "contra", "nós"]
    assert dividir_em_linhas(palavras, 30, 2) == ["Se Deus é por nós,", "quem será contra nós"]


def test_texto_que_nao_cabe_devolve_none():
    assert dividir_em_linhas(("palavra " * 30).split(), 42, 2) is None


def test_palavra_gigante_sozinha_sempre_cabe():
    assert dividir_em_linhas(["x" * 60], 42, 2) == ["x" * 60]


def test_legendas_respeitam_o_limite_de_caracteres_e_linhas():
    texto = "A graça de Deus não depende do mérito do homem. " * 4
    legendas = montar_legendas(palavras_de(texto), max_linhas=2, max_caracteres=42)
    assert len(legendas) > 1
    for leg in legendas:
        assert len(leg.linhas) <= 2
        assert all(len(linha) <= 42 for linha in leg.linhas)


def test_legenda_nao_passa_da_duracao_maxima():
    legendas = montar_legendas(palavras_de("palavra " * 60, passo=0.5), duracao_maxima_s=7.0)
    assert all(leg.fim - leg.inicio <= 7.5 for leg in legendas)


def test_pausa_longa_entre_palavras_separa_as_legendas():
    palavras = palavras_de("Deus é bom", 0.0) + palavras_de("Amém então", 10.0)
    legendas = montar_legendas(palavras)
    assert len(legendas) == 2
    assert legendas[1].inicio == pytest.approx(10.0)


def test_fim_de_frase_fecha_a_legenda_se_ja_durou_o_bastante():
    palavras = palavras_de("Mas Deus não é injusto. Deus só pode ser amor.")
    legendas = montar_legendas(palavras)
    assert legendas[0].linhas == ("Mas Deus não é injusto.",)


def test_frase_curtinha_nao_fica_sozinha():
    palavras = palavras_de("Sim. Deus é bom e justo para sempre")
    legendas = montar_legendas(palavras)
    assert legendas[0].linhas[0].startswith("Sim. Deus")


def test_duracao_minima_e_respeitada_sem_invadir_a_proxima():
    palavras = palavras_de("Oi", 0.0, passo=0.3) + palavras_de("Depois", 0.5, passo=0.3)
    legendas = montar_legendas(palavras, pausa_quebra_s=0.1)
    assert legendas[0].fim <= legendas[1].inicio


def test_formato_do_tempo_e_do_arquivo_srt():
    assert formatar_tempo(3725.4) == "01:02:05,400"
    assert formatar_tempo(0.0005) == "00:00:00,001" or formatar_tempo(0.0005) == "00:00:00,000"
    srt = formatar_srt(
        [Legenda(1.0, 2.5, ("Deus é bom",)), Legenda(3.0, 5.0, ("Linha um", "linha dois"))]
    )
    assert srt == (
        "1\n00:00:01,000 --> 00:00:02,500\nDeus é bom\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\nLinha um\nlinha dois\n"
    )
    assert formatar_srt([]) == ""


def test_remapear_palavras_descarta_o_cortado_e_desloca_o_resto():
    mapa = MapaTempo([Trecho(0.0, 10.0), Trecho(20.0, 40.0)])  # 10 s cortados entre 10 e 20
    palavras = [Palavra("a", 5.0, 5.4), Palavra("sumida", 12.0, 12.4), Palavra("b", 25.0, 25.4)]
    novas = remapear_palavras(palavras, mapa)
    assert [p.texto for p in novas] == ["a", "b"]
    assert novas[1].inicio == pytest.approx(15.0)  # 25 − 10 s cortados


def test_introducao_desloca_todas_as_legendas():
    mapa = MapaTempo([Trecho(0.0, 40.0)], deslocamento=8.0)
    novas = remapear_palavras([Palavra("a", 5.0, 5.4)], mapa)
    assert novas[0].inicio == pytest.approx(13.0)


# --- vocabulário ---------------------------------------------------------------------------


def test_carrega_o_vocabulario_ignorando_comentarios_vazias_e_repetidas(tmp_path):
    arquivo = tmp_path / "v.txt"
    arquivo.write_text(
        "# comentário\n\nSpurgeon\n  Calvino  \nspurgeon\nPacto da graça\n", encoding="utf-8"
    )
    assert carregar_vocabulario(arquivo) == ["Spurgeon", "Calvino", "Pacto da graça"]
    assert carregar_vocabulario(tmp_path / "nao_existe.txt") == []


def test_o_vocabulario_inicial_do_projeto_carrega_e_tem_os_termos_prioritarios():
    from spurgeon.core.config import raiz_repositorio

    termos = carregar_vocabulario(raiz_repositorio() / "recursos" / "vocabulario_teologico.txt")
    assert len(termos) > 200
    assert termos[0] == "Spurgeon"
    assert "Miqueias" in termos[:20]


def test_dicas_param_no_limite_de_caracteres_sem_cortar_termo():
    termos = ["Spurgeon", "Calvino", "predestinação", "soberania"]
    assert montar_dicas(termos) == "Spurgeon, Calvino, predestinação, soberania"
    dicas = montar_dicas(termos, limite=20)
    assert dicas == "Spurgeon, Calvino"
    assert len(dicas) <= 20


def test_corrige_nome_proprio_de_baixa_confianca():
    palavras = palavras_de("como dizia Espurgeon sobre a graça", prob=0.9)
    palavras[2].prob = 0.4  # o Whisper não tinha certeza
    correcoes = corrigir_termos(palavras, ["Spurgeon"])
    assert [p.texto for p in palavras][2] == "Spurgeon"
    assert palavras[2].original == "Espurgeon"
    assert len(correcoes) == 1 and correcoes[0].depois == "Spurgeon"


def test_nao_mexe_em_palavra_que_o_whisper_tem_certeza():
    palavras = palavras_de("o Espurgeon disse", prob=0.95)
    assert corrigir_termos(palavras, ["Spurgeon"]) == []
    assert palavras[1].texto == "Espurgeon"


def test_nao_mexe_em_palavra_que_ja_e_um_termo_nem_em_palavra_curta():
    palavras = palavras_de("Calvino disse que Deus é bom", prob=0.3)
    assert corrigir_termos(palavras, ["Calvino", "Deus"]) == []


def test_correcao_preserva_pontuacao_e_maiuscula_de_inicio_de_frase():
    palavras = palavras_de("Justificaçao, graça", prob=0.4)
    corrigir_termos(palavras, ["justificação"])
    assert palavras[0].texto == "Justificação,"


def test_corrige_termo_de_varias_palavras():
    palavras = palavras_de("o pacto da graca nos salva", prob=0.5)
    corrigir_termos(palavras, ["pacto da graça"])
    assert " ".join(p.texto for p in palavras) == "o pacto da graça nos salva"


# --- blocos --------------------------------------------------------------------------------


def test_audio_curto_e_um_bloco_so():
    assert planejar_blocos(700.0, []) == [(0.0, 700.0)]


def test_blocos_cobrem_o_audio_inteiro_sem_buracos():
    blocos = planejar_blocos(3898.0, [])
    assert blocos[0][0] == 0.0
    assert blocos[-1][1] == 3898.0
    assert all(a[1] == b[0] for a, b in pairwise(blocos))
    assert all(300 <= fim - ini <= 900 for ini, fim in blocos)


def test_fronteira_vai_para_o_meio_da_maior_pausa_por_perto():
    pausas = [(580.0, 580.4), (612.0, 614.0), (800.0, 810.0)]  # a de 612 é a maior na folga
    blocos = planejar_blocos(2000.0, pausas)
    assert blocos[0][1] == pytest.approx(613.0)


def test_sem_pausa_por_perto_a_fronteira_fica_no_ponto_ideal():
    blocos = planejar_blocos(2000.0, [(1500.0, 1502.0)])
    assert blocos[0][1] == pytest.approx(600.0)


def test_ultimo_bloco_nunca_fica_muito_pequeno():
    blocos = planejar_blocos(1260.0, [])  # 600 + 600 + 60: o resto de 60 s é absorvido
    assert all(fim - ini >= 300 for ini, fim in blocos)


# --- pausas conferidas pela fala -----------------------------------------------------------


def test_fala_no_meio_da_pausa_a_divide_e_o_pedaco_curto_some():
    pausas = [(10.0, 30.0)]
    fala = [(18.0, 18.5)]  # alguém falou baixinho no meio
    restantes = subtrair_intervalos(pausas, fala, folga_s=0.15)
    assert restantes == [(10.0, pytest.approx(17.85)), (pytest.approx(18.65), 30.0)]


def test_pausa_sem_fala_fica_igual():
    assert subtrair_intervalos([(10.0, 30.0)], [(1.0, 2.0), (40.0, 41.0)]) == [(10.0, 30.0)]


def test_fala_que_cobre_a_pausa_inteira_a_elimina():
    assert subtrair_intervalos([(10.0, 12.0)], [(9.0, 13.0)]) == []


def test_fala_que_comeca_antes_da_pausa_encurta_o_inicio():
    restantes = subtrair_intervalos([(10.0, 20.0)], [(8.0, 11.0)], folga_s=0.0)
    assert restantes == [(11.0, 20.0)]


def test_pedacos_menores_que_o_minimo_somem():
    restantes = subtrair_intervalos(
        [(10.0, 11.0)], [(10.4, 10.7)], folga_s=0.0, duracao_minima_s=0.5
    )
    assert restantes == []
