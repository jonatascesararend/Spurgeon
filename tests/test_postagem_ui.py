"""A linha de agendamento e a janela de postagem do Studio Shorts."""

from __future__ import annotations

import queue
import threading
import time
import tkinter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import customtkinter as ctk
import pytest

from spurgeon.core.erros import Cancelado
from spurgeon.publicacao.agenda import RegistroDePostagens
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
from spurgeon.shorts.agendamento import guardar_horario
from spurgeon.ui import dialogos
from spurgeon.ui import postagem as pg
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.postagem import (
    JanelaDePostagem,
    LinhaDeAgendamento,
    preparar_assistido,
    resumo_dos_resultados,
)
from spurgeon.ui.seletores_de_horario import CalendarioMensal, RelogioDePonteiros
from tests.ambiente_grafico import criar_raiz
from tests.falso_de_rede import Relogio

pytestmark = pytest.mark.gui

BRASILIA = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 3, 12, 0).astimezone()


def bombear(janela, condicao, limite_s: float = 10.0) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        while True:  # o que as threads agendam é feito aqui, na thread da janela
            try:
                janela.fila.get_nowait()()
            except queue.Empty:
                break
        janela.update()
        if condicao():
            return
        time.sleep(0.01)
    pytest.fail("a condição não ficou verdadeira a tempo")


@pytest.fixture(scope="module")
def raiz():
    try:
        janela = criar_raiz()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


def _dica_de(widget) -> str:
    objeto = getattr(widget, "_dica_spurgeon", None)
    return objeto.texto if objeto is not None else ""


def _achar(pai, classe):
    """O primeiro widget da `classe` dentro de `pai` (a janelinha de um seletor, por exemplo)."""
    for filho in pai.winfo_children():
        if isinstance(filho, classe):
            return filho
        dentro = _achar(filho, classe)
        if dentro is not None:
            return dentro
    return None


# --- a linha do agendamento ---------------------------------------------------------------------
@pytest.fixture
def linha(raiz):
    relogio = Relogio(AGORA)
    mudancas: list[str] = []
    postou: list[int] = []
    tela = LinhaDeAgendamento(
        raiz, ao_mudar=mudancas.append, ao_postar=lambda: postou.append(1), relogio=relogio
    )
    tela.pack()
    raiz.update()
    tela.mudancas, tela.postou, tela.relogio = mudancas, postou, relogio  # type: ignore[attr-defined]
    yield tela
    tela.destroy()


def test_carregar_mostra_o_agendamento_sem_avisar_ninguem(linha):
    linha.carregar("2026-11-05T18:30", "YouTube agendado na rede")
    assert linha.check.get() == 1
    assert (linha.campo_data.get(), linha.campo_hora.get()) == ("05/11/2026", "18:30")
    assert linha.previsao.cget("text") == "em 2 dias e 6 h"
    assert linha.resumo.cget("text") == "YouTube agendado na rede"
    assert linha.mudancas == []


def test_carregar_sem_agendamento_deixa_tudo_vazio(linha):
    linha.carregar("2026-11-05T18:30")
    linha.carregar("")
    assert linha.check.get() == 0
    assert (linha.campo_data.get(), linha.campo_hora.get()) == ("", "")
    assert linha.previsao.cget("text") == "" and linha.horario() is None
    assert linha.mudancas == []


def test_marcar_sem_horario_sugere_amanha_as_18_e_avisa_uma_vez(linha):
    linha.check.select()
    linha._var_marcado.set(True)
    linha._ao_marcar()
    assert (linha.campo_data.get(), linha.campo_hora.get()) == ("04/11/2026", "18:00")
    assert linha.mudancas == ["2026-11-04T18:00"]


def test_editar_o_horario_guarda_so_o_que_e_valido_e_futuro(linha):
    linha.carregar("2026-11-05T18:30")
    linha.campo_hora.delete(0, "end")
    linha.campo_hora.insert(0, "25:00")  # inválido: nada é guardado
    assert linha.previsao.cget("text") == "data ou hora inválida" and linha.mudancas == []
    linha.campo_hora.delete(0, "end")
    linha.campo_hora.insert(0, "09:15")
    assert linha.mudancas[-1] == "2026-11-05T09:15"
    linha.campo_data.delete(0, "end")
    linha.campo_data.insert(0, "01/11/2026")  # no passado: avisa e não guarda
    assert linha.previsao.cget("text") == "esse horário já passou"
    assert linha.mudancas == ["2026-11-05T09:15"]
    assert linha.horario() is None


