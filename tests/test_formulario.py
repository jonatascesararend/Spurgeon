from datetime import date
from pathlib import Path

import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.ui.formulario import (
    ErroFormulario,
    ValoresFormulario,
    aviso_qualidade,
    aviso_whisper,
    calcular_habilitacoes,
    formatar_data,
    ler_data,
    para_opcoes,
    rotulos_qualidade,
    rotulos_whisper,
    sugerir_nome,
)


def erros_de(**campos) -> list[str]:
    with pytest.raises(ErroFormulario) as erro:
        para_opcoes(ValoresFormulario(**campos))
    return erro.value.mensagens


def test_formulario_padrao_vira_as_opcoes_padrao():
    assert para_opcoes(ValoresFormulario()) == OpcoesProcessamento()


def test_ida_e_volta_entre_opcoes_e_formulario():
    opcoes = OpcoesProcessamento(
        limiar_pausa_s=12.5, gerar_shorts=True, legendas_dinamicas=True, uso_cpu_percentual=100,
        whisper_modelo="medium", qualidade="personalizado", crf_personalizado=22,
        x264_preset_personalizado="fast", legenda_posicao_vertical=0.7,
    )  # fmt: skip
    assert para_opcoes(ValoresFormulario.de_opcoes(opcoes)) == opcoes


def test_aceita_virgula_e_ponto_nos_decimais():
    assert para_opcoes(ValoresFormulario(limiar_pausa="7,5")).limiar_pausa_s == 7.5
    assert para_opcoes(ValoresFormulario(limiar_pausa="7.5")).limiar_pausa_s == 7.5


def test_mostra_uma_casa_decimal_nos_segundos():
    v = ValoresFormulario.de_opcoes(OpcoesProcessamento())
    assert (v.limiar_pausa, v.pausa_residual) == ("10,0", "1,0")


@pytest.mark.parametrize("texto", ["0,4", "30,1", "abc", ""])
def test_limiar_de_pausa_invalido(texto):
    mensagens = erros_de(limiar_pausa=texto)
    assert len(mensagens) == 1
    assert mensagens[0].startswith("Tamanho mínimo da pausa")


def test_limiar_de_pausa_nos_extremos_da_faixa():
    assert para_opcoes(ValoresFormulario(limiar_pausa="0,5")).limiar_pausa_s == 0.5
    assert para_opcoes(ValoresFormulario(limiar_pausa="30")).limiar_pausa_s == 30.0


def test_limiar_so_e_conferido_se_o_video_longo_esta_marcado():
    v = ValoresFormulario(gerar_video_longo=False, gerar_shorts=True, limiar_pausa="lixo")
    assert para_opcoes(v).gerar_shorts


def test_campos_de_shorts_so_valem_com_shorts_marcado():
    assert para_opcoes(ValoresFormulario(shorts_max="lixo")).shorts_duracao_max_s == 180.0
    assert erros_de(gerar_shorts=True, shorts_max="lixo")[0].startswith("Duração máxima")


def test_short_maior_que_180_segundos_e_recusado():
    assert "entre 10 e 180" in erros_de(gerar_shorts=True, shorts_max="181")[0]


def test_duracao_minima_maior_que_a_maxima():
    mensagens = erros_de(gerar_shorts=True, shorts_min="50", shorts_max="40")
    assert any("mínima" in m for m in mensagens)


def test_varios_erros_vem_juntos():
    mensagens = erros_de(limiar_pausa="x", pausa_residual="y")
    assert len(mensagens) == 2


def test_nenhum_produto_marcado_e_recusado():
    mensagens = erros_de(gerar_video_longo=False, gerar_shorts=False)
    assert "ao menos um produto" in mensagens[0]


def test_legenda_dinamica_sem_shorts_e_ignorada_nao_recusada():
    assert para_opcoes(ValoresFormulario(legendas_dinamicas=True)).legendas_dinamicas is False


def test_introducao_sem_video_longo_e_ignorada():
    v = ValoresFormulario(gerar_video_longo=False, gerar_shorts=True, introducao=True)
    assert para_opcoes(v).introducao is False


def test_preset_personalizado_valida_o_crf():
    assert "CRF" in erros_de(qualidade="personalizado", crf="99")[0]
    o = para_opcoes(ValoresFormulario(qualidade="personalizado", crf="18", x264_preset="slow"))
    assert (o.crf_personalizado, o.x264_preset_personalizado) == (18, "slow")


