"""Categorias de corte: as 17 chaves, rótulos, cores, o prompt e o esquema enviado à IA."""

from __future__ import annotations

import itertools
import math
import re

import pytest
from pydantic import ValidationError

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.shorts import etapas as etapas_mod
from spurgeon.shorts.dados import (
    CATEGORIAS,
    COR_DA_CATEGORIA,
    ROTULOS_CATEGORIA,
    Avaliacao,
    Corte,
    cor_da_categoria,
    rotulo_da_categoria,
)
from spurgeon.shorts.etapas import AnalisarShorts, AvaliarShorts, SelecionarShorts, carregar_cortes
from spurgeon.shorts.llm import (
    BLOCO_DE_CATEGORIAS,
    DEFINICOES_DAS_CATEGORIAS,
    PROMPT_AVALIACAO,
    SISTEMA,
    AvaliacaoLLM,
    ErroLLM,
)
from spurgeon.shorts.pontuacao import calcular_nota
from spurgeon.shorts.selecao import montar_cortes
from tests import test_shorts as base

ctx_shorts, ollama = base.ctx_shorts, base.ollama  # fixtures do arquivo de Shorts
rodar = base.rodar

ANTIGAS = ("pergunta_gancho", "citacao_biblica", "aplicacao_pratica", "conclusao", "outra")
NOVAS = {
    "confronto_de_pecados": "Confronto de pecados",
    "exortacao": "Exortação",
    "apontando_cristo": "Apontando Cristo",
    "necessidade_de_arrependimento": "Necessidade de arrependimento",
    "doutrina_biblica": "Doutrina bíblica",
    "doutrina_reformada": "Doutrina reformada",
    "fato_historico": "Fato histórico",
    "oracao": "Oração",
    "profecia": "Profecia",
    "promessa": "Promessa",
    "anuncio_de_salvacao": "Anúncio de salvação",
    "esperanca": "Esperança",
}


def resposta(categoria: str, **mudancas) -> dict:
    return {
        "categoria": categoria,
        "nota": 8,
        "autossuficiente": True,
        "alerta_contexto": "",
        "motivo": "Ideia central completa.",
        **mudancas,
    }


# --- a lista de categorias -----------------------------------------------------------------


def test_sao_17_categorias_sem_repeticao_e_as_antigas_continuam_la():
    assert len(CATEGORIAS) == 17 == len(set(CATEGORIAS))
    assert set(CATEGORIAS) == set(ANTIGAS) | set(NOVAS)
    assert CATEGORIAS[:4] == (
        "pergunta_gancho",
        "citacao_biblica",
        "aplicacao_pratica",
        "conclusao",
    )
    assert CATEGORIAS[-1] == "outra"  # a ordem é fixa: as novas ficam entre as antigas e `outra`
    assert tuple(NOVAS) == CATEGORIAS[4:-1]


def test_chaves_sao_minusculas_sem_acento():
    for chave in CATEGORIAS:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*", chave), chave


def test_rotulos_exatos_das_doze_novas_e_das_antigas():
    for chave, rotulo in NOVAS.items():
        assert ROTULOS_CATEGORIA[chave] == rotulo
    assert ROTULOS_CATEGORIA["pergunta_gancho"] == "Pergunta-gancho"
    assert ROTULOS_CATEGORIA["citacao_biblica"] == "Citação bíblica"
    assert ROTULOS_CATEGORIA["aplicacao_pratica"] == "Aplicação prática"
    assert ROTULOS_CATEGORIA["conclusao"] == "Conclusão"
    assert ROTULOS_CATEGORIA["outra"] == "Outra"


def test_todas_as_tabelas_tem_exatamente_as_mesmas_chaves():
    for tabela in (ROTULOS_CATEGORIA, COR_DA_CATEGORIA, DEFINICOES_DAS_CATEGORIAS):
        assert set(tabela) == set(CATEGORIAS)
    rotulos = list(ROTULOS_CATEGORIA.values())
    assert len(rotulos) == len(set(rotulos))


