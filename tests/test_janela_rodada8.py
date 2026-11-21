"""A janela principal com as novidades da 1.2: corte, zoom, cor, vinhetas, Ollama e legendinhas."""

from __future__ import annotations

import contextlib
from datetime import date
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.projeto import criar_ou_retomar_projeto
from spurgeon.ui import app as app_mod
from spurgeon.ui import dialogos
from spurgeon.ui import painel_opcoes as painel_opcoes_mod
from spurgeon.ui.formulario import ValoresFormulario, para_opcoes
from tests import test_janela as base

pytestmark = pytest.mark.gui

janela, tela = base.janela, base.tela  # as fixtures da janela compartilhada
escolher_video = base.escolher_video


def textos_dos_filhos(widget) -> list[str]:
    achados = []
    for filho in widget.winfo_children():
        with contextlib.suppress(Exception):  # nem todo widget tem texto
            achados.append(str(filho.cget("text")))
        achados += textos_dos_filhos(filho)
    return achados


# --- opções avançadas -------------------------------------------------------------------------


def test_cada_opcao_avancada_tem_uma_legendinha_explicando(tela):
    textos = " | ".join(textos_dos_filhos(tela.opcoes._avancado))
    for trecho in (
        "Quanto silêncio sobra em cada pausa cortada",
        "Trechos mais curtos que isto não viram Short",
        "180 s (3 min) é o limite do YouTube",
        "O máximo de cortes sugeridos como principais",
        "aparecem como reserva no Studio Shorts",
        "A IA local que avalia os trechos",
        "Move o enquadramento vertical dos Shorts",
        "Ajustar o zoom e a legenda dos Shorts",  # a altura da legenda mudou para o painel próprio
        "Só vale com “Personalizado” em Qualidade",
        "Não apaga os áudios e partes pesadas",
        "Com isto ligado o Windows não suspende",
    ):
        assert trecho in textos, trecho


def test_modelo_do_ollama_e_uma_caixa_de_selecao_e_nao_um_campo_de_texto(tela):
    assert isinstance(tela.opcoes._menu_ollama, ctk.CTkOptionMenu)
    assert "ollama_modelo" not in tela.opcoes._por_nome  # não há mais um campo para digitar


def test_lista_do_ollama_mostra_instalados_e_recomendados(tela):
    tela.opcoes.definir_modelos_ollama(["llama3.1:8b", "qwen3:8b"])
    valores = list(tela.opcoes._menu_ollama.cget("values"))
    assert "llama3.1:8b" in valores and "qwen3:8b" in valores
    assert any("qwen3:4b" in v and "será baixado" in v for v in valores)
    assert tela.opcoes.ollama.get() == "qwen3:8b"


def test_escolher_outro_modelo_do_ollama_chega_nas_opcoes(tela):
    tela.opcoes.definir_modelos_ollama(["llama3.1:8b", "qwen3:8b"])
    tela.opcoes._menu_ollama.set("llama3.1:8b")
    tela.opcoes._mudou_ollama("llama3.1:8b")
    assert tela.opcoes.valores().ollama_modelo == "llama3.1:8b"
    outro = "qwen3:4b · não instalado (será baixado)"
    tela.opcoes._mudou_ollama(outro)
    assert tela.opcoes.valores().ollama_modelo == "qwen3:4b"  # o nome, sem o rótulo


def test_ollama_fora_do_ar_ainda_mostra_o_modelo_atual_e_os_recomendados(tela):
    tela.opcoes.textos["ollama_modelo"].set("meu-modelo:7b")
    tela.opcoes.definir_modelos_ollama([])
    assert "não encontrado" in tela.opcoes.ollama.get()
    assert tela.opcoes.valores().ollama_modelo == "meu-modelo:7b"
    assert any("qwen3:8b" in v for v in tela.opcoes._menu_ollama.cget("values"))


def test_atualizar_a_lista_do_ollama_pede_a_lista_ao_app(tela, monkeypatch):
    chamadas = []
    monkeypatch.setattr(app_mod, "listar_modelos_instalados", lambda: chamadas.append(1) or ["x:1"])
    tela._listar_ollama()
    base.bombear(tela, lambda: "x:1" in list(tela.opcoes._menu_ollama.cget("values")))
    assert chamadas


