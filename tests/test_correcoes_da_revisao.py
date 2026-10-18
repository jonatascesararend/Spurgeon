"""Correções feitas depois da revisão independente da rodada 1.2."""

from __future__ import annotations

import hashlib
import json

import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.formatacao import formatar_tempo_preciso
from spurgeon.shorts import etapas as etapas_mod
from spurgeon.shorts.etapas import (
    AnalisarShorts,
    AvaliarShorts,
    SelecionarShorts,
    _mesmo_trecho,
    carregar_cortes,
    gravar_cortes,
)
from spurgeon.ui.formulario import ValoresFormulario, para_opcoes
from tests import test_shorts as base
from tests.test_render import corte

ctx_shorts, ollama = base.ctx_shorts, base.ollama  # fixtures do arquivo de Shorts
rodar = base.rodar


# --- título de outro trecho nunca é reaproveitado ---------------------------------------------


def test_mesmo_trecho_aceita_corte_editado_mas_nao_outro_trecho():
    original = corte(1, 100.0, 150.0)
    assert _mesmo_trecho(corte(1, 102.0, 148.0), original)  # ajustado pela pessoa
    assert not _mesmo_trecho(corte(1, 300.0, 350.0), original)  # outro lugar, mesmo número
    assert not _mesmo_trecho(corte(1, 140.0, 200.0), original)  # só encosta


def test_titulo_do_corte_antigo_nao_vai_para_um_trecho_diferente_com_o_mesmo_numero(
    ctx_shorts, ollama
):
    for etapa in (AnalisarShorts(), AvaliarShorts(), SelecionarShorts()):
        rodar(etapa, ctx_shorts)
    cortes = carregar_cortes(ctx_shorts.pastas.cortes_shorts)
    cortes[0].titulo = "Título do trecho antigo"
    cortes[0].inicio += 2000.0  # a análise foi refeita: este número agora é outro trecho
    cortes[0].fim += 2000.0
    gravar_cortes(ctx_shorts.pastas.cortes_shorts, cortes)
    id_ = cortes[0].id
    rodar(SelecionarShorts(), ctx_shorts)
    novo = {c.id: c for c in carregar_cortes(ctx_shorts.pastas.cortes_shorts)}[id_]
    assert novo.titulo != "Título do trecho antigo"


# --- as avaliações de outros trechos não se perdem --------------------------------------------