def test_desmarcar_apaga_o_agendamento(linha):
    linha.carregar("2026-11-05T18:30")
    linha._var_marcado.set(False)
    linha._ao_marcar()
    assert linha.mudancas == [""] and linha.previsao.cget("text") == ""


def test_a_previsao_acompanha_o_relogio(linha):
    linha.carregar("2026-11-03T14:00")
    assert linha.previsao.cget("text") == "em 2 h"
    linha.relogio.agora = AGORA + timedelta(minutes=90)
    linha.repintar()
    assert linha.previsao.cget("text") == "em 30 min"
    linha.relogio.agora = AGORA + timedelta(hours=3)  # passou: o Studio vai virar "postado"
    linha.repintar()
    assert linha.previsao.cget("text") == "esse horário já passou"


def test_o_botao_de_postar_avisa_e_tudo_pode_ser_desabilitado(linha):
    linha.botao_postar.invoke()
    assert linha.postou == [1]
    linha.definir_habilitado(False)
    assert str(linha.botao_postar.cget("state")) == "disabled"
    assert str(linha.campo_data.cget("state")) == "disabled"
    linha.definir_habilitado(True)
    assert str(linha.check.cget("state")) == "normal"


def test_todo_controle_da_linha_tem_dica(linha):
    controles = (
        linha.check, linha.campo_data, linha.botao_data, linha.campo_hora, linha.botao_hora,
        linha.botao_postar,
    )  # fmt: skip
    for widget in controles:
        assert len(_dica_de(widget)) > 20, widget


def test_escolher_o_dia_no_calendario_ja_marca_o_agendamento_e_avisa_uma_vez(linha):
    linha._ao_escolher_o_dia(date(2026, 11, 10))
    assert linha.check.get() == 1
    assert (linha.campo_data.get(), linha.campo_hora.get()) == ("10/11/2026", "18:00")
    assert linha.mudancas == ["2026-11-10T18:00"]


def test_escolher_a_hora_no_relogio_ja_marca_o_agendamento_e_avisa_uma_vez(linha):
    linha._ao_escolher_a_hora(9, 45)
    assert linha.check.get() == 1
    assert (linha.campo_data.get(), linha.campo_hora.get()) == ("04/11/2026", "09:45")
    assert linha.mudancas == ["2026-11-04T09:45"]


def test_escolher_o_dia_com_o_agendamento_ja_marcado_so_troca_a_data(linha):
    linha.carregar("2026-11-05T18:30")
    linha._ao_escolher_o_dia(date(2026, 11, 20))
    assert (linha.campo_data.get(), linha.campo_hora.get()) == ("20/11/2026", "18:30")
    assert linha.mudancas == ["2026-11-20T18:30"]
    linha._ao_escolher_a_hora(7, 15)
    assert linha.mudancas[-1] == "2026-11-20T07:15" and len(linha.mudancas) == 2


def test_escolher_hoje_depois_da_hora_padrao_avisa_que_ja_passou_e_nao_guarda(linha):
    linha.relogio.agora = AGORA.replace(hour=19)  # as 18h de hoje já passaram
    linha._ao_escolher_o_dia(date(2026, 11, 3))
    assert linha.previsao.cget("text") == "esse horário já passou" and linha.mudancas == []
    linha._ao_escolher_a_hora(21, 0)  # escolhendo uma hora que ainda vem, passa a valer
    assert linha.mudancas == ["2026-11-03T21:00"]


def test_os_botoes_do_calendario_e_do_relogio_seguem_o_habilitado(linha):
    linha.definir_habilitado(False)
    assert str(linha.botao_data.cget("state")) == "disabled"
    assert str(linha.botao_hora.cget("state")) == "disabled"
    linha.definir_habilitado(True)
    assert str(linha.botao_data.cget("state")) == "normal"
    assert str(linha.botao_hora.cget("state")) == "normal"


def test_o_calendario_da_linha_abre_no_dia_que_esta_escrito(linha):
    linha.carregar("2027-01-15T18:30")
    linha.botao_data.abrir()
    calendario = _achar(linha.botao_data.janela_aberta, CalendarioMensal)
    assert calendario is not None and calendario.mes_mostrado == (2027, 1)
    calendario.escolher(date(2027, 1, 20))  # fecha a janelinha e escreve a data
    assert linha.campo_data.get() == "20/01/2027" and linha.mudancas[-1] == "2027-01-20T18:30"
    assert not linha.botao_data.janela_aberta.winfo_exists()