def test_rotulo_da_categoria():
    assert rotulo_da_categoria("apontando_cristo") == "Apontando Cristo"
    assert rotulo_da_categoria("outra") == "Outra"
    assert rotulo_da_categoria("categoria_do_futuro") == "Categoria_do_futuro"  # a própria chave
    assert rotulo_da_categoria("aplicacao") == "Aplicacao"
    assert rotulo_da_categoria("") == ""


# --- cores ---------------------------------------------------------------------------------


def _linear(canal: int) -> float:
    c = canal / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(cor: str) -> tuple[int, int, int]:
    return int(cor[1:3], 16), int(cor[3:5], 16), int(cor[5:7], 16)


def _luminancia(cor: str) -> float:
    r, g, b = (_linear(c) for c in _rgb(cor))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste_com_branco(cor: str) -> float:
    return 1.05 / (_luminancia(cor) + 0.05)


def _lab(cor: str) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in _rgb(cor))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def test_cada_categoria_tem_uma_cor_hex_so_dela():
    cores = [cor_da_categoria(c) for c in CATEGORIAS]
    assert len(cores) == len(set(cores)) == 17
    for cor in cores:
        assert re.fullmatch(r"#[0-9a-f]{6}", cor), cor


def test_cor_tem_contraste_de_pelo_menos_3_para_1_com_texto_branco():
    for chave in CATEGORIAS:
        assert _contraste_com_branco(cor_da_categoria(chave)) >= 3.0, chave


def test_cores_sao_bem_distinguiveis_entre_si():
    pares = [
        (math.dist(_lab(a), _lab(b)), ca, cb)
        for (ca, a), (cb, b) in itertools.combinations(COR_DA_CATEGORIA.items(), 2)
    ]
    menor, ca, cb = min(pares)
    assert menor >= 15.0, f"{ca} e {cb} são parecidas demais (distância {menor:.1f})"


def test_nenhuma_cor_e_cinza_exceto_a_de_outra():
    for chave, cor in COR_DA_CATEGORIA.items():
        if chave != "outra":
            r, g, b = _rgb(cor)
            assert max(r, g, b) - min(r, g, b) >= 60, chave


def test_categoria_desconhecida_usa_a_cor_de_outra():
    assert cor_da_categoria("categoria_do_futuro") == cor_da_categoria("outra")
    assert cor_da_categoria("") == COR_DA_CATEGORIA["outra"]
    assert cor_da_categoria("aplicacao") == COR_DA_CATEGORIA["outra"]  # chave que nunca existiu
    assert cor_da_categoria("profecia") != cor_da_categoria("outra")


# --- o esquema e a validação ---------------------------------------------------------------


def test_o_literal_da_resposta_aceita_as_17():
    enum = AvaliacaoLLM.model_json_schema()["properties"]["categoria"]["enum"]
    assert enum == list(CATEGORIAS) and len(enum) == 17
    for chave in CATEGORIAS:
        assert AvaliacaoLLM.model_validate(resposta(chave)).categoria == chave


@pytest.mark.parametrize(
    "invalida", ["sermao_inventado", "Exortação", "exortação", "", "aplicacao"]
)
def test_categoria_fora_da_lista_e_recusada(invalida):
    with pytest.raises(ValidationError):
        AvaliacaoLLM.model_validate(resposta(invalida))


def test_o_esquema_enviado_ao_ollama_lista_as_17_como_enum():
    falso = base.OllamaFalso([resposta("promessa")])
    base.cliente(falso).avaliar("", "x", "", 40)
    esquema = falso.chamadas[0]["format"]
    assert esquema["properties"]["categoria"]["enum"] == list(CATEGORIAS)
    assert "categoria" in esquema["required"]


@pytest.mark.parametrize("chave", list(NOVAS))
def test_resposta_com_categoria_nova_vira_avaliacao(chave):
    falso = base.OllamaFalso([resposta(chave, nota=9, alerta_contexto=" cuidado ")])
    av = base.cliente(falso).avaliar("antes", "o trecho", "depois", 50.0)
    assert av == Avaliacao(chave, 9, True, "cuidado", "Ideia central completa.")
    assert len(falso.chamadas) == 1