def test_abrir_um_projeto_mostra_o_modelo_do_ollama_dele(tela, tmp_path, video_falso):
    criado = criar_ou_retomar_projeto(
        tmp_path / "Projetos", video_falso, "Com Llama", date(2026, 10, 4),
        OpcoesProcessamento(gerar_shorts=True, ollama_modelo="llama3.1:8b"),
    )  # fmt: skip
    from tests.test_shorts import cortes_do_sermao, gravar_transcricao, sermao_falso

    palavras = sermao_falso(frases=100)
    gravar_transcricao(criado.pastas.transcricao, palavras)
    from spurgeon.shorts.etapas import gravar_cortes

    gravar_cortes(criado.pastas.cortes_shorts, cortes_do_sermao(palavras))
    tela._abrir_projeto(criado.pastas.raiz)
    assert tela.opcoes.valores().ollama_modelo == "llama3.1:8b"
    assert "llama3.1:8b" in tela.opcoes.ollama.get()


# --- introdução, vinheta e fade ---------------------------------------------------------------


def test_escolher_o_arquivo_da_introducao_e_da_vinheta(tela, tmp_path, monkeypatch):
    abertura = tmp_path / "abertura.mp4"
    abertura.write_bytes(b"x")
    monkeypatch.setattr(painel_opcoes_mod.filedialog, "askopenfilename", lambda **_k: str(abertura))
    tela.opcoes._escolher_arquivo("introducao_arquivo", "Introdução")
    assert tela.opcoes.valores().introducao_arquivo == str(abertura)
    rotulo, _padrao = tela.opcoes._nomes_de_arquivo["introducao_arquivo"]
    assert rotulo.cget("text") == "abertura.mp4"
    tela.opcoes._definir_arquivo("introducao_arquivo", "")  # "Usar o padrão"
    assert rotulo.cget("text") == "introducao.mp4 (padrão)"
    monkeypatch.setattr(painel_opcoes_mod.filedialog, "askopenfilename", lambda **_k: "")
    tela.opcoes._escolher_arquivo("vinheta_arquivo", "Vinheta final")  # cancelou
    assert tela.opcoes.valores().vinheta_arquivo == ""


def test_arquivo_escolhido_destrava_a_caixa_mesmo_sem_o_padrao_em_recursos(tela, tmp_path):
    assert str(tela.opcoes._por_nome["introducao"].cget("state")) == "disabled"  # sem recursos/
    abertura = tmp_path / "abertura.mp4"
    abertura.write_bytes(b"x")
    tela.opcoes._definir_arquivo("introducao_arquivo", str(abertura))
    assert str(tela.opcoes._por_nome["introducao"].cget("state")) == "normal"
    tela.opcoes._definir_arquivo("introducao_arquivo", str(tmp_path / "sumiu.mp4"))
    assert str(tela.opcoes._por_nome["introducao"].cget("state")) == "disabled"
    assert any("sumiu.mp4" in t for t in textos_dos_filhos(tela.opcoes))  # a dica diz qual falta


def test_fade_so_fica_ativo_com_introducao_ou_vinheta_marcadas(tela, tmp_path):
    abertura = tmp_path / "abertura.mp4"
    abertura.write_bytes(b"x")
    tela.opcoes._definir_arquivo("introducao_arquivo", str(abertura))
    assert str(tela.opcoes._por_nome["fade_vinhetas"].cget("state")) == "disabled"
    tela.opcoes.bools["introducao"].set(True)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["fade_vinhetas"].cget("state")) == "normal"
    assert str(tela.opcoes._por_nome["fade_vinhetas_s"].cget("state")) == "disabled"
    tela.opcoes.bools["fade_vinhetas"].set(True)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["fade_vinhetas_s"].cget("state")) == "normal"
    opcoes = para_opcoes(tela._valores(), tela.config_app.padroes)
    assert opcoes.fade_vinhetas and opcoes.fade_vinhetas_s == 0.5
    assert opcoes.introducao_arquivo == str(abertura)


# --- começo, fim, zoom e tratamento no formulário ---------------------------------------------


@pytest.mark.ffmpeg
def test_o_video_escolhido_chega_aos_quadros_de_exemplo(tela, video_teste):
    assert not tela.fonte.pronta
    escolher_video(tela, video_teste)
    assert tela.fonte.pronta and tela.fonte.video == video_teste
    assert tela.fonte.info.duracao_s == pytest.approx(20.0, abs=0.2)