# --- a janela de postagem -----------------------------------------------------------------------
class Rede(Plataforma):
    def __init__(self, chave: str, *, disponivel=True, problemas=(), final=AGENDADO, espera=None):
        super().__init__(None)  # type: ignore[arg-type]
        self.chave, self.nome = chave, chave.title() + " Teste"
        self._disponivel, self._problemas = disponivel, list(problemas)
        self.final, self.espera = final, espera
        self.publicou: list[datetime | None] = []

    @property
    def conectada(self) -> bool:
        return self._disponivel

    def problemas(self, pacote, quando):
        return list(self._problemas)

    def publicar(self, pacote, quando, *, ao_progresso=None, cancelado=None):
        if ao_progresso:
            ao_progresso(0.5)
        if self.espera is not None:
            self.espera.wait(5)
            if cancelado is not None and cancelado():
                raise Cancelado("cancelado")
        self.publicou.append(quando)
        dados = {"legenda": "L", "arquivo": str(pacote.video), "url": "https://t"}
        return Resultado(
            self.chave, self.final, "id", "https://rede/x",
            quando.isoformat() if quando else "", dados=dados if self.final == ASSISTIDO else {},
        )  # fmt: skip


@pytest.fixture
def pacote(tmp_path) -> Pacote:
    arquivo = tmp_path / "short_01_x.mp4"
    arquivo.write_bytes(b"v")
    return Pacote(arquivo, "Deus é fiel", "d", ("fé",), 45.0)


@pytest.fixture
def janela(raiz, tmp_path, pacote):
    criadas: list[JanelaDePostagem] = []

    def abrir(redes=None, *, quando=None, com_pacote=True, **extra):
        fila: queue.Queue = queue.Queue()
        registro = RegistroDePostagens(tmp_path / "postagens.json")
        dados = {"terminou": [], "assistiu": []}
        tela = JanelaDePostagem(
            raiz,
            titulo_do_short="Deus é fiel",
            pacote=pacote if com_pacote else None,
            plataformas=redes or {"youtube": Rede("youtube"), "instagram": Rede("instagram")},
            registro=registro,
            id_do_corte=7,
            quando=quando,
            tarefas=Tarefas(fila.put),
            relogio=Relogio(AGORA),
            ao_terminar=lambda r, q: dados["terminou"].append((r, q)),
            ao_assistir=dados["assistiu"].append,
            modal=False,
            **extra,
        )
        tela.fila, tela.registro, tela.dados = fila, registro, dados  # type: ignore[attr-defined]
        criadas.append(tela)
        raiz.update()
        return tela

    yield abrir
    for tela in criadas:
        if tela.winfo_exists():
            tela.destroy()


def test_short_sem_arquivo_nao_deixa_postar(janela):
    tela = janela(com_pacote=False)
    assert "ainda não foi renderizado" in tela.winfo_children()[1].cget("text")
    assert str(tela.botao_enviar.cget("state")) == "disabled"
    assert all(str(c.cget("state")) == "disabled" for _v, c, _n in tela.linhas.values())


def test_cada_rede_mostra_o_que_a_impede_ou_o_que_vai_acontecer(janela, tmp_path):
    redes = {
        "youtube": Rede("youtube"),
        "instagram": Rede("instagram", disponivel=False),
        "facebook": Rede("facebook", problemas=["O Reel de Página aceita de 3 a 90 s."]),
        "tiktok": Rede("tiktok"),
    }
    RegistroDePostagens(tmp_path / "postagens.json").gravar(
        7, Resultado("tiktok", AGENDADO, quando="2026-11-04T15:00:00-03:00")
    )
    tela = janela(redes)
    textos = {k: n.cget("text") for k, (_v, _c, n) in tela.linhas.items()}
    estados = {k: str(c.cget("state")) for k, (_v, c, _n) in tela.linhas.items()}
    assert "pode fechar o aplicativo" in textos["youtube"] and estados["youtube"] == "normal"
    assert textos["instagram"] == "não conectado (aba Integrações)"
    assert estados["instagram"] == "disabled"
    assert "3 a 90 s" in textos["facebook"] and estados["facebook"] == "disabled"
    assert textos["tiktok"].startswith("já está: agendado na rede (04/11/2026 às ") and (
        estados["tiktok"] == "disabled"
    )
    assert str(tela.botao_enviar.cget("state")) == "normal"  # o YouTube basta