def test_categoria_inventada_e_recusada_e_tenta_de_novo():
    falso = base.OllamaFalso([resposta("sermao_inventado"), resposta("exortacao", nota=6)])
    av = base.cliente(falso).avaliar("", "x", "", 40)
    assert av.categoria == "exortacao" and av.nota == 6
    assert len(falso.chamadas) == 2
    temperaturas = [c["options"]["temperature"] for c in falso.chamadas]
    assert temperaturas[0] < temperaturas[1]


def test_tres_categorias_inventadas_levantam_erro_da_ia():
    falso = base.OllamaFalso([resposta("inventada")] * 3)
    with pytest.raises(ErroLLM):
        base.cliente(falso).avaliar("", "x", "", 40)
    assert len(falso.chamadas) == 3


# --- o prompt ------------------------------------------------------------------------------


def _prompt_enviado(antes="", trecho="o trecho", depois="", duracao=45.0) -> str:
    falso = base.OllamaFalso([resposta("outra")])
    base.cliente(falso).avaliar(antes, trecho, depois, duracao)
    return falso.chamadas[0]["messages"][1]["content"]


def test_prompt_define_cada_categoria_em_uma_linha_na_ordem_da_lista():
    prompt = _prompt_enviado()
    assert "{" not in prompt and "}" not in prompt  # nenhum campo ficou sem preencher
    posicoes = []
    for chave in CATEGORIAS:
        linha = f'  "{chave}": {DEFINICOES_DAS_CATEGORIAS[chave]}'
        assert linha in prompt.splitlines()
        posicoes.append(prompt.index(linha))
    assert posicoes == sorted(posicoes)
    assert len(BLOCO_DE_CATEGORIAS.splitlines()) == 17


def test_prompt_pede_a_categoria_que_melhor_descreve_e_mantem_o_resto():
    prompt = _prompt_enviado(trecho="TRECHO-DE-TESTE")
    assert "UMA" in prompt and "MELHOR" in prompt and "ideia central" in prompt
    assert "TRECHO-DE-TESTE" in prompt
    for campo in ("- nota:", "- autossuficiente:", "- alerta_contexto:", "- motivo:"):
        assert campo in prompt
    assert prompt.index("- categoria:") < prompt.index("- nota:")


def test_definicoes_nao_trazem_exemplos_de_frases_nem_de_titulos():
    for chave, texto in DEFINICOES_DAS_CATEGORIAS.items():
        assert texto == texto.strip() and texto, chave
        assert not any(aspas in texto for aspas in ('"', "“", "”", "«", "'")), chave
        assert "por exemplo" not in texto.lower() and "como em" not in texto.lower(), chave


def test_prompt_com_o_pior_caso_cabe_com_folga_no_contexto_de_4096_tokens():
    # 180 s de fala rápida: ~4,5 palavras/s de ~7 letras; as janelas de contexto da etapa
    trecho = " ".join(["palavra"] * 810)
    prompt = _prompt_enviado("a" * 300, trecho, "b" * 200, 180.0)
    total = len(SISTEMA) + len(prompt)
    # português gasta ~3 caracteres por token no Qwen; separa 700 para a resposta e a margem
    assert total / 3 <= 4096 - 700, f"{total} caracteres, ~{total / 3:.0f} tokens"
    assert len(PROMPT_AVALIACAO) + len(BLOCO_DE_CATEGORIAS) < 3600  # o que não é trecho


# --- compatibilidade: nada do que já foi feito se perde ------------------------------------


def test_avaliacao_gravada_com_categoria_antiga_continua_carregando():
    for chave in ANTIGAS:
        gravada = {
            "categoria": chave,
            "nota": 8,
            "autossuficiente": True,
            "alerta_contexto": "",
            "motivo": "Antiga.",
        }
        av = Avaliacao.de_dict(gravada)
        assert av.categoria == chave and av.para_dict() == gravada
        assert chave in CATEGORIAS


