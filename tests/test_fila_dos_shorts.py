"""A fila dos Shorts: renderizar e depois programar no YouTube, um passo por vez (`shorts/fila.py`
e `remoto/despachante.py`). Nenhum teste abre o Chrome, renderiza de verdade ou toca o YouTube."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from spurgeon.canal.postar import ResultadoDaPostagem
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.modelo import AGENDADO, Resultado
from spurgeon.remoto import dados, postagem
from spurgeon.remoto.despachante import Despachante, Vez, reagendar_no_youtube
from spurgeon.shorts import fila
from tests.test_remoto import criar_projeto

AGORA = datetime(2026, 11, 20, 14, 0)


@pytest.fixture
def pastas(tmp_path) -> PastasProjeto:
    """O projeto do controle remoto: o corte 3 renderizado e pronto, o 7 nunca renderizado."""
    pastas = PastasProjeto(criar_projeto(tmp_path / "Projetos"))
    (pastas.shorts / "postados" / "short_01_titulo_3.mp4").rename(
        pastas.shorts / "short_01_titulo_3.mp4"
    )
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        c["postado"], c["postado_em"] = False, ""
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    registro = {"3": {"arquivo": "short_01_titulo_3.mp4", "assinatura": "a"}}
    pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")
    return pastas


class FilaFalsa:
    """A `remoto.fila.Fila` sem trabalhador: `terminar()` faz o papel do fim da renderização."""

    def __init__(self, pastas=None, *, deixa_pronto: bool = True):
        self.rodando = False
        self.pedidos: list[tuple] = []
        self.ultimo_erro = self.ultimo_resultado = ""
        self._pastas, self._deixa_pronto = pastas, deixa_pronto

    def iniciar(self, pastas, *, ajustes=None, so_os_shorts=None, refazer=False, aprovar=None):
        self.pedidos.append((pastas.raiz.name, list(so_os_shorts or []), refazer))
        self.rodando = True

    def terminar(self) -> None:
        self.rodando = False
        if not self._deixa_pronto:
            self.ultimo_resultado, self.ultimo_erro = "erro", "faltou o vídeo"
            return
        pastas = self._pastas
        registro = json.loads(pastas.shorts_renderizados.read_text("utf-8"))
        for id_ in self.pedidos[-1][1]:
            arquivo = f"short_{id_}.mp4"
            (pastas.shorts / arquivo).write_bytes(b"0" * 100)
            registro[str(id_)] = {"arquivo": arquivo, "assinatura": "a"}
        pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")


def _pronto(pastas, id_) -> bool:
    return next(s for s in dados.shorts_no_disco(pastas) if s.corte.id == id_).situacao == "pronto"


@pytest.fixture
def pronto_de_mentira(monkeypatch):
    """O que a `FilaFalsa` "renderiza" passa a valer como pronto (a assinatura não é a real)."""
    import spurgeon.remoto.dados as modulo

    original = modulo.situacao_do_short

    def situacao(corte, registro, existe, **kw):
        return "pronto" if existe and registro else original(corte, registro, existe, **kw)

    monkeypatch.setattr(modulo, "situacao_do_short", situacao)
    monkeypatch.setattr(modulo, "video_original", lambda _p: "video.mp4")


def _despachante(pastas, tmp_path, *, fila_falsa=None, programar=None, reagendar=None, **kw):
    eventos = []
    d = Despachante(
        lambda: pastas.raiz.parent, fila=fila_falsa or FilaFalsa(pastas),
        vez=Vez(tmp_path / "vez.json"), procurar=lambda: [],
        programar=programar or (lambda p, i, a: "Programado no YouTube."),
        reagendar=reagendar or (lambda p, i, a: "Horário trocado."),
        ao_terminar=eventos.append, relogio=lambda: AGORA, **kw,
    )  # fmt: skip
    return d, eventos


# --- o arquivo da fila -------------------------------------------------------------------------


def test_pedir_postagem_e_render_no_mesmo_item(pastas):
    item = fila.pedir_postagem(pastas, 7, AGORA + timedelta(hours=4), " Sermão ", agora=AGORA)
    assert item.acao == fila.POSTAR and item.quando == "2026-11-20T18:00"
    assert item.relacionado == "Sermão"
    fila.pedir_render(pastas, [7, 3])
    lidos = {i.corte: i for i in fila.FilaDoProjeto(pastas).itens()}
    assert lidos[7].acao == fila.POSTAR and lidos[7].renderizar  # ganhou o "renderizar antes"
    assert lidos[3].acao == fila.SO_RENDERIZAR
    with pytest.raises(fila.PedidoInvalido, match="15 minutos"):
        fila.pedir_postagem(pastas, 7, AGORA + timedelta(minutes=5), "", agora=AGORA)
    assert fila.tirar_da_fila(pastas, 3) and fila.FilaDoProjeto(pastas).do_corte(3) is None


def test_o_limite_de_5_minutos_do_reagendamento():
    marcado = AGORA + timedelta(hours=2)
    assert fila.pode_reagendar(marcado, marcado + timedelta(hours=1), AGORA) == ""
    assert "menos de 5 minutos" in fila.pode_reagendar(AGORA + timedelta(minutes=4), marcado, AGORA)
    assert "5 minutos à frente" in fila.pode_reagendar(marcado, AGORA + timedelta(minutes=3), AGORA)
    assert "já está programado" in fila.pode_reagendar(marcado, marcado, AGORA)


def test_as_marcas_de_cada_short(pastas):
    render = fila.RenderEmAndamento(1, (3, 7, 8), atual=7, feitos=(3,))
    assert fila.situacao_na_fila(7, None, render, pronto=False) == fila.RENDERIZANDO
    assert fila.situacao_na_fila(8, None, render, pronto=False) == fila.NA_FILA_DO_RENDER
    assert fila.situacao_na_fila(8, None, render, pronto=True) == ""  # pronto: só é conferido
    assert fila.situacao_na_fila(3, None, render, pronto=True) == ""
    postar = fila.ItemDaFila(5, fila.POSTAR, quando="2026-11-20T18:00")
    assert fila.situacao_na_fila(5, postar, None, pronto=False) == fila.NA_FILA_DO_RENDER
    assert fila.situacao_na_fila(5, postar, None, pronto=True) == fila.NA_FILA_DA_PROGRAMACAO
    no_youtube = fila.ItemDaFila(5, fila.POSTAR, fase=fila.FASE_YOUTUBE)
    assert fila.situacao_na_fila(5, no_youtube, None, pronto=True) == fila.PROGRAMANDO
    com_erro = fila.ItemDaFila(5, fila.POSTAR, erro="sem login")
    marca = fila.MarcaDaFila(fila.situacao_na_fila(5, com_erro, None, pronto=True), com_erro)
    assert marca.situacao == fila.COM_ERRO and "sem login" in marca.detalhe
    assert fila.ROTULO_DA_SITUACAO[fila.NA_FILA_DO_RENDER] == "⏳ na fila para renderizar"


def test_o_render_conta_o_que_esta_fazendo(pastas):
    fila.marcar_render(pastas, [3, 7], atual=7, feitos=[3], pid=4242)
    assert fila.render_em_andamento(pastas, vivo=lambda _p: False) is None  # o processo morreu
    andamento = fila.render_em_andamento(pastas, vivo=lambda p: p == 4242)
    assert andamento.atual == 7 and andamento.falta(7) and not andamento.falta(3)
    marcas = fila.marcas_do_projeto(pastas, lambda _i: False, [3, 7, 9], vivo=lambda _p: True)
    assert set(marcas) == {7} and marcas[7].rotulo == "⚙ renderizando agora"
    fila.limpar_render(pastas)
    assert fila.render_em_andamento(pastas) is None


# --- a vez -------------------------------------------------------------------------------------


def test_so_um_processo_anda_a_fila(tmp_path):
    relogio = [1000.0]
    vivos = {1, 2}
    a = Vez(tmp_path / "vez.json", pid=1, relogio=lambda: relogio[0], vivo=lambda p: p in vivos)
    b = Vez(tmp_path / "vez.json", pid=2, relogio=lambda: relogio[0], vivo=lambda p: p in vivos)
    assert a.tomar() and not b.tomar() and a.tomar()
    vivos.discard(1)  # a janela fechou sem soltar
    assert b.tomar() and not a.tomar()
    relogio[0] += 120  # o servidor travou: depois de um minuto sem sinal, a vez muda
    vivos.add(1)
    assert a.tomar() and not b.tomar()
    a.soltar()
    assert not (tmp_path / "vez.json").exists()


# --- o despachante -----------------------------------------------------------------------------


def test_renderiza_antes_e_programa_depois(pastas, tmp_path, pronto_de_mentira):
    programados = []

    def programar(p, item, andamento):
        andamento("Programando a data e a hora…")
        assert fila.FilaDoProjeto(p).do_corte(item.corte).texto == "Programando a data e a hora…"
        programados.append((item.corte, item.quando))
        return "Programado no YouTube para 20/11 às 18:00.\nhttps://youtube.com/shorts/x"

    falsa = FilaFalsa(pastas)
    d, eventos = _despachante(pastas, tmp_path, fila_falsa=falsa, programar=programar)
    fila.pedir_postagem(pastas, 7, AGORA + timedelta(hours=4), "Sermão", agora=AGORA)
    fila.pedir_postagem(pastas, 3, AGORA + timedelta(hours=5), "Sermão", agora=AGORA)
    d.passo()
    assert falsa.pedidos == [(pastas.raiz.name, [7], False)]  # só o que falta renderizar
    assert fila.FilaDoProjeto(pastas).do_corte(7).fase == fila.FASE_RENDER
    d.passo()  # renderizando: o YouTube espera, mesmo com o Short 3 já pronto
    assert programados == []
    falsa.terminar()
    d.passo()  # a renderização acabou: o pedido mais antigo vai ao YouTube
    assert programados == [(7, "2026-11-20T18:00")]
    assert eventos[-1].texto == "Programado no YouTube para 20/11 às 18:00."
    assert eventos[-1].link == "https://youtube.com/shorts/x"
    d.passo()
    assert [c for c, _q in programados] == [7, 3]
    assert fila.FilaDoProjeto(pastas).itens() == []


def test_espera_qualquer_processamento_do_pc(pastas, tmp_path, pronto_de_mentira):
    rodando = [123]
    d, _e = _despachante(pastas, tmp_path)
    d._procurar = lambda: rodando
    fila.pedir_render(pastas, [7])
    d.passo()
    assert d.fila.pedidos == []
    rodando.clear()
    d.passo()
    assert d.fila.pedidos == [(pastas.raiz.name, [7], False)]


def test_render_que_falha_para_o_item_e_a_fila_segue(pastas, tmp_path, pronto_de_mentira):
    falsa = FilaFalsa(pastas, deixa_pronto=False)
    programados = []
    d, eventos = _despachante(
        pastas, tmp_path, fila_falsa=falsa,
        programar=lambda p, i, a: programados.append(i.corte) or "ok",
    )  # fmt: skip
    fila.pedir_postagem(pastas, 7, AGORA + timedelta(hours=4), "", agora=AGORA)
    fila.pedir_postagem(pastas, 3, AGORA + timedelta(hours=5), "", agora=AGORA)
    d.passo()
    falsa.terminar()
    d.passo()
    item = fila.FilaDoProjeto(pastas).do_corte(7)
    assert "faltou o vídeo" in item.erro and eventos[0].erro == item.erro
    assert programados == [3]  # o com erro fica parado; o outro segue


def test_renderizar_pedido_pelo_botao_sai_da_fila_ao_ficar_pronto(
    pastas, tmp_path, pronto_de_mentira
):
    falsa = FilaFalsa(pastas)
    d, eventos = _despachante(pastas, tmp_path, fila_falsa=falsa)
    fila.pedir_render(pastas, [7])
    d.passo()
    falsa.terminar()
    d.passo()
    assert fila.FilaDoProjeto(pastas).itens() == [] and eventos[0].texto == "Renderizado."
    fila.pedir_render(pastas, [3])  # já pronto (por outro caminho): nem renderiza
    d.passo()
    assert len(falsa.pedidos) == 1 and fila.FilaDoProjeto(pastas).itens() == []


def test_horario_que_chegou_na_fila_nao_e_programado(pastas, tmp_path, pronto_de_mentira):
    programados = []
    d, eventos = _despachante(pastas, tmp_path, programar=lambda p, i, a: programados.append(1))
    fila.pedir_postagem(
        pastas, 3, AGORA + timedelta(minutes=20), "", agora=AGORA - timedelta(hours=1)
    )
    d._relogio = lambda: AGORA + timedelta(minutes=19)
    d.passo()
    assert programados == [] and "escolha outro horário" in eventos[0].erro


def test_erro_no_youtube_fica_no_item(pastas, tmp_path, pronto_de_mentira):
    def falha(p, i, a):
        raise RuntimeError("O YouTube Studio não abriu logado")

    d, eventos = _despachante(pastas, tmp_path, programar=falha)
    fila.pedir_postagem(pastas, 3, AGORA + timedelta(hours=5), "", agora=AGORA)
    d.passo()
    item = fila.FilaDoProjeto(pastas).do_corte(3)
    assert item.fase == fila.FASE_ESPERANDO and "não abriu logado" in item.erro
    marcas = fila.marcas_do_projeto(pastas, lambda _i: True, [3])
    assert marcas[3].situacao == fila.COM_ERRO
    d.passo()  # com erro, não tenta de novo sozinho
    assert len(eventos) == 1
    fila.pedir_postagem(pastas, 3, AGORA + timedelta(hours=6), "", agora=AGORA)  # pedir de novo
    d.passo()
    assert len(eventos) == 2


def test_outro_processo_com_a_vez_nao_deixa_andar(pastas, tmp_path, pronto_de_mentira):
    Vez(tmp_path / "vez.json", pid=999_999, vivo=lambda _p: True).tomar()
    d, _e = _despachante(pastas, tmp_path)
    d.vez._vivo = lambda _p: True
    fila.pedir_render(pastas, [7])
    d.passo()
    assert d.fila.pedidos == []


def test_ao_pegar_a_vez_solta_o_que_ficou_pela_metade(pastas, tmp_path, pronto_de_mentira):
    fila.pedir_postagem(pastas, 3, AGORA + timedelta(hours=5), "", agora=AGORA)
    fila.FilaDoProjeto(pastas).mudar(3, fase=fila.FASE_YOUTUBE, texto="Abrindo…")  # a janela caiu
    feitos = []
    d, _e = _despachante(pastas, tmp_path, programar=lambda p, i, a: feitos.append(i.fase) or "ok")
    d.passo()
    assert feitos == [fila.FASE_ESPERANDO]  # retomado do começo


# --- reagendar ---------------------------------------------------------------------------------


def test_reagendar_troca_a_hora_e_acerta_o_short(pastas, tmp_path):
    marcado = datetime(2026, 11, 20, 18, 0)
    postagem.registrar_sucesso(
        pastas, 3, ResultadoDaPostagem("abc12345678", "https://youtube.com/shorts/abc12345678", "",
                                       "2026-11-20T18:00"), marcado, mover_arquivos=False,
    )  # fmt: skip
    item = fila.pedir_reagendamento(pastas, 3, marcado, datetime(2026, 11, 21, 9, 30), agora=AGORA)
    assert item.acao == fila.REAGENDAR

    class Studio:
        def __enter__(self):
            return "studio"

        def __exit__(self, *_a):
            return False

    pedidos = []

    def reprogramar(studio, video_id, quando, **_kw):
        pedidos.append((studio, video_id, quando))
        return quando

    texto = reagendar_no_youtube(
        pastas, item, lambda _t: None, abrir=Studio, reprogramar=reprogramar, agora=lambda: AGORA
    )
    assert pedidos == [("studio", "abc12345678", datetime(2026, 11, 21, 9, 30))]
    assert "21/11 às 09:30" in texto
    corte = next(c for c in dados.carregar_cortes(pastas) if c.id == 3)
    assert corte.agendado_para == "2026-11-21T09:30"
    youtube = postagem.do_youtube(pastas, 3)
    assert youtube.estado == AGENDADO and youtube.quando.startswith("2026-11-21T09:30")


def test_reagendar_perto_da_hora_e_recusado(pastas):
    perto = datetime(2026, 11, 20, 14, 4).astimezone().isoformat()
    postagem.registro(pastas).gravar(3, Resultado("youtube", AGENDADO, "abc12345678", quando=perto))
    item = fila.ItemDaFila(3, fila.REAGENDAR, quando="2026-11-21T09:30")
    with pytest.raises(fila.PedidoInvalido, match="menos de 5 minutos"):
        reagendar_no_youtube(pastas, item, lambda _t: None, abrir=None, agora=lambda: AGORA)
