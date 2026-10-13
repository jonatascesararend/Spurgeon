"""Tela de revisão da transcrição: funções puras e o painel de verdade (janela escondida)."""

import tkinter

import pytest

from spurgeon.core.projeto import PastasProjeto
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao
from spurgeon.ui import dialogos
from spurgeon.ui.revisao import PAINEIS
from spurgeon.ui.revisao_transcricao import (
    ErroDeLinhas,
    PainelRevisaoTranscricao,
    ler_linhas,
    montar_linhas,
    trocar_no_texto,
)


def segmento(texto: str, inicio: float, prob: float = 0.95) -> Segmento:
    palavras = [
        Palavra(t, inicio + i * 0.4, inicio + i * 0.4 + 0.35, prob)
        for i, t in enumerate(texto.split())
    ]
    return Segmento(palavras)


@pytest.fixture
def transcricao() -> Transcricao:
    return Transcricao(
        "tiny",
        200.0,
        blocos=[
            Bloco(
                0.0,
                200.0,
                [segmento("A graça de Deus", 3.0), segmento("Espurgeon disse isto", 125.0, 0.4)],
                True,
            )
        ],
    )


# --- funções puras -------------------------------------------------------------------------


def test_cada_segmento_vira_uma_linha_com_horario(transcricao):
    linhas = montar_linhas(transcricao)
    assert [marca for marca, _ in linhas] == ["[0:03] ", "[2:05] "]
    assert [t for t, _ in linhas[0][1]] == ["A", "graça", "de", "Deus"]


def test_palavrinhas_curtas_nao_poluem_os_destaques():
    # "e", "o" e "se" têm confiança baixa o tempo todo sem estarem erradas; "senhor" é um erro
    # real (deveria ser "Senhor") e precisa aparecer.
    t = Transcricao(
        "tiny",
        10.0,
        blocos=[
            Bloco(
                0.0,
                10.0,
                [
                    Segmento(
                        [
                            Palavra("e", 0.0, 0.1, 0.1),
                            Palavra("o", 0.1, 0.2, 0.1),
                            Palavra("senhor", 0.2, 0.6, 0.27),
                        ]
                    )
                ],
                True,
            )
        ],
    )
    _, palavras = montar_linhas(t)[0]
    assert [baixa for _, baixa in palavras] == [False, False, True]


def test_palavras_de_baixa_confianca_sao_marcadas_mas_as_ja_corrigidas_nao(transcricao):
    transcricao.blocos[0].segmentos[1].palavras[0].original = "Espurgeon"  # corrigida
    _, palavras = montar_linhas(transcricao)[1]
    assert [baixa for _, baixa in palavras] == [False, True, True]  # "Espurgeon" saiu da lista


def test_ler_linhas_devolve_o_texto_sem_o_horario():
    conteudo = "[0:03] A graça de Deus\n[2:05] Spurgeon disse isto\n"
    assert ler_linhas(conteudo, 2) == ["A graça de Deus", "Spurgeon disse isto"]


def test_ler_linhas_com_texto_apagado_devolve_vazio_para_aquele_trecho():
    assert ler_linhas("[0:03] \n[2:05] ok", 2) == ["", "ok"]


def test_linha_a_mais_ou_a_menos_e_recusada_com_explicacao():
    with pytest.raises(ErroDeLinhas, match="2 trechos, mas o texto tem 3 linhas"):
        ler_linhas("[0:03] a\n[2:05] b\nc", 2)
    with pytest.raises(ErroDeLinhas, match="não divida nem junte"):
        ler_linhas("[0:03] só uma", 2)


def test_linha_sem_horario_e_recusada_dizendo_qual():
    with pytest.raises(ErroDeLinhas, match="linha 2 perdeu o"):
        ler_linhas("[0:03] a\nsem horário", 2)


def test_horario_com_horas_tambem_vale():
    assert ler_linhas("[1:02:05] texto", 1) == ["texto"]


def test_trocar_no_texto_nao_mexe_no_horario_e_mantem_a_inicial_maiuscula():
    conteudo = "[1:05] Espurgeon e espurgeon\n[2:10] Espurgeonista"
    novo, total = trocar_no_texto(conteudo, "espurgeon", "Spurgeon")
    assert novo == "[1:05] Spurgeon e Spurgeon\n[2:10] Espurgeonista"  # só a palavra inteira
    assert total == 2


def test_trocar_por_minuscula_preserva_maiuscula_de_inicio():
    novo, _ = trocar_no_texto("[0:01] Graca e graca", "graca", "graça")
    assert novo == "[0:01] Graça e graça"


def test_trocar_palavra_ausente_ou_vazia_nao_faz_nada():
    assert trocar_no_texto("[0:01] ok", "xyz", "abc") == ("[0:01] ok", 0)
    assert trocar_no_texto("[0:01] ok", "  ", "abc") == ("[0:01] ok", 0)


