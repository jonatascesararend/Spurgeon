"""Studio Shorts: o título que renomeia (lista, arquivo, .txt) e o agendamento que vira postado."""

from __future__ import annotations

import queue
import tkinter
from datetime import datetime, timedelta
from typing import ClassVar

import customtkinter as ctk
import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.publicacao.modelo import (
    AGENDADO,
    ASSISTIDO,
    ENVIADO,
    NA_FILA,
    Plataforma,
    Resultado,
)
from spurgeon.publicacao.pacote import registro_do_projeto
from spurgeon.render import shorts as render_shorts
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_PRONTO,
    detalhes_do_registro,
    ler_registro,
    planejar_shorts,
    texto_de_publicacao,
)
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.shorts.selecao import nome_de_arquivo
from spurgeon.ui import dialogos
from spurgeon.ui import studio_shorts as studio
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.postagem import ContextoDePublicacao
from spurgeon.ui.studio_shorts import EditorDeCortes
from tests import test_studio_shorts as base
from tests.ambiente_grafico import criar_raiz
from tests.falso_de_rede import Relogio

pytestmark = pytest.mark.gui

caixas, pastas = base.caixas, base.pastas  # as fixtures compartilhadas

AGORA = datetime(2026, 11, 3, 12, 0).astimezone()


class Rede(Plataforma):
    def __init__(self, chave: str):
        super().__init__(None)  # type: ignore[arg-type]
        self.chave, self.nome = chave, chave.title()

    @property
    def conectada(self) -> bool:
        return True


@pytest.fixture(scope="module")
def raiz():
    try:
        janela = criar_raiz()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


@pytest.fixture
def relogio():
    return Relogio(AGORA)


@pytest.fixture
def atividade() -> list[str]:
    return []


@pytest.fixture
def editor(raiz, pastas, relogio, atividade):
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    contexto = ContextoDePublicacao(
        plataformas=lambda: {"youtube": Rede("youtube"), "instagram": Rede("instagram")},
        tarefas=Tarefas(queue.Queue().put),
        registrar=atividade.append,
    )
    ed = EditorDeCortes(
        janela, pastas, legenda_queimada=lambda: True, publicacao=contexto, relogio=relogio
    )
    ed.pack(fill="both", expand=True)
    ed.pastas = pastas
    yield ed
    janela.destroy()


def _renderizado(editor, pastas, corte: Corte) -> str:
    """Um Short "já renderizado" com o nome que o corte tem agora; devolve o nome dentro de
    `shorts_gerados/`."""
    curto = next(s for s in planejar_shorts(editor._cortes) if s.corte.id == corte.id)
    arquivo = pastas.shorts / curto.video
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_bytes(b"video")
    arquivo.with_suffix(".txt").write_text(texto_de_publicacao(corte), encoding="utf-8")
    registro = ler_registro(pastas.shorts_renderizados)
    registro[str(corte.id)] = {
        "arquivo": curto.video, "assinatura": "a", **detalhes_do_registro(corte),
    }  # fmt: skip
    escrever_json_atomico(pastas.shorts_renderizados, registro)
    editor.recarregar_registro()
    return curto.video


def _no_arquivo(pastas, id_do_corte: int) -> Corte:
    return next(c for c in carregar_cortes(pastas.cortes_shorts) if c.id == id_do_corte)


def _texto_da_lista(editor, i: int, nome: str = "titulo") -> str:
    return editor._rotulos_da_linha[i][nome].cget("text")


# --- o título renomeia a lista, o arquivo e o .txt ---------------------------------------------
def test_o_titulo_digitado_aparece_na_lista_na_hora(editor):
    i = editor._atual
    editor._var_titulo.set("Um título novo e específico")
    assert _texto_da_lista(editor, i) == "Um título novo e específico"
    assert editor._cortes[i].titulo != "Um título novo e específico"  # o corte só recebe ao salvar
    editor._var_titulo.set("   ")
    assert _texto_da_lista(editor, i) == editor._cortes[i].texto[:60]  # vazio: começo do texto


def test_trocar_de_corte_nao_leva_o_titulo_para_a_linha_errada(editor):
    a = editor._atual
    b = next(i for i in editor._ordem if i != a)
    titulo_original_de_b = editor._cortes[b].titulo
    editor._var_titulo.set("Título que só vale para o primeiro")
    editor.selecionar(b)
    assert _texto_da_lista(editor, a) == "Título que só vale para o primeiro"
    assert editor._cortes[a].titulo == "Título que só vale para o primeiro"
    assert _texto_da_lista(editor, b) == titulo_original_de_b


