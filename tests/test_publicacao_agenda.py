"""O registro das postagens, o orquestrador de várias redes e o agendador local."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.erros import Cancelado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.agenda import (
    Agendador,
    RegistroDePostagens,
    criar_plataformas,
    publicar_em,
    resumo_das_redes,
)
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDePublicacao, ErroDeRede, ReconectarNecessario
from spurgeon.publicacao.modelo import (
    AGENDADO,
    ASSISTIDO,
    ENVIADO,
    ERRO,
    NA_FILA,
    Pacote,
    Plataforma,
    Resultado,
)
from spurgeon.publicacao.pacote import arquivo_do_short, pacote_do_corte, registro_do_projeto
from spurgeon.render.shorts import ler_registro
from spurgeon.shorts.dados import Corte

BRASILIA = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 2, 12, 0, tzinfo=BRASILIA)


class Falsa(Plataforma):
    """Uma rede de mentira: devolve o que o teste mandar, ou levanta o erro."""

    def __init__(self, chave: str, *, estado=ENVIADO, erro=None, retomada=None):
        super().__init__(None)  # type: ignore[arg-type]
        self.chave = chave
        self.nome = chave.title()
        self.estado, self.erro, self.retomada = estado, erro, retomada
        self.publicou = 0

    @property
    def conectada(self) -> bool:
        return True

    def publicar(self, pacote, quando, *, ao_progresso=None, cancelado=None):
        self.publicou += 1
        if ao_progresso:
            ao_progresso(0.5)
        if self.erro:
            raise self.erro
        return Resultado(self.chave, self.estado, f"id-{self.chave}")

    def retomar(self, resultado, pacote, *, ao_progresso=None, cancelado=None):
        if isinstance(self.retomada, Exception):
            raise self.retomada
        return self.retomada(resultado) if self.retomada else resultado


@pytest.fixture
def pacote(tmp_path) -> Pacote:
    arquivo = tmp_path / "v.mp4"
    arquivo.write_bytes(b"x")
    return Pacote(arquivo, "Título", "", (), 30.0)


@pytest.fixture
def registro(tmp_path) -> RegistroDePostagens:
    return RegistroDePostagens(tmp_path / "postagens.json")


# --- registro -------------------------------------------------------------------------------------
def test_registro_grava_le_e_remove(registro):
    assert registro.do_corte(7) == {}
    registro.gravar(7, Resultado("youtube", AGENDADO, "V1", quando="2026-11-04T15:00:00Z"))
    registro.gravar(7, Resultado("instagram", NA_FILA, quando="2026-11-04T15:00:00-03:00"))
    assert set(registro.do_corte(7)) == {"youtube", "instagram"}
    assert registro.do_corte(7)["youtube"].id_remoto == "V1"
    registro.remover(7, "youtube")
    assert set(registro.do_corte(7)) == {"instagram"}
    registro.remover(7, "instagram")
    assert registro.do_corte(7) == {} and registro._ler() == {}
    registro.remover(7, "nada")  # não quebra


def test_registro_na_fila_so_traz_a_fila_do_horario_mais_cedo_ao_mais_tarde(registro):
    registro.gravar(1, Resultado("instagram", NA_FILA, quando="2026-11-05T10:00:00-03:00"))
    registro.gravar(2, Resultado("instagram", NA_FILA, quando="2026-11-03T10:00:00-03:00"))
    registro.gravar(3, Resultado("youtube", AGENDADO, quando="2026-11-01T10:00:00Z"))
    assert [i for i, _ in registro.na_fila()] == [2, 1]


def test_registro_arquivo_estragado_nao_derruba(registro):
    registro.caminho.write_text("{não é json", "utf-8")
    assert registro.do_corte(1) == {} and registro.na_fila() == []
    registro.gravar(1, Resultado("youtube", ENVIADO))  # e volta a funcionar
    assert registro.do_corte(1)["youtube"].estado == ENVIADO


# --- publicar em várias redes ---------------------------------------------------------------------
def test_publicar_em_registra_cada_rede_e_uma_que_falha_nao_impede_as_outras(pacote, registro):
    redes = [
        Falsa("youtube"),
        Falsa("facebook", erro=ErroDePublicacao("o Reel passa de 90 s")),
        Falsa("instagram", estado=NA_FILA),
    ]
    andamento: list[tuple[str, float]] = []
    resultados = publicar_em(
        redes, 5, pacote, AGORA + timedelta(days=1), registro,
        ao_progresso=lambda chave, f: andamento.append((chave, f)),
    )  # fmt: skip
    assert [r.estado for r in resultados] == [ENVIADO, ERRO, NA_FILA]
    assert "90 s" in resultados[1].detalhe
    assert {r: v.estado for r, v in registro.do_corte(5).items()} == {
        "youtube": ENVIADO, "facebook": ERRO, "instagram": NA_FILA,
    }  # fmt: skip
    assert [c for c, _ in andamento] == ["youtube", "facebook", "instagram"]


def test_publicar_em_nao_repete_o_que_ja_esta_guardado_a_menos_que_force(pacote, registro):
    youtube, instagram = Falsa("youtube"), Falsa("instagram")
    registro.gravar(5, Resultado("youtube", AGENDADO, "V1"))
    registro.gravar(5, Resultado("instagram", ERRO, detalhe="deu ruim"))
    resultados = publicar_em([youtube, instagram], 5, pacote, None, registro)
    assert youtube.publicou == 0 and instagram.publicou == 1  # erro antigo não bloqueia
    assert resultados[0].id_remoto == "V1" and resultados[1].estado == ENVIADO
    publicar_em([youtube], 5, pacote, None, registro, forcar=True)
    assert youtube.publicou == 1


def test_publicar_em_cancelado_para_e_nao_grava_o_que_nao_rodou(pacote, registro):
    primeira, segunda = Falsa("youtube"), Falsa("tiktok")
    pedidos = iter([False, True])
    with pytest.raises(Cancelado):
        publicar_em([primeira, segunda], 5, pacote, None, registro, cancelado=lambda: next(pedidos))
    assert primeira.publicou == 1 and segunda.publicou == 0
    assert set(registro.do_corte(5)) == {"youtube"}


def test_publicar_em_erro_de_cancelamento_vindo_da_rede_sobe(pacote, registro):
    with pytest.raises(Cancelado):
        publicar_em([Falsa("youtube", erro=Cancelado("x"))], 5, pacote, None, registro)
    assert registro.do_corte(5) == {}


# --- agendador local ------------------------------------------------------------------------------
def _fila(registro, estado=NA_FILA):
    registro.gravar(9, Resultado("instagram", estado, quando="2026-11-03T10:00:00-03:00"))


def test_agendador_avanca_a_fila_e_devolve_so_o_que_mudou_de_estado(pacote, registro):
    _fila(registro)

    def publicar(r):
        return Resultado("instagram", ENVIADO, "M1", "https://i/m1")

    agendador = Agendador(
        {"instagram": Falsa("instagram", retomada=publicar)}, registro, lambda _id: pacote
    )
    mudaram = agendador.rodar_pendentes()
    assert [(i, r.estado) for i, r in mudaram] == [(9, ENVIADO)]
    assert registro.do_corte(9)["instagram"].id_remoto == "M1"
    assert agendador.rodar_pendentes() == []  # a fila esvaziou


def test_agendador_passo_intermediario_grava_mas_nao_conta_como_mudanca(pacote, registro):
    _fila(registro)

    def preparar(r):
        return Resultado("instagram", NA_FILA, quando=r.quando, dados={"fase": "pronto"})

    agendador = Agendador(
        {"instagram": Falsa("instagram", retomada=preparar)}, registro, lambda _id: pacote
    )
    assert agendador.rodar_pendentes() == []
    assert registro.do_corte(9)["instagram"].dados == {"fase": "pronto"}


def test_agendador_sem_internet_tenta_de_novo_no_proximo_minuto(pacote, registro):
    _fila(registro)
    sem_rede = ErroDeRede("Sem conexão", codigo=0)
    agendador = Agendador(
        {"instagram": Falsa("instagram", retomada=sem_rede)}, registro, lambda _id: pacote
    )
    assert agendador.rodar_pendentes() == []
    assert registro.do_corte(9)["instagram"].estado == NA_FILA  # continua na fila


@pytest.mark.parametrize(
    "erro",
    [
        ErroDeRede("recusou", codigo=400),
        ReconectarNecessario("conecte de novo"),
        ErroDePublicacao("vídeo recusado"),
    ],
)
def test_agendador_erro_definitivo_vira_erro_registrado(pacote, registro, erro):
    _fila(registro)
    agendador = Agendador(
        {"instagram": Falsa("instagram", retomada=erro)}, registro, lambda _id: pacote
    )
    mudaram = agendador.rodar_pendentes()
    assert [(i, r.estado) for i, r in mudaram] == [(9, ERRO)]
    guardado = registro.do_corte(9)["instagram"]
    assert guardado.estado == ERRO and str(erro) in guardado.detalhe
    assert guardado.quando == "2026-11-03T10:00:00-03:00"  # o horário não se perde


def test_agendador_short_sem_arquivo_vira_erro(registro):
    _fila(registro)
    agendador = Agendador({"instagram": Falsa("instagram")}, registro, lambda _id: None)
    assert agendador.rodar_pendentes()[0][1].estado == ERRO
    assert "renderize" in registro.do_corte(9)["instagram"].detalhe


def test_agendador_ignora_rede_desconhecida(pacote, registro):
    _fila(registro)
    assert Agendador({}, registro, lambda _id: pacote).rodar_pendentes() == []


def test_resumo_das_redes_para_a_lista(registro):
    resultados = {
        "youtube": Resultado("youtube", AGENDADO),
        "instagram": Resultado("instagram", NA_FILA),
        "tiktok": Resultado("tiktok", ASSISTIDO),  # só "preparado": sem dados, não polui a lista
    }
    assert (
        resumo_das_redes(resultados) == "YouTube agendado na rede · Instagram na fila do Spurgeon"
    )
    assert resumo_das_redes({}) == ""


def test_criar_plataformas_na_ordem_da_tela(tmp_path):
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    assert list(criar_plataformas(cofre)) == [
        "youtube", "instagram", "facebook", "tiktok", "upload_post",
    ]  # fmt: skip


# --- do Short do projeto ao pacote ----------------------------------------------------------------
def _corte(**extra) -> Corte:
    base = {
        "id": 4, "inicio": 10.0, "fim": 60.0, "texto": "t", "categoria": "outra", "nota": 80.0,
        "detalhe": {}, "titulo": "Deus é fiel", "descricao": "Palavra.", "hashtags": ["fé"],
    }  # fmt: skip
    return Corte(**{**base, **extra})


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    p = PastasProjeto(tmp_path / "proj")
    p.criar()
    p.shorts.mkdir(parents=True, exist_ok=True)
    return p


def test_pacote_do_corte_acha_o_arquivo_onde_o_registro_diz(pastas):
    corte = _corte()
    assert pacote_do_corte(pastas, corte) is None  # ainda não renderizado
    (pastas.shorts / "postados").mkdir()
    arquivo = pastas.shorts / "postados" / "short_01_deus.mp4"
    arquivo.write_bytes(b"video")
    escrever_json_atomico(
        pastas.shorts_renderizados,
        {"4": {"arquivo": "postados/short_01_deus.mp4", "assinatura": "a"}},
    )
    pacote = pacote_do_corte(pastas, corte)
    assert pacote is not None and pacote.video == arquivo
    assert (pacote.titulo, pacote.descricao, pacote.hashtags) == (
        "Deus é fiel",
        "Palavra.",
        ("fé",),
    )
    assert pacote.duracao_s == pytest.approx(50.0)
    assert arquivo_do_short(pastas, corte) == arquivo
    arquivo.unlink()  # registro sem arquivo
    assert pacote_do_corte(pastas, corte) is None
    assert ler_registro(pastas.shorts_renderizados)["4"]  # o registro não foi mexido


def test_registro_do_projeto_fica_em_work(pastas):
    assert registro_do_projeto(pastas).caminho == pastas.work / "postagens.json"
