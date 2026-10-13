"""Início e fim dos cortes ajustados ao silêncio de verdade (medidas de 50 ms sintéticas)."""

import pytest

from spurgeon.shorts.bordas import JANELA_S, refinar_bordas
from spurgeon.shorts.dados import Candidato

LIMIAR = -55.0
FALA, MUDO = -20.0, -80.0


def niveis(fala: list[tuple[float, float]], total: float = 60.0, ruido=()) -> list:
    """Janelas de 50 ms: FALA onde há voz, MUDO no resto; `ruido` = (início, fim, nível) extras."""
    medidas = []
    n = round(total / JANELA_S)
    for i in range(n):
        t = round(i * JANELA_S, 6)
        nivel = MUDO
        if any(a <= t < b for a, b in fala):
            nivel = FALA
        for a, b, db in ruido:
            if a <= t < b:
                nivel = db
        medidas.append((t, nivel))
    return medidas


def candidato(inicio: float, fim: float, antes: float, depois: float) -> Candidato:
    return Candidato(
        id=1, primeira_frase=0, ultima_frase=1, inicio=inicio, fim=fim, texto="t", pre_nota=0.0,
        sinais={"pausa_antes_s": antes, "pausa_depois_s": depois},
    )  # fmt: skip


def test_comeca_um_pouco_antes_de_a_voz_subir_mesmo_com_o_whisper_atrasado():
    # a voz sobe em 9,95 s, mas o Whisper diz que a 1ª palavra começa em 10,05 s
    medidas = niveis([(9.95, 40.0)])
    inicio, fim = refinar_bordas(candidato(10.05, 39.0, 2.0, 2.0), medidas, LIMIAR)
    assert inicio == pytest.approx(9.85, abs=0.001)  # 100 ms de silêncio antes da voz
    assert fim > 39.0  # a voz vai até 40,0 s: o corte espera ela morrer


def test_termina_depois_de_a_voz_morrer_e_nao_no_tempo_da_ultima_palavra():
    medidas = niveis([(10.0, 20.0)])
    inicio, fim = refinar_bordas(candidato(10.0, 19.8, 3.0, 3.0), medidas, LIMIAR)
    assert fim == pytest.approx(20.1, abs=0.001)  # a voz morre em 20,0 s + 100 ms de silêncio
    assert inicio == pytest.approx(9.9, abs=0.001)


def test_nao_invade_a_proxima_frase():
    # pausa de só 0,2 s entre uma frase e a seguinte, que começa em 20,2 s
    medidas = niveis([(10.0, 20.0), (20.2, 30.0)])
    _, fim = refinar_bordas(candidato(10.0, 19.9, 3.0, 0.3), medidas, LIMIAR)
    assert 20.0 <= fim <= 20.2


def test_sem_silencio_nenhum_corta_no_ponto_mais_baixo():
    medidas = niveis([(5.0, 30.0)], ruido=[(19.5, 19.55, -40.0)])  # um respiro fundo, mas audível
    _, fim = refinar_bordas(candidato(5.0, 19.2, 1.0, 1.0), medidas, LIMIAR)
    assert fim == pytest.approx(19.55, abs=0.06)


def test_frases_coladas_usam_o_respiro_curto():
    medidas = niveis([(5.0, 60.0)])
    inicio, fim = refinar_bordas(candidato(10.0, 20.0, 0.04, 0.04), medidas, LIMIAR)
    assert inicio == pytest.approx(10.0 - 0.02)
    assert fim == pytest.approx(20.0 + 0.02)


def test_nunca_passa_de_60_segundos():
    medidas = niveis([(10.0, 70.0)], total=80.0)
    inicio, fim = refinar_bordas(candidato(10.05, 69.4, 2.0, 2.0), medidas, LIMIAR)
    assert fim - inicio <= 60.0 + 1e-9
    assert inicio <= 10.05  # a primeira palavra continua dentro do corte
    assert fim >= 69.4  # e a última também


def test_o_corte_nao_comeca_antes_do_zero():
    medidas = niveis([(0.05, 12.0)])
    inicio, _ = refinar_bordas(candidato(0.1, 11.9, 0.1, 2.0), medidas, LIMIAR)
    assert inicio >= 0.0


def test_o_silencio_curto_no_meio_de_uma_pausa_longa_com_ruido_nao_engana():
    # na pausa há um estalo (−40 dB, acima do limiar) logo antes da fala: o corte vem depois dele
    medidas = niveis([(10.0, 20.0)], ruido=[(9.0, 9.1, -40.0)])
    inicio, _ = refinar_bordas(candidato(10.0, 19.9, 3.0, 3.0), medidas, LIMIAR)
    assert inicio > 9.1
