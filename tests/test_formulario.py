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
    assert para_opcoes(ValoresFormulario(shorts_max="lixo")).shorts_duracao_max_s == 60.0
    assert erros_de(gerar_shorts=True, shorts_max="lixo")[0].startswith("Duração máxima")


def test_short_maior_que_60_segundos_e_recusado():
    assert "entre 10 e 60" in erros_de(gerar_shorts=True, shorts_max="61")[0]


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