def test_o_painel_da_transcricao_esta_registrado():
    assert "revisar_transcricao" in PAINEIS


# --- painel de verdade ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def raiz():
    import customtkinter as ctk

    try:
        janela = ctk.CTk()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


@pytest.fixture
def aberto(raiz, transcricao, tmp_path, monkeypatch):
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    transcricao.salvar(pastas.transcricao)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dialogos, "mostrar", lambda _j, titulo, msg, **_k: avisos.append((titulo, msg)) or "OK"
    )
    painel = PainelRevisaoTranscricao(raiz, pastas, modal=False)
    painel.avisos = avisos
    painel.pastas = pastas
    yield painel
    if painel.winfo_exists():
        painel.destroy()


@pytest.mark.gui
def test_painel_mostra_um_trecho_por_linha(aberto):
    conteudo = aberto._texto._textbox.get("1.0", "end-1c").rstrip("\n")
    assert conteudo.split("\n") == ["[0:03] A graça de Deus", "[2:05] Espurgeon disse isto"]


@pytest.mark.gui
def test_painel_marca_em_laranja_so_o_que_o_whisper_duvidou(aberto):
    caixa = aberto._texto._textbox
    marcadas = caixa.tag_ranges("baixa")
    assert marcadas  # as 3 palavras do segundo trecho
    textos = [caixa.get(marcadas[i], marcadas[i + 1]) for i in range(0, len(marcadas), 2)]
    assert " ".join(textos).split() == ["Espurgeon", "disse", "isto"]


@pytest.mark.gui
def test_corrigir_e_salvar_grava_no_arquivo_e_marca_como_revisada(aberto):
    caixa = aberto._texto._textbox
    posicao = caixa.search("Espurgeon", "1.0")
    caixa.delete(posicao, f"{posicao}+9c")
    caixa.insert(posicao, "Spurgeon")
    assert aberto.salvar_e_fechar() is True

    gravada = Transcricao.carregar(aberto.pastas.transcricao)
    assert gravada.revisada
    assert gravada.segmentos[1].texto == "Spurgeon disse isto"
    assert gravada.segmentos[1].palavras[0].original == "Espurgeon"
    assert gravada.segmentos[1].palavras[0].inicio == pytest.approx(125.0)  # tempo preservado


@pytest.mark.gui
def test_salvar_sem_mudar_nada_nao_reescreve_o_arquivo(aberto):
    antes = aberto.pastas.transcricao.stat().st_mtime_ns
    assert aberto.salvar_e_fechar() is True
    assert aberto.pastas.transcricao.stat().st_mtime_ns == antes
    assert aberto.alteracoes == 0


@pytest.mark.gui
def test_juntar_linhas_nao_deixa_salvar_e_explica(aberto):
    caixa = aberto._texto._textbox
    caixa.delete("1.end", "2.0")  # apaga a quebra entre as duas linhas
    assert aberto.salvar_e_fechar() is False
    assert aberto.winfo_exists()  # continua aberto para a pessoa corrigir
    assert "não divida nem junte" in aberto.avisos[-1][1]


@pytest.mark.gui
def test_trocar_em_tudo_pelo_painel(aberto):
    aberto._busca.insert(0, "espurgeon")
    aberto._troca.insert(0, "Spurgeon")
    aberto._trocar_em_tudo()
    assert "[2:05] Spurgeon disse isto" in aberto._texto._textbox.get("1.0", "end-1c")
    assert aberto._contagem.cget("text") == "1 troca"


@pytest.mark.gui
def test_cancelar_sem_editar_fecha_direto_sem_perguntar(aberto):
    aberto._cancelar()
    assert not aberto.winfo_exists()
    assert aberto.avisos == []


@pytest.mark.gui
def test_cancelar_depois_de_editar_pergunta_e_nao_descarta_se_a_pessoa_recusar(aberto, monkeypatch):
    aberto._texto._textbox.insert("1.end", " alterado")
    monkeypatch.setattr(dialogos, "confirmar", lambda *_a, **_k: False)
    aberto._cancelar()
    assert aberto.winfo_exists()  # continua aberto: a pessoa disse "continuar editando"
    monkeypatch.setattr(dialogos, "confirmar", lambda *_a, **_k: True)
    aberto._cancelar()
    assert not aberto.winfo_exists()
    # e nada foi gravado
    assert not Transcricao.carregar(aberto.pastas.transcricao).revisada


@pytest.mark.gui
def test_busca_conta_e_marca_os_achados(aberto):
    aberto._busca.insert(0, "isto")
    assert aberto._marcar_achados() == 1
    assert aberto._texto._textbox.tag_ranges("achado")
