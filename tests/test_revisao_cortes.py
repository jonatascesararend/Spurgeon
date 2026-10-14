"""Janela de aprovação dos cortes dos Shorts (janela escondida)."""

import contextlib
import tkinter

import pytest

from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.ui import dialogos
from spurgeon.ui.revisao import PAINEIS, abrir_revisao
from spurgeon.ui.revisao_cortes import (
    PainelAprovacaoCortes,
    formatar_nota,
    formatar_total,
)
from tests.test_shorts import cortes_do_sermao, gravar_transcricao, sermao_falso


def test_o_painel_dos_cortes_esta_registrado():
    assert "aprovar_cortes" in PAINEIS


def test_formatacao_da_tela():
    assert formatar_nota(71.6) == "72"
    assert formatar_total(125) == "2 min 05 s"
    assert formatar_total(42) == "42 s"


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
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    palavras = sermao_falso(frases=100)
    gravar_transcricao(pastas.transcricao, palavras)
    cortes = cortes_do_sermao(palavras)
    for n, c in enumerate(cortes, start=1):
        c.titulo = f"Título {n}"
        c.descricao = f"Descrição {n}"
        c.hashtags = ["#shorts", "#fé"]
        c.slug = f"titulo_{n}"
        c.motivo = "Boa aplicação."
    cortes[0].alerta_contexto = "pode soar legalista"
    cortes[0].detalhe["alerta_de_contexto"] = -10.0
    gravar_cortes(pastas.cortes_shorts, cortes)
    return pastas


@pytest.fixture
def caixas(monkeypatch):
    """Registra as caixas de diálogo e responde “sim” (o botão principal) a todas."""
    vistas: list[tuple[str, str]] = []

    def mostrar(_janela, titulo, mensagem, *, botoes=("OK",), tipo="info"):
        vistas.append((titulo, mensagem))
        return botoes[-1]

    monkeypatch.setattr(dialogos, "mostrar", mostrar)
    return vistas


@pytest.fixture
def aberto(raiz, pastas, caixas):
    painel = PainelAprovacaoCortes(raiz, pastas, modal=False)
    painel.pastas = pastas
    painel.caixas = caixas
    yield painel
    if painel.winfo_exists():
        painel.destroy()


@pytest.mark.gui
def test_todos_os_cortes_principais_vem_marcados_e_as_reservas_desmarcadas(aberto, pastas):
    cortes = carregar_cortes(pastas.cortes_shorts)
    assert [m.get() for m in aberto.editor._marcas] == [c.incluir for c in cortes]
    assert any(c.reserva and not c.incluir for c in cortes)
    assert sum(m.get() for m in aberto.editor._marcas) == sum(not c.reserva for c in cortes)
    assert f"{sum(not c.reserva for c in cortes)} de {len(cortes)} marcados" in (
        aberto.editor._resumo.cget("text")
    )


@pytest.mark.gui
def test_lista_vem_na_ordem_da_nota_e_pode_ir_para_a_ordem_do_horario(aberto):
    notas = [aberto.editor._cortes[i].nota for i in aberto.editor._ordem]
    assert notas == sorted(notas, reverse=True)
    aberto.editor._seletor_ordem.set("Horário")
    aberto.editor._reordenar()
    inicios = [aberto.editor._cortes[i].inicio for i in aberto.editor._ordem]
    assert inicios == sorted(inicios)


@pytest.mark.gui
def test_o_detalhe_mostra_a_nota_o_alerta_e_o_texto(aberto):
    i = next(i for i, c in enumerate(aberto.editor._cortes) if c.alerta_contexto)
    aberto.editor.selecionar(i)
    corte = aberto.editor._cortes[i]
    assert f"Nota {corte.nota:.0f} de 100" in aberto.editor._rotulo_nota.cget("text")
    assert "pode soar legalista" in aberto.editor._rotulo_alerta.cget("text")
    assert aberto.editor._var_titulo.get() == corte.titulo
    # a legenda aparece dividida em blocos de até 2 linhas, como no vídeo
    legenda = aberto.editor._texto.get("1.0", "end-1c")
    assert " ".join(legenda.split()) == " ".join(corte.texto.split())
    assert "\n" in legenda
    barra, valor = aberto.editor._partes["retencao"]
    assert valor.cget("text") == f"{corte.detalhe['retencao']:g} de 40"
    assert barra.get() == pytest.approx(corte.detalhe["retencao"] / 40)


@pytest.mark.gui
def test_desmarcar_todos_e_marcar_todos(aberto):
    aberto.editor.marcar_todos(False)
    assert not any(m.get() for m in aberto.editor._marcas)
    assert "0 de" in aberto.editor._resumo.cget("text")
    aberto.editor.marcar_todos(True)
    assert all(c.incluir for c in aberto.editor._cortes)


@pytest.mark.gui
def test_editar_o_titulo_e_salvar_grava_no_arquivo(aberto, pastas):
    aberto.editor.selecionar(aberto.editor._ordem[0])
    aberto.editor._var_titulo.set("  Novo título ")
    aberto.editor._campo_hashtags.delete(0, "end")
    aberto.editor._campo_hashtags.insert(0, "graça, #Miquéias")
    aberto.editor.selecionar(aberto.editor._ordem[1])  # trocar de corte também guarda o anterior
    assert aberto.salvar_e_fechar() is True
    gravados = {c.id: c for c in carregar_cortes(pastas.cortes_shorts)}
    primeiro = gravados[aberto.editor._cortes[aberto.editor._ordem[0]].id]
    assert primeiro.titulo == "Novo título"
    assert primeiro.hashtags == ["#graça", "#Miquéias"]
    assert aberto.salvo