def test_opcoes_que_a_tela_nao_mostra_sao_preservadas():
    base = OpcoesProcessamento(lufs_shorts=-12.0, srt_max_linhas=3)
    o = para_opcoes(ValoresFormulario(), base)
    assert (o.lufs_shorts, o.srt_max_linhas) == (-12.0, 3)


def test_habilitacoes_seguem_as_escolhas():
    h = calcular_habilitacoes(
        ValoresFormulario(gerar_video_longo=True, gerar_shorts=False),
        introducao_existe=True, vinheta_existe=False,
    )  # fmt: skip
    assert h.limiar_pausa and h.introducao and not h.vinheta_final
    assert not h.legendas_dinamicas and not h.campos_shorts and h.pode_iniciar

    h = calcular_habilitacoes(
        ValoresFormulario(gerar_video_longo=False, gerar_shorts=True),
        introducao_existe=True, vinheta_existe=True,
    )  # fmt: skip
    assert not h.limiar_pausa and not h.introducao and not h.vinheta_final
    assert h.legendas_dinamicas and h.aprovar_cortes and h.campos_shorts

    h = calcular_habilitacoes(
        ValoresFormulario(gerar_video_longo=False, gerar_shorts=False),
        introducao_existe=True, vinheta_existe=True,
    )  # fmt: skip
    assert not h.pode_iniciar


def test_rotulos_do_whisper_vao_do_mais_leve_ao_mais_pesado():
    rotulos = rotulos_whisper("large-v3-turbo")
    assert list(rotulos.values()) == [
        "tiny", "base", "small", "medium", "large-v3-turbo", "large-v3",
    ]  # fmt: skip
    marcado = [r for r in rotulos if "recomendado" in r]
    assert marcado == ["large-v3-turbo · médio-pesado · recomendado para este PC"]
    assert all("recomendado" not in r for r in rotulos_whisper(None))


def test_rotulos_de_qualidade():
    rotulos = rotulos_qualidade()
    assert rotulos["Máxima qualidade (veryslow, CRF 16)"] == "maxima"
    assert rotulos["Personalizado"] == "personalizado"
    assert len(rotulos) == 5


def test_sugestao_de_nome_pelo_arquivo():
    resultado = sugerir_nome(Path("C:/x/sermao_graca__soberana_parte2.mp4"))
    assert resultado == "sermao graca soberana parte2"


def test_sugestao_de_nome_preserva_o_hifen_dos_versiculos():
    assert sugerir_nome(Path("Downloads/Miqueias 2. 6-11.MOV")) == "Miqueias 2. 6-11"


def test_data_no_formato_brasileiro():
    assert formatar_data(date(2026, 10, 12)) == "12/10/2026"
    assert ler_data(" 12/10/2026 ") == date(2026, 10, 12)
    for ruim in ("2026-10-12", "31/02/2026", "", "ontem"):
        with pytest.raises(ErroFormulario):
            ler_data(ruim)


def test_avisos_de_tempo():
    assert "horas" in aviso_whisper("large-v3", cuda_utilizavel=False)
    assert aviso_whisper("large-v3", cuda_utilizavel=True) is None
    assert aviso_whisper("large-v3-turbo", cuda_utilizavel=False) is None
    assert "testar" in aviso_whisper("tiny", cuda_utilizavel=True)
    assert "muito lenta" in aviso_qualidade("maxima", nvenc=False)
    assert aviso_qualidade("maxima", nvenc=True) is None
    assert aviso_qualidade("equilibrado", nvenc=False) is None


# --- modos e marca d'água -----------------------------------------------------------------------


def habilitacoes(**campos):
    return calcular_habilitacoes(
        ValoresFormulario(**campos), introducao_existe=True, vinheta_existe=True
    )


def test_modo_so_shorts_vira_so_shorts_e_nao_renderiza_o_video_longo():
    opcoes = para_opcoes(ValoresFormulario(modo="so_shorts", gerar_shorts=False))
    assert opcoes.modo == "so_shorts"
    assert opcoes.gerar_shorts is True  # neste modo os Shorts são o produto
    assert opcoes.processa_shorts and not opcoes.processa_video_longo


