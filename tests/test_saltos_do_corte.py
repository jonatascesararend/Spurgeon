"""Cortes com saltos: tirar uma palavra ou uma frase do meio do Short e continuar logo depois."""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest

from spurgeon.core.ffmpeg import sondar
from spurgeon.render.shorts import RenderizarShorts
from spurgeon.shorts.analise import separar_frases
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import (
    LimiteInvalido,
    abaixo_do_minimo,
    comparar_cortes,
    definir_limite,
    faixas_do_corte,
    incluir_palavras,
    indices_das_palavras,
    indices_incluidos,
    legenda_preservada,
    linhas_da_tabela,
    mover_limite,
    mover_por_frase,
    palavras_com_a_legenda,
    palavras_do_corte,
    passa_do_limite,
    resumir,
    tirar_palavras,
)
from spurgeon.shorts.etapas import gravar_cortes
from spurgeon.shorts.pausas import (
    duracao_do_short,
    pausas_do_corte,
    saltos_do_corte,
    trechos_mantidos,
    trechos_sem_saltos,
)
from tests import test_pausas_do_short as base
from tests.test_shorts import cortes_do_sermao, sermao_falso

ctx_pausas, material_numerado = base.ctx_pausas, base.material_numerado


def _cenario():
    palavras = sermao_falso(frases=100)
    corte = cortes_do_sermao(palavras)[0]
    return palavras, corte, separar_frases(palavras)


# --- o modelo ------------------------------------------------------------------------------------


def test_a_duracao_do_short_desconta_os_saltos_e_a_janela_lida_nao():
    corte = Corte(1, 10.0, 70.0, "t", "outra", 80.0, {}, saltos=[[20.0, 25.0], [40.0, 42.5]])
    assert corte.janela_s == pytest.approx(60.0)
    assert corte.duracao == pytest.approx(60.0 - 5.0 - 2.5)
    assert Corte(1, 10.0, 70.0, "t", "outra", 80.0, {}).duracao == pytest.approx(60.0)


def test_cortes_antigos_sem_saltos_nem_pausa_continuam_carregando():
    antigo = {
        "id": 1, "inicio": 1.0, "fim": 5.0, "texto": "t", "categoria": "outra", "nota": 1.0,
        "detalhe": {},
    }  # fmt: skip
    corte = Corte.de_dict(antigo)
    assert corte.saltos == [] and corte.pausa_s == 0.0 and corte.duracao == pytest.approx(4.0)
    idem = Corte.de_dict(corte.para_dict())
    assert idem == corte


def test_o_limite_de_180_s_vale_sobre_a_duracao_do_short_e_nao_sobre_a_janela():
    longo = Corte(1, 0.0, 200.0, "t", "outra", 80.0, {})
    assert passa_do_limite(longo)
    com_salto = replace(longo, saltos=[[50.0, 80.0]])  # 170 s de Short
    assert not passa_do_limite(com_salto)
    assert resumir([com_salto]).duracao_s == pytest.approx(170.0)


# --- tirar e incluir palavras (a borracha e o marca-texto) --------------------------------------


