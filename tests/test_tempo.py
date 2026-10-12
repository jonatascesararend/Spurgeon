import pytest

from spurgeon.core.tempo import MapaTempo, Trecho, calcular_trechos_mantidos


def test_corta_pausa_longa_e_deixa_o_residual():
    trechos = calcular_trechos_mantidos(100.0, [(20.0, 35.0)], limiar_s=10.0, residual_s=1.0)
    assert trechos == [Trecho(0.0, 20.5), Trecho(34.5, 100.0)]


def test_pausa_menor_que_o_limiar_nao_e_cortada():
    trechos = calcular_trechos_mantidos(100.0, [(20.0, 29.0)], limiar_s=10.0, residual_s=1.0)
    assert trechos == [Trecho(0.0, 100.0)]


def test_pausa_exatamente_no_limiar_e_cortada():
    trechos = calcular_trechos_mantidos(100.0, [(20.0, 30.0)], limiar_s=10.0, residual_s=0.0)
    assert trechos == [Trecho(0.0, 20.0), Trecho(30.0, 100.0)]


def test_pausas_que_se_sobrepoem_viram_um_corte():
    trechos = calcular_trechos_mantidos(
        100.0, [(10.0, 25.0), (24.0, 40.0)], limiar_s=10.0, residual_s=1.0
    )
    assert trechos == [Trecho(0.0, 10.5), Trecho(39.5, 100.0)]


def test_residual_maior_que_a_pausa_nao_corta():
    trechos = calcular_trechos_mantidos(100.0, [(20.0, 30.0)], limiar_s=5.0, residual_s=12.0)
    assert trechos == [Trecho(0.0, 100.0)]


def test_pausas_no_inicio_e_no_fim_do_video():
    trechos = calcular_trechos_mantidos(
        100.0, [(0.0, 12.0), (88.0, 100.0)], limiar_s=10.0, residual_s=1.0
    )
    assert trechos == [Trecho(0.0, 0.5), Trecho(11.5, 88.5), Trecho(99.5, 100.0)]


def test_pausa_que_passa_do_fim_e_limitada_a_duracao():
    trechos = calcular_trechos_mantidos(100.0, [(90.0, 130.0)], limiar_s=10.0, residual_s=0.0)
    assert trechos == [Trecho(0.0, 90.0)]


@pytest.fixture
def mapa():
    return MapaTempo([Trecho(0.0, 20.5), Trecho(34.5, 100.0)])


def test_duracao_editada(mapa):
    assert mapa.duracao_editada == pytest.approx(86.0)


def test_original_para_editado_antes_e_depois_do_corte(mapa):
    assert mapa.original_para_editado(10.0) == pytest.approx(10.0)
    assert mapa.original_para_editado(34.5) == pytest.approx(20.5)
    assert mapa.original_para_editado(50.0) == pytest.approx(36.0)


def test_tempo_dentro_de_pausa_cortada_cai_no_ponto_do_corte(mapa):
    assert mapa.original_para_editado(27.0) == pytest.approx(20.5)
    assert not mapa.mantido(27.0)
    assert mapa.mantido(10.0)


def test_ida_e_volta_nos_trechos_mantidos(mapa):
    for original in (0.0, 5.5, 34.5, 60.0, 100.0):
        editado = mapa.original_para_editado(original)
        assert mapa.editado_para_original(editado) == pytest.approx(original)


def test_no_ponto_do_corte_a_volta_vai_para_o_inicio_do_trecho_seguinte(mapa):
    # 20,5 (fim do 1º trecho) e 34,5 (início do 2º) caem no mesmo instante editado.
    assert mapa.original_para_editado(20.5) == pytest.approx(mapa.original_para_editado(34.5))
    assert mapa.editado_para_original(20.5) == pytest.approx(34.5)


def test_deslocamento_soma_a_duracao_da_introducao():
    mapa = MapaTempo([Trecho(0.0, 20.5), Trecho(34.5, 100.0)], deslocamento=5.0)
    assert mapa.original_para_editado(10.0) == pytest.approx(15.0)
    assert mapa.editado_para_original(15.0) == pytest.approx(10.0)


def test_intervalo_que_atravessa_um_corte_vira_dois_pedacos(mapa):
    assert mapa.remapear_intervalo(18.0, 40.0) == [
        (pytest.approx(18.0), pytest.approx(20.5)),
        (pytest.approx(20.5), pytest.approx(26.0)),
    ]


def test_intervalo_dentro_de_pausa_cortada_some(mapa):
    assert mapa.remapear_intervalo(25.0, 30.0) == []


def test_trechos_invalidos_sao_recusados():
    with pytest.raises(ValueError):
        MapaTempo([Trecho(10.0, 20.0), Trecho(15.0, 30.0)])
    with pytest.raises(ValueError):
        MapaTempo([Trecho(10.0, 10.0)])


def test_mapa_sem_trechos():
    mapa = MapaTempo([], deslocamento=2.0)
    assert mapa.duracao_editada == 0.0
    assert mapa.original_para_editado(5.0) == 2.0