def test_modo_so_shorts_ignora_introducao_e_vinheta():
    opcoes = para_opcoes(ValoresFormulario(modo="so_shorts", introducao=True, vinheta_final=True))
    assert not opcoes.introducao and not opcoes.vinheta_final


def test_modo_so_vinhetas_exige_ao_menos_uma():
    assert any("introdução" in m for m in erros_de(modo="so_vinhetas"))
    opcoes = para_opcoes(ValoresFormulario(modo="so_vinhetas", vinheta_final=True))
    assert opcoes.so_vinhetas and opcoes.vinheta_final
    assert not opcoes.gerar_shorts and not opcoes.limpar_audio


def test_modo_so_vinhetas_desliga_shorts_marca_e_limpeza():
    v = ValoresFormulario(
        modo="so_vinhetas", introducao=True, gerar_shorts=True, limpar_audio=True,
        legendas_dinamicas=True, marca_dagua=True,
    )  # fmt: skip
    opcoes = para_opcoes(v)
    assert not (opcoes.gerar_shorts or opcoes.legendas_dinamicas or opcoes.marca_dagua)
    assert not opcoes.limpar_audio


def test_habilitacoes_por_modo():
    completo = habilitacoes()
    assert completo.gerar_video_longo and completo.gerar_shorts and completo.limiar_pausa
    assert completo.pode_iniciar and not completo.legendas_dinamicas
    shorts = habilitacoes(modo="so_shorts")
    assert not shorts.gerar_video_longo and not shorts.limiar_pausa and not shorts.introducao
    assert shorts.legendas_dinamicas and shorts.aprovar_cortes and shorts.pode_iniciar
    vinhetas = habilitacoes(modo="so_vinhetas")
    assert vinhetas.introducao and vinhetas.vinheta_final
    assert not (vinhetas.limpar_audio or vinhetas.marca_dagua or vinhetas.revisar_transcricao)
    assert not vinhetas.pode_iniciar  # nenhuma das duas marcada
    assert habilitacoes(modo="so_vinhetas", vinheta_final=True).pode_iniciar


def test_habilitacoes_da_marca_dependem_do_que_sera_gerado():
    sem_marca = habilitacoes()
    assert not (sem_marca.marca_campos or sem_marca.marca_no_video_longo)
    com = habilitacoes(marca_dagua=True)
    assert com.marca_campos and com.marca_no_video_longo and not com.marca_nos_shorts
    com_shorts = habilitacoes(marca_dagua=True, gerar_shorts=True, gerar_video_longo=False)
    assert com_shorts.marca_nos_shorts and not com_shorts.marca_no_video_longo


