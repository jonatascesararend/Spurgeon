import pytest
from pydantic import ValidationError

from spurgeon.core.config import (
    PRESETS_QUALIDADE,
    ConfigApp,
    OpcoesProcessamento,
    carregar_config,
    salvar_config,
)
from spurgeon.core.erros import ErroConfiguracao


def test_padroes_seguem_o_projeto():
    o = OpcoesProcessamento()
    assert o.limiar_pausa_s == 10.0
    assert o.pausa_residual_s == 1.0
    assert o.uso_cpu_percentual == 75
    assert o.whisper_modelo == "large-v3-turbo"
    assert (o.shorts_duracao_min_s, o.shorts_duracao_max_s) == (30.0, 60.0)
    assert o.pausar_revisar_transcricao and o.pausar_aprovar_cortes
    assert o.qualidade == "maxima"
    assert o.preset_qualidade().preset == "veryslow"
    assert o.preset_qualidade().crf == 16


@pytest.mark.parametrize("limiar", [0.4, 30.1])
def test_limiar_de_pausa_fora_da_faixa_e_recusado(limiar):
    with pytest.raises(ValidationError):
        OpcoesProcessamento(limiar_pausa_s=limiar)


@pytest.mark.parametrize("limiar", [0.5, 10.5, 30.0])
def test_limiar_de_pausa_decimal_dentro_da_faixa(limiar):
    assert OpcoesProcessamento(limiar_pausa_s=limiar).limiar_pausa_s == limiar


def test_short_nao_passa_de_60_segundos():
    with pytest.raises(ValidationError):
        OpcoesProcessamento(shorts_duracao_max_s=61)


def test_duracao_minima_nao_passa_da_maxima():
    with pytest.raises(ValidationError):
        OpcoesProcessamento(shorts_duracao_min_s=50, shorts_duracao_max_s=40)


def test_uso_de_cpu_so_aceita_75_ou_100():
    assert OpcoesProcessamento(uso_cpu_percentual=100).uso_cpu_percentual == 100
    with pytest.raises(ValidationError):
        OpcoesProcessamento(uso_cpu_percentual=80)


def test_preset_personalizado():
    o = OpcoesProcessamento(qualidade="personalizado", x264_preset_personalizado="fast",
                            crf_personalizado=22)  # fmt: skip
    assert (o.preset_qualidade().preset, o.preset_qualidade().crf) == ("fast", 22)
    with pytest.raises(ValidationError):
        OpcoesProcessamento(x264_preset_personalizado="nao-existe")


def test_presets_da_tabela_do_projeto():
    assert {k: (v.preset, v.crf) for k, v in PRESETS_QUALIDADE.items()} == {
        "maxima": ("veryslow", 16),
        "alta": ("slow", 18),
        "equilibrado": ("medium", 20),
        "rapido": ("veryfast", 23),
    }


def test_combinacoes_invalidas_de_opcoes():
    assert OpcoesProcessamento().problemas() == []
    assert OpcoesProcessamento(gerar_video_longo=False).problemas()
    assert OpcoesProcessamento(legendas_dinamicas=True).problemas()
    assert OpcoesProcessamento(gerar_video_longo=False, gerar_shorts=True,
                               introducao=True).problemas()  # fmt: skip
    assert OpcoesProcessamento(gerar_shorts=True, legendas_dinamicas=True).problemas() == []
    with pytest.raises(ErroConfiguracao):
        OpcoesProcessamento(gerar_video_longo=False).validar_para_execucao()


def test_assinatura_muda_so_com_os_campos_pedidos():
    base = OpcoesProcessamento()
    assert base.assinatura(["limpar_audio"]) == OpcoesProcessamento().assinatura(["limpar_audio"])
    assert base.assinatura(["limpar_audio"]) != OpcoesProcessamento(limpar_audio=True).assinatura(
        ["limpar_audio"]
    )
    assert base.assinatura(["limpar_audio"]) == OpcoesProcessamento(
        normalizar_volume=True
    ).assinatura(["limpar_audio"])


def test_config_vai_e_volta_em_toml(tmp_path):
    arquivo = tmp_path / "config.toml"
    config = ConfigApp()
    config.padroes.limiar_pausa_s = 7.5
    config.pastas.projetos = tmp_path / "MeusProjetos"
    salvar_config(config, arquivo)
    lido = carregar_config(arquivo)
    assert lido.padroes.limiar_pausa_s == 7.5
    assert lido.pastas.projetos == tmp_path / "MeusProjetos"


def test_sem_arquivo_usa_os_padroes(tmp_path):
    assert carregar_config(tmp_path / "nao_existe.toml") == ConfigApp()


def test_config_invalida_da_erro_claro(tmp_path):
    arquivo = tmp_path / "config.toml"
    arquivo.write_text("[padroes]\nlimiar_pausa_s = 99\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao):
        carregar_config(arquivo)
    arquivo.write_text("isto não é toml ===", encoding="utf-8")
    with pytest.raises(ErroConfiguracao):
        carregar_config(arquivo)


def test_chaves_desconhecidas_sao_ignoradas(tmp_path):
    arquivo = tmp_path / "config.toml"
    arquivo.write_text("[padroes]\nopcao_de_versao_futura = true\n", encoding="utf-8")
    assert carregar_config(arquivo).padroes == OpcoesProcessamento()