def test_avaliacoes_de_uma_analise_anterior_continuam_guardadas(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    antes = set(json.loads(ctx_shorts.pastas.avaliacoes.read_text())["avaliacoes"])
    ctx_shorts.opcoes.shorts_duracao_max_s = 60.0  # outros candidatos
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    depois = set(json.loads(ctx_shorts.pastas.avaliacoes.read_text())["avaliacoes"])
    assert antes <= depois  # as avaliações dos trechos longos não foram apagadas
    ctx_shorts.opcoes.shorts_duracao_max_s = 180.0  # voltar: nada precisa ser avaliado de novo
    ollama.chamadas.clear()
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    assert ollama.chamadas == []


def test_avaliacoes_de_outro_modelo_nao_se_misturam(ctx_shorts, ollama):
    rodar(AnalisarShorts(), ctx_shorts)
    rodar(AvaliarShorts(), ctx_shorts)
    _, candidatos = etapas_mod.carregar_candidatos(ctx_shorts.pastas.candidatos)
    etapas_mod._gravar_avaliacoes(ctx_shorts.pastas.avaliacoes, "outro:1b", {}, candidatos)
    gravado = json.loads(ctx_shorts.pastas.avaliacoes.read_text())
    assert gravado["modelo"] == "outro:1b" and gravado["avaliacoes"] == {}


# --- a assinatura ignora o que está desligado -------------------------------------------------


def antiga(opcoes: OpcoesProcessamento, campos) -> str:
    """A fórmula de antes da 1.2: todos os campos pedidos, crus."""
    dados = opcoes.model_dump(mode="json")
    recorte = {c: dados[c] for c in sorted(campos)}
    return hashlib.sha1(
        json.dumps(recorte, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]


def test_projeto_sem_nada_novo_mantem_a_assinatura_de_antes():
    campos = ("qualidade", "crf_personalizado", "introducao", "marca_dagua", "limiar_pausa_s")
    todas = (*campos, "zoom_pct", "tratamento_ativo", "video_inicio_s", "fade_vinhetas")
    o = OpcoesProcessamento()
    assert o.assinatura(todas) == antiga(o, campos)  # os campos novos, no padrão, não entram


def test_opcoes_desligadas_nao_mudam_a_assinatura():
    campos = (
        "zoom_pct", "zoom_centro_x_pct", "tratamento_ativo", "tratamento_brilho",
        "introducao_arquivo", "fade_vinhetas", "fade_vinhetas_s",
    )  # fmt: skip
    base_ = OpcoesProcessamento().assinatura(campos)
    assert OpcoesProcessamento(zoom_centro_x_pct=60).assinatura(campos) == base_  # sem zoom
    assert OpcoesProcessamento(tratamento_brilho=40).assinatura(campos) == base_  # desligado
    assert OpcoesProcessamento(introducao_arquivo="a.mp4").assinatura(campos) == base_
    assert OpcoesProcessamento(fade_vinhetas_s=2.0).assinatura(campos) == base_
    assert OpcoesProcessamento(fade_vinhetas=True).assinatura(campos) == base_  # sem vinheta


def test_opcoes_ligadas_mudam_a_assinatura():
    campos = (
        "zoom_pct", "zoom_centro_x_pct", "tratamento_ativo", "tratamento_brilho",
        "introducao_arquivo", "fade_vinhetas", "fade_vinhetas_s", "video_inicio_s",
    )  # fmt: skip
    base_ = OpcoesProcessamento().assinatura(campos)
    ligadas = [
        OpcoesProcessamento(zoom_pct=120),
        OpcoesProcessamento(zoom_pct=120, zoom_centro_x_pct=60),
        OpcoesProcessamento(tratamento_ativo=True, tratamento_brilho=10),
        OpcoesProcessamento(introducao=True, introducao_arquivo="a.mp4"),
        OpcoesProcessamento(introducao=True, fade_vinhetas=True),
        OpcoesProcessamento(introducao=True, fade_vinhetas=True, fade_vinhetas_s=1.0),
        OpcoesProcessamento(video_inicio_s=7.0),
    ]
    assinaturas = {o.assinatura(campos) for o in ligadas}
    assert base_ not in assinaturas and len(assinaturas) == len(ligadas)


def test_enquadramento_sem_zoom_ignora_o_centro():
    assert (
        OpcoesProcessamento(zoom_centro_x_pct=70).enquadramento
        == OpcoesProcessamento().enquadramento
    )
    assert OpcoesProcessamento(zoom_pct=130, zoom_centro_x_pct=70).enquadramento.centro_x_pct == 70


# --- tempos ------------------------------------------------------------------------------------


def test_tempo_exato_da_linha_de_comando_nao_e_arredondado_ao_reabrir():
    o = OpcoesProcessamento(video_inicio_s=7.25, video_fim_s=100.0)
    v = ValoresFormulario.de_opcoes(o)
    assert v.video_inicio == "0:07,250" and v.video_fim == "1:40,0"
    assert para_opcoes(v).video_inicio_s == pytest.approx(7.25)
    assert formatar_tempo_preciso(501.3) == "8:21,3"  # o formato de sempre, nos décimos
    assert formatar_tempo_preciso(501.3, casas=3) == "8:21,300"


def test_fim_negativo_ainda_no_campo_vale_ao_validar_o_formulario(video_teste):
    """Clicar em Iniciar não tira o foco do campo: o "-0:05" ainda está lá."""
    import customtkinter as ctk

    from spurgeon.core.ffmpeg import sondar
    from spurgeon.ui.assincrono import Tarefas
    from spurgeon.ui.painel_aparar import PainelAparar
    from spurgeon.ui.quadros import FonteDeQuadros

    try:
        raiz = ctk.CTk()
    except Exception:
        pytest.skip("sem ambiente gráfico")
    raiz.withdraw()
    try:
        fonte = FonteDeQuadros(Tarefas(lambda f: None))
        painel = PainelAparar(
            raiz, ValoresFormulario(), fonte=fonte, tarefas=Tarefas(lambda f: None),
            ao_mudar=lambda: None,
        )  # fmt: skip
        fonte.definir_video(video_teste, sondar(video_teste))
        painel.var_fim.set("-0:05")
        valores = painel.valores()
        assert valores["video_fim"] == "0:15,0"
        assert para_opcoes(ValoresFormulario(**valores)).video_fim_s == 15.0
    finally:
        raiz.destroy()
