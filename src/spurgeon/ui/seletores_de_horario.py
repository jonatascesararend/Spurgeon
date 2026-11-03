"""Escolher a data num calendário e a hora num relógio de ponteiros (agendar um Short).

- `CalendarioMensal`: o mês com os dias para clicar (os que já passaram ficam apagados).
- `RelogioDePonteiros`: um mostrador em dois passos, primeiro a hora (00 a 23, em dois anéis) e
  depois os minutos em blocos de 15 (00, 15, 30, 45).
- `BotaoDoCalendario` e `BotaoDoRelogio`: os botões ao lado dos campos de data e de hora, que abrem
  cada um numa janelinha e devolvem a escolha. Os campos continuam aceitando digitação.

A geometria (qual hora, qual minuto, qual dia está sob o clique) fica em funções puras, sem Tk.
"""

from __future__ import annotations

import calendar
import contextlib
import math
import tkinter
from collections.abc import Callable
from datetime import date
from tkinter import StringVar, TclError

import customtkinter as ctk

from spurgeon.publicacao.modelo import Relogio, agora_local
from spurgeon.shorts.agendamento import ler_data, ler_hora
from spurgeon.ui.dicas import dica

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip
DIAS_DA_SEMANA = ("D", "S", "T", "Q", "Q", "S", "S")  # a semana começa no domingo
MINUTOS = (0, 15, 30, 45)

# o desenho (o aplicativo só tem tema escuro, como os outros Canvas da interface)
_FUNDO = "#242424"
_FACE = "#2f2f2f"
_TEXTO = "#ececec"
_TEXTO_APAGADO = "#6a6a6a"
_DESTAQUE = "#1f6aa5"
_SOBRE = "#3d3d3d"
_HOJE = "#ffd54a"
_COR_SUAVE = ("gray35", "gray65")

# calendário
_LARGURA_DA_CELULA, _ALTURA_DA_CELULA, _ALTURA_DO_CABECALHO = 36, 30, 26
_SEMANAS_DESENHADAS = 6  # o quadro tem sempre 6 linhas, para a janelinha não mudar de tamanho
_RAIO_DO_DIA = 14

# relógio
_LADO_DO_RELOGIO = 224
_CENTRO = _LADO_DO_RELOGIO / 2
_RAIO_DA_FACE = 106.0
_RAIO_DO_ANEL_DE_FORA = 82.0
_RAIO_DO_ANEL_DE_DENTRO = 52.0
_CORTE_ENTRE_ANEIS = (_RAIO_DO_ANEL_DE_FORA + _RAIO_DO_ANEL_DE_DENTRO) / 2
_RAIO_DO_NUMERO = 15.0
_RAIO_DO_MINUTO = 24.0
_MIOLO = 14.0  # um clique tão perto do centro não escolhe nada


def _fonte(tamanho: int, *, negrito: bool = False) -> tuple:
    return ("Segoe UI", tamanho, "bold") if negrito else ("Segoe UI", tamanho)


# --- geometria (sem Tk) ---------------------------------------------------------------------------
def semanas_do_mes(ano: int, mes: int) -> list[list[date]]:
    """As semanas do mês, de domingo a sábado (os dias das pontas são de meses vizinhos)."""
    return calendar.Calendar(firstweekday=6).monthdatescalendar(ano, mes)


def mes_vizinho(ano: int, mes: int, passo: int) -> tuple[int, int]:
    """O mês `passo` meses depois (ou antes, se negativo): `(2026, 12), +1 -> (2027, 1)`."""
    indice = ano * 12 + (mes - 1) + passo
    return indice // 12, indice % 12 + 1


def rotulo_do_mes(ano: int, mes: int) -> str:
    return f"{MESES[mes - 1]} de {ano}"