def test_arquivo_de_avaliacoes_com_categorias_antigas_e_novas_e_relido(tmp_path):
    caminho = tmp_path / "avaliacoes.json"
    candidatos = [base.candidato(i, i * 60.0, i * 60.0 + 50) for i in range(6)]
    antigas = {c.id: Avaliacao(ANTIGAS[c.id % 5], 7, True, "", "Antiga.") for c in candidatos[:5]}
    etapas_mod._gravar_avaliacoes(caminho, "qwen3:8b", antigas, candidatos)
    # uma avaliação nova (categoria nova) soma-se às antigas sem tirar nenhuma
    nova = Avaliacao("profecia", 9, True, "", "Nova.")
    etapas_mod._gravar_avaliacoes(caminho, "qwen3:8b", {**antigas, 5: nova}, candidatos)
    lidas = etapas_mod._ler_avaliacoes(caminho, "qwen3:8b", candidatos)
    assert {i: a.categoria for i, a in lidas.items()} == {
        **{c.id: ANTIGAS[c.id % 5] for c in candidatos[:5]},
        5: "profecia",
    }
    assert ler_json(caminho)["versao"] == 2


def test_formato_por_id_dos_projetos_antigos_continua_valendo(tmp_path):
    caminho = tmp_path / "avaliacoes.json"
    candidatos = [base.candidato(i, i * 60.0, i * 60.0 + 50) for i in range(3)]
    escrever_json_atomico(
        caminho,
        {
            "modelo": "qwen3:8b",
            "avaliacoes": {
                str(c.id): Avaliacao("pergunta_gancho", 8, True, "", "").para_dict()
                for c in candidatos
            },
        },
    )
    lidas = etapas_mod._ler_avaliacoes(caminho, "qwen3:8b", candidatos)
    assert {a.categoria for a in lidas.values()} == {"pergunta_gancho"} and len(lidas) == 3


def test_corte_gravado_com_categoria_antiga_continua_carregando():
    c = Corte(
        id=1, inicio=0.0, fim=40.0, texto="t", categoria="citacao_biblica", nota=80.0, detalhe={}
    )
    assert Corte.de_dict(c.para_dict()).categoria == "citacao_biblica"
    assert rotulo_da_categoria(c.categoria) == "Citação bíblica"


def test_a_nota_nao_depende_da_categoria():
    c = base.candidato()
    resultados = [calcular_nota(c, base.avaliacao(categoria=chave)) for chave in CATEGORIAS]
    assert len(resultados) == 17
    assert all(r == resultados[0] for r in resultados)  # a mesma nota e o mesmo detalhe


def test_selecao_mantem_a_categoria_nova_no_corte():
    c = base.candidato(0, 0.0, 50.0)
    avaliacoes = {0: base.avaliacao(categoria="anuncio_de_salvacao")}
    cortes = montar_cortes([c], avaliacoes, limite=5, nota_minima=0)
    assert [x.categoria for x in cortes] == ["anuncio_de_salvacao"]


def test_trocar_o_prompt_nao_invalida_as_avaliacoes_ja_feitas(ctx_shorts, ollama, monkeypatch):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)  # a IA de mentira responde "aplicacao_pratica"
    assert len(ollama.chamadas) == 6
    guardadas = ler_json(ctx_shorts.pastas.avaliacoes)
    ollama.chamadas.clear()
    ollama.respostas = [resposta("profecia")] * 6  # outra opinião, se perguntassem de novo
    monkeypatch.setattr("spurgeon.shorts.llm.PROMPT_AVALIACAO", "outro prompt {categorias}")
    rodar(AvaliarShorts(), ctx_shorts)
    assert ollama.chamadas == []  # o cache vale por trecho e modelo, não pelo texto do prompt
    assert ler_json(ctx_shorts.pastas.avaliacoes) == guardadas


def test_etapas_levam_a_categoria_nova_da_avaliacao_ate_o_corte(ctx_shorts, ollama):
    padrao = ollama.resposta_padrao

    def com_categoria_nova(kwargs):
        r = padrao(kwargs)
        return {**r, "categoria": "necessidade_de_arrependimento"} if "categoria" in r else r

    ollama.resposta_padrao = com_categoria_nova
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    avaliacoes = ler_json(ctx_shorts.pastas.avaliacoes)["avaliacoes"]
    assert {v["categoria"] for v in avaliacoes.values()} == {"necessidade_de_arrependimento"}
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    assert cortes and {c.categoria for c in cortes} == {"necessidade_de_arrependimento"}
