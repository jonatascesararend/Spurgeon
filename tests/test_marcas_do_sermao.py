"""Os trechos que a IA avaliou (com a categoria) e os Shorts já feitos, para marcar o texto."""

from __future__ import annotations

from dataclasses import replace

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts.analise import gerar_candidatos, separar_frases
from spurgeon.shorts.dados import Avaliacao
from spurgeon.shorts.etapas import marca_do_candidato
from spurgeon.shorts.marcas import (
    Detectado,
    categoria_de_cada_frase,
    palavras_do_intervalo,
    short_de_cada_palavra,
    shorts_feitos,
    trechos_detectados,
)
from tests.test_shorts import cortes_do_sermao, sermao_falso


@pytest.fixture
def sermao():
    palavras = sermao_falso(frases=60)
    return palavras, separar_frases(palavras)


@pytest.fixture
def projeto(tmp_path, sermao) -> PastasProjeto:
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    return pastas


def _gravar_analise(pastas, palavras, frases, categorias, *, versao=2):
    """Grava `candidatos.json` e `avaliacoes.json` como a análise faz; devolve os candidatos."""
    candidatos = gerar_candidatos(palavras, frases)
    escrever_json_atomico(
        pastas.candidatos,
        {"frases": [], "candidatos": [c.para_dict() for c in candidatos]},
        indent=None,
    )
    avaliacoes = {}
    for c, (categoria, nota) in zip(candidatos, categorias, strict=False):
        chave = marca_do_candidato(c) if versao >= 2 else str(c.id)
        avaliacoes[chave] = Avaliacao(categoria, nota, True, "", "").para_dict()
    escrever_json_atomico(
        pastas.avaliacoes,
        {"versao": versao, "modelo": "m", "avaliacoes": avaliacoes},
        indent=None,
    )
    return candidatos


def test_o_intervalo_de_tempo_vira_palavras(sermao):
    palavras, _frases = sermao
    assert palavras_do_intervalo(palavras, palavras[12].inicio, palavras[40].fim) == (12, 40)
    assert palavras_do_intervalo(palavras, palavras[12].inicio + 0.01, palavras[40].fim) == (12, 40)
    assert palavras_do_intervalo(palavras, 9999.0, 10000.0) is None


def test_so_os_candidatos_avaliados_viram_trechos_detectados(projeto, sermao):
    palavras, frases = sermao
    candidatos = _gravar_analise(
        projeto, palavras, frases, [("exortacao", 8), ("profecia", 6), ("oracao", 9)]
    )
    achados = trechos_detectados(projeto, palavras)
    assert [d.categoria for d in achados] == ["exortacao", "profecia", "oracao"]
    primeiro = achados[0]
    alvo = candidatos[0]
    assert palavras[primeiro.primeira].inicio == pytest.approx(alvo.inicio, abs=0.06)
    assert palavras[primeiro.ultima].fim == pytest.approx(alvo.fim, abs=0.06)
    assert primeiro.nota == 8 and len(achados) < len(candidatos)


def test_projeto_antigo_com_avaliacoes_por_id_tambem_vale(projeto, sermao):
    palavras, frases = sermao
    _gravar_analise(projeto, palavras, frases, [("esperanca", 7)], versao=1)
    assert [d.categoria for d in trechos_detectados(projeto, palavras)] == ["esperanca"]


def test_sem_analise_ou_com_arquivo_estragado_nao_ha_trechos(projeto, sermao):
    palavras, frases = sermao
    assert trechos_detectados(projeto, palavras) == []
    _gravar_analise(projeto, palavras, frases, [("exortacao", 8)])
    projeto.avaliacoes.write_text("{isto não é json", encoding="utf-8")
    assert trechos_detectados(projeto, palavras) == []
    assert trechos_detectados(projeto, []) == []


def test_cada_frase_leva_a_categoria_do_trecho_de_maior_nota_que_a_cobre(sermao):
    _palavras, frases = sermao
    amplo = Detectado(frases[2].primeira, frases[8].ultima, "doutrina_biblica", 6)
    melhor = Detectado(frases[4].primeira, frases[5].ultima, "exortacao", 9)
    por_frase = categoria_de_cada_frase([amplo, melhor], frases)
    assert {por_frase[k].categoria for k in (2, 3, 6, 7, 8)} == {"doutrina_biblica"}
    assert {por_frase[k].categoria for k in (4, 5)} == {"exortacao"}  # a nota maior vence
    assert 1 not in por_frase and 9 not in por_frase  # ninguém cobre


def test_com_a_mesma_nota_vale_o_trecho_mais_curto(sermao):
    _palavras, frases = sermao
    longo = Detectado(frases[2].primeira, frases[9].ultima, "conclusao", 7)
    curto = Detectado(frases[4].primeira, frases[4].ultima, "promessa", 7)
    por_frase = categoria_de_cada_frase([longo, curto], frases)
    assert por_frase[4].categoria == "promessa" and por_frase[5].categoria == "conclusao"


def test_um_trecho_que_cobre_so_parte_da_frase_marca_a_frase_toda(sermao):
    _palavras, frases = sermao
    meio = Detectado(frases[3].primeira + 4, frases[3].ultima - 2, "fato_historico", 5)
    assert list(categoria_de_cada_frase([meio], frases)) == [3]


def test_os_shorts_feitos_dizem_quais_palavras_falam_sem_os_saltos(sermao):
    palavras, _frases = sermao
    cortes = cortes_do_sermao(palavras)
    primeiro = cortes[0]
    feitos = shorts_feitos(palavras, cortes)
    inteiro = feitos[0]
    assert inteiro.id == primeiro.id and inteiro.categoria == primeiro.categoria
    meio = sorted(inteiro.indices)[20:24]
    de, ate = palavras[meio[0]].inicio - 0.02, palavras[meio[-1]].fim + 0.02
    com_salto = replace(primeiro, saltos=[[de, ate]])
    sem = shorts_feitos(palavras, [com_salto])[0]
    assert sem.indices == inteiro.indices - set(meio)


def test_a_palavra_de_dois_shorts_fica_com_o_primeiro_da_lista(sermao):
    palavras, _frases = sermao
    cortes = cortes_do_sermao(palavras)
    a = replace(cortes[0], id=1, titulo="A")
    b = replace(cortes[0], id=2, titulo="B")
    por_palavra = short_de_cada_palavra(shorts_feitos(palavras, [a, b]))
    assert {f.titulo for f in por_palavra.values()} == {"A"}
    assert short_de_cada_palavra(shorts_feitos(palavras, [b, a]))[min(por_palavra)].titulo == "B"
