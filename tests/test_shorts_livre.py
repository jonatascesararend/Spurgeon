"""Short livre: candidato de um trecho qualquer, busca no texto, criação do corte e manuais."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.shorts.analise import candidato_do_trecho, gerar_candidatos, separar_frases
from spurgeon.shorts.busca import procurar_palavras, procurar_tema, sem_acento
from spurgeon.shorts.dados import (
    ID_BASE_DOS_MANUAIS,
    ORIGEM_AUTOMATICA,
    ORIGEM_MANUAL,
    Avaliacao,
    Corte,
)
from spurgeon.shorts.etapas import cortes_manuais_guardados, gravar_cortes
from spurgeon.shorts.livre import (
    DURACAO_MAXIMA_LIVRE_S,
    Trecho,
    duracao_do_trecho,
    gerar_corte_livre,
    mover_ponta,
    problema_do_trecho,
    proximo_id,
    resumo_das_pontas,
)
from spurgeon.shorts.llm import MetadadosLLM
from spurgeon.transcricao.formato import Palavra
from tests.test_shorts import sermao_falso


def _cliente_falso(nota: int = 8):
    chamadas: list[str] = []

    class Cliente:
        def avaliar(self, antes, trecho, depois, duracao):
            chamadas.append("avaliar")
            return Avaliacao("outra", nota, True, "", "Boa ideia central.")

    return Cliente(), chamadas


def _metadados(cliente, sermao, texto, categoria, duracao, titulos):
    return MetadadosLLM(
        titulo=f"Título de {len(titulos)}",
        descricao="Um resumo do trecho.",
        hashtags=["#shorts", "#fe"],
        slug="titulo-do-trecho",
    )


def _palavras_de(texto: str) -> list[Palavra]:
    return [Palavra(t, i * 0.4, i * 0.4 + 0.3, 0.9) for i, t in enumerate(texto.split())]


@pytest.fixture
def sermao():
    palavras = sermao_falso()
    return palavras, separar_frases(palavras)


# --- o candidato de um trecho qualquer -------------------------------------------------------


def test_trecho_de_frases_inteiras_tem_os_mesmos_sinais_da_analise_automatica(sermao):
    palavras, frases = sermao
    alvo = gerar_candidatos(palavras, frases)[0]
    do_trecho = candidato_do_trecho(
        alvo.id,
        palavras,
        frases,
        frases[alvo.primeira_frase].primeira,
        frases[alvo.ultima_frase].ultima,
    )
    assert do_trecho.sinais == alvo.sinais
    assert (do_trecho.inicio, do_trecho.fim, do_trecho.texto) == (alvo.inicio, alvo.fim, alvo.texto)
    assert do_trecho.pre_nota == pytest.approx(alvo.pre_nota)


def test_trecho_pode_comecar_e_terminar_no_meio_de_uma_frase(sermao):
    palavras, frases = sermao
    c = candidato_do_trecho(7, palavras, frases, 14, 80)
    assert c.texto.split()[0] == palavras[14].texto
    assert c.texto.split()[-1] == palavras[80].texto
    assert c.inicio == palavras[14].inicio and c.fim == palavras[80].fim
    esperado = palavras[14].inicio - palavras[13].fim
    assert c.sinais["pausa_antes_s"] == pytest.approx(esperado, abs=0.01)


def test_trecho_invalido_levanta_erro(sermao):
    palavras, frases = sermao
    with pytest.raises(ValueError):
        candidato_do_trecho(1, palavras, frases, 10, 5)
    with pytest.raises(ValueError):
        candidato_do_trecho(1, palavras, frases, 0, len(palavras))


# --- escolher e ajustar o trecho --------------------------------------------------------------


def test_problema_do_trecho_avisa_curto_e_longo_demais(sermao):
    palavras, _frases = sermao
    assert "Escolha o começo" in problema_do_trecho(palavras, None)
    assert "pelo menos" in problema_do_trecho(palavras, Trecho(0, 5))
    assert "máximo" in problema_do_trecho(palavras, Trecho(0, len(palavras) - 1))
    assert problema_do_trecho(palavras, Trecho(0, 100)) is None
    assert palavras[100].fim - palavras[0].inicio < DURACAO_MAXIMA_LIVRE_S


def test_mover_ponta_por_palavra_e_por_frase(sermao):
    palavras, frases = sermao
    t = Trecho(12, 90)  # a frase 1 vai da palavra 12 à 23
    assert mover_ponta(palavras, frases, t, "inicio", -1, por_frase=False) == Trecho(11, 90)
    assert mover_ponta(palavras, frases, t, "fim", 1, por_frase=False) == Trecho(12, 91)
    assert mover_ponta(palavras, frases, t, "inicio", -1, por_frase=True).primeira == 0
    assert mover_ponta(palavras, frases, t, "inicio", 1, por_frase=True).primeira == 24
    assert mover_ponta(palavras, frases, t, "fim", 1, por_frase=True).ultima == frases[7].ultima


def test_mover_ponta_nao_sai_do_sermao_nem_inverte_o_trecho(sermao):
    palavras, frases = sermao
    assert mover_ponta(palavras, frases, Trecho(0, 50), "inicio", -1, por_frase=False).primeira == 0
    ultimo = len(palavras) - 1
    esticado = mover_ponta(palavras, frases, Trecho(5, ultimo), "fim", 1, por_frase=False)
    assert esticado.ultima == ultimo
    assert mover_ponta(palavras, frases, Trecho(7, 7), "inicio", 1, por_frase=False) == Trecho(7, 7)


def test_resumo_das_pontas_mostra_o_comeco_e_o_fim(sermao):
    palavras, _frases = sermao
    texto = resumo_das_pontas(palavras, Trecho(10, 40), quantas=3)
    assert palavras[10].texto in texto and palavras[40].texto in texto


# --- criar o corte ----------------------------------------------------------------------------


def test_gerar_corte_livre_faz_o_corte_como_os_automaticos(sermao):
    palavras, frases = sermao
    cliente, chamadas = _cliente_falso()
    avisos: list[str] = []
    corte = gerar_corte_livre(
        cliente,
        palavras=palavras,
        frases=frases,
        trecho=Trecho(14, 130),
        id_=ID_BASE_DOS_MANUAIS,
        sermao="Sermão de teste",
        titulos_usados=["Outro título"],
        nomes_usados=["titulo_do_trecho"],
        ao_andamento=avisos.append,
        criar_metadados=_metadados,
    )
    assert corte.origem == ORIGEM_MANUAL and corte.incluir and not corte.reserva
    assert corte.id == ID_BASE_DOS_MANUAIS
    assert corte.titulo == "Título de 1" and corte.hashtags == ["#shorts", "#fe"]
    assert corte.slug != "titulo_do_trecho"  # não repete o nome de um arquivo já usado
    assert 0 < corte.nota <= 100 and corte.detalhe["retencao"] == 32.0
    assert corte.motivo == "Boa ideia central." and corte.categoria == "outra"
    assert corte.inicio <= palavras[14].inicio and corte.fim >= palavras[130].fim
    assert corte.texto.split()[0] == palavras[14].texto
    assert chamadas == ["avaliar"] and len(avisos) == 2


def test_gerar_corte_livre_recusa_trecho_curto_sem_chamar_a_ia(sermao):
    palavras, frases = sermao
    cliente, chamadas = _cliente_falso()
    with pytest.raises(ValueError, match="pelo menos"):
        gerar_corte_livre(
            cliente, palavras=palavras, frases=frases, trecho=Trecho(0, 4), id_=1_000_000,
            sermao="x", criar_metadados=_metadados,
        )  # fmt: skip
    assert chamadas == []


def _corte(id_: int, **mudancas) -> Corte:
    base = {
        "id": id_, "inicio": 0.0, "fim": 40.0, "texto": "a", "categoria": "outra",
        "nota": 50.0, "detalhe": {},
    }  # fmt: skip
    return Corte(**{**base, **mudancas})


def test_proximo_id_comeca_no_base_e_nao_repete():
    automatico = _corte(3)
    manual = _corte(ID_BASE_DOS_MANUAIS + 4, origem=ORIGEM_MANUAL)
    assert proximo_id([]) == ID_BASE_DOS_MANUAIS
    assert proximo_id([automatico]) == ID_BASE_DOS_MANUAIS
    assert proximo_id([automatico, manual]) == ID_BASE_DOS_MANUAIS + 5


def test_corte_antigo_sem_origem_e_automatico():
    dados = _corte(1).para_dict()
    dados.pop("origem")
    assert Corte.de_dict(dados).origem == ORIGEM_AUTOMATICA


def test_cortes_manuais_sobrevivem_a_uma_nova_selecao(tmp_path):
    arquivo = tmp_path / "cortes_shorts.json"
    manual = _corte(ID_BASE_DOS_MANUAIS, inicio=100.0, fim=150.0, origem=ORIGEM_MANUAL)
    assert cortes_manuais_guardados(arquivo) == []  # sem arquivo
    gravar_cortes(arquivo, [_corte(1), manual])
    assert [c.id for c in cortes_manuais_guardados(arquivo)] == [ID_BASE_DOS_MANUAIS]
    escrever_json_atomico(arquivo, {"quebrado": True})
    assert cortes_manuais_guardados(arquivo) == []  # arquivo inesperado não derruba a etapa


# --- busca ------------------------------------------------------------------------------------


def test_busca_por_palavras_tolera_erro_acento_e_variacao():
    palavras = _palavras_de(
        "A graça de Deus é soberana. Ele perdoou os nossos pecados na cruz. "
        "Hoje falamos sobre oração e jejum."
    )
    frases = separar_frases(palavras)
    assert len(frases) == 3
    assert procurar_palavras(palavras, frases, "graca")[0].texto.startswith("A graça")
    assert procurar_palavras(palavras, frases, "gaça")[0].texto.startswith("A graça")  # erro
    assert procurar_palavras(palavras, frases, "perdoar")[0].texto.startswith("Ele perdoou")
    assert procurar_palavras(palavras, frases, "oracao jejum")[0].texto.startswith("Hoje")
    assert procurar_palavras(palavras, frases, "xyzqw") == []
    assert procurar_palavras(palavras, frases, "   ") == []
    destacadas = procurar_palavras(palavras, frases, "perdoar")[0].palavras
    assert [palavras[i].texto for i in destacadas] == ["perdoou"]


def test_sem_acento():
    assert sem_acento("Graça ÂNGELO") == "graca angelo"


class _NlpDeMentira:
    """Vetores de 2 dimensões: [5, 0] para o tema do perdão, [0, 1] para o resto."""

    TEMA = frozenset({"perdão", "perdao", "misericórdia", "culpa", "graça"})

    def make_doc(self, texto):
        def token(t):
            limpo = t.casefold().strip(".,")
            vetor = np.array([5.0, 0.0] if limpo in self.TEMA else [0.0, 1.0])
            return SimpleNamespace(
                is_alpha=limpo.isalpha(), is_stop=False, has_vector=True, vector=vetor
            )

        return [token(t) for t in texto.split()]


def test_busca_por_tema_acha_o_assunto_sem_a_palavra_exata():
    palavras = _palavras_de(
        "O clima estava frio naquele dia de inverno. "
        "A culpa pesava e a misericórdia de Deus alcançou o pecador. "
        "Depois seguimos para o almoço na casa do irmão. "
        "Falamos de futebol e de política a tarde inteira."
    )
    frases = separar_frases(palavras)
    achados = procurar_tema(
        palavras, frases, "perdão", _NlpDeMentira(), frases_por_janela=1, passo=1
    )
    assert achados and "misericórdia" in achados[0].texto
    faixas = [(a.primeira, a.ultima) for a in achados]  # as janelas escolhidas não se sobrepõem
    for i, (de, ate) in enumerate(faixas):
        assert all(ate < d or de > a for j, (d, a) in enumerate(faixas) if j != i)


# --- pedaços separados: "frase 1 + frase 3" -----------------------------------------------------


def test_trecho_de_indices_guarda_o_que_ficou_de_fora(sermao):
    _palavras, frases = sermao
    f1, f3 = frases[1], frases[3]
    escolhidas = [*range(f1.primeira, f1.ultima + 1), *range(f3.primeira, f3.ultima + 1)]
    t = Trecho.de_indices(escolhidas)
    assert (t.primeira, t.ultima) == (f1.primeira, f3.ultima)
    assert t.fora == frozenset(range(frases[2].primeira, frases[2].ultima + 1))
    assert t.indices == escolhidas
    assert t.pedacos == [(f1.primeira, f1.ultima), (f3.primeira, f3.ultima)]
    assert Trecho.de_indices([]) is None
    assert Trecho.de_indices([7, 8, 9]) == Trecho(7, 9)  # sem buracos é o trecho de sempre


def test_a_duracao_do_trecho_nao_conta_o_que_ficou_de_fora(sermao):
    palavras, frases = sermao
    inteiro = Trecho(frases[1].primeira, frases[5].ultima)
    sem_a_do_meio = Trecho.de_indices(
        i for i in inteiro.indices if not frases[3].primeira <= i <= frases[3].ultima
    )
    a_frase = frases[3].fim - frases[3].inicio
    assert duracao_do_trecho(palavras, sem_a_do_meio) < duracao_do_trecho(palavras, inteiro)
    assert duracao_do_trecho(palavras, inteiro) - duracao_do_trecho(
        palavras, sem_a_do_meio
    ) == pytest.approx(a_frase + 2 * 0.6, abs=0.3)  # a frase e as pausas vizinhas


def test_mover_ponta_pula_as_palavras_que_ficaram_de_fora(sermao):
    palavras, frases = sermao
    t = Trecho(10, 90, frozenset({11, 12}))
    assert mover_ponta(palavras, frases, t, "inicio", 1, por_frase=False).primeira == 13
    t = Trecho(10, 90, frozenset({88, 89}))
    assert mover_ponta(palavras, frases, t, "fim", -1, por_frase=False).ultima == 87
    longe = mover_ponta(
        palavras, frases, Trecho(10, 90, frozenset({50})), "fim", -1, por_frase=False
    )
    assert longe.fora == frozenset({50})  # o buraco do meio continua
    curto = mover_ponta(
        palavras, frases, Trecho(10, 90, frozenset({50})), "inicio", 1, por_frase=False
    )
    assert curto.fora == frozenset({50})
    # encurtar o fim até passar do buraco: o buraco some junto (já está fora do trecho)
    dentro = Trecho(10, 60, frozenset({50}))
    passou = mover_ponta(palavras, frases, dentro, "fim", -1, por_frase=True)
    assert passou.ultima < 60 and all(10 < i < passou.ultima for i in passou.fora)


def test_gerar_corte_livre_com_pedaco_de_fora_vira_corte_com_salto(sermao):
    palavras, frases = sermao
    cliente, chamadas = _cliente_falso()
    textos_vistos: list[str] = []

    def metadados(cliente, sermao, texto, categoria, duracao, titulos):
        textos_vistos.append(texto)
        return _metadados(cliente, sermao, texto, categoria, duracao, titulos)

    f = frases
    escolhidas = [i for k in (2, 3, 5, 6) for i in range(f[k].primeira, f[k].ultima + 1)]
    trecho = Trecho.de_indices(escolhidas)  # as frases 2, 3, 5 e 6: a 4 fica de fora
    corte = gerar_corte_livre(
        cliente, palavras=palavras, frases=frases, trecho=trecho, id_=ID_BASE_DOS_MANUAIS,
        sermao="Sermão de teste", criar_metadados=metadados,
    )  # fmt: skip
    assert corte.origem == ORIGEM_MANUAL and not corte.editado
    assert len(corte.saltos) == 1  # a frase 4 (e só ela) é o salto
    de, ate = corte.saltos[0]
    assert f[3].fim <= de < ate <= f[5].inicio
    sem = palavras[f[4].primeira].texto
    assert sem not in corte.texto.split() and palavras[f[5].primeira].texto in corte.texto
    assert textos_vistos == [corte.texto]  # o título é escrito sobre o que o Short fala
    assert corte.inicio <= palavras[f[2].primeira].inicio and corte.fim >= palavras[f[6].ultima].fim
    assert corte.duracao == pytest.approx(
        (corte.fim - corte.inicio) - (ate - de)
    )  # a duração já desconta o salto
    assert chamadas == ["avaliar"]


def test_pedacos_curtos_demais_juntos_continuam_pedindo_pelo_menos_dez_segundos(sermao):
    palavras, _frases = sermao
    t = Trecho.de_indices([*range(0, 6), *range(30, 36)])  # dois pedaços de ~2 s
    assert "pelo menos" in problema_do_trecho(palavras, t)