def test_salvar_com_titulo_novo_renomeia_o_video_o_txt_e_o_registro(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor._var_titulo.set("Deus governa os dias do homem")

    assert editor.salvar(exigir_ate_o_limite=False)

    esperado = nome_de_arquivo("Deus governa os dias do homem", set())
    assert corte.slug == esperado and _no_arquivo(pastas, corte.id).slug == esperado
    novo = ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]
    assert novo.endswith(f"_{esperado}.mp4") and novo != antigo
    assert (pastas.shorts / novo).exists() and not (pastas.shorts / antigo).exists()
    texto = (pastas.shorts / novo).with_suffix(".txt")
    assert "Título: Deus governa os dias do homem" in texto.read_text("utf-8")
    assert not (pastas.shorts / antigo).with_suffix(".txt").exists()
    assert editor.situacao(corte) == SITUACAO_PRONTO  # só o nome mudou: não pede renderizar


def test_salvar_sem_mudar_o_titulo_mantem_o_nome_que_a_ia_deu(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    slug = corte.slug
    editor._houve_mudanca()
    assert editor.salvar(exigir_ate_o_limite=False)
    assert corte.slug == slug and (pastas.shorts / antigo).exists()


def test_titulo_trocado_duas_vezes_renomeia_duas_vezes(editor, pastas):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    for titulo in ("Primeiro título bom", "Segundo título melhor"):
        editor._var_titulo.set(titulo)
        assert editor.salvar(exigir_ate_o_limite=False)
        nome = ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]
        assert nome.endswith(f"_{nome_de_arquivo(titulo, set())}.mp4")
        assert (pastas.shorts / nome).exists()
    assert len(list(pastas.shorts.glob("*.mp4"))) == 1  # nenhum arquivo ficou para trás


def test_dois_titulos_trocados_de_uma_vez_nao_se_atropelam(editor, pastas):
    a = editor._atual
    b = next(i for i in editor._ordem if i != a)
    antigos = [_renderizado(editor, pastas, editor._cortes[i]) for i in (a, b)]
    editor._var_titulo.set("Título novo do primeiro")
    editor.selecionar(b)
    editor._var_titulo.set("Título novo do segundo")
    assert editor.salvar(exigir_ate_o_limite=False)
    registro = ler_registro(pastas.shorts_renderizados)
    novos = [registro[str(editor._cortes[i].id)]["arquivo"] for i in (a, b)]
    assert len(set(novos)) == 2 and all((pastas.shorts / n).exists() for n in novos)
    assert not any((pastas.shorts / n).exists() for n in antigos if n not in novos)


def test_txt_acompanha_a_descricao_e_as_hashtags_editadas(editor, pastas):
    corte = editor._cortes[editor._atual]
    nome = _renderizado(editor, pastas, corte)
    editor._campo_descricao.delete("1.0", "end")
    editor._campo_descricao.insert("1.0", "Uma descrição bem melhor.")
    editor._var_hashtags.set("#fé #esperança")
    editor._marcar_mudanca()
    assert editor.salvar(exigir_ate_o_limite=False)
    texto = (pastas.shorts / nome).with_suffix(".txt").read_text("utf-8")
    assert "Uma descrição bem melhor." in texto and "#fé #esperança" in texto


def test_arquivo_preso_nao_impede_salvar_e_avisa_na_tela(editor, pastas, monkeypatch):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)

    def preso(origem, destino):
        raise PermissionError(13, "usado por outro processo")

    monkeypatch.setattr(render_shorts, "_mover_par", preso)
    editor._var_titulo.set("Título novo com arquivo preso")
    assert editor.salvar(exigir_ate_o_limite=False)
    assert (pastas.shorts / antigo).exists()  # nada se perdeu
    assert ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"] == antigo
    assert "Não consegui mover" in editor._rotulo_postado.cget("text")
    assert _no_arquivo(pastas, corte.id).titulo == "Título novo com arquivo preso"


