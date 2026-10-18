import pytest

from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.core.pipeline import Etapa, Pipeline, ResultadoEtapa, ResultadoPipeline


class Roteiro:
    """Registra o que rodou e permite programar falha, cancelamento e revisão."""

    def __init__(self):
        self.chamadas: list[str] = []
        self.falhar: set[str] = set()
        self.cancelar_em: str | None = None
        self.revisar: set[str] = set()


def criar_etapas(roteiro: Roteiro) -> list[Etapa]:
    class Base(Etapa):
        def executar(self, ctx):
            roteiro.chamadas.append(self.id)
            if self.id in roteiro.falhar:
                raise RuntimeError(f"falha em {self.id}")
            if roteiro.cancelar_em == self.id:
                ctx.cancelar.set()
                ctx.ponto_seguro()
            (ctx.pastas.work / f"{self.id}.txt").write_text(self.id)
            if self.id in roteiro.revisar:
                return ResultadoEtapa.AGUARDANDO_REVISAO
            return ResultadoEtapa.CONCLUIDA

        def artefatos(self, ctx):
            return [ctx.pastas.work / f"{self.id}.txt"]

    class A(Base):
        id, nome = "a", "Etapa A"
        opcoes_relevantes = ("limpar_audio",)

    class B(Base):
        id, nome = "b", "Etapa B"
        depende_de = ("a",)

    class C(Base):
        id, nome = "c", "Etapa C"
        depende_de = ("b",)

        def ativa(self, opcoes):
            return opcoes.gerar_shorts

    return [A(), B(), C()]


@pytest.fixture
def roteiro():
    return Roteiro()


@pytest.fixture
def pipeline(roteiro):
    return Pipeline(criar_etapas(roteiro))


def status(ctx):
    return {e.id: e.status for e in ctx.estado.etapas}


def test_roda_as_etapas_ativas_em_ordem(pipeline, roteiro, contexto):
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    assert roteiro.chamadas == ["a", "b"]
    assert status(contexto) == {"a": StatusEtapa.CONCLUIDA, "b": StatusEtapa.CONCLUIDA}
    assert contexto.estado.etapa("a").artefatos == ["_work/a.txt"]


def test_grava_estado_e_log_no_projeto(pipeline, contexto):
    pipeline.executar(contexto)
    em_disco = carregar_estado(contexto.pastas.raiz)
    assert [e.status for e in em_disco.etapas] == [StatusEtapa.CONCLUIDA] * 2
    log = contexto.pastas.log.read_text(encoding="utf-8")
    assert "Iniciando: Etapa A" in log
    assert "Concluído: Etapa B" in log
    assert "| a " in log  # coluna da etapa


def test_retomada_pula_o_que_ja_terminou(pipeline, roteiro, contexto):
    pipeline.executar(contexto)
    roteiro.chamadas.clear()
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    assert roteiro.chamadas == []
    assert "já concluída" in contexto.pastas.log.read_text(encoding="utf-8")


def test_erro_para_o_pipeline_e_a_retomada_refaz_so_a_etapa_com_erro(pipeline, roteiro, contexto):
    roteiro.falhar = {"b"}
    assert pipeline.executar(contexto) == ResultadoPipeline.ERRO
    assert status(contexto) == {"a": StatusEtapa.CONCLUIDA, "b": StatusEtapa.ERRO}
    assert "falha em b" in contexto.estado.etapa("b").erro
    assert "Traceback" in contexto.pastas.log.read_text(encoding="utf-8")

    roteiro.falhar = set()
    roteiro.chamadas.clear()
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    assert roteiro.chamadas == ["b"]


def test_cancelamento_deixa_o_projeto_retomavel(pipeline, roteiro, contexto):
    roteiro.cancelar_em = "b"
    assert pipeline.executar(contexto) == ResultadoPipeline.CANCELADO
    assert status(contexto)["b"] == StatusEtapa.CANCELADA

    roteiro.cancelar_em = None
    contexto.cancelar.clear()
    roteiro.chamadas.clear()
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    assert roteiro.chamadas == ["b"]


def test_ponto_de_revisao_pausa_e_so_segue_apos_aprovar(pipeline, roteiro, contexto):
    contexto.opcoes.gerar_shorts = True
    roteiro.revisar = {"b"}
    assert pipeline.executar(contexto) == ResultadoPipeline.AGUARDANDO_REVISAO
    assert status(contexto)["b"] == StatusEtapa.AGUARDANDO_REVISAO
    assert roteiro.chamadas == ["a", "b"]

    # Reabrir sem aprovar continua parado, sem rodar nada.
    assert pipeline.executar(contexto) == ResultadoPipeline.AGUARDANDO_REVISAO
    assert roteiro.chamadas == ["a", "b"]

    pipeline.aprovar_revisao(contexto, "b")
    assert pipeline.executar(contexto) == ResultadoPipeline.CONCLUIDO
    assert roteiro.chamadas == ["a", "b", "c"]


