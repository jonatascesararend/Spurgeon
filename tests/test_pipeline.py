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
                ctx.verificar_cancelamento()
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


def test_arquivo_gerado_que_sumiu_refaz_a_etapa_e_as_seguintes(pipeline, roteiro, contexto):
    pipeline.executar(contexto)
    (contexto.pastas.work / "a.txt").unlink()
    roteiro.chamadas.clear()
    pipeline.executar(contexto)
    assert roteiro.chamadas == ["a", "b"]


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
    assert Pipeline.progresso_total(contexto) == 0.0
    pipeline.executar(contexto)
    assert Pipeline.progresso_total(contexto) == 1.0


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