def celula_do_ponto(x: float, y: float) -> tuple[int, int] | None:
    """`(linha, coluna)` do quadro dos dias sob o ponto, ou None se está fora (ou no cabeçalho)."""
    if x < 0 or y < _ALTURA_DO_CABECALHO:
        return None
    coluna = int(x // _LARGURA_DA_CELULA)
    linha = int((y - _ALTURA_DO_CABECALHO) // _ALTURA_DA_CELULA)
    if coluna > 6 or linha >= _SEMANAS_DESENHADAS:
        return None
    return linha, coluna


def angulo_do_ponto(dx: float, dy: float) -> float:
    """O ângulo de um ponto em relação ao centro, em graus: 0 é em cima e cresce no sentido
    horário (3 horas = 90). `dx` cresce para a direita e `dy` para baixo, como na tela."""
    return math.degrees(math.atan2(dx, -dy)) % 360


def hora_do_ponto(dx: float, dy: float) -> int:
    """A hora (0 a 23) mais perto do ponto: no anel de fora, de 00 a 11; no de dentro, de 12
    a 23."""
    posicao = round(angulo_do_ponto(dx, dy) / 30) % 12
    return posicao if math.hypot(dx, dy) >= _CORTE_ENTRE_ANEIS else 12 + posicao


def minuto_do_ponto(dx: float, dy: float) -> int:
    """O bloco de minutos mais perto do ponto: 00 em cima, 15 à direita, 30 embaixo e 45 à
    esquerda."""
    return MINUTOS[round(angulo_do_ponto(dx, dy) / 90) % 4]


def posicao_no_mostrador(posicao: int, raio: float) -> tuple[float, float]:
    """O ponto do mostrador (pixels do quadro) da posição 0 a 11 (0 em cima), a esta distância."""
    angulo = math.radians(posicao * 30)
    return _CENTRO + raio * math.sin(angulo), _CENTRO - raio * math.cos(angulo)


def ponto_da_hora(hora: int) -> tuple[float, float]:
    """Onde o número da hora (0 a 23) fica no mostrador."""
    raio = _RAIO_DO_ANEL_DE_FORA if hora < 12 else _RAIO_DO_ANEL_DE_DENTRO
    return posicao_no_mostrador(hora % 12, raio)


def ponto_do_minuto(minuto: int) -> tuple[float, float]:
    """Onde o bloco de minutos (0, 15, 30 ou 45) fica no mostrador."""
    return posicao_no_mostrador(minuto // 5, _RAIO_DO_ANEL_DE_FORA)


# --- o calendário ---------------------------------------------------------------------------------
class CalendarioMensal(ctk.CTkFrame):
    """Um mês para escolher o dia. Os dias antes de `hoje` não se escolhem.

    `ao_escolher(dia)` recebe o dia clicado; `ao_cancelar()` (se houver) o botão "Cancelar".
    """

    def __init__(
        self,
        master,
        *,
        escolhida: date | None,
        hoje: date,
        ao_escolher: Callable[[date], None],
        ao_cancelar: Callable[[], None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._hoje = hoje
        self._escolhida = escolhida
        self._ao_escolher = ao_escolher
        base = escolhida if escolhida is not None and escolhida >= hoje else hoje
        self._ano, self._mes = base.year, base.month
        self._semanas: list[list[date]] = []
        self._sobre: date | None = None

        cima = ctk.CTkFrame(self, fg_color="transparent")
        cima.pack(fill="x")
        cima.columnconfigure(1, weight=1)
        self.botao_anterior = ctk.CTkButton(
            cima, text="◀", width=34, height=28, command=lambda: self.ir_para_o_mes(-1)
        )
        self.botao_anterior.grid(row=0, column=0)
        dica(self.botao_anterior, "Mostra o mês anterior (não dá para voltar antes de hoje).")
        self.rotulo_do_mes = ctk.CTkLabel(cima, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.rotulo_do_mes.grid(row=0, column=1)
        self.botao_proximo = ctk.CTkButton(
            cima, text="▶", width=34, height=28, command=lambda: self.ir_para_o_mes(+1)
        )
        self.botao_proximo.grid(row=0, column=2)
        dica(self.botao_proximo, "Mostra o próximo mês.")

        self._tela = tkinter.Canvas(
            self,
            width=7 * _LARGURA_DA_CELULA,
            height=_ALTURA_DO_CABECALHO + _SEMANAS_DESENHADAS * _ALTURA_DA_CELULA,
            bg=_FUNDO, highlightthickness=0,
        )  # fmt: skip
        self._tela.pack(pady=(6, 6))
        self._tela.bind("<Button-1>", self._clicou)
        self._tela.bind("<Motion>", self._passou)
        self._tela.bind("<Leave>", self._saiu)
        dica(
            self._tela,
            "Clique no dia da postagem. Os dias que já passaram ficam apagados; o contorno "
            "amarelo é hoje.",
        )

        baixo = ctk.CTkFrame(self, fg_color="transparent")
        baixo.pack(fill="x")
        self.botao_hoje = ctk.CTkButton(
            baixo, text="Hoje", width=80, height=28, command=lambda: self.escolher(hoje)
        )
        self.botao_hoje.pack(side="left")
        dica(self.botao_hoje, "Escolhe o dia de hoje.")
        if ao_cancelar is not None:
            self.botao_cancelar = ctk.CTkButton(
                baixo, text="Cancelar", width=80, height=28,
                fg_color=("gray70", "gray30"), command=ao_cancelar,
            )  # fmt: skip
            self.botao_cancelar.pack(side="right")
            dica(self.botao_cancelar, "Fecha o calendário sem mudar a data.")
        self._mostrar_o_mes()

    @property
    def mes_mostrado(self) -> tuple[int, int]:
        return self._ano, self._mes

    def ir_para_o_mes(self, passo: int) -> None:
        novo = mes_vizinho(self._ano, self._mes, passo)
        if novo < (self._hoje.year, self._hoje.month):
            return  # um mês inteiro no passado: nada para escolher
        self._ano, self._mes = novo
        self._mostrar_o_mes()

    def escolher(self, dia: date) -> bool:
        """Escolhe o dia (False, sem avisar ninguém, se ele já passou)."""
        if dia < self._hoje:
            return False
        self._escolhida = dia
        self._ao_escolher(dia)
        return True

    # --- o desenho --------------------------------------------------------------------------------
    def _mostrar_o_mes(self) -> None:
        self._semanas = semanas_do_mes(self._ano, self._mes)
        self.rotulo_do_mes.configure(text=rotulo_do_mes(self._ano, self._mes))
        no_atual = (self._ano, self._mes) <= (self._hoje.year, self._hoje.month)
        self.botao_anterior.configure(state="disabled" if no_atual else "normal")
        self._desenhar()

    def _centro_da_celula(self, linha: int, coluna: int) -> tuple[float, float]:
        return (
            (coluna + 0.5) * _LARGURA_DA_CELULA,
            _ALTURA_DO_CABECALHO + (linha + 0.5) * _ALTURA_DA_CELULA,
        )

    def _desenhar(self) -> None:
        tela = self._tela
        tela.delete("all")
        for coluna, letra in enumerate(DIAS_DA_SEMANA):
            x = (coluna + 0.5) * _LARGURA_DA_CELULA
            tela.create_text(
                x, _ALTURA_DO_CABECALHO / 2, text=letra, fill=_TEXTO_APAGADO,
                font=_fonte(10, negrito=True),
            )  # fmt: skip
        for linha, semana in enumerate(self._semanas):
            for coluna, dia in enumerate(semana):
                if dia.month != self._mes:
                    continue
                x, y = self._centro_da_celula(linha, coluna)
                pode = dia >= self._hoje
                escolhido = dia == self._escolhida
                fundo = _DESTAQUE if escolhido else (_SOBRE if dia == self._sobre and pode else "")
                contorno = _HOJE if dia == self._hoje else ""
                if fundo or contorno:
                    tela.create_oval(
                        x - _RAIO_DO_DIA, y - _RAIO_DO_DIA, x + _RAIO_DO_DIA, y + _RAIO_DO_DIA,
                        fill=fundo, outline=contorno, width=2 if contorno else 0,
                    )  # fmt: skip
                tela.create_text(
                    x, y, text=str(dia.day), font=_fonte(11, negrito=escolhido),
                    fill="white" if escolhido else (_TEXTO if pode else _TEXTO_APAGADO),
                )  # fmt: skip

    # --- o mouse ----------------------------------------------------------------------------------
    def _dia_no_ponto(self, x: float, y: float) -> date | None:
        celula = celula_do_ponto(x, y)
        if celula is None or celula[0] >= len(self._semanas):
            return None
        dia = self._semanas[celula[0]][celula[1]]
        return dia if dia.month == self._mes else None

    def _clicou(self, evento) -> None:
        dia = self._dia_no_ponto(evento.x, evento.y)
        if dia is not None:
            self.escolher(dia)

    def _passou(self, evento) -> None:
        dia = self._dia_no_ponto(evento.x, evento.y)
        if dia is not None and dia < self._hoje:
            dia = None
        with contextlib.suppress(TclError):
            self._tela.configure(cursor="hand2" if dia is not None else "")
        if dia != self._sobre:
            self._sobre = dia
            self._desenhar()

    def _saiu(self, _evento=None) -> None:
        if self._sobre is not None:
            self._sobre = None
            self._desenhar()


# --- o relógio ------------------------------------------------------------------------------------
class RelogioDePonteiros(ctk.CTkFrame):
    """Um relógio em dois passos: a hora (00 a 23) e depois os minutos em blocos de 15.

    O anel de fora tem as horas de 00 a 11 e o de dentro as de 12 a 23. Escolhida a hora, o
    mostrador passa sozinho para os minutos; escolhido o bloco de minutos, `ao_escolher(hora,
    minuto)` é chamado. Tocar na hora ou nos minutos do alto volta ao passo respectivo.
    """

    def __init__(
        self,
        master,
        *,
        hora: int,
        minuto: int | None,
        ao_escolher: Callable[[int, int], None],
        ao_cancelar: Callable[[], None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._hora = hora % 24
        self._minuto = minuto if minuto in MINUTOS else None
        self._ao_escolher = ao_escolher
        self._passo = "hora"
        self._sobre: int | None = None

        cima = ctk.CTkFrame(self, fg_color="transparent")
        cima.pack()
        grande = ctk.CTkFont(size=34, weight="bold")
        self.botao_hora = ctk.CTkButton(
            cima, text="", width=72, height=52, font=grande, command=self.voltar_para_a_hora
        )
        self.botao_hora.grid(row=0, column=0)
        dica(self.botao_hora, "A hora escolhida. Clique para escolher a hora de novo.")
        ctk.CTkLabel(cima, text=":", font=grande).grid(row=0, column=1, padx=2)
        self.botao_minuto = ctk.CTkButton(
            cima, text="", width=72, height=52, font=grande, command=self.ir_para_os_minutos
        )
        self.botao_minuto.grid(row=0, column=2)
        dica(self.botao_minuto, "Os minutos escolhidos. Clique para escolher os minutos.")

        self.instrucao = ctk.CTkLabel(self, text="", text_color=_COR_SUAVE)
        self.instrucao.pack(pady=(4, 0))
        self._tela = tkinter.Canvas(
            self, width=_LADO_DO_RELOGIO, height=_LADO_DO_RELOGIO, bg=_FUNDO,
            highlightthickness=0,
        )  # fmt: skip
        self._tela.pack(pady=(2, 6))
        self._tela.bind("<Button-1>", self._clicou)
        self._tela.bind("<Motion>", self._passou)
        self._tela.bind("<Leave>", self._saiu)
        dica(self._tela, self._texto_da_dica)

        if ao_cancelar is not None:
            self.botao_cancelar = ctk.CTkButton(
                self, text="Cancelar", width=90, height=28,
                fg_color=("gray70", "gray30"), command=ao_cancelar,
            )  # fmt: skip
            self.botao_cancelar.pack(anchor="e")
            dica(self.botao_cancelar, "Fecha o relógio sem mudar a hora.")
        self._mostrar()

    @property
    def passo(self) -> str:
        """`"hora"` ou `"minuto"`: o que o mostrador está pedindo agora."""
        return self._passo

    @property
    def escolha(self) -> tuple[int, int | None]:
        return self._hora, self._minuto

    def voltar_para_a_hora(self) -> None:
        self._passo = "hora"
        self._mostrar()

    def ir_para_os_minutos(self) -> None:
        self._passo = "minuto"
        self._mostrar()

    def escolher_hora(self, hora: int) -> None:
        """Escolhe a hora e passa para os minutos."""
        self._hora = hora % 24
        self.ir_para_os_minutos()

    def escolher_minuto(self, minuto: int) -> None:
        """Escolhe o bloco de minutos e entrega a hora marcada."""
        if minuto not in MINUTOS:
            return
        self._minuto = minuto
        self._mostrar()
        self._ao_escolher(self._hora, minuto)

    # --- o desenho --------------------------------------------------------------------------------
    def _texto_da_dica(self) -> str:
        if self._passo == "hora":
            return (
                "Clique na hora. O anel de fora vai de 00 a 11 (madrugada e manhã) e o de dentro "
                "de 12 a 23 (tarde e noite). Depois você escolhe os minutos."
            )
        return "Clique nos minutos: 00, 15, 30 ou 45. É o último passo: o horário já fica marcado."

    def _mostrar(self) -> None:
        na_hora = self._passo == "hora"
        self.botao_hora.configure(
            text=f"{self._hora:02d}", fg_color=_DESTAQUE if na_hora else ("gray70", "gray30")
        )
        self.botao_minuto.configure(
            text="--" if self._minuto is None else f"{self._minuto:02d}",
            fg_color=("gray70", "gray30") if na_hora else _DESTAQUE,
        )
        self.instrucao.configure(text="Escolha a hora" if na_hora else "Escolha os minutos")
        self._sobre = None
        self._desenhar()

    def _desenhar(self) -> None:
        tela = self._tela
        tela.delete("all")
        c = _CENTRO
        tela.create_oval(
            c - _RAIO_DA_FACE, c - _RAIO_DA_FACE, c + _RAIO_DA_FACE, c + _RAIO_DA_FACE,
            fill=_FACE, outline="",
        )  # fmt: skip
        if self._passo == "hora":
            valores = list(range(24))
            marcado, ponto, raio_do_marcado = self._hora, ponto_da_hora, _RAIO_DO_NUMERO
        else:
            valores = list(MINUTOS)
            marcado, ponto, raio_do_marcado = self._minuto, ponto_do_minuto, _RAIO_DO_MINUTO
        if marcado is not None:
            x, y = ponto(marcado)
            tela.create_line(c, c, x, y, fill=_DESTAQUE, width=3)
        tela.create_oval(c - 5, c - 5, c + 5, c + 5, fill=_DESTAQUE, outline="")
        for valor in valores:
            x, y = ponto(valor)
            escolhido = valor == marcado
            if escolhido or valor == self._sobre:
                raio = raio_do_marcado
                tela.create_oval(
                    x - raio, y - raio, x + raio, y + raio,
                    fill=_DESTAQUE if escolhido else _SOBRE, outline="",
                )  # fmt: skip
            interno = self._passo == "hora" and valor >= 12
            tamanho = 11 if interno else (14 if self._passo == "minuto" else 12)
            tela.create_text(
                x, y, text=f"{valor:02d}", font=_fonte(tamanho, negrito=escolhido),
                fill="white" if escolhido else _TEXTO,
            )  # fmt: skip

    # --- o mouse ----------------------------------------------------------------------------------
    def _valor_no_ponto(self, x: float, y: float) -> int | None:
        dx, dy = x - _CENTRO, y - _CENTRO
        distancia = math.hypot(dx, dy)
        if distancia > _RAIO_DA_FACE or distancia < _MIOLO:
            return None
        return hora_do_ponto(dx, dy) if self._passo == "hora" else minuto_do_ponto(dx, dy)

    def _clicou(self, evento) -> None:
        valor = self._valor_no_ponto(evento.x, evento.y)
        if valor is None:
            return
        if self._passo == "hora":
            self.escolher_hora(valor)
        else:
            self.escolher_minuto(valor)

    def _passou(self, evento) -> None:
        valor = self._valor_no_ponto(evento.x, evento.y)
        with contextlib.suppress(TclError):
            self._tela.configure(cursor="hand2" if valor is not None else "")
        if valor != self._sobre:
            self._sobre = valor
            self._desenhar()

    def _saiu(self, _evento=None) -> None:
        if self._sobre is not None:
            self._sobre = None
            self._desenhar()


# --- as janelinhas e os botões --------------------------------------------------------------------
class JanelaDeSeletor(ctk.CTkToplevel):
    """A janelinha que guarda um seletor, aberta logo abaixo do botão que a chamou.

    Segura o foco enquanto está aberta (Esc ou o X fecham sem escolher) e devolve o foco à janela
    de antes, que pode ser uma caixa modal como a de postagem.
    """

    def __init__(self, ancora, titulo: str, montar: Callable[[JanelaDeSeletor], tkinter.Widget]):
        topo = ancora.winfo_toplevel()
        super().__init__(topo)
        self.title(titulo)
        self.transient(topo)
        self.resizable(False, False)
        self._grab_anterior = topo.grab_current()
        montar(self).pack(padx=14, pady=12)
        self.bind("<Escape>", lambda _e: self.fechar())
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.update_idletasks()
        self._posicionar(ancora)
        self.after(60, self._tomar_o_foco)

    def _posicionar(self, ancora) -> None:
        largura, altura = self.winfo_reqwidth(), self.winfo_reqheight()
        x = ancora.winfo_rootx()
        y = ancora.winfo_rooty() + ancora.winfo_height() + 4
        if y + altura > self.winfo_screenheight() - 48:  # sem espaço embaixo: abre em cima
            y = ancora.winfo_rooty() - altura - 4
        x = min(x, self.winfo_screenwidth() - largura - 8)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _tomar_o_foco(self) -> None:
        with contextlib.suppress(TclError):  # janela ainda não visível (testes escondidos)
            self.focus()
            self.grab_set()

    def fechar(self) -> None:
        anterior = self._grab_anterior
        with contextlib.suppress(TclError):
            self.grab_release()
        self.destroy()
        if anterior is not None:
            with contextlib.suppress(TclError):
                anterior.grab_set()


class _BotaoQueAbreSeletor(ctk.CTkButton):
    titulo = ""

    def __init__(self, master, *, icone: str, ajuda: str):
        super().__init__(
            master, text=icone, width=34, height=28, fg_color=("gray70", "gray30"),
            hover_color=("gray60", "gray40"), font=ctk.CTkFont(size=16), command=self.abrir,
        )  # fmt: skip
        dica(self, ajuda)
        self.janela_aberta: JanelaDeSeletor | None = None

    def abrir(self) -> None:
        if self.janela_aberta is not None and self.janela_aberta.winfo_exists():
            self.janela_aberta.focus()
            return
        self.janela_aberta = JanelaDeSeletor(self, self.titulo, self.montar)

    def montar(self, janela: JanelaDeSeletor) -> tkinter.Widget:
        raise NotImplementedError


class BotaoDoCalendario(_BotaoQueAbreSeletor):
    """O 📅 ao lado do campo da data: abre o calendário já no dia que está escrito."""

    titulo = "Escolher o dia"

    def __init__(
        self,
        master,
        *,
        var_data: StringVar,
        ao_escolher: Callable[[date], None],
        relogio: Relogio = agora_local,
    ):
        super().__init__(
            master, icone="📅",
            ajuda="Abre um calendário para escolher o dia. Você também pode digitar a data.",
        )  # fmt: skip
        self._var_data = var_data
        self._ao_escolher = ao_escolher
        self._relogio = relogio

    def montar(self, janela: JanelaDeSeletor) -> tkinter.Widget:
        hoje = self._relogio().date()
        return CalendarioMensal(
            janela,
            escolhida=ler_data(self._var_data.get(), hoje=hoje),
            hoje=hoje,
            ao_escolher=lambda dia: self._escolheu(janela, dia),
            ao_cancelar=janela.fechar,
        )

    def _escolheu(self, janela: JanelaDeSeletor, dia: date) -> None:
        janela.fechar()
        self._ao_escolher(dia)


class BotaoDoRelogio(_BotaoQueAbreSeletor):
    """O 🕒 ao lado do campo da hora: abre o relógio já na hora que está escrita."""

    titulo = "Escolher a hora"

    def __init__(
        self,
        master,
        *,
        var_hora: StringVar,
        ao_escolher: Callable[[int, int], None],
        padrao: tuple[int, int] = (18, 0),
    ):
        super().__init__(
            master, icone="🕒",
            ajuda="Abre um relógio: escolha a hora e depois os minutos (de 15 em 15). Você "
            "também pode digitar a hora.",
        )  # fmt: skip
        self._var_hora = var_hora
        self._ao_escolher = ao_escolher
        self._padrao = padrao

    def montar(self, janela: JanelaDeSeletor) -> tkinter.Widget:
        hora, minuto = ler_hora(self._var_hora.get()) or self._padrao
        return RelogioDePonteiros(
            janela,
            hora=hora,
            minuto=minuto,
            ao_escolher=lambda h, m: self._escolheu(janela, h, m),
            ao_cancelar=janela.fechar,
        )

    def _escolheu(self, janela: JanelaDeSeletor, hora: int, minuto: int) -> None:
        janela.fechar()
        self._ao_escolher(hora, minuto)
