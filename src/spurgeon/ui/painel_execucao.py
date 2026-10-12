"""Área de execução: botões, progresso, tempo restante, aviso de revisão e painel de logs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

import customtkinter as ctk

_COR_AVISO = "#e0a030"
_COR_ERRO = "#e05555"
_COR_OK = "#4caf72"
_MAX_LINHAS_LOG = 4000


class EstadoUI(StrEnum):
    OCIOSO = "ocioso"
    VERIFICANDO = "verificando"
    EXECUTANDO = "executando"
    PAUSANDO = "pausando"
    PAUSADO = "pausado"
    REVISAO = "revisao"
    CONCLUIDO = "concluido"
    CANCELADO = "cancelado"
    ERRO = "erro"


_TITULOS: dict[EstadoUI, tuple[str, str, str | None]] = {
    EstadoUI.OCIOSO: ("Pronto para iniciar", "Escolha o vídeo e as opções ao lado.", None),
    EstadoUI.VERIFICANDO: ("Verificando o ambiente…", "", None),
    EstadoUI.EXECUTANDO: ("Processando…", "", None),
    EstadoUI.PAUSANDO: ("Pausando…", "Aguardando um ponto seguro da etapa atual.", _COR_AVISO),
    EstadoUI.PAUSADO: ("Pausado", "Clique em Retomar para continuar.", _COR_AVISO),
    EstadoUI.REVISAO: ("Aguardando a sua revisão", "", _COR_AVISO),
    EstadoUI.CONCLUIDO: ("Concluído", "", _COR_OK),
    EstadoUI.CANCELADO: (
        "Cancelado",
        "O projeto pode ser retomado: inicie de novo com o mesmo vídeo.",
        _COR_AVISO,
    ),
    EstadoUI.ERRO: ("Erro no processamento", "", _COR_ERRO),
}
_PODE_INICIAR = {EstadoUI.OCIOSO, EstadoUI.CONCLUIDO, EstadoUI.CANCELADO, EstadoUI.ERRO}
_PODE_PAUSAR = {EstadoUI.EXECUTANDO, EstadoUI.PAUSANDO, EstadoUI.PAUSADO}
_RETOMAVEL = {EstadoUI.CANCELADO, EstadoUI.ERRO}


class PainelExecucao(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        ao_iniciar: Callable[[], None],
        ao_pausar_retomar: Callable[[], None],
        ao_cancelar: Callable[[], None],
        ao_abrir_pasta: Callable[[], None],
        ao_abrir_revisao: Callable[[], None],
        ao_aprovar: Callable[[], None],
        ao_fechar_revisao: Callable[[], None],
    ):
        super().__init__(master, fg_color="transparent")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)
        self._estado = EstadoUI.OCIOSO

        self._titulo = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=22, weight="bold"), anchor="w"
        )
        self._titulo.grid(row=0, column=0, sticky="ew")
        self._detalhe = ctk.CTkLabel(
            self, text="", text_color="gray60", anchor="w", justify="left", wraplength=560
        )
        self._detalhe.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        linha_barra = ctk.CTkFrame(self, fg_color="transparent")
        linha_barra.grid(row=2, column=0, sticky="ew")
        linha_barra.columnconfigure(0, weight=1)
        self._barra = ctk.CTkProgressBar(linha_barra, height=14)
        self._barra.grid(row=0, column=0, sticky="ew")
        self._barra.set(0)
        self._pct = ctk.CTkLabel(linha_barra, text="0%", width=52, anchor="e")
        self._pct.grid(row=0, column=1, padx=(8, 0))

        self._etapa = ctk.CTkLabel(self, text="", anchor="w", justify="left")
        self._etapa.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self._tempo = ctk.CTkLabel(self, text="", anchor="w", text_color="gray60")
        self._tempo.grid(row=4, column=0, sticky="ew")

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.grid(row=5, column=0, sticky="ew", pady=10)
        self.botao_iniciar = ctk.CTkButton(
            botoes,
            text="Iniciar processamento",
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=ao_iniciar,
        )
        self.botao_iniciar.pack(side="left")
        self.botao_pausar = ctk.CTkButton(
            botoes,
            text="Pausar",
            height=42,
            width=100,
            command=ao_pausar_retomar,
            fg_color=("gray70", "gray30"),
        )
        self.botao_pausar.pack(side="left", padx=(8, 0))
        self.botao_cancelar = ctk.CTkButton(
            botoes,
            text="Cancelar",
            height=42,
            width=100,
            command=ao_cancelar,
            fg_color=("gray70", "gray30"),
        )
        self.botao_cancelar.pack(side="left", padx=(8, 0))
        self.botao_pasta = ctk.CTkButton(
            botoes,
            text="Abrir pasta do projeto",
            height=42,
            command=ao_abrir_pasta,
            fg_color="transparent",
            border_width=1,
        )
        self.botao_pasta.pack(side="right")

        self._banner = ctk.CTkFrame(self, border_width=1, border_color=_COR_AVISO)
        self._texto_banner = ctk.CTkLabel(
            self._banner, text="", anchor="w", justify="left", wraplength=520
        )
        self._texto_banner.pack(fill="x", padx=12, pady=(10, 6))
        linha = ctk.CTkFrame(self._banner, fg_color="transparent")
        linha.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(linha, text="Abrir revisão", command=ao_abrir_revisao, width=120).pack(
            side="left"
        )
        ctk.CTkButton(linha, text="Aprovar e continuar", command=ao_aprovar, width=150).pack(
            side="left", padx=8
        )
        ctk.CTkButton(
            linha, text="Fechar", command=ao_fechar_revisao, width=80, fg_color=("gray70", "gray30")
        ).pack(side="left")

        self._log = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self._log.grid(row=6, column=0, sticky="nsew")
        self._log.configure(state="disabled")
        self._log._textbox.tag_config("aviso", foreground=_COR_AVISO)
        self._log._textbox.tag_config("erro", foreground=_COR_ERRO)
        self.definir_estado(EstadoUI.OCIOSO)

    # --- estado -------------------------------------------------------------------------------
    @property
    def estado(self) -> EstadoUI:
        return self._estado

    def definir_estado(
        self, estado: EstadoUI, detalhe: str = "", *, tem_projeto: bool | None = None
    ) -> None:
        self._estado = estado
        titulo, padrao, cor = _TITULOS[estado]
        self._titulo.configure(text=titulo, text_color=cor if cor else ("gray10", "gray90"))
        self._detalhe.configure(
            text=detalhe or padrao, text_color=_COR_ERRO if estado == EstadoUI.ERRO else "gray60"
        )

        self.botao_iniciar.configure(
            state="normal" if estado in _PODE_INICIAR else "disabled",
            text="Retomar processamento" if estado in _RETOMAVEL else "Iniciar processamento",
        )
        self.botao_pausar.configure(
            state="normal" if estado in _PODE_PAUSAR else "disabled",
            text="Retomar" if estado in (EstadoUI.PAUSANDO, EstadoUI.PAUSADO) else "Pausar",
        )
        self.botao_cancelar.configure(state="normal" if estado in _PODE_PAUSAR else "disabled")
        if tem_projeto is not None:
            self.botao_pasta.configure(state="normal" if tem_projeto else "disabled")
        if estado != EstadoUI.REVISAO:
            self._banner.grid_remove()
        if estado == EstadoUI.OCIOSO:
            self.zerar_progresso()

    def mostrar_revisao(self, nome_etapa: str) -> None:
        self._texto_banner.configure(
            text=f"O processamento parou para a sua revisão: {nome_etapa}.\n"
            "Revise e clique em “Aprovar e continuar”. Você também pode fechar o aplicativo e "
            "voltar depois: o projeto continua deste ponto."
        )
        self._banner.grid(row=5, column=0, sticky="ew", pady=(0, 10))

    # --- progresso ----------------------------------------------------------------------------
    def zerar_progresso(self) -> None:
        self._barra.set(0)
        self._pct.configure(text="0%")
        self._etapa.configure(text="")
        self._tempo.configure(text="")

    def atualizar_progresso(self, total: float, texto_etapa: str) -> None:
        self._barra.set(min(1.0, max(0.0, total)))
        self._pct.configure(text=f"{round(total * 100)}%")
        self._etapa.configure(text=texto_etapa)

    def definir_etapa(self, texto: str) -> None:
        self._etapa.configure(text=texto)

    def definir_tempo(self, texto: str) -> None:
        self._tempo.configure(text=texto)

    # --- log ----------------------------------------------------------------------------------
    def adicionar_log(self, linha: str, nivel: int = logging.INFO) -> None:
        etiqueta = "erro" if nivel >= logging.ERROR else "aviso" if nivel >= logging.WARNING else ""
        self._log.configure(state="normal")
        self._log.insert("end", linha + "\n", etiqueta or None)
        excesso = int(self._log.index("end-1c").split(".")[0]) - _MAX_LINHAS_LOG
        if excesso > 0:
            self._log.delete("1.0", f"{excesso + 1}.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def separar_log(self) -> None:
        """Linha divisória entre duas execuções, se o log já tem conteúdo."""
        if self._log.get("1.0", "end").strip():
            self.adicionar_log("─" * 48)

    def limpar_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
