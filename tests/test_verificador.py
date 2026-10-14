"""Verificador de português (offline): dicionário brasileiro, nomes, repetição, pontuação."""

from __future__ import annotations

import pytest

pytest.importorskip("spylls")
pytest.importorskip("spellchecker")

from spurgeon.transcricao.dicionario import DicionarioBr
from spurgeon.transcricao.verificador import Verificador
from spurgeon.transcricao.vocabulario import (
    acrescentar_ao_vocabulario,
    carregar_vocabulario,
)

VOCABULARIO = ["Spurgeon", "Calvino", "Miqueias", "Jonathan Edwards", "predestinação"]


@pytest.fixture
def verificador(dicionario_br) -> Verificador:
    return Verificador(VOCABULARIO, dicionario=dicionario_br)


def achar(verificador: Verificador, *linhas: str, tipo: str | None = None):
    problemas = verificador.verificar(list(linhas))
    return [p for p in problemas if tipo is None or p.tipo == tipo]


def test_aceita_as_formas_brasileiras(verificador):
    texto = "Foi incômodo perceber aspectos da decepção e da percepção conosco, disse Antônio."
    assert achar(verificador, texto) == []


def test_aceita_flexoes_de_verbos_e_pronomes_ligados(verificador):
    assert achar(verificador, "Nós precisávamos vê-lo, fazê-lo e ouvi-los.") == []


def test_nomes_do_vocabulario_nao_sao_erro(verificador):
    assert achar(verificador, "Spurgeon citou Calvino e Jonathan Edwards.") == []


def test_aponta_palavra_inexistente_com_sugestoes(verificador):
    [problema] = achar(verificador, "Ele estava amusando de tudo.", tipo="ortografia")
    assert problema.trecho == "amusando"
    assert "abusando" in problema.sugestoes or "acusando" in problema.sugestoes
    texto = "Ele estava amusando de tudo."
    assert texto[problema.inicio : problema.fim] == "amusando"


def test_sugere_a_forma_brasileira_acentuada(verificador):
    [problema] = achar(verificador, "Foi uma boa avaliaçao do texto.", tipo="ortografia")
    assert "avaliação" in problema.sugestoes


def test_grafia_de_portugal_e_apontada(verificador):
    [problema] = achar(verificador, "Foi uma perceção forte.", tipo="ortografia")
    assert problema.trecho == "perceção"


def test_palavra_repetida_muitas_vezes_e_nome(verificador):
    texto = "Blimbrado liderou. Blimbrado voltou. Depois, Blimbrado orou."
    assert achar(verificador, texto, tipo="ortografia") == []


def test_palavra_desconhecida_que_aparece_uma_vez_e_erro(verificador):
    assert [p.trecho for p in achar(verificador, "O blimbrado voltou.", tipo="ortografia")] == [
        "blimbrado"
    ]


def test_repeticao_seguida(verificador):
    [problema] = achar(verificador, "Nós nós não podemos esquecer.", tipo="repeticao")
    assert problema.trecho == "Nós nós"
    assert problema.sugestoes == ("Nós",)


def test_repeticao_com_pontuacao_no_meio_nao_conta(verificador):
    assert achar(verificador, "Era muito, muito bom. Bom, bom!", tipo="repeticao") == []


def test_frase_em_minuscula_depois_do_ponto(verificador):
    texto = "Ele disse isso. e depois saiu."
    [problema] = achar(verificador, texto, tipo="maiuscula")
    assert texto[problema.inicio : problema.fim] == "e"
    assert problema.sugestoes == ("E",)


def test_frase_em_minuscula_entre_segmentos(verificador):
    [problema] = achar(verificador, "Quem é o pastor?", "o pastor é Cristo.", tipo="maiuscula")
    assert problema.linha == 1


def test_abreviacao_nao_encerra_a_frase(verificador):
    assert achar(verificador, "O sr. silva e a dra. maria chegaram.", tipo="maiuscula") == []


def test_nome_de_deus_em_minuscula(verificador):
    [problema] = achar(verificador, "Louvamos a deus pela sua graça.", tipo="maiuscula")
    assert problema.sugestoes == ("Deus",)


def test_pontuacao_solta(verificador):
    texto = "Ele veio , e viu ."
    problemas = achar(verificador, texto, tipo="pontuacao")
    assert [texto[p.inicio : p.fim] for p in problemas] == [" ,", " ."]
    assert [p.sugestoes for p in problemas] == [(",",), (".",)]


def test_hifen_solto_do_pronome(verificador):
    texto = "Precisamos chamá -las pelo nome."
    [problema] = achar(verificador, texto, tipo="pontuacao")
    assert texto[problema.inicio : problema.fim] == " -las"
    assert problema.sugestoes == ("-las",)


def test_parentese_sem_par(verificador):
    [problema] = achar(verificador, "Veja o texto (aqui para todos.", tipo="pontuacao")
    assert problema.mensagem == "Parêntese sem par"


def test_ignorar_uma_palavra_nao_avisa_de_novo(verificador):
    [problema] = achar(verificador, "O blimbrado voltou.", tipo="ortografia")
    verificador.ignorar(problema)
    assert achar(verificador, "O blimbrado voltou.", tipo="ortografia") == []


def test_aceitar_palavra_no_vocabulario(verificador):
    verificador.aceitar_palavra("Blimbrado")
    assert achar(verificador, "O blimbrado voltou.", tipo="ortografia") == []


def test_sugestoes_a_fundo_usam_o_hunspell(verificador):
    sugestoes = verificador.sugerir("encontrei", a_fundo=False)
    assert sugestoes == ()  # palavra certa: nada a sugerir
    fundo = verificador.sugerir("percepsão", a_fundo=True)
    assert "percepção" in fundo


def test_sem_os_arquivos_do_hunspell_usa_so_o_dicionario_simples(tmp_path):
    simples = Verificador([], dicionario=DicionarioBr(tmp_path / "nao_existe" / "pt_BR"))
    assert not simples.dicionario.disponivel
    assert simples.verificar(["Isso é uma frase normal."]) == []


def test_acrescentar_ao_vocabulario(tmp_path):
    arquivo = tmp_path / "vocabulario.txt"
    arquivo.write_text("# termos\nSpurgeon", encoding="utf-8")  # sem quebra no fim
    assert acrescentar_ao_vocabulario(arquivo, "Blimbrado") is True
    assert acrescentar_ao_vocabulario(arquivo, "blimbrado") is False  # já existe
    assert carregar_vocabulario(arquivo) == ["Spurgeon", "Blimbrado"]
    novo = tmp_path / "novo" / "vocabulario.txt"
    assert acrescentar_ao_vocabulario(novo, "Yavé") is True
    assert carregar_vocabulario(novo) == ["Yavé"]