def test_modo_agendar_pede_um_horario_valido_e_muda_o_botao(janela):
    tela = janela()
    assert tela.botao_enviar.cget("text") == "Publicar agora"
    assert str(tela.campo_data.cget("state")) == "disabled"
    tela._var_modo.set("agendar")
    tela._avaliar()
    assert tela.botao_enviar.cget("text") == "Agendar"
    assert "Escreva a data" in tela.aviso_do_horario.cget("text")
    assert str(tela.botao_enviar.cget("state")) == "disabled"
    tela._var_data.set("01/11/2026")
    tela._var_hora.set("10:00")
    assert tela.aviso_do_horario.cget("text") == "Esse horário já passou."
    tela._var_data.set("05/11/2026")
    assert (
        tela.aviso_do_horario.cget("text") == ""
        and str(tela.botao_enviar.cget("state")) == "normal"
    )


def test_a_janela_abre_ja_no_horario_agendado_do_short(janela):
    quando = AGORA + timedelta(days=2)
    tela = janela(quando=quando)
    assert tela._var_modo.get() == "agendar"
    assert tela._var_data.get() == f"{quando:%d/%m/%Y}"
    assert tela.botao_enviar.cget("text") == "Agendar"


def test_agendar_envia_so_as_redes_marcadas_e_registra(janela):
    quando = AGORA + timedelta(days=2)
    youtube, instagram = Rede("youtube"), Rede("instagram", final=NA_FILA)
    tela = janela({"youtube": youtube, "instagram": instagram}, quando=quando)
    tela.linhas["instagram"][0].set(False)  # a pessoa desmarcou o Instagram

    tela.enviar()
    bombear(tela, lambda: not tela.ocupada)

    assert len(youtube.publicou) == 1 and youtube.publicou[0] == quando.replace(microsecond=0)
    assert instagram.publicou == []
    assert [r.plataforma for r in tela.resultados] == ["youtube"]
    assert tela.registro.do_corte(7)["youtube"].estado == AGENDADO
    ((resultados, horario),) = tela.dados["terminou"]
    assert horario == quando.replace(microsecond=0) and resultados[0].estado == AGENDADO
    assert "agendado na rede" in tela.mensagem.cget("text")
    # a rede que acabou de agendar não pode ser enviada de novo
    assert tela.linhas["youtube"][2].cget("text").startswith("já está: agendado na rede")


def test_publicar_agora_pede_confirmacao_e_voltar_nao_envia(janela, monkeypatch):
    youtube = Rede("youtube", final=ENVIADO)
    tela = janela({"youtube": youtube})
    monkeypatch.setattr(dialogos, "confirmar", lambda *a, **k: False)
    tela.enviar()
    assert youtube.publicou == [] and not tela.ocupada

    monkeypatch.setattr(dialogos, "confirmar", lambda *a, **k: True)
    tela.enviar()
    bombear(tela, lambda: not tela.ocupada)
    assert youtube.publicou == [None] and tela.resultados[0].estado == ENVIADO


def test_resultado_assistido_chama_o_passo_assistido(janela, monkeypatch):
    tiktok = Rede("tiktok", final=ASSISTIDO)
    tela = janela({"tiktok": tiktok}, quando=AGORA + timedelta(days=1))
    tela.enviar()
    bombear(tela, lambda: not tela.ocupada)
    assert [r.plataforma for r in tela.dados["assistiu"]] == ["tiktok"]


def test_erro_de_uma_rede_aparece_e_nao_impede_a_outra(janela, monkeypatch):
    class Quebrada(Rede):
        def publicar(self, *a, **k):
            raise pg.ErroSpurgeon("A rede recusou o vídeo.")

    youtube = Rede("youtube", final=ENVIADO)
    tela = janela({"youtube": youtube, "instagram": Quebrada("instagram")})
    monkeypatch.setattr(dialogos, "confirmar", lambda *a, **k: True)
    tela.enviar()
    bombear(tela, lambda: not tela.ocupada)
    estados = {r.plataforma: r.estado for r in tela.resultados}
    assert estados == {"youtube": ENVIADO, "instagram": ERRO}
    texto = tela.mensagem.cget("text")
    assert "Instagram Teste: não deu certo. A rede recusou o vídeo." in texto
    assert tela.mensagem.cget("text_color") == pg._COR_ERRO
    assert (
        tela.linhas["instagram"][2].cget("text").startswith("da última vez deu erro")
    )  # dá para tentar