def test_marca_dagua_vai_para_as_opcoes(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    opcoes = para_opcoes(
        ValoresFormulario(
            marca_dagua=True,
            marca_imagem=str(logo),
            marca_canto="sup_esq",
            marca_tamanho_pct=22.0,
            marca_remover_fundo=True,
            marca_nos_shorts=False,
        )
    )
    assert opcoes.marca_dagua and opcoes.marca_imagem == str(logo)
    assert opcoes.marca_canto == "sup_esq" and opcoes.marca_tamanho_pct == 22.0
    assert opcoes.marca_no_longo and not opcoes.marca_nos_curtos
    assert ValoresFormulario.de_opcoes(opcoes).marca_canto == "sup_esq"


def test_marca_dagua_sem_imagem_ou_com_imagem_inexistente_e_erro(tmp_path):
    assert any("imagem" in m for m in erros_de(marca_dagua=True))
    assert any(
        "não foi encontrada" in m
        for m in erros_de(marca_dagua=True, marca_imagem=str(tmp_path / "sumiu.png"))
    )


def test_marca_dagua_tamanho_fora_da_faixa_e_erro(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    mensagens = erros_de(marca_dagua=True, marca_imagem=str(logo), marca_tamanho_pct=80.0)
    assert any("Tamanho da marca" in m for m in mensagens)


def test_marca_dagua_precisa_de_um_destino_entre_o_que_sera_gerado(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    # só o vídeo longo está sendo gerado, mas a marca só foi pedida nos Shorts
    mensagens = erros_de(marca_dagua=True, marca_imagem=str(logo), marca_no_video_longo=False)
    assert any("onde ela entra" in m for m in mensagens)


# --- começo e fim do vídeo, zoom, tratamento, vinhetas e Ollama ------------------------------


def test_comeco_e_fim_viram_segundos_do_original():
    o = para_opcoes(ValoresFormulario(video_inicio="0:07", video_fim="1:04:56,5"))
    assert (o.video_inicio_s, o.video_fim_s) == (7.0, 3896.5)
    padrao = para_opcoes(ValoresFormulario())
    assert (padrao.video_inicio_s, padrao.video_fim_s) == (0.0, 0.0)  # vazio: o vídeo todo


def test_comeco_e_fim_voltam_para_o_formulario_do_jeito_que_o_usuario_escreve():
    o = OpcoesProcessamento(video_inicio_s=7.0, video_fim_s=3896.5)
    v = ValoresFormulario.de_opcoes(o)
    assert (v.video_inicio, v.video_fim) == ("0:07,0", "1:04:56,5")
    assert para_opcoes(v) == o
    vazio = ValoresFormulario.de_opcoes(OpcoesProcessamento())
    assert (vazio.video_inicio, vazio.video_fim) == ("", "")


def test_comeco_e_fim_invalidos():
    assert any("Começar em" in m for m in erros_de(video_inicio="abc"))
    assert any("Terminar em" in m for m in erros_de(video_fim="1:xx"))
    assert any("depois do ponto" in m for m in erros_de(video_inicio="2:00", video_fim="1:00"))
    assert any("depois do ponto" in m for m in erros_de(video_inicio="1:00", video_fim="1:00"))


def test_tempo_negativo_conta_a_partir_do_fim_do_video():
    from spurgeon.ui.formulario import ler_tempo_do_video

    assert ler_tempo_do_video("-2:04", duracao_s=3900.0) == 3776.0
    assert ler_tempo_do_video("−2:04", duracao_s=3900.0) == 3776.0  # o sinal tipográfico também
    assert ler_tempo_do_video("0:07") == 7.0
    assert ler_tempo_do_video("-9:00", duracao_s=60.0) == 0.0  # nunca antes do começo
    with pytest.raises(ValueError, match="duração"):
        ler_tempo_do_video("-2:04")


def test_comeco_e_fim_so_valem_fora_do_modo_so_vinhetas():
    v = ValoresFormulario(modo="so_vinhetas", introducao=True, video_inicio="lixo")
    assert para_opcoes(v).video_inicio_s == 0.0  # nem é lido


@pytest.mark.parametrize("campo", ["zoom_pct", "zoom_centro_x_pct", "zoom_centro_y_pct"])
def test_zoom_fora_da_faixa_e_recusado(campo):
    assert erros_de(**{campo: 400.0 if campo == "zoom_pct" else 101.0})
    assert erros_de(**{campo: 50.0 if campo == "zoom_pct" else -1.0})


def test_zoom_e_tratamento_fazem_a_ida_e_volta():
    o = OpcoesProcessamento(
        zoom_pct=125.0, zoom_centro_x_pct=54.0, zoom_centro_y_pct=48.0, zoom_nos_shorts=False,
        tratamento_ativo=True, tratamento_brilho=10, tratamento_contraste=-5,
        tratamento_saturacao=20, tratamento_temperatura=-8, tratamento_matiz=3,
        tratamento_brancos=-12, tratamento_sombras=25, tratamento_nos_shorts=False,
    )  # fmt: skip
    assert para_opcoes(ValoresFormulario.de_opcoes(o)) == o


def test_ajuste_do_tratamento_fora_de_100_e_recusado():
    assert any("Brilho" in m for m in erros_de(tratamento_brilho=120.0))


def test_introducao_e_vinheta_escolhidas_precisam_existir(tmp_path):
    arquivo = tmp_path / "abertura.mp4"
    arquivo.write_bytes(b"x")
    v = ValoresFormulario(introducao=True, introducao_arquivo=str(arquivo))
    assert para_opcoes(v).introducao_arquivo == str(arquivo)
    ruim = ValoresFormulario(introducao=True, introducao_arquivo=str(tmp_path / "sumiu.mp4"))
    assert any("introdução não foi encontrado" in m for m in erros_de(**ruim.__dict__))
    ruim = ValoresFormulario(vinheta_final=True, vinheta_arquivo=str(tmp_path / "sumiu.mp4"))
    assert any("vinheta final não foi encontrado" in m for m in erros_de(**ruim.__dict__))
    # desmarcada, a escolha antiga não atrapalha
    assert para_opcoes(ValoresFormulario(introducao=False, introducao_arquivo="nada.mp4"))


def test_fade_so_e_conferido_com_introducao_ou_vinheta_e_o_fade_ligado():
    assert para_opcoes(ValoresFormulario(fade_vinhetas_s="lixo")).fade_vinhetas_s == 0.5
    assert para_opcoes(ValoresFormulario(fade_vinhetas=True, fade_vinhetas_s="lixo"))
    mensagens = erros_de(introducao=False, vinheta_final=True, fade_vinhetas=True,
                         fade_vinhetas_s="9", vinheta_arquivo="")  # fmt: skip
    assert any("Duração do fade" in m for m in mensagens)


def test_habilitacoes_do_fade_e_dos_arquivos():
    v = ValoresFormulario(introducao=False, vinheta_final=False)
    h = calcular_habilitacoes(v, introducao_existe=True, vinheta_existe=True)
    assert h.escolher_introducao and h.escolher_vinheta
    assert not h.fade_vinhetas and not h.fade_vinhetas_s  # sem introdução nem vinheta marcadas
    v = ValoresFormulario(introducao=True, fade_vinhetas=True)
    h = calcular_habilitacoes(v, introducao_existe=True, vinheta_existe=False)
    assert h.fade_vinhetas and h.fade_vinhetas_s
    h = calcular_habilitacoes(
        ValoresFormulario(modo="so_shorts", gerar_shorts=True),
        introducao_existe=True, vinheta_existe=True,
    )  # fmt: skip
    assert not h.escolher_introducao and not h.fade_vinhetas


def test_habilitacoes_de_corte_zoom_e_tratamento_no_modo_so_vinhetas():
    completo = calcular_habilitacoes(
        ValoresFormulario(), introducao_existe=True, vinheta_existe=True
    )
    assert completo.aparar and completo.zoom and completo.tratamento
    assert not completo.zoom_nos_shorts and not completo.tratamento_nos_shorts  # sem Shorts
    com_shorts = calcular_habilitacoes(
        ValoresFormulario(gerar_shorts=True), introducao_existe=True, vinheta_existe=True
    )
    assert com_shorts.zoom_nos_shorts and com_shorts.tratamento_nos_shorts
    vinhetas = calcular_habilitacoes(
        ValoresFormulario(modo="so_vinhetas", introducao=True),
        introducao_existe=True, vinheta_existe=True,
    )  # fmt: skip
    assert not vinhetas.aparar and not vinhetas.zoom and not vinhetas.tratamento


def test_lista_do_ollama_junta_instalados_recomendados_e_o_escolhido():
    from spurgeon.ui.formulario import rotulos_ollama

    rotulos = rotulos_ollama(["llama3.1:8b", "qwen3:8b"], "qwen3:8b")
    assert rotulos["llama3.1:8b"] == "llama3.1:8b" and rotulos["qwen3:8b"] == "qwen3:8b"
    assert rotulos["qwen3:4b · não instalado (será baixado)"] == "qwen3:4b"
    assert list(rotulos.values()).count("qwen3:8b") == 1  # o instalado não aparece de novo
    sem_servidor = rotulos_ollama([], "meu-modelo:7b")
    assert sem_servidor["meu-modelo:7b · não encontrado no Ollama"] == "meu-modelo:7b"
    assert "qwen3:8b · não instalado (será baixado)" in sem_servidor


def test_listar_modelos_instalados_lida_com_servidor_fora_do_ar(monkeypatch):
    import io
    import json

    from spurgeon.core import ollama as ollama_mod

    assert ollama_mod.listar_modelos_instalados("http://127.0.0.1:9", espera_s=0.2) == []

    class Resposta(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    corpo = json.dumps({"models": [{"name": "b:1"}, {"name": "a:2"}, {"x": 1}]}).encode()
    monkeypatch.setattr(ollama_mod.urllib.request, "urlopen", lambda *_a, **_k: Resposta(corpo))
    assert ollama_mod.listar_modelos_instalados() == ["a:2", "b:1"]
