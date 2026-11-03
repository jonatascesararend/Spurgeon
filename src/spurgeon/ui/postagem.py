"""Postar e agendar um Short nas redes, pelo Studio Shorts.

- `LinhaDeAgendamento`: a caixa "Agendado para [data] [hora]" ao lado do "Já postado", com a
  previsão ("em 2 dias e 3 h") e o botão que abre a janela de postagem.
- `JanelaDePostagem`: escolhe as redes e o horário, envia em segundo plano (uma rede de cada vez) e
  mostra o resultado de cada uma. O que a rede não agenda fica na fila do Spurgeon (Instagram) ou
  vira um passo assistido (TikTok).
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import BooleanVar, StringVar, TclError

import customtkinter as ctk

from spurgeon.core.erros import Cancelado, ErroSpurgeon
from spurgeon.publicacao.agenda import ESTADOS_QUE_BLOQUEIAM, RegistroDePostagens, publicar_em
from spurgeon.publicacao.modelo import (
    ASSISTIDO,
    ERRO,
    Pacote,
    Plataforma,
    Relogio,
    Resultado,
    agora_local,
)
from spurgeon.shorts.agendamento import (
    faltam,
    guardar_horario,
    interpretar,
    ler_horario,
    rotulo_do_horario,
)
from spurgeon.ui import dialogos
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.dicas import dica
from spurgeon.ui.estabilidade import RotuloQueSome
from spurgeon.ui.seletores_de_horario import BotaoDoCalendario, BotaoDoRelogio

_COR_ERRO = "#e05555"
_COR_AVISO = "#e0a030"
_COR_OK = "#3fb950"
_COR_SUAVE = ("gray35", "gray65")
HORA_PADRAO_DO_AGENDAMENTO = 18  # ao marcar "agendado" sem horário: amanhã, a esta hora

NOTAS_DAS_REDES = {
    "youtube": "O YouTube guarda o horário e publica sozinho (pode fechar o aplicativo).",
    "instagram": "A API não agenda: o Spurgeon publica na hora marcada, com o aplicativo aberto.",
    "facebook": "O Facebook guarda o horário e publica sozinho (Reel de 3 a 90 s).",
    "tiktok": "Copia a legenda e abre o TikTok Studio e a pasta do vídeo, para você agendar lá.",
    "upload_post": "O serviço pago guarda o horário e publica nas redes ligadas a ele.",
}


@dataclass
class ContextoDePublicacao:
    """O que o Studio precisa para postar: as redes, as tarefas em segundo plano e o registro."""

    plataformas: Callable[[], dict[str, Plataforma]]
    tarefas: Tarefas
    registrar: Callable[[str], None] = lambda _texto: None  # o registro de atividade da aba


# --- passos assistidos (TikTok) -------------------------------------------------------------------
def _mostrar_no_explorer(arquivo: Path) -> None:
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["explorer", f"/select,{arquivo}"], creationflags=sem_janela)


def preparar_assistido(
    janela,
    resultado: Resultado,
    *,
    abrir_url: Callable[[str], object] = webbrowser.open,
    mostrar_arquivo: Callable[[Path], None] = _mostrar_no_explorer,
) -> None:
    """O que o TikTok assistido faz: copia a legenda, mostra o arquivo e abre o TikTok Studio."""
    dados = resultado.dados
    legenda = str(dados.get("legenda", ""))
    if legenda:
        janela.clipboard_clear()
        janela.clipboard_append(legenda)
    if dados.get("arquivo"):
        mostrar_arquivo(Path(dados["arquivo"]))
    endereco = str(dados.get("url") or resultado.url)
    if endereco:
        abrir_url(endereco)


# --- a linha do agendamento -----------------------------------------------------------------------
class LinhaDeAgendamento(ctk.CTkFrame):
    """`[✓] Agendado para [dd/mm/aaaa] 📅 [hh:mm] 🕒  em 2 dias e 3 h   [Postar nas redes…]`.

    `ao_mudar(texto)` recebe o horário guardado (`AAAA-MM-DDTHH:MM`) quando a pessoa marca com um
    horário válido e futuro, ou `""` quando desmarca. Escrever por programa (`carregar`) não avisa.
    O calendário (📅) e o relógio (🕒) escrevem nos mesmos campos de quem digita; escolher por eles
    já marca o agendamento.
    """

    def __init__(
        self,
        master,
        *,
        ao_mudar: Callable[[str], None],
        ao_postar: Callable[[], None],
        relogio: Relogio = agora_local,
    ):
        super().__init__(master, fg_color="transparent")
        self._ao_mudar = ao_mudar
        self._relogio = relogio
        self._carregando = False
        self._ultimo = ""
        self._var_marcado = BooleanVar()
        self._var_data = StringVar()
        self._var_hora = StringVar()
        self.columnconfigure(5, weight=1)
        self.check = ctk.CTkCheckBox(
            self, text="Agendado para", variable=self._var_marcado, command=self._ao_marcar
        )
        self.check.grid(row=0, column=0, sticky="w")
        dica(
            self.check,
            "Marque para guardar a previsão de quando este Short vai ao ar. Grava na hora e leva "
            "o arquivo para a pasta shorts_gerados/agendados. Quando a hora passa, o Short vira "
            "“já postado” sozinho e o arquivo vai para a pasta dos postados.",
        )
        self.campo_data = ctk.CTkEntry(
            self, width=100, textvariable=self._var_data, placeholder_text="dd/mm/aaaa"
        )
        self.campo_data.grid(row=0, column=1, padx=(8, 2))
        dica(self.campo_data, "A data da postagem: 05/11/2026 (ou só 05/11).")
        self.botao_data = BotaoDoCalendario(
            self, var_data=self._var_data, ao_escolher=self._ao_escolher_o_dia, relogio=relogio
        )
        self.botao_data.grid(row=0, column=2, padx=(0, 8))
        self.campo_hora = ctk.CTkEntry(
            self, width=64, textvariable=self._var_hora, placeholder_text="hh:mm"
        )
        self.campo_hora.grid(row=0, column=3, padx=(0, 2))
        dica(self.campo_hora, "A hora da postagem: 18:30 (ou 18h30, ou só 18).")
        self.botao_hora = BotaoDoRelogio(
            self,
            var_hora=self._var_hora,
            ao_escolher=self._ao_escolher_a_hora,
            padrao=(HORA_PADRAO_DO_AGENDAMENTO, 0),
        )
        self.botao_hora.grid(row=0, column=4, padx=(0, 8))
        self.previsao = ctk.CTkLabel(
            self, text="", anchor="w", font=ctk.CTkFont(size=12), text_color=_COR_SUAVE
        )
        self.previsao.grid(row=0, column=5, sticky="ew")
        self.botao_postar = ctk.CTkButton(
            self, text="Postar nas redes…", width=140, command=ao_postar
        )
        self.botao_postar.grid(row=0, column=6, padx=(8, 0))
        dica(
            self.botao_postar,
            "Abre a janela para enviar este Short às redes ligadas na aba Integrações, agora ou "
            "no horário escolhido (YouTube, Instagram, Facebook, TikTok).",
        )
        self.resumo = RotuloQueSome(
            self, anchor="w", justify="left", wraplength=520, text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12), text="",
        )  # fmt: skip
        self.resumo.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(2, 0))
        self._var_data.trace_add("write", lambda *_a: self._ao_editar())
        self._var_hora.trace_add("write", lambda *_a: self._ao_editar())

    # --- de fora para dentro --------------------------------------------------------------------
    def carregar(self, guardado: str, resumo: str = "") -> None:
        """Mostra o agendamento do corte aberto (sem avisar ninguém)."""
        self._carregando = True
        try:
            quando = ler_horario(guardado)
            self._var_marcado.set(quando is not None)
            self._var_data.set(f"{quando:%d/%m/%Y}" if quando else "")
            self._var_hora.set(f"{quando:%H:%M}" if quando else "")
            self._ultimo = guardado if quando else ""
            self.resumo.configure(text=resumo)
            self.repintar()
        finally:
            self._carregando = False

    def repintar(self) -> None:
        """Refaz o "em 2 dias e 3 h" (chamado de tempos em tempos)."""
        quando = self._horario_digitado()
        if not self._var_marcado.get():
            self.previsao.configure(text="", text_color=_COR_SUAVE)
        elif quando is None:
            self.previsao.configure(text="data ou hora inválida", text_color=_COR_ERRO)
        elif quando <= self._relogio().astimezone():
            self.previsao.configure(text="esse horário já passou", text_color=_COR_ERRO)
        else:
            self.previsao.configure(text=faltam(quando, self._relogio()), text_color=_COR_SUAVE)

    def horario(self) -> datetime | None:
        """O horário válido e futuro que está na tela (None se desmarcado ou inválido)."""
        quando = self._horario_digitado()
        if not self._var_marcado.get() or quando is None:
            return None
        return quando if quando > self._relogio().astimezone() else None

    def definir_habilitado(self, habilitado: bool) -> None:
        estado = "normal" if habilitado else "disabled"
        for widget in (
            self.check, self.campo_data, self.botao_data, self.campo_hora, self.botao_hora,
            self.botao_postar,
        ):  # fmt: skip
            widget.configure(state=estado)

    # --- de dentro para fora --------------------------------------------------------------------
    def _horario_digitado(self) -> datetime | None:
        return interpretar(self._var_data.get(), self._var_hora.get(), hoje=self._relogio().date())

    def _ao_marcar(self) -> None:
        if self._var_marcado.get() and not (self._var_data.get() or self._var_hora.get()):
            self._preencher_o_que_falta()
        self._emitir()

    def _preencher_o_que_falta(self) -> None:
        """Sem data, amanhã; sem hora, a hora padrão. Não avisa: quem chama emite uma vez só."""
        amanha = (self._relogio() + timedelta(days=1)).astimezone()
        amanha = amanha.replace(hour=HORA_PADRAO_DO_AGENDAMENTO, minute=0)
        antes, self._carregando = self._carregando, True
        try:
            if not self._var_data.get().strip():
                self._var_data.set(f"{amanha:%d/%m/%Y}")
            if not self._var_hora.get().strip():
                self._var_hora.set(f"{amanha:%H:%M}")
        finally:
            self._carregando = antes

    def _ao_escolher_o_dia(self, dia: date) -> None:
        """O calendário escolheu um dia: marca o agendamento (se não estava) e escreve a data."""
        self._marcar_pelo_seletor()
        self._var_data.set(f"{dia:%d/%m/%Y}")

    def _ao_escolher_a_hora(self, hora: int, minuto: int) -> None:
        """O relógio escolheu a hora: marca o agendamento (se não estava) e escreve a hora."""
        self._marcar_pelo_seletor()
        self._var_hora.set(f"{hora:02d}:{minuto:02d}")

    def _marcar_pelo_seletor(self) -> None:
        antes, self._carregando = self._carregando, True  # a escolha emite uma vez só, depois
        try:
            self._var_marcado.set(True)
            self._preencher_o_que_falta()
        finally:
            self._carregando = antes

    def _ao_editar(self) -> None:
        if not self._carregando and self._var_marcado.get():
            self._emitir()

    def _emitir(self) -> None:
        self.repintar()
        if not self._var_marcado.get():
            novo = ""
        else:
            quando = self.horario()
            if quando is None:  # incompleto ou no passado: nada a guardar ainda
                return
            novo = guardar_horario(quando)
        if novo != self._ultimo:
            self._ultimo = novo
            self._ao_mudar(novo)


# --- a janela de postagem -------------------------------------------------------------------------
class JanelaDePostagem(ctk.CTkToplevel):
    """Escolhe as redes e o horário e envia, mostrando o andamento de cada rede."""

    def __init__(
        self,
        master,
        *,
        titulo_do_short: str,
        pacote: Pacote | None,
        plataformas: dict[str, Plataforma],
        registro: RegistroDePostagens,
        id_do_corte: int,
        quando: datetime | None,
        tarefas: Tarefas,
        relogio: Relogio = agora_local,
        ao_terminar: Callable[[list[Resultado], datetime | None], None] | None = None,
        ao_assistir: Callable[[Resultado], None] | None = None,
        modal: bool = True,
    ):
        super().__init__(master)
        self.title("Postar nas redes")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self._pacote = pacote
        self._plataformas = plataformas
        self._registro = registro
        self._id = id_do_corte
        self._tarefas = tarefas
        self._relogio = relogio
        self._ao_terminar = ao_terminar
        self._ao_assistir = ao_assistir
        self._evento: threading.Event | None = None
        self._andamento: dict[str, float] = {}
        self._fechada = False
        self.resultados: list[Resultado] = []
        self.linhas: dict[str, tuple[BooleanVar, ctk.CTkCheckBox, ctk.CTkLabel]] = {}
        self._var_modo = StringVar(value="agendar" if quando else "agora")
        self._var_data = StringVar(value=f"{quando:%d/%m/%Y}" if quando else "")
        self._var_hora = StringVar(value=f"{quando:%H:%M}" if quando else "")
        self.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=titulo_do_short or "(sem título)", anchor="w", wraplength=520,
            justify="left", font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 2))  # fmt: skip
        detalhe = (
            f"{pacote.video.name} · {pacote.duracao_s:.0f} s"
            if pacote is not None
            else "Este Short ainda não foi renderizado: gere os Shorts antes de postar."
        )
        ctk.CTkLabel(
            self, text=detalhe, anchor="w", wraplength=520, justify="left",
            text_color=_COR_SUAVE if pacote is not None else _COR_ERRO,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))  # fmt: skip

        quadro_quando = ctk.CTkFrame(self, fg_color="transparent")
        quadro_quando.grid(row=2, column=0, sticky="ew", padx=20)
        self.radio_agora = ctk.CTkRadioButton(
            quadro_quando, text="Publicar agora", variable=self._var_modo, value="agora",
            command=self._avaliar,
        )  # fmt: skip
        self.radio_agora.grid(row=0, column=0, sticky="w")
        dica(self.radio_agora, "Envia já e publica já (menos no TikTok, que é assistido).")
        self.radio_agendar = ctk.CTkRadioButton(
            quadro_quando, text="Agendar para", variable=self._var_modo, value="agendar",
            command=self._avaliar,
        )  # fmt: skip
        self.radio_agendar.grid(row=0, column=1, padx=(18, 6))
        dica(self.radio_agendar, "Envia agora e deixa para publicar no horário escolhido.")
        self.campo_data = ctk.CTkEntry(
            quadro_quando, width=100, textvariable=self._var_data, placeholder_text="dd/mm/aaaa"
        )
        self.campo_data.grid(row=0, column=2, padx=(0, 2))
        dica(self.campo_data, "A data: 05/11/2026 (ou só 05/11).")
        self.botao_data = BotaoDoCalendario(
            quadro_quando, var_data=self._var_data, relogio=relogio,
            ao_escolher=lambda dia: self._var_data.set(f"{dia:%d/%m/%Y}"),
        )  # fmt: skip
        self.botao_data.grid(row=0, column=3, padx=(0, 6))
        self.campo_hora = ctk.CTkEntry(
            quadro_quando, width=64, textvariable=self._var_hora, placeholder_text="hh:mm"
        )
        self.campo_hora.grid(row=0, column=4, padx=(0, 2))
        dica(self.campo_hora, "A hora: 18:30 (ou 18h30, ou só 18).")
        self.botao_hora = BotaoDoRelogio(
            quadro_quando, var_hora=self._var_hora,
            ao_escolher=lambda h, m: self._var_hora.set(f"{h:02d}:{m:02d}"),
            padrao=(HORA_PADRAO_DO_AGENDAMENTO, 0),
        )  # fmt: skip
        self.botao_hora.grid(row=0, column=5)
        self.aviso_do_horario = RotuloQueSome(
            self, anchor="w", justify="left", wraplength=520, text_color=_COR_ERRO, text=""
        )
        self.aviso_do_horario.grid(row=3, column=0, sticky="ew", padx=20)
        self._var_data.trace_add("write", lambda *_a: self._avaliar())
        self._var_hora.trace_add("write", lambda *_a: self._avaliar())

        ctk.CTkLabel(
            self, text="Redes", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=4, column=0, sticky="w", padx=20, pady=(12, 2))  # fmt: skip
        quadro_redes = ctk.CTkFrame(self, fg_color="transparent")
        quadro_redes.grid(row=5, column=0, sticky="ew", padx=20)
        quadro_redes.columnconfigure(1, weight=1)
        for linha, (chave, plataforma) in enumerate(plataformas.items()):
            marcada = BooleanVar(value=True)
            caixa = ctk.CTkCheckBox(quadro_redes, text=plataforma.nome, variable=marcada, width=200)
            caixa.grid(row=linha, column=0, sticky="nw", pady=4)
            dica(caixa, NOTAS_DAS_REDES.get(chave, plataforma.nome))
            nota = ctk.CTkLabel(
                quadro_redes, text="", anchor="w", justify="left", wraplength=300,
                font=ctk.CTkFont(size=12),
            )  # fmt: skip
            nota.grid(row=linha, column=1, sticky="ew", padx=(10, 0), pady=4)
            self.linhas[chave] = (marcada, caixa, nota)

        self.mensagem = RotuloQueSome(self, anchor="w", justify="left", wraplength=520, text="")
        self.mensagem.grid(row=6, column=0, sticky="ew", padx=20, pady=(10, 0))
        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=7, column=0, sticky="ew", padx=20, pady=(12, 16))
        self.botao_enviar = ctk.CTkButton(rodape, text="Enviar", width=130, command=self.enviar)
        self.botao_enviar.pack(side="right", padx=(8, 0))
        dica(
            self.botao_enviar,
            "Envia o Short às redes marcadas, uma de cada vez, e mostra aqui o que cada uma fez.",
        )
        self.botao_fechar = ctk.CTkButton(
            rodape, text="Fechar", width=100, fg_color=("gray70", "gray30"), command=self._fechar
        )
        self.botao_fechar.pack(side="right")
        dica(
            self.botao_fechar, "Fecha esta janela. Se um envio está em andamento, ele é cancelado."
        )

        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self._avaliar()
        self.update_idletasks()
        x = master.winfo_toplevel().winfo_rootx() + 80
        y = master.winfo_toplevel().winfo_rooty() + 60
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        if modal:
            self.after(150, self.focus)
            with contextlib.suppress(TclError):  # janela ainda não visível (testes escondidos)
                self.grab_set()

    # --- o horário --------------------------------------------------------------------------------
    def _horario_escolhido(self) -> tuple[datetime | None, str]:
        """`(horário, problema)`: horário None com problema vazio = publicar agora."""
        if self._var_modo.get() == "agora":
            return None, ""
        quando = interpretar(
            self._var_data.get(), self._var_hora.get(), hoje=self._relogio().date()
        )
        if quando is None:
            return None, "Escreva a data (05/11/2026) e a hora (18:30) do agendamento."
        if quando <= self._relogio().astimezone():
            return None, "Esse horário já passou."
        return quando, ""

    @property
    def ocupada(self) -> bool:
        return self._evento is not None

    # --- avaliar cada rede ----------------------------------------------------------------------
    def _avaliar(self) -> None:
        if self._fechada or self.ocupada:
            return
        quando, problema_do_horario = self._horario_escolhido()
        agendando = self._var_modo.get() == "agendar"
        for entrada in (self.campo_data, self.botao_data, self.campo_hora, self.botao_hora):
            entrada.configure(state="normal" if agendando else "disabled")
        self.aviso_do_horario.configure(text=problema_do_horario)
        anteriores = self._registro.do_corte(self._id)
        algum = False
        for chave, plataforma in self._plataformas.items():
            marcada, caixa, nota = self.linhas[chave]
            anterior = anteriores.get(chave)
            texto, cor, pode = self._situacao(
                plataforma, anterior, quando, bool(problema_do_horario)
            )
            nota.configure(text=texto, text_color=cor)
            caixa.configure(state="normal" if pode else "disabled")
            if not pode:
                marcada.set(False)
            algum = algum or pode
        pronto = self._pacote is not None and not problema_do_horario and algum
        self.botao_enviar.configure(
            state="normal" if pronto else "disabled",
            text="Agendar" if agendando else "Publicar agora",
        )

    def _situacao(self, plataforma, anterior, quando, horario_ruim) -> tuple[str, object, bool]:
        """O texto ao lado da rede, a cor dele e se dá para marcá-la."""
        if self._pacote is None:
            return "", _COR_SUAVE, False
        if anterior is not None and anterior.estado in ESTADOS_QUE_BLOQUEIAM:
            marcado = _horario_do_registro(anterior.quando)
            quando_marcado = f" ({marcado})" if marcado else ""
            return f"já está: {anterior.rotulo}{quando_marcado}", _COR_OK, False
        if not plataforma.disponivel:
            return "não conectado (aba Integrações)", _COR_SUAVE, False
        problemas = [] if horario_ruim else plataforma.problemas(self._pacote, quando)
        if problemas:
            return " ".join(problemas), _COR_ERRO, False
        if anterior is not None and anterior.estado == ERRO:
            return f"da última vez deu erro: {anterior.detalhe}", _COR_AVISO, True
        return NOTAS_DAS_REDES.get(plataforma.chave, ""), _COR_SUAVE, True

    # --- enviar ---------------------------------------------------------------------------------
    def _marcadas(self) -> list[Plataforma]:
        return [
            p for chave, p in self._plataformas.items()
            if self.linhas[chave][0].get() and str(self.linhas[chave][1].cget("state")) == "normal"
        ]  # fmt: skip

    def enviar(self) -> None:
        if self._pacote is None or self.ocupada:
            return
        quando, problema = self._horario_escolhido()
        escolhidas = self._marcadas()
        if problema or not escolhidas:
            self.mensagem.configure(
                text=problema or "Marque pelo menos uma rede.", text_color=_COR_ERRO
            )
            return
        if quando is None and not dialogos.confirmar(
            self,
            "Publicar agora",
            "O Short será publicado agora em: " + ", ".join(p.nome for p in escolhidas) + ".",
            sim="Publicar",
            nao="Voltar",
        ):
            return
        self._evento = cancelamento = threading.Event()
        self._andamento = {}
        self.botao_enviar.configure(state="disabled")
        self.mensagem.configure(text="Enviando…", text_color=_COR_SUAVE)
        pacote = self._pacote
        self._tarefas.rodar(
            lambda: publicar_em(
                escolhidas,
                self._id,
                pacote,
                quando,
                self._registro,
                ao_progresso=self._anotar,
                cancelado=cancelamento.is_set,
            ),
            lambda resultados: self._terminou(resultados, quando),
            lambda erro: self._falhou(erro, quando),
        )
        self._acompanhar()

    def _anotar(self, chave: str, fracao: float) -> None:
        self._andamento[chave] = fracao  # vem de outra thread: só guarda; a janela lê depois

    def _acompanhar(self) -> None:
        if self._fechada or not self.ocupada:
            return
        if self._andamento:
            partes = [
                f"{self._plataformas[c].nome.split(' ')[0]} {int(f * 100)}%"
                for c, f in self._andamento.items() if c in self._plataformas
            ]  # fmt: skip
            self.mensagem.configure(text="Enviando… " + " · ".join(partes), text_color=_COR_SUAVE)
        self.after(400, self._acompanhar)

    def _terminou(self, resultados: list[Resultado], quando: datetime | None) -> None:
        self._evento = None
        self.resultados = resultados
        if self._ao_terminar is not None:
            self._ao_terminar(resultados, quando)
        for r in resultados:
            if r.estado == ASSISTIDO and r.dados and self._ao_assistir is not None:
                self._ao_assistir(r)
        if self._fechada:
            return
        houve_erro = any(r.estado == ERRO for r in resultados)
        self.mensagem.configure(
            text=resumo_dos_resultados(resultados, self._plataformas),
            text_color=_COR_ERRO if houve_erro else _COR_OK,
        )
        self._avaliar()

    def _falhou(self, erro: Exception, quando: datetime | None) -> None:
        self._evento = None
        if self._fechada:
            return
        if isinstance(erro, Cancelado):
            texto = "Envio cancelado. O que já tinha sido enviado fica registrado."
        elif isinstance(erro, ErroSpurgeon):
            texto = str(erro)
        else:
            texto = f"Erro inesperado: {erro}"
        self.mensagem.configure(text=texto, text_color=_COR_ERRO)
        self._avaliar()

    def _fechar(self) -> None:
        if self._evento is not None:
            self._evento.set()  # cancela: o envio em andamento para no próximo ponto seguro
        self._fechada = True
        with contextlib.suppress(TclError):
            self.grab_release()
        self.destroy()


def _horario_do_registro(iso: str) -> str:
    """`05/11/2026 às 18:30` a partir do ISO guardado num resultado; vazio se não houver."""
    try:
        return rotulo_do_horario(datetime.fromisoformat(iso)) if iso else ""
    except ValueError:
        return ""


def resumo_dos_resultados(resultados: list[Resultado], plataformas: dict[str, Plataforma]) -> str:
    """Uma linha por rede: o que aconteceu, o endereço e o que a pessoa precisa saber."""
    linhas = []
    for r in resultados:
        nome = plataformas[r.plataforma].nome if r.plataforma in plataformas else r.plataforma
        linha = f"{nome}: {r.rotulo}"
        if r.estado == ERRO:
            linha = f"{nome}: não deu certo. {r.detalhe}"
        else:
            marcado = _horario_do_registro(r.quando)
            if marcado:
                linha += f" para {marcado}"
            if r.url and r.estado != ASSISTIDO:
                linha += f"\n    {r.url}"
            if r.detalhe:
                linha += f"\n    {r.detalhe}"
            if r.aviso:
                linha += f"\n    ⚠ {r.aviso}"
        linhas.append(linha)
    return "\n".join(linhas)