def test_fechar_durante_o_envio_cancela_e_nao_quebra(janela, monkeypatch):
    espera = threading.Event()
    youtube = Rede("youtube", final=ENVIADO, espera=espera)
    tela = janela({"youtube": youtube}, quando=AGORA + timedelta(days=1))
    tela.enviar()
    assert tela.ocupada and str(tela.botao_enviar.cget("state")) == "disabled"
    tela._fechar()
    assert not tela.winfo_exists()
    # a thread acorda, vê o cancelamento e entrega o erro na fila; nada disso mexe na janela morta
    espera.set()
    time.sleep(0.2)
    while not tela.fila.empty():
        tela.fila.get_nowait()()
    assert tela.registro.do_corte(7) == {}


def test_todo_controle_da_janela_tem_dica(janela):
    tela = janela()
    caixas = [c for _v, c, _n in tela.linhas.values()]
    controles = [
        tela.radio_agora, tela.radio_agendar, tela.campo_data, tela.botao_data, tela.campo_hora,
        tela.botao_hora, tela.botao_enviar, tela.botao_fechar, *caixas,
    ]  # fmt: skip
    assert all(len(_dica_de(w)) > 15 for w in controles)


def test_na_janela_de_postagem_o_calendario_e_o_relogio_so_valem_ao_agendar(janela):
    tela = janela()
    assert str(tela.botao_data.cget("state")) == "disabled"
    assert str(tela.botao_hora.cget("state")) == "disabled"
    tela._var_modo.set("agendar")
    tela._avaliar()
    assert str(tela.botao_data.cget("state")) == "normal"
    assert str(tela.botao_hora.cget("state")) == "normal"


def test_na_janela_de_postagem_escolher_no_calendario_e_no_relogio_preenche_os_campos(janela):
    tela = janela()
    tela._var_modo.set("agendar")
    tela._avaliar()
    tela.botao_data.abrir()
    _achar(tela.botao_data.janela_aberta, CalendarioMensal).escolher(date(2026, 11, 12))
    tela.botao_hora.abrir()
    relogio = _achar(tela.botao_hora.janela_aberta, RelogioDePonteiros)
    relogio.escolher_hora(8)
    relogio.escolher_minuto(30)
    assert (tela._var_data.get(), tela._var_hora.get()) == ("12/11/2026", "08:30")
    assert tela.aviso_do_horario.cget("text") == ""
    assert str(tela.botao_enviar.cget("state")) == "normal"


# --- textos e passos assistidos -----------------------------------------------------------------
def test_resumo_dos_resultados_por_rede():
    redes = {"youtube": Rede("youtube"), "tiktok": Rede("tiktok")}
    texto = resumo_dos_resultados(
        [
            Resultado("youtube", AGENDADO, "V", "https://y/V", "2026-11-04T15:00:00-03:00",
                      aviso="O YouTube deixou o vídeo privado."),
            Resultado("tiktok", ERRO, detalhe="O TikTok recusou."),
            Resultado("instagram", ASSISTIDO, detalhe="Copiado."),
        ],
        redes,
    )  # fmt: skip
    assert "Youtube Teste: agendado na rede para 04/11/2026 às 15:00\n    https://y/V" in texto
    assert "⚠ O YouTube deixou o vídeo privado." in texto
    assert "Tiktok Teste: não deu certo. O TikTok recusou." in texto
    assert "instagram: preparado\n    Copiado." in texto


def test_passo_assistido_copia_a_legenda_mostra_o_arquivo_e_abre_o_estudio(raiz):
    abertos: list[str] = []
    mostrados: list[Path] = []
    r = Resultado(
        "tiktok", ASSISTIDO, url="https://www.tiktok.com/tiktokstudio/upload",
        dados={"legenda": "Deus é fiel\n\n#fé", "arquivo": r"C:\p\short.mp4"},
    )  # fmt: skip
    preparar_assistido(raiz, r, abrir_url=abertos.append, mostrar_arquivo=mostrados.append)
    assert raiz.clipboard_get() == "Deus é fiel\n\n#fé"
    assert abertos == ["https://www.tiktok.com/tiktokstudio/upload"]
    assert mostrados == [Path(r"C:\p\short.mp4")]


def test_o_agendamento_guardado_na_linha_e_o_que_o_corte_guarda():
    assert guardar_horario(datetime(2026, 11, 4, 18, 0).astimezone()) == "2026-11-04T18:00"
    assert isinstance(ctk.CTkToplevel, type)