@pytest.mark.ffmpeg
def test_comeco_e_fim_digitados_chegam_nas_opcoes_e_na_estimativa(tela, video_teste):
    escolher_video(tela, video_teste)
    antes = tela.entrada.rotulo_estimativa.cget("text")
    tela.entrada.aparar.var_inicio.set("0:03")
    tela.entrada.aparar.var_fim.set("-0:05")
    tela.entrada.aparar._confirmar("inicio")
    tela.entrada.aparar._confirmar("fim")
    opcoes = para_opcoes(tela._valores(), tela.config_app.padroes)
    assert (opcoes.video_inicio_s, opcoes.video_fim_s) == (3.0, 15.0)
    assert tela.fonte.janela_util() == (3.0, 15.0)  # os quadros de exemplo saem de dentro dela
    assert tela.entrada.rotulo_estimativa.cget("text") != ""  # a estimativa foi refeita
    assert antes


@pytest.mark.ffmpeg
def test_escolher_outro_video_zera_o_comeco_e_o_fim(tela, video_teste, video_falso):
    escolher_video(tela, video_teste)
    tela.entrada.aparar.var_inicio.set("0:03")
    tela.entrada.definir_video(video_falso)
    assert tela.entrada.aparar.valores() == {"video_inicio": "", "video_fim": ""}


def test_comeco_e_fim_nao_viram_preferencia_para_o_proximo_video(tela):
    valores = ValoresFormulario(video_inicio="0:07", video_fim="1:00:00", zoom_pct=130.0)
    tela.opcoes.aplicar_valores(valores)
    tela.entrada.aplicar_valores(valores)
    preferencias = tela._preferencias(OpcoesProcessamento())
    assert (preferencias.video_inicio_s, preferencias.video_fim_s) == (0.0, 0.0)
    assert preferencias.zoom_pct == 130.0  # o estilo, sim, fica salvo


def test_zoom_e_tratamento_da_tela_entram_nas_opcoes(tela):
    tela.entrada.zoom.zoom.set(125)
    tela.entrada.zoom._moveu_zoom()
    tela.opcoes.tratamento.ajustes["contraste"].set(30)
    tela.opcoes.tratamento._moveu("contraste")
    opcoes = para_opcoes(tela._valores(), tela.config_app.padroes)
    assert opcoes.zoom_pct == 125.0 and opcoes.enquadramento.ativo
    assert opcoes.tratamento_ativo and opcoes.tratamento_contraste == 30.0
    enquadramento, tratamento = tela._estilo()
    assert enquadramento.zoom_pct == 125 and tratamento.contraste == 30


def test_abrir_projeto_traz_de_volta_corte_zoom_e_tratamento(tela, tmp_path, video_falso):
    from spurgeon.shorts.etapas import gravar_cortes
    from tests.test_shorts import cortes_do_sermao, gravar_transcricao, sermao_falso

    opcoes = OpcoesProcessamento(
        gerar_shorts=True, video_inicio_s=7.0, video_fim_s=3000.0, zoom_pct=140.0,
        zoom_centro_x_pct=54.0, tratamento_ativo=True, tratamento_saturacao=15.0,
        fade_vinhetas=True,
    )  # fmt: skip
    criado = criar_ou_retomar_projeto(
        tmp_path / "Projetos", video_falso, "Com Estilo", date(2026, 10, 4), opcoes
    )
    palavras = sermao_falso(frases=100)
    gravar_transcricao(criado.pastas.transcricao, palavras)
    gravar_cortes(criado.pastas.cortes_shorts, cortes_do_sermao(palavras))
    tela._abrir_projeto(criado.pastas.raiz)
    v = tela._valores()
    assert (v.video_inicio, v.video_fim) == ("0:07,0", "50:00,0")
    assert v.zoom_pct == 140.0 and v.zoom_centro_x_pct == 54.0
    assert v.tratamento_ativo and v.tratamento_saturacao == 15.0
    assert v.fade_vinhetas
    # o Studio só usa a fala do trecho do vídeo que o projeto usa
    assert tela.studio.editor is not None
    assert all(p.inicio >= 7.0 for p in tela.studio.editor._palavras)