def test_tirar_uma_frase_do_meio_vira_um_salto_e_o_resto_continua():
    palavras, corte, frases = _cenario()
    primeira, ultima = indices_das_palavras(palavras, corte)
    meio = frases[len(frases) // 2]
    # a frase do meio do corte
    do_corte = [f for f in frases if primeira <= f.primeira and f.ultima <= ultima]
    meio = do_corte[len(do_corte) // 2]
    novo = tirar_palavras(palavras, corte, range(meio.primeira, meio.ultima + 1))
    assert len(novo.saltos) == 1 and novo.editado
    de, ate = novo.saltos[0]
    assert palavras[meio.primeira - 1].fim <= de <= palavras[meio.primeira].inicio
    assert palavras[meio.ultima].fim <= ate <= palavras[meio.ultima + 1].inicio
    assert novo.duracao < corte.duracao - (meio.fim - meio.inicio) + 1.0
    assert (novo.inicio, novo.fim) == pytest.approx((corte.inicio, corte.fim), abs=0.5)
    incluidas = indices_incluidos(palavras, novo)
    assert not set(range(meio.primeira, meio.ultima + 1)) & set(incluidas)
    assert len(incluidas) == (ultima - primeira + 1) - (meio.ultima - meio.primeira + 1)
    assert [p.texto for p in palavras_do_corte(palavras, novo)] == [
        palavras[i].texto for i in incluidas
    ]
    assert novo.texto == " ".join(palavras[i].texto for i in incluidas)
    assert len(faixas_do_corte(palavras, novo)) == 2


def test_tirar_uma_so_palavra_do_meio_tambem_vira_salto():
    palavras, corte, _ = _cenario()
    primeira, _ = indices_das_palavras(palavras, corte)
    erro = primeira + 30
    novo = tirar_palavras(palavras, corte, [erro])
    assert len(novo.saltos) == 1
    assert erro not in indices_incluidos(palavras, novo)
    assert novo.duracao == pytest.approx(corte.duracao - (novo.saltos[0][1] - novo.saltos[0][0]))


def test_tirar_palavras_das_pontas_encurta_o_short_sem_salto():
    palavras, corte, _ = _cenario()
    primeira, ultima = indices_das_palavras(palavras, corte)
    sem_comeco = tirar_palavras(palavras, corte, range(primeira, primeira + 5))
    assert sem_comeco.saltos == [] and indices_das_palavras(palavras, sem_comeco)[0] == primeira + 5
    sem_fim = tirar_palavras(palavras, corte, range(ultima - 3, ultima + 1))
    assert sem_fim.saltos == [] and indices_das_palavras(palavras, sem_fim)[1] == ultima - 4


def test_tirar_tudo_nao_deixa_o_short_sem_palavra():
    palavras, corte, _ = _cenario()
    primeira, ultima = indices_das_palavras(palavras, corte)
    with pytest.raises(LimiteInvalido, match="sem nenhuma palavra"):
        tirar_palavras(palavras, corte, range(primeira, ultima + 1))


def test_incluir_palavras_longe_junta_dois_pedacos_e_vizinhas_so_aumentam_o_corte():
    palavras, corte, _ = _cenario()
    _, ultima = indices_das_palavras(palavras, corte)
    vizinhas = incluir_palavras(palavras, corte, range(ultima + 1, ultima + 6))
    assert vizinhas.saltos == [] and indices_das_palavras(palavras, vizinhas)[1] == ultima + 5
    longe = incluir_palavras(palavras, corte, range(ultima + 40, ultima + 50))
    assert len(longe.saltos) == 1  # "a frase 1 + a frase 3": o que ficou no meio é salto
    assert not set(range(ultima + 1, ultima + 40)) & set(indices_incluidos(palavras, longe))
    assert indices_incluidos(palavras, longe)[-10:] == list(range(ultima + 40, ultima + 50))
    de, ate = longe.saltos[0]
    assert palavras[ultima].fim <= de and ate <= palavras[ultima + 40].inicio


def test_tirar_e_incluir_de_volta_devolve_as_mesmas_palavras():
    palavras, corte, _ = _cenario()
    primeira, _ = indices_das_palavras(palavras, corte)
    sem = tirar_palavras(palavras, corte, range(primeira + 20, primeira + 32))
    assert sem.saltos
    de_volta = incluir_palavras(palavras, sem, range(primeira + 20, primeira + 32))
    assert de_volta.saltos == []
    assert indices_incluidos(palavras, de_volta) == indices_incluidos(palavras, corte)
    assert (de_volta.inicio, de_volta.fim) == pytest.approx((corte.inicio, corte.fim), abs=0.01)


# --- os botões de começo e fim num corte com saltos ----------------------------------------------


def _com_salto(palavras, corte):
    primeira, _ = indices_das_palavras(palavras, corte)
    return tirar_palavras(palavras, corte, range(primeira + 24, primeira + 36))


def test_os_botoes_de_palavra_andam_pelas_palavras_que_estao_no_short():
    palavras, corte, _ = _cenario()
    cortado = _com_salto(palavras, corte)
    primeira, ultima = indices_das_palavras(palavras, cortado)
    menos = mover_limite(palavras, cortado, "inicio", 3)
    assert indices_das_palavras(palavras, menos)[0] == primeira + 3 and menos.saltos
    mais = mover_limite(palavras, cortado, "inicio", -2)
    assert indices_das_palavras(palavras, mais)[0] == primeira - 2
    fim_menos = mover_limite(palavras, cortado, "fim", -4)
    assert indices_das_palavras(palavras, fim_menos)[1] == ultima - 4
    fim_mais = mover_limite(palavras, cortado, "fim", 2)
    assert indices_das_palavras(palavras, fim_mais)[1] == ultima + 2
    assert fim_mais.saltos and fim_mais.saltos[0] == pytest.approx(cortado.saltos[0])


def test_tirar_palavras_do_comeco_alem_do_primeiro_pedaco_leva_o_comeco_para_depois_do_salto():
    palavras, corte, _ = _cenario()
    cortado = _com_salto(palavras, corte)
    primeira, _ = indices_das_palavras(palavras, cortado)
    # as 24 palavras antes do salto, mais 2 do pedaço seguinte
    novo = mover_limite(palavras, cortado, "inicio", 26)
    assert novo.saltos == []
    assert indices_das_palavras(palavras, novo)[0] == primeira + 36 + 2


def test_os_botoes_de_frase_e_o_tempo_digitado_funcionam_com_saltos():
    palavras, corte, frases = _cenario()
    cortado = _com_salto(palavras, corte)
    primeira, ultima = indices_das_palavras(palavras, cortado)
    mais = mover_por_frase(palavras, cortado, "fim", 1, frases)
    assert indices_das_palavras(palavras, mais)[1] > ultima and mais.saltos
    menos = mover_por_frase(palavras, cortado, "fim", -1, frases)
    assert indices_das_palavras(palavras, menos)[1] < ultima and menos.saltos
    antes = mover_por_frase(palavras, cortado, "inicio", -1, frases)
    assert indices_das_palavras(palavras, antes)[0] < primeira
    alvo = palavras[ultima - 15].fim
    mudado = definir_limite(palavras, cortado, "fim", alvo)
    assert indices_das_palavras(palavras, mudado)[1] == ultima - 15
    recuado = definir_limite(palavras, cortado, "inicio", palavras[primeira - 8].inicio)
    assert indices_das_palavras(palavras, recuado)[0] == primeira - 8 and recuado.saltos


def test_o_corte_com_saltos_nao_pode_ficar_com_menos_de_10_s():
    palavras, corte, _ = _cenario()
    primeira, ultima = indices_das_palavras(palavras, corte)
    quase_tudo = tirar_palavras(palavras, corte, range(primeira + 5, ultima - 5))
    assert abaixo_do_minimo(quase_tudo)  # a ferramenta deixa; a tela avisa
    with pytest.raises(LimiteInvalido, match="menos de 10"):
        mover_limite(palavras, quase_tudo, "fim", -3)


# --- o que mudou, palavra por palavra -------------------------------------------------------------


def test_comparar_cortes_distingue_o_que_saiu_do_meio_do_que_saiu_das_pontas():
    palavras, corte, _ = _cenario()
    primeira, ultima = indices_das_palavras(palavras, corte)
    sem_meio = tirar_palavras(palavras, corte, range(primeira + 10, primeira + 14))
    m = comparar_cortes(palavras, corte, sem_meio)
    assert m.meio_tirou == tuple(palavras[i].texto for i in range(primeira + 10, primeira + 14))
    assert not (m.inicio_tirou or m.fim_tirou or m.inicio_acrescentou or m.fim_acrescentou)
    assert m.houve and m.ponta == "meio"
    volta = comparar_cortes(palavras, sem_meio, corte)
    assert volta.meio_acrescentou == m.meio_tirou and volta.ponta == "meio"
    nas_duas = tirar_palavras(palavras, sem_meio, [primeira, ultima])
    m2 = comparar_cortes(palavras, sem_meio, nas_duas)
    assert m2.inicio_tirou == (palavras[primeira].texto,) and m2.ponta == "fim"


# --- a legenda corrigida acompanha ---------------------------------------------------------------


def test_a_correcao_de_uma_palavra_que_fica_sobrevive_a_tirar_palavras_do_meio():
    palavras, corte, _ = _cenario()
    primeira, _ = indices_das_palavras(palavras, corte)
    textos = [palavras[i].texto for i in indices_incluidos(palavras, corte)]
    textos[3] = "corrigida"
    corrigido = replace(corte, legenda=" ".join(textos))
    sem_meio = tirar_palavras(palavras, corrigido, range(primeira + 20, primeira + 28))
    nova = legenda_preservada(palavras, corrigido, replace(sem_meio, legenda=""))
    assert "corrigida" in nova.split() and palavras[primeira + 20].texto not in nova.split()
    esperado = [t for k, t in enumerate(textos) if not 20 <= k < 28]
    assert nova.split() == esperado
    # a palavra corrigida tirada leva a correção embora
    sem_a_corrigida = tirar_palavras(palavras, corrigido, [primeira + 3])
    assert legenda_preservada(palavras, corrigido, sem_a_corrigida) == ""


def test_a_legenda_corrigida_so_mostra_as_palavras_que_estao_no_short():
    palavras, corte, _ = _cenario()
    primeira, _ = indices_das_palavras(palavras, corte)
    sem_meio = tirar_palavras(palavras, corte, range(primeira + 5, primeira + 9))
    assert len(palavras_com_a_legenda(palavras, sem_meio)) == len(
        indices_incluidos(palavras, sem_meio)
    )


# --- a tabela ----------------------------------------------------------------------------------


def test_a_tabela_mostra_o_pedaco_cortado_entre_dois_marcadores():
    palavras, corte, frases = _cenario()
    primeira, ultima = indices_das_palavras(palavras, corte)
    do_corte = [f for f in frases if primeira <= f.primeira and f.ultima <= ultima]
    meio = do_corte[len(do_corte) // 2]
    cortado = tirar_palavras(palavras, corte, range(meio.primeira, meio.ultima + 1))
    linhas = linhas_da_tabela(palavras, cortado, frases, fora=24)
    tipos = [linha.tipo for linha in linhas]
    assert tipos.count("corta") == tipos.count("volta") == 1
    assert not {"inicio", "fim"} & set(tipos)  # o começo e o fim do Short não têm marcador
    ordem = [t for t in tipos if t in ("corta", "volta")]
    assert ordem == ["corta", "volta"]
    c, v = tipos.index("corta"), tipos.index("volta")
    i = tipos.index("dentro")
    ultimo = len(tipos) - 1 - tipos[::-1].index("dentro")
    assert set(tipos[c + 1 : v]) == {"cortado"}
    assert [t for linha in linhas[c + 1 : v] for t in linha.textos] == [
        palavras[k].texto for k in range(meio.primeira, meio.ultima + 1)
    ]
    assert set(tipos[i:c]) == {"dentro"} and set(tipos[v + 1 : ultimo + 1]) == {"dentro"}
    # o texto das linhas do Short é a legenda: sem as palavras cortadas
    dentro = [t for linha in linhas if linha.tipo == "dentro" for t in linha.textos]
    assert dentro == [palavras[k].texto for k in indices_incluidos(palavras, cortado)]


def test_a_tabela_com_a_legenda_corrigida_poe_as_palavras_no_pedaco_certo():
    palavras, corte, frases = _cenario()
    primeira, _ = indices_das_palavras(palavras, corte)
    cortado = tirar_palavras(palavras, corte, range(primeira + 24, primeira + 36))
    incluidas = [palavras[k].texto for k in indices_incluidos(palavras, cortado)]
    editado = incluidas.copy()
    editado[0], editado[-1] = "primeira", "ultima"
    linhas = linhas_da_tabela(palavras, cortado, frases, fora=5, texto_dentro=" ".join(editado))
    tipos = [linha.tipo for linha in linhas]
    assert [t for t in tipos if t in ("corta", "volta")] == ["corta", "volta"]
    k = tipos.index("corta")
    antes = [t for linha in linhas[:k] if linha.tipo == "dentro" for t in linha.textos]
    depois = [t for linha in linhas[k:] if linha.tipo == "dentro" for t in linha.textos]
    assert antes[0] == "primeira" and depois[-1] == "ultima" and len(antes) == 24


# --- as pausas e os saltos juntos ----------------------------------------------------------------


def test_os_saltos_saem_do_short_e_a_pausa_do_vao_atravessado_nao_conta_duas_vezes():
    palavras = base.fala(
        ("a", 1.0, 1.5), ("b", 2.0, 2.5), ("c", 3.0, 3.5), ("d", 4.5, 5.0), ("e", 6.0, 6.5),
    )  # fmt: skip
    corte = base.corte(0.8, 7.0, saltos=[[2.6, 4.4]])  # tira c (e o vão antes e depois dele)
    assert len(saltos_do_corte(corte)) == 1
    assert saltos_do_corte(corte)[0] == pytest.approx((1.8, 3.6))
    sem = trechos_sem_saltos(corte)
    assert [(round(t.inicio, 3), round(t.fim, 3)) for t in sem] == [(0.0, 1.8), (3.6, 6.2)]
    # o vão b→d (2,5 → 4,5) contém o salto: é "pausa" de verdade só até onde o salto não vai
    pausas = pausas_do_corte(palavras, corte)  # a→b e d→e; b→d o salto já cortou
    assert len(pausas) == 2
    assert pausas[0] == pytest.approx((0.7, 1.2)) and pausas[1] == pytest.approx((4.2, 5.2))
    assert duracao_do_short(palavras, corte) == pytest.approx(1.8 + 2.6)
    # com a pausa de 0,4 s encurtando o que sobrou, a duração só diminui
    com_pausa = replace(corte, pausa_s=0.4)
    assert duracao_do_short(palavras, com_pausa) < duracao_do_short(palavras, corte)
    trechos = trechos_mantidos(palavras, com_pausa)
    assert trechos[0].inicio == 0.0 and trechos[-1].fim == pytest.approx(6.2)
    for a, b in pairwise(trechos):
        assert a.fim <= b.inicio  # em ordem e sem sobreposição


# --- o render de verdade -----------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_render_com_salto_tira_o_pedaco_e_a_legenda_acompanha(ctx_pausas, material_numerado):
    corte = replace(base.CORTE_DO_RENDER, pausa_s=0.0, saltos=[[2.8, 5.1]])  # entre "dois" e "tres"
    gravar_cortes(ctx_pausas.pastas.cortes_shorts, [corte])
    ctx_pausas.etapa_id = "renderizar_shorts"
    RenderizarShorts().executar(ctx_pausas)
    saida = ctx_pausas.pastas.shorts / "short_01_corte_1.mp4"
    esperado = corte.duracao
    assert esperado == pytest.approx(base.CORTE_DO_RENDER.janela_s - 2.3)
    assert sondar(saida).duracao_s == pytest.approx(esperado, abs=0.15)
    roteiro = (ctx_pausas.pastas.work / "legendas" / "short_01_corte_1.filter").read_text(
        encoding="utf-8"
    )
    assert "split=2" in roteiro and "asplit=2" in roteiro
    ass = (ctx_pausas.pastas.work / "legendas" / "short_01_corte_1.ass").read_text(encoding="utf-8")
    assert "tres" in ass and "dois" in ass
