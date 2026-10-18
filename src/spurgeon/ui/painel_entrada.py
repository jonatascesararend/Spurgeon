"""Seção "Vídeo": arquivo de entrada, nome do sermão, data da gravação e pasta dos projetos."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.formulario import EXTENSOES_VIDEO, ValoresFormulario, sugerir_nome
from spurgeon.ui.painel_aparar import PainelAparar, abrir_no_player
from spurgeon.ui.painel_shorts import PainelShorts
from spurgeon.ui.painel_zoom import PainelZoom
from spurgeon.ui.presets import RepositorioDePresets
from spurgeon.ui.quadros import FonteDeQuadros
from spurgeon.ui.secao import SecaoRecolhivel


class PainelEntrada(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        pasta_entrada: Path,
        pasta_projetos: Path,
        ao_escolher_video: Callable[[Path], None],
        valores: ValoresFormulario,
        fonte: FonteDeQuadros,
        tarefas: Tarefas,
        presets: RepositorioDePresets,
        ao_mudar: Callable[[], None],
        estilo: Callable[[], tuple[Enquadramento, TratamentoImagem]] | None = None,
        abrir_previa: Callable[[Path], None] = abrir_no_player,
        ao_abrir_projeto: Callable[[Path], None] | None = None,
        opcoes_atuais: Callable[[], OpcoesProcessamento | None] | None = None,
        pasta_fontes: Path | None = None,
    ):
        super().__init__(master)
        self._pasta_entrada = pasta_entrada
        self._ao_escolher_video = ao_escolher_video
        self._ao_abrir_projeto = ao_abrir_projeto
        self.var_video = ctk.StringVar()
        self.var_nome = ctk.StringVar()
        self.var_data = ctk.StringVar()
        self.var_projetos = ctk.StringVar(value=str(pasta_projetos))
        self._controles: list[ctk.CTkBaseClass] = []

        self.columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text="1. Vídeo", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4)
        )

        # O campo do arquivo usa a largura toda; os botões vêm numa linha própria logo abaixo.
        self._linha(1, "Arquivo", self.var_video, "Cole o caminho ou clique em Escolher…", span=2)
        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 2))
        for texto, comando, largura in (
            ("Escolher vídeo…", self._escolher_arquivo, 120),
            ("Abrir projeto…", self._escolher_projeto, 120),
        ):
            botao = ctk.CTkButton(botoes, text=texto, width=largura, command=comando)
            botao.pack(side="left", padx=(0, 8))
            self._controles.append(botao)
            if texto.startswith("Abrir"):
                self.botao_projeto = botao
        self.rotulo_info = ctk.CTkLabel(
            self, text="", text_color="gray60", anchor="w", justify="left", wraplength=360
        )
        self.rotulo_info.grid(row=3, column=1, columnspan=2, sticky="w", padx=(0, 12))
        self.rotulo_projeto = ctk.CTkLabel(
            self, text="", text_color="gray60", anchor="w", justify="left", wraplength=440
        )
        self.rotulo_projeto.grid(row=8, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 8))

        self._linha(4, "Nome do sermão", self.var_nome, "Vira o nome da pasta do projeto", span=2)
        self._linha(5, "Data da gravação", self.var_data, "dd/mm/aaaa", largura=130)
        self._linha(6, "Pasta de projetos", self.var_projetos, "")
        self._botao(6, "Alterar…", self._escolher_pasta_projetos)
        self.rotulo_estimativa = ctk.CTkLabel(
            self, text="", text_color="gray60", anchor="w", justify="left", wraplength=360
        )
        self.rotulo_estimativa.grid(
            row=7, column=1, columnspan=2, sticky="w", padx=(0, 12), pady=(2, 8)
        )

        # o corte, o zoom e a legenda dos Shorts ficam em outro cartão (`montar_ajustes`)
        self._ajustes = {
            "valores": valores, "fonte": fonte, "tarefas": tarefas, "presets": presets,
            "ao_mudar": ao_mudar, "estilo": estilo, "abrir_previa": abrir_previa,
            "opcoes_atuais": opcoes_atuais or (lambda: OpcoesProcessamento()),
            "pasta_fontes": pasta_fontes,
        }  # fmt: skip
        self.aparar: PainelAparar | None = None
        self.zoom: PainelZoom | None = None
        self.shorts: PainelShorts | None = None
        self._bloqueado = False
        self._so_vinhetas = False
        self._shorts = True

        entrada = self._controles[0]
        entrada.bind("<Return>", lambda _e: self._confirmar_caminho())
        entrada.bind("<FocusOut>", lambda _e: self._confirmar_caminho())
        self._ultimo_caminho: str = ""

    # --- montagem -----------------------------------------------------------------------------
    def montar_ajustes(self, pai) -> None:
        """Cria o corte do começo e do fim, o zoom e a legenda dos Shorts dentro de `pai`.

        Ficam fora deste cartão para a janela poder pôr "O que gerar" entre a escolha do vídeo e
        o corte; os três continuam sendo controlados daqui (`valores`, `habilitar`…).
        """
        a = self._ajustes
        self.secoes = {
            nome: SecaoRecolhivel(pai, titulo)
            for nome, titulo in (
                ("aparar", "Cortar o começo e o fim do vídeo"),
                ("zoom", "Ajustar o zoom (aproximar a câmera)"),
                ("shorts", "Ajustar o zoom e a legenda dos Shorts"),
            )
        }
        for secao in self.secoes.values():
            secao.pack(fill="x", pady=(0, 2))
        self.aparar = PainelAparar(
            self.secoes["aparar"].corpo, a["valores"], fonte=a["fonte"], tarefas=a["tarefas"],
            ao_mudar=a["ao_mudar"],
            estilo=a["estilo"], abrir=a["abrir_previa"],
        )  # fmt: skip
        self.zoom = PainelZoom(
            self.secoes["zoom"].corpo,
            a["valores"],
            fonte=a["fonte"],
            presets=a["presets"],
            ao_mudar=a["ao_mudar"],
        )
        self.shorts = PainelShorts(
            self.secoes["shorts"].corpo, a["valores"], fonte=a["fonte"], tarefas=a["tarefas"],
            opcoes_atuais=a["opcoes_atuais"], ao_mudar=a["ao_mudar"],
            pasta_fontes=a["pasta_fontes"],
        )  # fmt: skip
        for painel in (self.aparar, self.zoom, self.shorts):
            painel.rotulo_titulo.pack_forget()  # o cabeçalho da seção já diz o que é
            painel.pack(fill="x", pady=(0, 6))
        self._habilitar_secoes()

    def _linha(
        self,
        linha: int,
        rotulo: str,
        variavel: ctk.StringVar,
        dica: str,
        largura: int = 0,
        span: int = 1,
    ):
        ctk.CTkLabel(self, text=rotulo, anchor="w").grid(
            row=linha, column=0, sticky="w", padx=(12, 8), pady=4
        )
        entrada = ctk.CTkEntry(self, textvariable=variavel, placeholder_text=dica)
        if largura:
            entrada.configure(width=largura)
            entrada.grid(row=linha, column=1, sticky="w", pady=4)
        else:
            entrada.grid(
                row=linha,
                column=1,
                columnspan=span,
                sticky="ew",
                padx=(0, 12 if span > 1 else 0),
                pady=4,
            )
        self._controles.append(entrada)

    def _botao(
        self, linha: int, texto: str, comando: Callable[[], None], *, coluna: int = 2, largura=90
    ) -> ctk.CTkButton:
        botao = ctk.CTkButton(self, text=texto, width=largura, command=comando)
        botao.grid(row=linha, column=coluna, padx=(8 if coluna == 2 else 0, 12), pady=4)
        self._controles.append(botao)
        return botao

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

    def _escolher_projeto(self) -> None:
        """Escolhe a pasta de um projeto já criado, para reabri-lo e aproveitar o que foi feito."""
        pasta_projetos = Path(self.var_projetos.get().strip() or ".")
        inicial = pasta_projetos if pasta_projetos.exists() else Path.home()
        pasta = filedialog.askdirectory(
            title="Escolha a pasta do projeto (a que tem o estado.json)",
            initialdir=str(inicial),
            mustexist=True,
        )
        if pasta and self._ao_abrir_projeto is not None:
            self._ao_abrir_projeto(Path(pasta))

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
        if self.aparar is not None:
            self.aparar.var_inicio.set("")  # outro vídeo: os tempos do anterior não valem
            self.aparar.var_fim.set("")
        self.rotulo_info.configure(text="Lendo o vídeo…", text_color="gray60")
        self._ao_escolher_video(caminho)

    def abrir_projeto(self, video: Path, nome: str, data: str, pasta_projetos: Path) -> None:
        """Preenche a tela com o que o projeto já tem (nome, data e vídeo originais)."""
        self._ultimo_caminho = str(video)
        self.var_video.set(str(video))
        self.var_nome.set(nome)
        self.var_data.set(data)
        self.var_projetos.set(str(pasta_projetos))
        self.mostrar_estimativa("")

    def mostrar_projeto(self, texto: str) -> None:
        self.rotulo_projeto.configure(text=texto)

    def mostrar_info(self, texto: str, *, erro: bool = False) -> None:
        self.rotulo_info.configure(text=texto, text_color="#e05555" if erro else "gray60")

    def mostrar_estimativa(self, texto: str) -> None:
        self.rotulo_estimativa.configure(text=texto)

    def caminho_video(self) -> Path | None:
        texto = self.var_video.get().strip().strip('"')
        return Path(texto) if texto else None

    def pasta_projetos(self) -> Path:
        return Path(self.var_projetos.get().strip() or ".")

    def habilitar(self, ativo: bool) -> None:
        self._bloqueado = not ativo
        estado = "normal" if ativo else "disabled"
        for controle in self._controles:
            controle.configure(state=estado)
        self._habilitar_secoes()

    def modo_so_vinhetas(self, so_vinhetas: bool, *, shorts: bool) -> None:
        """No modo só vinhetas o vídeo não é recodificado: cortar e dar zoom não valem."""
        if (so_vinhetas, shorts) == (self._so_vinhetas, self._shorts):
            return  # nada mudou (isto roda a cada ajuste de um controle deslizante)
        self._so_vinhetas = so_vinhetas
        self._shorts = shorts
        self._habilitar_secoes()

    def _habilitar_secoes(self) -> None:
        if self.aparar is None:  # os ajustes ainda não foram montados
            return
        livre = not self._bloqueado and not self._so_vinhetas
        self.aparar.habilitar(livre)
        self.zoom.habilitar(livre, shorts=self._shorts)
        self.shorts.habilitar(livre and self._shorts)

    def valores(self) -> dict:
        if self.aparar is None:
            return {}
        return {**self.aparar.valores(), **self.zoom.valores(), **self.shorts.valores()}

    def aplicar_valores(self, v: ValoresFormulario) -> None:
        if self.aparar is None:
            return
        self.aparar.aplicar(v)
        self.zoom.aplicar(v)
        self.shorts.aplicar(v)