def test_modo_so_vinhetas_apaga_corte_zoom_e_tratamento(tela):
    tela.opcoes.definir_modo("so_vinhetas")
    tela._atualizar_estimativa()
    assert str(tela.entrada.aparar._botoes[0].cget("state")) == "disabled"
    assert str(tela.entrada.zoom._filhos[0].cget("state")) == "disabled"
    assert str(tela.opcoes.tratamento._check_ativo.cget("state")) == "disabled"
    tela.opcoes.definir_modo("completo")
    tela._atualizar_estimativa()
    assert str(tela.entrada.aparar._botoes[0].cget("state")) == "normal"
    assert str(tela.opcoes.tratamento._check_ativo.cget("state")) == "normal"


def test_bloquear_durante_o_processamento_trava_os_novos_controles(tela):
    tela._bloquear(True)
    assert str(tela.entrada.aparar._botoes[0].cget("state")) == "disabled"
    assert str(tela.entrada.zoom._filhos[0].cget("state")) == "disabled"
    tela._bloquear(False)
    assert str(tela.entrada.aparar._botoes[0].cget("state")) == "normal"


def test_zoom_nos_shorts_so_fica_ativo_com_shorts(tela):
    tela._atualizar_estimativa()
    assert str(tela.entrada.zoom._check_shorts.cget("state")) == "disabled"
    tela.opcoes.bools["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    assert str(tela.entrada.zoom._check_shorts.cget("state")) == "normal"
    assert str(tela.opcoes.tratamento._check_shorts.cget("state")) == "normal"


def test_o_studio_recebe_a_janela_e_a_aparencia_da_tela(tela):
    tela.entrada.aparar.var_inicio.set("0:05")
    assert tela.studio._janela_do_video() == (5.0, None)
    antes = tela._assinatura_visual()
    tela.entrada.zoom.zoom.set(150)
    tela.entrada.zoom._moveu_zoom()
    assert tela._assinatura_visual() != antes


def test_presets_da_tela_vao_para_a_configuracao_do_programa(tela, monkeypatch):
    monkeypatch.setattr(dialogos, "pedir_texto", lambda *_a, **_k: "Meu estilo")
    tela.opcoes.tratamento.ajustes["brilho"].set(15)
    tela.opcoes.tratamento._moveu("brilho")
    tela.opcoes.tratamento.salvar_preset()
    assert tela.config_app.presets_tratamento["Meu estilo"].brilho == 15
    tela.entrada.zoom.zoom.set(130)
    tela.entrada.zoom._moveu_zoom()
    tela.entrada.zoom.salvar_preset()
    assert tela.config_app.presets_enquadramento["Meu estilo"].zoom_pct == 130
    del tela.config_app.presets_tratamento["Meu estilo"]
    del tela.config_app.presets_enquadramento["Meu estilo"]


def test_iniciar_recusa_comeco_e_fim_incoerentes(tela, video_falso):
    tela.entrada.var_video.set(str(video_falso))
    tela.entrada.aparar.var_inicio.set("2:00")
    tela.entrada.aparar.var_fim.set("1:00")
    tela._iniciar()
    assert [t for t, _ in tela.caixas.mostradas] == ["Confira os campos"]
    assert "depois do ponto" in tela.caixas.mostradas[0][1]


def test_estimativa_considera_so_o_trecho_usado():
    from spurgeon.ui.estimativa import estimar_tempo_total

    inteiro = OpcoesProcessamento(qualidade="rapido")
    cortado = OpcoesProcessamento(qualidade="rapido", video_inicio_s=600.0, video_fim_s=1800.0)
    _total_i, render_i = estimar_tempo_total(3600.0, inteiro, hdr=True)
    _total_c, render_c = estimar_tempo_total(3600.0, cortado, hdr=True)
    assert render_c == pytest.approx(render_i * 1200 / 3600)


def test_preflight_recusa_trecho_sem_video(tmp_path, video_falso, monkeypatch):
    from spurgeon.core import preflight

    monkeypatch.setattr(preflight, "sondar", lambda _v: SimpleNamespace(duracao_s=100.0))
    ruim = preflight._janela_do_video(video_falso, OpcoesProcessamento(video_inicio_s=95.0))
    assert ruim.nivel == preflight.Nivel.ERRO and "não tem nem 10 s" in ruim.detalhe
    bom = preflight._janela_do_video(
        video_falso, OpcoesProcessamento(video_inicio_s=7.0, video_fim_s=80.0)
    )
    assert bom.nivel == preflight.Nivel.OK and "7 s a 80 s" in bom.detalhe