def test_so_aprova_o_que_esta_aguardando_revisao(pipeline, contexto):
    pipeline.executar(contexto)
    with pytest.raises(ValueError):
        pipeline.aprovar_revisao(contexto, "a")


def test_mudar_opcao_refaz_a_etapa_e_quem_depende_dela(pipeline, roteiro, contexto):
    pipeline.executar(contexto)
    roteiro.chamadas.clear()
    contexto.opcoes.limpar_audio = True
    pipeline.executar(contexto)
    assert roteiro.chamadas == ["a", "b"]
    assert "as opções mudaram" in contexto.pastas.log.read_text(encoding="utf-8")


def test_opcao_irrelevante_nao_refaz_nada(pipeline, roteiro, contexto):
    pipeline.executar(contexto)
    roteiro.chamadas.clear()
    contexto.opcoes.normalizar_volume = True
    pipeline.executar(contexto)
    assert roteiro.chamadas == []


def test_arquivo_gerado_que_sumiu_so_refaz_a_propria_etapa(pipeline, roteiro, contexto):
    # o arquivo reaparece igual: o que veio depois dele (a transcrição) continua valendo
    pipeline.executar(contexto)
    (contexto.pastas.work / "a.txt").unlink()
    roteiro.chamadas.clear()
    pipeline.executar(contexto)
    assert roteiro.chamadas == ["a"]
    assert status(contexto)["b"] == StatusEtapa.CONCLUIDA


def test_etapa_inativa_fica_fora_do_estado_e_entra_quando_ativada(pipeline, roteiro, contexto):
    pipeline.executar(contexto)
    assert "c" not in status(contexto)

    contexto.opcoes.gerar_shorts = True
    roteiro.chamadas.clear()
    pipeline.executar(contexto)
    assert roteiro.chamadas == ["c"]
    assert status(contexto)["c"] == StatusEtapa.CONCLUIDA


def test_etapa_em_andamento_apos_queda_do_programa_e_refeita(pipeline, roteiro, contexto):
    pipeline.executar(contexto)
    contexto.estado.etapa("b").status = StatusEtapa.EM_ANDAMENTO  # como se o app tivesse caído
    roteiro.chamadas.clear()
    pipeline.executar(contexto)
    assert roteiro.chamadas == ["b"]


def test_progresso_total_e_o_das_etapas_ativas(pipeline, contexto):
    assert pipeline.progresso_total(contexto) == 0.0
    pipeline.executar(contexto)
    assert pipeline.progresso_total(contexto) == 1.0


def test_progresso_total_pondera_pelo_peso_da_etapa(roteiro, contexto):
    a, b, _ = criar_etapas(roteiro)
    type(a).peso = 1.0
    type(b).peso = 3.0
    pipeline = Pipeline([a, b])
    pipeline.preparar(contexto)
    contexto.estado.etapa("a").progresso = 1.0  # a (peso 1) pronta, b (peso 3) parada
    assert pipeline.progresso_total(contexto) == pytest.approx(0.25)


def test_avisa_o_progresso_no_inicio_e_no_fim_de_cada_etapa(pipeline, contexto):
    recebido = []
    contexto.ao_progresso = lambda id_etapa, fracao: recebido.append((id_etapa, fracao))
    pipeline.executar(contexto)
    assert recebido == [("a", 0.0), ("a", 1.0), ("b", 0.0), ("b", 1.0)]


def test_pausa_segura_segura_a_etapa_ate_retomar(contexto):
    import threading
    import time

    avisos = []
    contexto.ao_pausa = avisos.append
    contexto.pausar.set()
    terminou = threading.Event()

    def trabalhar():
        contexto.ponto_seguro()
        terminou.set()

    fio = threading.Thread(target=trabalhar)
    fio.start()
    time.sleep(0.5)
    assert not terminou.is_set()
    assert avisos == [True]
    contexto.pausar.clear()
    fio.join(timeout=3)
    assert terminou.is_set()
    assert avisos == [True, False]


def test_cancelar_durante_a_pausa_interrompe(contexto):
    import threading
    import time

    from spurgeon.core.erros import Cancelado

    contexto.pausar.set()
    resultado = []

    def trabalhar():
        try:
            contexto.ponto_seguro()
        except Cancelado:
            resultado.append("cancelado")

    fio = threading.Thread(target=trabalhar)
    fio.start()
    time.sleep(0.4)
    contexto.cancelar.set()
    fio.join(timeout=3)
    assert resultado == ["cancelado"]