# --- agendar: grava na hora, aparece na lista e vira "postado" quando a hora passa --------------
def test_marcar_agendado_sugere_amanha_as_18_grava_na_hora_e_mostra_na_lista(editor, pastas):
    corte = editor._cortes[editor._atual]
    linha = editor._linha_agendamento
    assert not corte.agendado_para
    linha._var_marcado.set(True)
    linha._ao_marcar()
    assert corte.agendado_para == "2026-11-04T18:00"
    assert _no_arquivo(pastas, corte.id).agendado_para == "2026-11-04T18:00"
    assert "📅 agendado 04/11 18:00" in _texto_da_lista(editor, editor._atual, "info")
    assert "1 agendado" in editor._resumo.cget("text")
    assert not editor.mudou  # gravou só o campo: não deixou o editor "sujo"


def test_mudar_e_tirar_o_horario(editor, pastas):
    corte = editor._cortes[editor._atual]
    linha = editor._linha_agendamento
    linha._var_marcado.set(True)
    linha._ao_marcar()
    linha._var_data.set("10/11/2026")
    linha._var_hora.set("09:30")
    assert _no_arquivo(pastas, corte.id).agendado_para == "2026-11-10T09:30"
    linha._var_marcado.set(False)
    linha._ao_marcar()
    assert corte.agendado_para == "" and _no_arquivo(pastas, corte.id).agendado_para == ""
    assert "agendado" not in _texto_da_lista(editor, editor._atual, "info")


def test_o_agendamento_aparece_ao_abrir_outro_corte_e_ao_voltar(editor):
    a = editor._atual
    b = next(i for i in editor._ordem if i != a)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    editor.selecionar(b)
    assert editor._linha_agendamento.check.get() == 0
    editor.selecionar(a)
    assert editor._linha_agendamento.check.get() == 1
    assert editor._linha_agendamento.campo_hora.get() == "18:00"


def test_passada_a_hora_o_short_vira_postado_e_o_arquivo_vai_para_a_subpasta(
    editor,
    pastas,
    relogio,
):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()  # amanhã às 18:00

    relogio.agora = AGORA + timedelta(days=1, hours=6, minutes=1)
    editor._conferir_agendamentos()

    assert corte.postado and corte.agendado_para == "" and corte.postado_em == "2026-11-04"
    gravado = _no_arquivo(pastas, corte.id)
    assert gravado.postado and gravado.agendado_para == "" and gravado.postado_em == "2026-11-04"
    nome = ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]
    assert nome.startswith("postados/") and (pastas.shorts / nome).exists()
    info = _texto_da_lista(editor, editor._atual, "info")
    assert "✔ postado" in info and "agendado" not in info
    assert "já postado" in editor._resumo.cget("text")
    assert editor._var_postado.get() is True
    assert editor._linha_agendamento.check.get() == 0


def test_antes_da_hora_nada_muda(editor, pastas, relogio):
    corte = editor._cortes[editor._atual]
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    relogio.agora = AGORA + timedelta(days=1, hours=5)
    editor._conferir_agendamentos()
    assert not corte.postado and corte.agendado_para == "2026-11-04T18:00"
    assert editor._linha_agendamento.previsao.cget("text") == "em 1 h"


def test_agendamento_vencido_ja_vira_postado_ao_abrir_o_projeto(editor, pastas):
    cortes = carregar_cortes(pastas.cortes_shorts)
    cortes[0].agendado_para = "2026-10-20T09:00"  # a hora passou com o aplicativo fechado
    gravar_cortes(pastas.cortes_shorts, cortes)
    editor.carregar()
    assert _no_arquivo(pastas, cortes[0].id).postado
    assert _no_arquivo(pastas, cortes[0].id).postado_em == "2026-10-20"
    assert next(c for c in editor._cortes if c.id == cortes[0].id).postado


def test_marcar_postado_na_mao_tira_o_agendamento(editor, pastas):
    corte = editor._cortes[editor._atual]
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert corte.postado and corte.agendado_para == ""
    assert _no_arquivo(pastas, corte.id).agendado_para == ""
    assert editor._linha_agendamento.check.get() == 0


# --- o arquivo acompanha o agendamento: agendados/ -> postados/ ---------------------------------
def test_agendar_leva_o_arquivo_para_agendados_na_hora_e_a_tela_diz_onde_esta(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()  # amanhã às 18:00
    nome = ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]
    assert nome == f"agendados/{antigo}"
    assert (pastas.shorts / nome).exists() and not (pastas.shorts / antigo).exists()
    assert (pastas.shorts / nome).with_suffix(".txt").read_text("utf-8").startswith("AGENDADO PARA")
    assert "shorts_gerados/agendados" in editor._linha_agendamento.resumo.cget("text")
    assert not editor.mudou  # gravou só o campo: não deixou o editor "sujo"