@pytest.mark.gui
def test_mover_o_inicio_muda_o_corte_e_marca_como_editado(aberto):
    aberto.editor.selecionar(aberto.editor._ordem[0])
    i = aberto.editor._atual
    antes = aberto.editor._cortes[i]
    assert aberto.editor.mover("inicio", -1)
    depois = aberto.editor._cortes[i]
    assert depois.inicio < antes.inicio and depois.editado and depois.nota == antes.nota
    legenda = aberto.editor._texto.get("1.0", "end-1c")
    assert " ".join(legenda.split()) == " ".join(depois.texto.split())
    assert aberto.editor.mover("inicio", 1)
    assert aberto.editor._cortes[i].inicio == pytest.approx(antes.inicio)


@pytest.mark.gui
def test_passar_de_60_segundos_e_permitido_mas_avisa_na_tela(aberto):
    aberto.editor.selecionar(aberto.editor._ordem[0])
    assert aberto.editor._rotulo_limite.cget("text") == ""
    for _ in range(80):
        assert aberto.editor.mover("fim", 1)
    assert aberto.editor._cortes[aberto.editor._atual].duracao > 60.0
    assert "não pode ser um Short" in aberto.editor._rotulo_limite.cget("text")
    assert "mais de 60 s" in aberto.editor._resumo.cget("text")


@pytest.mark.gui
def test_salvar_recusa_corte_marcado_com_mais_de_60_segundos(aberto, pastas):
    aberto.editor.selecionar(aberto.editor._ordem[0])
    for _ in range(80):
        aberto.editor.mover("fim", 1)
    assert aberto.salvar_e_fechar() is False
    assert any("Corte com mais de 60 segundos" in t for t, _ in aberto.caixas)
    assert aberto.winfo_exists() and not aberto.salvo
    aberto.editor.alternar_atual()  # desmarcado, o corte longo não impede mais
    assert aberto.salvar_e_fechar() is True


@pytest.mark.gui
def test_salvar_sem_titulo_nos_marcados_e_recusado(aberto, pastas):
    aberto.editor.selecionar(aberto.editor._ordem[0])
    aberto.editor._var_titulo.set("   ")
    assert aberto.salvar_e_fechar() is False
    assert any("sem título" in m for _, m in aberto.caixas)
    assert not aberto.salvo and aberto.winfo_exists()
    assert all(c.titulo for c in carregar_cortes(pastas.cortes_shorts))  # arquivo intacto


@pytest.mark.gui
def test_salvar_sem_nenhum_corte_marcado_pede_confirmacao(aberto, pastas, monkeypatch):
    aberto.editor.marcar_todos(False)
    respostas = iter(["Voltar", "Salvar"])
    monkeypatch.setattr(dialogos, "mostrar", lambda *_a, **_k: next(respostas))
    assert aberto.salvar_e_fechar() is False
    assert aberto.salvar_e_fechar() is True
    assert not any(c.incluir for c in carregar_cortes(pastas.cortes_shorts))


@pytest.mark.gui
def test_cancelar_sem_mudancas_fecha_sem_perguntar_e_nao_mexe_no_arquivo(aberto, pastas):
    antes = pastas.cortes_shorts.read_bytes()
    aberto._cancelar()
    assert not aberto.winfo_exists()
    assert aberto.caixas == []
    assert pastas.cortes_shorts.read_bytes() == antes


@pytest.mark.gui
def test_cancelar_com_mudancas_pergunta_e_descarta(aberto, pastas, monkeypatch):
    antes = pastas.cortes_shorts.read_bytes()
    aberto.editor.marcar_todos(False)
    monkeypatch.setattr(dialogos, "mostrar", lambda _j, _t, _m, *, botoes=("OK",), **_k: botoes[0])
    aberto._cancelar()  # “Continuar editando”
    assert aberto.winfo_exists()
    monkeypatch.setattr(dialogos, "mostrar", lambda _j, _t, _m, *, botoes=("OK",), **_k: botoes[-1])
    aberto._cancelar()  # “Descartar”
    assert not aberto.winfo_exists()
    assert pastas.cortes_shorts.read_bytes() == antes


@pytest.mark.gui
def test_abrir_a_revisao_pelo_id_da_etapa_sem_cortes_avisa(raiz, tmp_path, caixas):
    pastas = PastasProjeto(tmp_path / "vazio")
    pastas.criar()
    assert abrir_revisao(raiz, "aprovar_cortes", pastas) is True
    assert any("ainda não foram escolhidos" in m for _, m in caixas)


@pytest.mark.gui
def test_atalhos_andam_pela_lista_e_marcam_o_corte_aberto(aberto):
    primeiro = aberto.editor._ordem[0]
    aberto.editor.selecionar(primeiro)
    aberto.editor._andar(1)
    assert aberto.editor._atual == aberto.editor._ordem[1]
    aberto.editor._andar(-1)
    aberto.editor._andar(-1)  # já é o primeiro: não sai da lista
    assert aberto.editor._atual == primeiro
    antes = aberto.editor._marcas[primeiro].get()
    aberto.editor.alternar_atual()
    assert aberto.editor._marcas[primeiro].get() is (not antes)
    assert aberto.editor._cortes[primeiro].incluir is (not antes)


@pytest.mark.gui
def test_a_lista_mostra_inicio_e_fim_de_cada_corte(aberto):
    textos = []

    def coletar(widget):
        for filho in widget.winfo_children():
            if hasattr(filho, "cget"):
                with contextlib.suppress(Exception):  # nem todo widget tem texto
                    textos.append(filho.cget("text"))
            coletar(filho)

    coletar(aberto.editor._lista)
    assert any("–" in t and " s" in t for t in textos if isinstance(t, str))