def test_progresso_chega_ao_callback(pipeline, roteiro, contexto):
    recebido = []
    contexto.ao_progresso = lambda id_etapa, fracao: recebido.append((id_etapa, fracao))
    etapa = pipeline.etapas[0]
    contexto.etapa_id = etapa.id
    contexto.progresso(0.5)
    contexto.progresso(1.7)  # acima de 1 é limitado
    assert recebido == [("a", 0.5), ("a", 1.0)]


def test_dependencia_fora_de_ordem_e_ids_repetidos_sao_recusados(roteiro):
    a, b, _ = criar_etapas(roteiro)
    with pytest.raises(ValueError):
        Pipeline([b, a])
    with pytest.raises(ValueError):
        Pipeline([a, a])


def _linhas_do_log(contexto) -> str:
    return contexto.pastas.log.read_text(encoding="utf-8")


def test_log_diz_a_posicao_e_a_duracao_de_cada_etapa(pipeline, contexto):
    pipeline.executar(contexto)
    log = _linhas_do_log(contexto)
    assert "Iniciando: Etapa A (etapa 1 de 2)" in log
    assert "Iniciando: Etapa B (etapa 2 de 2)" in log
    assert "Concluído: Etapa A em " in log
    assert "Modo completo: 2 etapas ativas, 2 por fazer" in log


def test_executar_vigiado_devolve_o_resultado_e_avisa_enquanto_espera(contexto):
    import time

    def devagar():
        time.sleep(0.7)
        return 42

    resultado = contexto.executar_vigiado(
        "Passo lento", devagar, detalhe=lambda: "5 de 10 MB", intervalo_s=0.2
    )
    assert resultado == 42
    log = _linhas_do_log(contexto)
    assert "Passo lento: ainda em andamento há " in log
    assert "(5 de 10 MB)" in log


def test_executar_vigiado_repassa_a_falha_do_passo(contexto):
    def quebra():
        raise ValueError("deu ruim")

    with pytest.raises(ValueError, match="deu ruim"):
        contexto.executar_vigiado("Passo", quebra)


def test_executar_vigiado_cancela_na_hora_sem_esperar_o_passo(contexto):
    import threading
    import time

    from spurgeon.core.erros import Cancelado

    solto = threading.Event()
    contexto.cancelar.set()
    inicio = time.monotonic()
    with pytest.raises(Cancelado):
        contexto.executar_vigiado("Passo", lambda: solto.wait(30))
    assert time.monotonic() - inicio < 5
    solto.set()
    assert "Cancelamento pedido durante: Passo" in _linhas_do_log(contexto)


def test_executar_vigiado_avisa_que_a_pausa_so_vale_depois_do_passo(contexto):
    import time

    contexto.pausar.set()
    contexto.executar_vigiado("Passo", lambda: time.sleep(0.5))
    log = _linhas_do_log(contexto)
    assert "Pausa pedida: ela vale quando “Passo” terminar" in log


def test_executar_vigiado_alerta_quando_demora_demais(contexto):
    import time

    contexto.executar_vigiado(
        "Passo", lambda: time.sleep(0.6), alerta_apos_s=0.2, alerta="Confira o disco."
    )
    assert "Passo está demorando mais que o normal. Confira o disco." in _linhas_do_log(contexto)


def test_batimento_avisa_enquanto_o_bloco_roda(contexto):
    import time

    with contexto.batimento("Bloco 1", detalhe=lambda: "3 trechos", intervalo_s=0.2):
        time.sleep(0.7)
    log = _linhas_do_log(contexto)
    assert "Bloco 1: ainda em andamento há " in log
    assert "(3 trechos)" in log


def test_batimento_nao_diz_que_continua_trabalhando_enquanto_esta_em_pausa(contexto):
    import time

    contexto.pausar.set()
    with contexto.batimento("Bloco 1", intervalo_s=0.1):
        time.sleep(0.5)
    log = _linhas_do_log(contexto)
    assert "Pausa pedida: ela vale no próximo ponto seguro" in log
    assert "Bloco 1: ainda em andamento" not in log


def test_passo_demorado_mostra_no_log_onde_esta_parado(contexto):
    import time

    def trava_aqui():
        time.sleep(0.8)

    contexto.executar_vigiado("Passo", trava_aqui, alerta_apos_s=0.2, alerta="Confira o disco.")
    log = _linhas_do_log(contexto)
    assert "Onde o passo está parado agora" in log
    assert "trava_aqui" in log
