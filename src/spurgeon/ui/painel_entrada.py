"""Seção "Vídeo": arquivo de entrada, nome do sermão, data da gravação e pasta dos projetos."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from spurgeon.ui.formulario import EXTENSOES_VIDEO, sugerir_nome


class PainelEntrada(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        pasta_entrada: Path,
        pasta_projetos: Path,
        ao_escolher_video: Callable[[Path], None],
    ):
        super().__init__(master)
        self._pasta_entrada = pasta_entrada
        self._ao_escolher_video = ao_escolher_video
        self.var_video = ctk.StringVar()
        self.var_nome = ctk.StringVar()
        self.var_data = ctk.StringVar()
        self.var_projetos = ctk.StringVar(value=str(pasta_projetos))
        self._controles: list[ctk.CTkBaseClass] = []

        self.columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text="1. Vídeo", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4)
        )

        self._linha(1, "Arquivo", self.var_video, "Cole o caminho ou clique em Escolher…")
        self._botao(1, "Escolher…", self._escolher_arquivo)
        self.rotulo_info = ctk.CTkLabel(
            self, text="", text_color="gray60", anchor="w", justify="left"
        )
        self.rotulo_info.grid(row=2, column=1, columnspan=2, sticky="w", padx=(0, 12))

        self._linha(3, "Nome do sermão", self.var_nome, "Vira o nome da pasta do projeto")
        self._linha(4, "Data da gravação", self.var_data, "dd/mm/aaaa", largura=130)
        self._linha(5, "Pasta de projetos", self.var_projetos, "")
        self._botao(5, "Alterar…", self._escolher_pasta_projetos)
        ctk.CTkLabel(self, text="").grid(row=6, pady=2)

        entrada = self._controles[0]
        entrada.bind("<Return>", lambda _e: self._confirmar_caminho())
        entrada.bind("<FocusOut>", lambda _e: self._confirmar_caminho())
        self._ultimo_caminho: str = ""

    # --- montagem -----------------------------------------------------------------------------
    def _linha(self, linha: int, rotulo: str, variavel: ctk.StringVar, dica: str, largura: int = 0):
        ctk.CTkLabel(self, text=rotulo, anchor="w").grid(
            row=linha, column=0, sticky="w", padx=(12, 8), pady=4
        )
        entrada = ctk.CTkEntry(self, textvariable=variavel, placeholder_text=dica)
        if largura:
            entrada.configure(width=largura)
            entrada.grid(row=linha, column=1, sticky="w", pady=4)
        else:
            entrada.grid(row=linha, column=1, sticky="ew", pady=4)
        self._controles.append(entrada)

    def _botao(self, linha: int, texto: str, comando: Callable[[], None]) -> None:
        botao = ctk.CTkButton(self, text=texto, width=90, command=comando)
        botao.grid(row=linha, column=2, padx=(8, 12), pady=4)
        self._controles.append(botao)

    # --- ações --------------------------------------------------------------------------------
    def _escolher_arquivo(self) -> None:
        inicial = self._pasta_entrada if self._pasta_entrada.exists() else Path.home()
        padrao = " ".join(f"*{e}" for e in EXTENSOES_VIDEO)
        caminho = filedialog.askopenfilename(
            title="Escolha o vídeo",
            initialdir=str(inicial),
            filetypes=[("Vídeos", padrao), ("Todos os arquivos", "*.*")],
        )
        if caminho:
            self.definir_video(Path(caminho))

    def _escolher_pasta_projetos(self) -> None:
        pasta = filedialog.askdirectory(
            title="Pasta dos projetos", initialdir=self.var_projetos.get() or str(Path.home())
        )
        if pasta:
            self.var_projetos.set(str(Path(pasta)))

    def _confirmar_caminho(self) -> None:
        texto = self.var_video.get().strip().strip('"')
        if texto and texto != self._ultimo_caminho and Path(texto).is_file():
            self.definir_video(Path(texto))

    # --- interface com a janela principal -----------------------------------------------------
    def definir_video(self, caminho: Path) -> None:
        self._ultimo_caminho = str(caminho)
        self.var_video.set(str(caminho))
        self.var_nome.set(sugerir_nome(caminho))
        self.var_data.set("")
        self.rotulo_info.configure(text="Lendo o vídeo…", text_color="gray60")
        self._ao_escolher_video(caminho)

    def mostrar_info(self, texto: str, *, erro: bool = False) -> None:
        self.rotulo_info.configure(text=texto, text_color="#e05555" if erro else "gray60")

    def caminho_video(self) -> Path | None:
        texto = self.var_video.get().strip().strip('"')
        return Path(texto) if texto else None

    def pasta_projetos(self) -> Path:
        return Path(self.var_projetos.get().strip() or ".")

    def habilitar(self, ativo: bool) -> None:
        estado = "normal" if ativo else "disabled"
        for controle in self._controles:
            controle.configure(state=estado)
