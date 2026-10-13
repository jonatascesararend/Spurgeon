import pytest

from spurgeon.ui.estimativa import EstimadorTempo, formatar_duracao, formatar_restante


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora

    def passar(self, segundos):
        self.agora += segundos


@pytest.fixture
def relogio():
    return Relogio()


def test_sem_dados_suficientes_nao_estima(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar()
    relogio.passar(2)
    est.atualizar(0.5)  # pouco tempo decorrido
    assert est.restante(0.5) is None
    est.atualizar(0.001)  # pouco progresso
    assert est.restante(0.001) is None


def test_estima_pela_velocidade_observada(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar()
    relogio.passar(60)
    est.atualizar(0.25)  # 25% em 60 s => 240 s no total
    assert est.restante(0.25) == pytest.approx(180)
    relogio.passar(60)
    assert est.restante(0.5) == pytest.approx(120, rel=0.01)


def test_tempo_em_pausa_nao_conta(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar()
    relogio.passar(30)
    est.pausar()
    relogio.passar(600)  # o usuário foi almoçar
    est.retomar()
    relogio.passar(30)
    assert est.decorrido() == pytest.approx(60)
    est.atualizar(0.25)
    assert est.restante(0.25) == pytest.approx(180)


def test_decorrido_nao_avanca_durante_a_pausa(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar()
    relogio.passar(10)
    est.pausar()
    relogio.passar(100)
    assert est.decorrido() == pytest.approx(10)


def test_retomando_um_projeto_so_conta_o_que_falta(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar(fracao_inicial=0.5)  # metade já estava pronta
    relogio.passar(60)
    est.atualizar(0.75)  # fez 25 pontos em 60 s; faltam 25 => mais 60 s
    assert est.restante(0.75) == pytest.approx(60, rel=0.01)


def test_media_movel_suaviza_oscilacoes(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar()
    relogio.passar(60)
    est.atualizar(0.25)
    primeira = est.restante(0.25)
    relogio.passar(10)
    est.atualizar(0.26)  # ritmo mais lento: a medida bruta daria ~199 s, mas só pesa 30%
    bruta = (70 / 0.26) * 0.74
    assert primeira < est.restante(0.26) < bruta


def test_restante_nunca_fica_negativo(relogio):
    est = EstimadorTempo(relogio)
    est.iniciar()
    relogio.passar(60)
    est.atualizar(0.5)
    assert est.restante(1.0) == 0.0


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [(0, "0 s"), (45, "45 s"), (125, "2 min 05 s"), (3600, "1 h 00 min"), (5025, "1 h 23 min")],
)
def test_formatar_duracao(segundos, esperado):
    assert formatar_duracao(segundos) == esperado


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [
        (None, "calculando…"),
        (20, "menos de 1 min"),
        (60, "cerca de 1 min"),
        (750, "cerca de 12 min"),
        (4800, "cerca de 1 h 20 min"),
    ],
)
def test_formatar_restante(segundos, esperado):
    assert formatar_restante(segundos) == esperado


# --- estimativa antes de começar -------------------------------------------------------------


def test_estimativa_do_sermao_real_com_maxima_qualidade_fica_na_casa_das_horas():
    from spurgeon.core.config import OpcoesProcessamento
    from spurgeon.ui.estimativa import estimar_tempo_total

    opcoes = OpcoesProcessamento(gerar_shorts=True, limpar_audio=True, normalizar_volume=True)
    total, render = estimar_tempo_total(3900.0, opcoes, hdr=True)
    assert 4 * 3600 < render < 5.5 * 3600  # ~4 h 06 do vídeo longo + os Shorts
    assert render < total < 9 * 3600


def test_qualidade_menor_e_video_sdr_diminuem_a_estimativa():
    from spurgeon.core.config import OpcoesProcessamento
    from spurgeon.ui.estimativa import estimar_tempo_total

    maxima = estimar_tempo_total(3900.0, OpcoesProcessamento(), hdr=True)[1]
    rapido = estimar_tempo_total(3900.0, OpcoesProcessamento(qualidade="rapido"), hdr=True)[1]
    sdr = estimar_tempo_total(3900.0, OpcoesProcessamento(), hdr=False)[1]
    assert rapido < maxima / 3 and sdr < maxima


def test_so_o_video_longo_nao_conta_a_ia_nem_os_shorts():
    from spurgeon.core.config import OpcoesProcessamento
    from spurgeon.ui.estimativa import estimar_tempo_total

    sem = estimar_tempo_total(3900.0, OpcoesProcessamento(), hdr=True)[0]
    com = estimar_tempo_total(3900.0, OpcoesProcessamento(gerar_shorts=True), hdr=True)[0]
    assert com - sem > 3900 * 0.9  # a IA e a renderização dos Shorts somam mais de uma hora


def test_texto_da_estimativa():
    from spurgeon.ui.estimativa import formatar_estimativa

    assert formatar_estimativa(300, 0) == "Tempo estimado nesta máquina: cerca de 5 min."
    texto = formatar_estimativa(7 * 3600 + 1800, 4 * 3600 + 360)
    assert "cerca de 7 h 30 min" in texto and "só a renderização: 4 h 06 min" in texto
