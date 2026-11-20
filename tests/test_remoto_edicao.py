"""Controle remoto, Fase R2 (Remoto.md): a trava do celular, a edição dos cortes, a aprovação, a
renderização e a fila de processamento. Nada aqui roda o trabalhador de verdade nem toca a rede."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from spurgeon.core.config import ConfigApp
from spurgeon.core.estado import Estado, EstadoEtapa, StatusEtapa, salvar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto import dados, edicao, sessao
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.fila import Fila, FilaOcupada, config_da_execucao
from spurgeon.remoto.servidor import criar_app
from spurgeon.shorts.edicao import LimiteInvalido
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA, NOME_DO_PROJETO, criar_projeto
from tests.test_remoto_audicao import _transcrever


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    _transcrever(pastas)
    return pastas


def _corte(pastas, id_):
    return next(c for c in dados.carregar_cortes(pastas) if c.id == id_)


def _estado(pastas) -> Estado:
    return Estado.model_validate_json(pastas.estado.read_text("utf-8"))


class Relogio:
    def __init__(self, agora=1000.0):
        self.agora = agora

    def __call__(self):
        return self.agora


# --- a trava --------------------------------------------------------------------------------


def test_trava_renova_com_o_mesmo_id_e_vence_sem_batimento(pastas):
    relogio = Relogio()
    primeira = sessao.tomar(pastas, relogio)
    relogio.agora += 30
    assert sessao.tomar(pastas, relogio) == primeira and sessao.ativa(pastas, relogio)
    relogio.agora += sessao.VALIDADE_S + 1
    assert not sessao.ativa(pastas, relogio)
    assert sessao.tomar(pastas, relogio) != primeira  # venceu: sessão nova
    sessao.soltar(pastas)
    assert not sessao.ativa(pastas, relogio)


def test_servidor_espera_o_recibo_so_se_a_janela_esta_no_projeto(pastas):
    relogio = Relogio()
    id_ = sessao.tomar(pastas, relogio)
    assert sessao.esperar_o_pc(pastas, id_, relogio=relogio) == "sem_janela"

    sessao.anunciar_janela(pastas.raiz, relogio)
    assert sessao.janela_com_o_projeto(pastas, relogio)

    def dormir_e_responder(_s):
        sessao.dar_recibo(pastas, id_)

    assert sessao.esperar_o_pc(pastas, id_, dormir=dormir_e_responder, relogio=relogio) == "salvou"

    sessao.dar_recibo(pastas, "outra")

    def dormir_sem_resposta(s):
        relogio.agora += s

    assert (
        sessao.esperar_o_pc(pastas, id_, espera_s=1, dormir=dormir_sem_resposta, relogio=relogio)
        == "sem_resposta"
    )
    relogio.agora += sessao.VALIDADE_DA_JANELA_S + 1  # a janela parou de anunciar: fechou
    assert not sessao.janela_com_o_projeto(pastas, relogio)
    sessao.anunciar_janela(Path("C:/outro"), relogio)
    assert not sessao.janela_com_o_projeto(pastas, relogio)  # aberta, mas em outro projeto
    sessao.fechar_janela()


# --- editar um corte ------------------------------------------------------------------------


def test_para_editar_traz_a_legenda_em_blocos_e_as_palavras_em_volta(pastas):
    ed = edicao.para_editar(pastas, 7)  # 100 a 140 s: p200 a p279
    assert (ed["primeira"], ed["ultima"]) == (200, 279)
    assert "\n" in ed["legenda"] and ed["legenda"].split()[0] == "p200"
    fora = [w for w in ed["contexto"] if not w["dentro"]]
    assert fora[0]["n"] == 200 - edicao.PALAVRAS_DE_CONTEXTO and fora[-1]["n"] == 279 + 40
    assert not ed["corrigida"] and not ed["acima_do_limite"]


def test_corrigir_a_legenda_e_voltar_a_transcricao(pastas):
    original = " ".join(f"p{i}" for i in range(200, 280))
    corte = edicao.corrigir_legenda(pastas, 7, original.replace("p210", "Deus"))
    assert "Deus" in corte.legenda and "Deus" in _corte(pastas, 7).legenda
    assert edicao.corrigir_legenda(pastas, 7, original).legenda == ""  # igual: não é correção
    edicao.corrigir_legenda(pastas, 7, original.replace("p210", "Deus"))
    assert edicao.corrigir_legenda(pastas, 7, "  ").legenda == ""


def test_mudar_as_pontas_mantem_a_correcao_da_legenda(pastas):
    original = " ".join(f"p{i}" for i in range(200, 280))
    edicao.corrigir_legenda(pastas, 7, original.replace("p210", "Deus"))
    corte = edicao.mudar_pontas(pastas, 7, primeira=190)
    assert corte.inicio == pytest.approx(95.0, abs=0.06) and corte.editado
    assert corte.legenda.split()[0] == "p190" and "Deus" in corte.legenda
    gravado = _corte(pastas, 7)
    assert gravado.inicio == corte.inicio and gravado.legenda == corte.legenda
    corte = edicao.mudar_pontas(pastas, 7, ultima=285)
    assert corte.fim == pytest.approx(142.9, abs=0.06)


def test_pontas_invalidas_nao_gravam_nada(pastas):
    antes = _corte(pastas, 7)
    with pytest.raises(LimiteInvalido, match="menos de 10"):
        edicao.mudar_pontas(pastas, 7, ultima=205)
    with pytest.raises(LimiteInvalido, match="depois do fim"):
        edicao.mudar_pontas(pastas, 7, primeira=270, ultima=260)
    with pytest.raises(LimiteInvalido):
        edicao.mudar_pontas(pastas, 7, primeira=10**6)
    assert _corte(pastas, 7) == antes


def test_mover_a_ponta_uma_palavra_e_marcar(pastas):
    corte = edicao.mover_ponta(pastas, 7, "fim", 2)
    assert corte.texto.split()[-1] == "p281"
    with pytest.raises(LimiteInvalido):
        edicao.mover_ponta(pastas, 7, "meio", 1)
    assert not edicao.marcar(pastas, 7, False).incluir and not _corte(pastas, 7).incluir


def test_situacao_de_cada_short_como_no_studio(pastas):
    por_id = {s.corte.id: s.situacao for s in dados.shorts_no_disco(pastas)}
    assert por_id == {3: "pronto", 7: "novo", 9: "novo"}
    registro = {"3": {"arquivo": "postados/short_01_titulo_3.mp4", "assinatura": "a",
                      "corte": [1.0, 2.0]}}  # fmt: skip
    pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")
    assert dados.shorts_no_disco(pastas)[0].situacao == "alterado"
    assert [s.corte.id for s in dados.a_renderizar(pastas)] == [3, 7]  # o 9 está desmarcado


# --- a fila ---------------------------------------------------------------------------------


class ExecutorFalso:
    criados: ClassVar[list] = []

    def __init__(self):
        self.iniciado = None
        self.enviados: list[str] = []
        self.ativo = False
        self.fila_de_eventos: list[dict] = []
        ExecutorFalso.criados.append(self)

    def iniciar(self, pasta, config, *, aprovar=None):
        self.iniciado = (Path(pasta), config, aprovar)
        self.ativo = True

    def enviar(self, comando):
        self.enviados.append(comando)

    def eventos(self):
        saida, self.fila_de_eventos = self.fila_de_eventos, []
        return saida


@pytest.fixture
def fila():
    ExecutorFalso.criados = []
    return Fila(criar_executor=ExecutorFalso, procurar=lambda: [])


def test_fila_ajusta_as_opcoes_e_usa_a_config_da_ultima_execucao(pastas, fila):
    config = ConfigApp()
    config.pastas.projetos = pastas.raiz.parent
    (pastas.work / "config_execucao.json").write_text(config.model_dump_json(), "utf-8")
    fila.iniciar(pastas, ajustes={"modo": "so_shorts", "pausar_aprovar_cortes": False})
    [executor] = ExecutorFalso.criados
    pasta, usada, aprovar = executor.iniciado
    assert pasta == pastas.raiz and aprovar is None
    assert usada.pastas.projetos == pastas.raiz.parent
    opcoes = _estado(pastas).opcoes
    assert opcoes.modo == "so_shorts" and not opcoes.pausar_aprovar_cortes
    assert fila.estado()["rodando"] and fila.estado()["projeto"] == pastas.raiz.name
    with pytest.raises(FilaOcupada):
        fila.iniciar(pastas)
    fila.enviar("pausar")
    assert executor.enviados == ["pausar"]


def test_fila_nao_comeca_com_outro_trabalhador_no_pc(pastas):
    ocupada = Fila(criar_executor=ExecutorFalso, procurar=lambda: [4321])
    assert "janela" in ocupada.ocupada()
    with pytest.raises(FilaOcupada):
        ocupada.iniciar(pastas)
    with pytest.raises(FilaOcupada):
        ocupada.enviar("pausar")


def test_fila_acompanha_os_eventos(fila):
    fila._tratar({"tipo": "progresso", "etapa": "renderizar_shorts", "total": 0.4})
    fila._tratar({"tipo": "pausa", "ativa": True})
    fila._tratar({"tipo": "fim", "resultado": "erro", "erro": "faltou disco"})
    assert (fila.etapa, fila.fracao, fila.pausado) == ("renderizar_shorts", 0.4, True)
    assert fila.estado()["ultimo_resultado"] == "erro" and fila.ultimo_erro == "faltou disco"


def test_sem_config_da_execucao_usa_a_atual(pastas):
    assert isinstance(config_da_execucao(pastas), ConfigApp)


# --- pelo servidor --------------------------------------------------------------------------


@pytest.fixture
def cliente(pastas, tmp_path, fila):
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), pastas.raiz.parent, Avisos(cofre, TransporteFalso()), "x",
        gravar_config=lambda _c: None, fila=fila,
    )  # fmt: skip
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    cliente.ctx, cliente.pastas = ctx, pastas
    return cliente


BASE = f"/api/projetos/{NOME_DO_PROJETO}"


def test_editar_exige_a_trava_do_celular(cliente):
    resposta = cliente.post(f"{BASE}/shorts/7/legenda", json={"texto": "uma legenda"})
    assert resposta.status_code == 409 and resposta.json() == {"erro": "sem_trava"}
    tomada = cliente.post(f"{BASE}/sessao").json()
    assert tomada["pc"] == "sem_janela" and tomada["sessao"]
    corrigida = cliente.post(f"{BASE}/shorts/7/legenda", json={"texto": "uma legenda"})
    assert corrigida.status_code == 200 and corrigida.json()["legenda"] == "uma legenda"
    assert cliente.post(f"{BASE}/sessao/soltar").json() == {"ok": True}
    assert not sessao.ativa(cliente.pastas)


def test_pontas_e_marcar_pelo_servidor(cliente):
    cliente.post(f"{BASE}/sessao")
    ed = cliente.get(f"{BASE}/shorts/7/edicao").json()
    assert ed["primeira"] == 200
    mais = cliente.post(f"{BASE}/shorts/7/pontas", json={"ponta": "inicio", "passo": -4})
    assert mais.status_code == 200 and mais.json()["duracao"] == pytest.approx(42.0, abs=0.2)
    curto = cliente.post(f"{BASE}/shorts/7/pontas", json={"ultima": 205})
    assert curto.status_code == 422 and "menos de 10" in curto.json()["erro"]
    assert cliente.post(f"{BASE}/shorts/7/pontas", json={"primeira": "x"}).status_code == 400
    assert (
        cliente.post(f"{BASE}/shorts/7/marcar", json={"incluir": False}).json()["incluir"] is False
    )
    assert cliente.post(f"{BASE}/shorts/999/marcar", json={"incluir": True}).status_code == 404


def test_nao_edita_enquanto_o_pc_escolhe_os_cortes(cliente, pastas):
    cliente.post(f"{BASE}/sessao")
    estado = _estado(pastas)
    estado.etapas.append(EstadoEtapa(id="selecionar_shorts", status=StatusEtapa.EM_ANDAMENTO))
    salvar_estado(pastas.raiz, estado)
    cliente.ctx.fila._procurar = lambda: [99]  # há um trabalhador de verdade rodando
    resposta = cliente.post(f"{BASE}/shorts/7/marcar", json={"incluir": False})
    assert resposta.status_code == 409 and "escolhendo os cortes" in resposta.json()["erro"]
    cliente.ctx.fila._procurar = lambda: []  # o "em andamento" era de um processamento que caiu
    assert cliente.post(f"{BASE}/shorts/7/marcar", json={"incluir": False}).status_code == 200


def test_aprovar_os_cortes_pelo_celular(cliente, pastas):
    assert cliente.post(f"{BASE}/aprovar", json={"etapa": "aprovar_cortes"}).status_code == 409
    estado = _estado(pastas)
    estado.etapas.append(EstadoEtapa(id="aprovar_cortes", status=StatusEtapa.AGUARDANDO_REVISAO))
    salvar_estado(pastas.raiz, estado)
    andamento = cliente.get(f"{BASE}/andamento").json()
    assert andamento["esperando_revisao"] and andamento["fila"] is None

    cliente.post(f"{BASE}/sessao")
    for id_ in (3, 7):
        cliente.post(f"{BASE}/shorts/{id_}/marcar", json={"incluir": False})
    nenhum = cliente.post(f"{BASE}/aprovar", json={"etapa": "aprovar_cortes"})
    assert nenhum.status_code == 422
    cliente.post(f"{BASE}/shorts/9/marcar", json={"incluir": True})
    assert cliente.post(f"{BASE}/aprovar", json={"etapa": "outra"}).status_code == 409
    ok = cliente.post(f"{BASE}/aprovar", json={"etapa": "aprovar_cortes"})
    assert ok.status_code == 200 and ok.json()["fila"]["rodando"]
    [executor] = ExecutorFalso.criados
    assert executor.iniciado[2] == "aprovar_cortes"
    assert cliente.get(f"{BASE}/andamento").json()["fila"]["projeto"] == pastas.raiz.name


def test_renderizar_pelo_celular_precisa_do_video_original(cliente, pastas, tmp_path):
    detalhe = cliente.get(BASE).json()
    assert detalhe["a_renderizar"] == 1 and not detalhe["tem_video_original"]  # o corte 7
    sem_video = cliente.post(f"{BASE}/renderizar")
    assert sem_video.status_code == 409 and "vídeo original" in sem_video.json()["erro"]

    video = tmp_path / "sermao.mp4"
    video.write_bytes(b"x")
    estado = _estado(pastas)
    estado.entrada.caminho = str(video)
    salvar_estado(pastas.raiz, estado)
    ok = cliente.post(f"{BASE}/renderizar")
    assert ok.status_code == 200 and ok.json()["shorts"] == 1
    opcoes = _estado(pastas).opcoes
    assert opcoes.modo == "so_shorts" and not opcoes.pausar_aprovar_cortes
    assert cliente.post(f"{BASE}/renderizar").status_code == 409  # já está rodando

    assert cliente.post("/api/processamento/pausar").json()["rodando"]
    assert ExecutorFalso.criados[0].enviados == ["pausar"]
    assert cliente.post("/api/processamento/explodir").status_code == 404
    assert cliente.get("/api/sistema").json()["fila"]["projeto"] == pastas.raiz.name


def test_nada_para_renderizar(cliente, pastas):
    edicao.marcar(pastas, 7, False)
    resposta = cliente.post(f"{BASE}/renderizar")
    assert resposta.status_code == 409 and "Nenhum Short" in resposta.json()["erro"]