def test_tirar_o_horario_devolve_o_arquivo_para_a_pasta_principal(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    editor._linha_agendamento._var_marcado.set(False)
    editor._linha_agendamento._ao_marcar()
    assert ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"] == antigo
    assert (pastas.shorts / antigo).exists()
    assert editor._linha_agendamento.resumo.cget("text") == ""


def test_passada_a_hora_o_arquivo_vai_de_agendados_direto_para_postados(editor, pastas, relogio):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    relogio.agora = AGORA + timedelta(days=1, hours=6, minutes=1)
    editor._conferir_agendamentos()
    assert not (pastas.shorts / "agendados" / antigo).exists()
    assert (pastas.shorts / "postados" / antigo).exists()
    assert (
        (pastas.shorts / "postados" / antigo)
        .with_suffix(".txt")
        .read_text("utf-8")
        .startswith("JÁ POSTADO")
    )


def test_marcar_postado_na_mao_tira_o_arquivo_de_agendados(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    editor._var_postado.set(True)
    editor._ao_marcar_postado()
    assert (pastas.shorts / "postados" / antigo).exists()
    assert not (pastas.shorts / "agendados" / antigo).exists()


def test_agendado_que_estava_fora_da_pasta_vai_para_agendados_ao_abrir_o_projeto(editor, pastas):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)
    gravados = carregar_cortes(pastas.cortes_shorts)
    next(c for c in gravados if c.id == corte.id).agendado_para = "2026-11-10T09:30"
    gravar_cortes(pastas.cortes_shorts, gravados)
    editor.carregar()
    assert (pastas.shorts / "agendados" / antigo).exists()
    assert not (pastas.shorts / antigo).exists()
    assert (
        ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"] == f"agendados/{antigo}"
    )


def test_arquivo_preso_ao_agendar_avisa_sem_desfazer_o_agendamento(editor, pastas, monkeypatch):
    corte = editor._cortes[editor._atual]
    antigo = _renderizado(editor, pastas, corte)

    def preso(origem, destino):
        raise PermissionError(13, "usado por outro processo")

    monkeypatch.setattr(render_shorts, "_mover_par", preso)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    assert corte.agendado_para == "2026-11-04T18:00"  # o horário vale mesmo assim
    assert (pastas.shorts / antigo).exists()  # nada se perdeu
    texto = editor._linha_agendamento.resumo.cget("text")
    assert "Não consegui mover" in texto and "usado por outro processo" in texto


def test_o_que_o_corte_guarda_sobrevive_a_refazer_a_selecao(pastas):
    cortes = carregar_cortes(pastas.cortes_shorts)
    cortes[0].agendado_para = "2026-12-01T10:00"
    gravar_cortes(pastas.cortes_shorts, cortes)
    assert carregar_cortes(pastas.cortes_shorts)[0].agendado_para == "2026-12-01T10:00"
    assert Corte.de_dict(cortes[0].para_dict()).agendado_para == "2026-12-01T10:00"


def test_sem_as_redes_o_botao_de_postar_some_mas_o_agendamento_fica(raiz, pastas):
    janela = ctk.CTkToplevel(raiz)
    janela.withdraw()
    ed = EditorDeCortes(janela, pastas, legenda_queimada=lambda: True)  # janela de aprovação
    ed.pack()
    try:
        assert ed._linha_agendamento.botao_postar.winfo_manager() == ""
        assert ed._linha_agendamento.check.winfo_manager() == "grid"
    finally:
        janela.destroy()


# --- postar nas redes (a janela é de mentira: o que importa é o que o Studio faz depois) -------
class JanelaFalsa:
    abertas: ClassVar[list[dict]] = []

    def __init__(self, master, **kwargs):
        JanelaFalsa.abertas.append(kwargs)


@pytest.fixture
def janela_falsa(monkeypatch):
    JanelaFalsa.abertas = []
    monkeypatch.setattr(studio, "JanelaDePostagem", JanelaFalsa)
    return JanelaFalsa


def test_postar_abre_a_janela_com_o_short_renderizado_e_o_horario_marcado(
    editor,
    pastas,
    janela_falsa,
):
    corte = editor._cortes[editor._atual]
    nome = _renderizado(editor, pastas, corte)
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    editor._abrir_postagem()
    (aberta,) = janela_falsa.abertas
    assert aberta["pacote"].video == pastas.shorts / "agendados" / nome  # agendar já o moveu
    assert aberta["titulo_do_short"] == corte.titulo and aberta["id_do_corte"] == corte.id
    assert aberta["quando"] == datetime(2026, 11, 4, 18, 0).astimezone()
    assert set(aberta["plataformas"]) == {"youtube", "instagram"}


def test_postar_um_short_nao_renderizado_abre_a_janela_sem_pacote(editor, janela_falsa):
    editor._abrir_postagem()
    assert janela_falsa.abertas[0]["pacote"] is None


def test_postar_com_o_corte_alterado_manda_renderizar_antes(editor, pastas, janela_falsa, caixas):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    corte.fim -= 2.0  # o corte mudou depois de renderizado
    assert editor.situacao(corte) == SITUACAO_ALTERADO
    editor._abrir_postagem()
    assert janela_falsa.abertas == []
    assert any("Renderize de novo" in mensagem for _t, mensagem in caixas)


def test_postar_salva_antes_para_o_que_vai_ser_o_que_esta_na_tela(editor, pastas, janela_falsa):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    editor._var_titulo.set("Título escrito agora e ainda não salvo")
    editor._abrir_postagem()
    assert _no_arquivo(pastas, corte.id).titulo == "Título escrito agora e ainda não salvo"
    assert janela_falsa.abertas[0]["titulo_do_short"] == "Título escrito agora e ainda não salvo"


def test_agendado_nas_redes_guarda_o_horario_no_short(editor, pastas, janela_falsa, atividade):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    editor._abrir_postagem()
    quando = AGORA + timedelta(days=2)
    resultados = [Resultado("youtube", AGENDADO), Resultado("instagram", NA_FILA)]
    janela_falsa.abertas[0]["ao_terminar"](resultados, quando)
    assert corte.agendado_para == "2026-11-05T12:00"
    assert _no_arquivo(pastas, corte.id).agendado_para == "2026-11-05T12:00"
    assert editor._linha_agendamento.check.get() == 1
    assert atividade == [
        "Youtube: “" + corte.titulo + "” agendado na rede",
        "Instagram: “" + corte.titulo + "” na fila do Spurgeon",
    ]


def test_publicado_agora_marca_como_ja_postado_e_move_o_arquivo(editor, pastas, janela_falsa):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    editor._abrir_postagem()
    janela_falsa.abertas[0]["ao_terminar"]([Resultado("youtube", ENVIADO)], None)
    assert corte.postado and _no_arquivo(pastas, corte.id).postado
    nome = ler_registro(pastas.shorts_renderizados)[str(corte.id)]["arquivo"]
    assert nome.startswith("postados/") and (pastas.shorts / nome).exists()
    assert "✔ postado" in _texto_da_lista(editor, editor._atual, "info")


def test_tiktok_assistido_ou_rascunho_nao_marcam_nada(editor, pastas, janela_falsa):
    corte = editor._cortes[editor._atual]
    _renderizado(editor, pastas, corte)
    editor._abrir_postagem()
    janela_falsa.abertas[0]["ao_terminar"]([Resultado("tiktok", ASSISTIDO)], None)
    assert not corte.postado and not corte.agendado_para


def test_o_resumo_das_redes_aparece_sob_o_agendamento(editor, pastas):
    corte = editor._cortes[editor._atual]
    registro_do_projeto(pastas).gravar(corte.id, Resultado("youtube", AGENDADO))
    registro_do_projeto(pastas).gravar(corte.id, Resultado("instagram", NA_FILA))
    editor.atualizar_redes()
    assert editor._linha_agendamento.resumo.cget("text") == (
        "YouTube agendado na rede · Instagram na fila do Spurgeon"
    )


def test_a_confirmacao_de_marcar_nao_usa_dialogo_nenhum(editor, monkeypatch):
    """Marcar o agendamento e a conferência periódica nunca abrem caixas na cara da pessoa."""
    monkeypatch.setattr(
        dialogos, "mostrar", lambda *a, **k: pytest.fail("não devia abrir caixa de diálogo")
    )
    editor._linha_agendamento._var_marcado.set(True)
    editor._linha_agendamento._ao_marcar()
    editor._conferir_agendamentos()
