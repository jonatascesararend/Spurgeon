"""Seção "Ajustar o zoom": aproxima a câmera do pregador, mostrando o que será cortado.

Um quadro do vídeo aparece com a área que o zoom corta escurecida e uma moldura no que fica. O zoom
é em % (100% = sem zoom) e o centro do recorte se escolhe clicando no quadro (ou pelos controles).
O recorte é ampliado de volta ao tamanho do vídeo, no vídeo longo e, se marcado, nos Shorts.
"""

from __future__ import annotations

import tkinter
from collections.abc import Callable

import customtkinter as ctk
from PIL import Image, ImageTk

from spurgeon.core.aparencia import Enquadramento
from spurgeon.ui import dialogos, dicas
from spurgeon.ui.composicao import ajustar_ao_tamanho, centro_pelo_clique, sombrear_fora_do_zoom
from spurgeon.ui.formulario import ValoresFormulario
from spurgeon.ui.presets import RepositorioDePresets
from spurgeon.ui.quadros import FonteDeQuadros, QuadroDeExemplo

_COR_DICA = "gray60"
_COR_AVISO = "#e0a030"
_COR_ERRO = "#e05555"
_LARGURA_DA_PREVIA = 440
_ZOOM_MAXIMO_NA_TELA = 200.0
_NITIDEZ_PERDIDA_ACIMA_DE = 150.0
_SEM_PRESET = "(nenhum preset)"


class PainelZoom(ctk.CTkFrame):
    def __init__(
        self,
        master,
        valores: ValoresFormulario,
        *,
        fonte: FonteDeQuadros,
        presets: RepositorioDePresets,
        ao_mudar: Callable[[], None],
    ):
        super().__init__(master, fg_color="transparent")
        self._fonte = fonte
        self._presets = presets
        self._ao_mudar = ao_mudar
        self._quadro: QuadroDeExemplo | None = None
        self._imagem: Image.Image | None = None  # o quadro, no tamanho da prévia
        self._foto: ImageTk.PhotoImage | None = None
        self._indice_do_quadro = 0
        self._aplicando = False

        self.zoom = ctk.DoubleVar(value=valores.zoom_pct)
        self.zoom_texto = ctk.StringVar(value=f"{valores.zoom_pct:g}")
        self.centro_x = ctk.DoubleVar(value=valores.zoom_centro_x_pct)
        self.centro_y = ctk.DoubleVar(value=valores.zoom_centro_y_pct)
        self.nos_shorts = ctk.BooleanVar(value=valores.zoom_nos_shorts)
        self.preset = ctk.StringVar(value=_SEM_PRESET)
        self._filhos: list[ctk.CTkBaseClass] = []

        self._montar()
        fonte.ao_mudar_o_video(self._video_mudou)
        self._atualizar_presets()
        self._desenhar()

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self) -> None:
        self.rotulo_titulo = ctk.CTkLabel(
            self, text="Ajustar o zoom (aproximar a câmera)", font=ctk.CTkFont(weight="bold")
        )  # a janela o esconde quando o painel vem dentro de uma seção recolhível
        self.rotulo_titulo.pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(
            self,
            text="A área escura será cortada; o que fica dentro da moldura é ampliado até o "
            "tamanho do vídeo. Clique no quadro para centrar o zoom no pregador.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12)
        self._aviso = ctk.CTkLabel(
            self, text="", text_color=_COR_DICA, anchor="w", justify="left", wraplength=430
        )
        self._aviso.pack(anchor="w", padx=12)
        altura = round(_LARGURA_DA_PREVIA * 9 / 16)
        self._tela = tkinter.Canvas(
            self, width=_LARGURA_DA_PREVIA, height=altura, bg="#1c1c1c", highlightthickness=0,
            cursor="crosshair",
        )  # fmt: skip
        self._tela.pack(padx=12, pady=(2, 4))
        self._imagem_na_tela = self._tela.create_image(0, 0, anchor="nw")
        self._tela.bind("<Button-1>", self._clicou)
        self._tela.bind("<B1-Motion>", self._clicou)
        dicas.dica(
            self._tela,
            "Clique ou arraste no quadro para centrar o zoom no pregador. A área escura é "
            "cortada; o que fica dentro da moldura é ampliado.",
        )

        linha_zoom = ctk.CTkFrame(self, fg_color="transparent")
        linha_zoom.pack(fill="x", padx=12, pady=(2, 0))
        ctk.CTkLabel(linha_zoom, text="Zoom", width=110, anchor="w").pack(side="left")
        slider = ctk.CTkSlider(
            linha_zoom, from_=100, to=_ZOOM_MAXIMO_NA_TELA, number_of_steps=100,
            variable=self.zoom, width=190, command=self._moveu_zoom,
        )  # fmt: skip
        slider.pack(side="left", padx=6)
        campo = ctk.CTkEntry(linha_zoom, textvariable=self.zoom_texto, width=52, justify="center")
        campo.pack(side="left")
        campo.bind("<Return>", lambda _e: self._digitou_zoom())
        campo.bind("<FocusOut>", lambda _e: self._digitou_zoom())
        ctk.CTkLabel(linha_zoom, text="%").pack(side="left", padx=(2, 0))
        self._filhos += [slider, campo]
        dicas.dica(
            slider,
            "Aproxima a câmera: 100% é o quadro inteiro e 130% corta as bordas e amplia o centro. "
            "Acima de 150% a imagem perde nitidez.",
        )
        dicas.dica(campo, "Digite o zoom (de 100 a 200) e aperte Enter.")

        for rotulo, variavel, ajuda in (
            (
                "Posição horizontal", self.centro_x,
                "Move a área ampliada para a esquerda ou para a direita (50% é o meio). "
                "Dá no mesmo que clicar no quadro.",
            ),
            (
                "Posição vertical", self.centro_y,
                "Move a área ampliada para cima ou para baixo (50% é o meio). "
                "Dá no mesmo que clicar no quadro.",
            ),
        ):  # fmt: skip
            linha = ctk.CTkFrame(self, fg_color="transparent")
            linha.pack(fill="x", padx=12, pady=(2, 0))
            ctk.CTkLabel(linha, text=rotulo, width=110, anchor="w").pack(side="left")
            controle = ctk.CTkSlider(
                linha, from_=0, to=100, number_of_steps=100, variable=variavel, width=190,
                command=lambda _v: self._moveu_centro(),
            )  # fmt: skip
            controle.pack(side="left", padx=6)
            dicas.dica(controle, ajuda)
            self._filhos.append(controle)

        self._info = ctk.CTkLabel(
            self, text="", text_color=_COR_DICA, anchor="w", justify="left", wraplength=430
        )
        self._info.pack(anchor="w", padx=12, pady=(2, 0))

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.pack(fill="x", padx=12, pady=(4, 0))
        for texto, comando, largura, ajuda in (
            (
                "Outro quadro", self.outro_quadro, 110,
                "Mostra outro quadro do vídeo, para conferir o enquadramento em momentos "
                "diferentes do sermão.",
            ),
            (
                "Sem zoom", self.zerar, 90,
                "Volta o zoom para 100% e centraliza a posição (nada é cortado).",
            ),
        ):  # fmt: skip
            botao = ctk.CTkButton(botoes, text=texto, width=largura, command=comando)
            botao.pack(side="left", padx=(0, 6))
            dicas.dica(botao, ajuda)
            self._filhos.append(botao)

        linha_preset = ctk.CTkFrame(self, fg_color="transparent")
        linha_preset.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(linha_preset, text="Preset", width=60, anchor="w").pack(side="left")
        self._menu_preset = ctk.CTkOptionMenu(
            linha_preset, variable=self.preset, values=[_SEM_PRESET], width=170,
            command=self._escolheu_preset, dynamic_resizing=False,
        )  # fmt: skip
        self._menu_preset.pack(side="left", padx=6)
        self._filhos.append(self._menu_preset)
        dicas.dica(
            self._menu_preset,
            "Escolha um enquadramento que você salvou antes (por exemplo, o da câmera de uma "
            "igreja): o zoom e a posição são aplicados na hora.",
        )
        for texto, comando, largura, ajuda in (
            (
                "Salvar preset…", self.salvar_preset, 110,
                "Guarda o zoom e a posição atuais com um nome, para reaproveitar nos próximos "
                "sermões. Se o nome já existe, o preset é substituído.",
            ),
            (
                "Excluir", self.excluir_preset, 64,
                "Apaga da lista o preset escolhido. Os vídeos já gerados não mudam.",
            ),
        ):  # fmt: skip
            botao = ctk.CTkButton(
                linha_preset, text=texto, width=largura, fg_color=("gray70", "gray30"),
                command=comando,
            )  # fmt: skip
            botao.pack(side="left", padx=(0, 4))
            dicas.dica(botao, ajuda)
            self._filhos.append(botao)
        ctk.CTkLabel(
            self,
            text="O preset guarda o zoom e a posição com um nome (por exemplo, o de cada igreja ou "
            "câmera), para usar nos próximos sermões.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12)
        self._check_shorts = ctk.CTkCheckBox(
            self, text="Aplicar também aos Shorts", variable=self.nos_shorts, command=self._mudou
        )
        self._check_shorts.pack(anchor="w", padx=12, pady=(8, 0))
        dicas.dica(
            self._check_shorts,
            "Marcado: o recorte vertical (9:16) dos Shorts sai de dentro da área ampliada. "
            "Desmarcado: só o vídeo longo recebe o zoom.",
        )
        ctk.CTkLabel(
            self,
            text="Nos Shorts o recorte vertical (9:16) sai de dentro desta área, e o “Deslocamento "
            "do recorte” das opções avançadas se soma à posição do zoom.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=(40, 12), pady=(0, 6))
        self._filhos.append(self._check_shorts)

    # --- valores ------------------------------------------------------------------------------
    def enquadramento(self) -> Enquadramento:
        return Enquadramento(
            zoom_pct=min(max(round(self.zoom.get()), 100), 300),
            centro_x_pct=round(self.centro_x.get(), 1),
            centro_y_pct=round(self.centro_y.get(), 1),
        )

    def valores(self) -> dict:
        e = self.enquadramento()
        return {
            "zoom_pct": e.zoom_pct,
            "zoom_centro_x_pct": e.centro_x_pct,
            "zoom_centro_y_pct": e.centro_y_pct,
            "zoom_nos_shorts": self.nos_shorts.get(),
        }

    def aplicar(self, v: ValoresFormulario) -> None:
        self.nos_shorts.set(v.zoom_nos_shorts)
        self._preencher(
            Enquadramento(
                zoom_pct=v.zoom_pct,
                centro_x_pct=v.zoom_centro_x_pct,
                centro_y_pct=v.zoom_centro_y_pct,
            )
        )

    def _preencher(self, e: Enquadramento) -> None:
        self._aplicando = True
        try:
            self.zoom.set(e.zoom_pct)
            self.zoom_texto.set(f"{e.zoom_pct:g}")
            self.centro_x.set(e.centro_x_pct)
            self.centro_y.set(e.centro_y_pct)
        finally:
            self._aplicando = False
        self._desenhar()

    # --- mudanças -----------------------------------------------------------------------------
    def _mudou(self) -> None:
        self._ao_mudar()

    def _moveu_zoom(self, _v=None) -> None:
        self.zoom_texto.set(f"{round(self.zoom.get())}")
        self._depois_de_mexer()

    def _digitou_zoom(self) -> None:
        try:
            valor = float(self.zoom_texto.get().replace(",", ".").replace("%", "").strip())
        except ValueError:
            self.zoom_texto.set(f"{round(self.zoom.get())}")
            return
        valor = min(max(valor, 100.0), _ZOOM_MAXIMO_NA_TELA)
        self.zoom.set(valor)
        self.zoom_texto.set(f"{valor:g}")
        self._depois_de_mexer()

    def _moveu_centro(self) -> None:
        self._depois_de_mexer()

    def _depois_de_mexer(self) -> None:
        if self._aplicando:
            return
        self.preset.set(_SEM_PRESET)
        self._desenhar()
        self._ao_mudar()

    def zerar(self) -> None:
        self._preencher(Enquadramento())
        self.preset.set(_SEM_PRESET)
        self._ao_mudar()

    def _clicou(self, evento) -> None:
        if self._imagem is None:
            return
        x, y = centro_pelo_clique(evento.x, evento.y, self._imagem.width, self._imagem.height)
        self._aplicando = True
        try:
            self.centro_x.set(round(x, 1))
            self.centro_y.set(round(y, 1))
        finally:
            self._aplicando = False
        self._depois_de_mexer()

    # --- quadro -------------------------------------------------------------------------------
    def _video_mudou(self) -> None:
        self._quadro = self._imagem = None
        self._indice_do_quadro = 0
        self._desenhar()
        self._carregar_quadro()

    def outro_quadro(self) -> None:
        self._indice_do_quadro += 1
        self._carregar_quadro()

    def _carregar_quadro(self) -> None:
        if not self._fonte.pronta:
            self._aviso.configure(
                text="Escolha um vídeo para ver o enquadramento.", text_color=_COR_DICA
            )
            return
        self._aviso.configure(text="Carregando um quadro do vídeo…", text_color=_COR_DICA)
        self._fonte.quadro(
            self._fonte.instante_de_exemplo(self._indice_do_quadro),
            self._quadro_pronto,
            self._quadro_falhou,
            chave="zoom_quadro",
        )

    def _quadro_pronto(self, quadro: QuadroDeExemplo) -> None:
        self._quadro = quadro
        self._imagem = ajustar_ao_tamanho(quadro.imagem, (_LARGURA_DA_PREVIA, _LARGURA_DA_PREVIA))
        self._aviso.configure(text="")
        self._tela.configure(height=self._imagem.height)
        self._desenhar()

    def _quadro_falhou(self, erro: Exception) -> None:
        self._aviso.configure(
            text=f"Não foi possível ler um quadro do vídeo: {erro}", text_color=_COR_ERRO
        )

    def _desenhar(self) -> None:
        e = self.enquadramento()
        if e.ativo:
            largura = 10000 / e.zoom_pct
            texto = f"Zoom de {e.zoom_pct:g}%: o recorte tem {largura:.0f}% da largura do quadro."
            if e.zoom_pct > _NITIDEZ_PERDIDA_ACIMA_DE:
                texto += " Acima de 150% a imagem perde bastante nitidez."
            self._info.configure(
                text=texto,
                text_color=_COR_AVISO if e.zoom_pct > _NITIDEZ_PERDIDA_ACIMA_DE else _COR_DICA,
            )
        else:
            self._info.configure(text="Sem zoom: o quadro inteiro.", text_color=_COR_DICA)
        if self._imagem is None:
            self._tela.itemconfigure(self._imagem_na_tela, image="")
            self._foto = None
            return
        nova = ImageTk.PhotoImage(sombrear_fora_do_zoom(self._imagem, e))
        self._tela.itemconfigure(self._imagem_na_tela, image=nova)  # a nova antes de soltar a velha
        self._foto = nova

    # --- presets ------------------------------------------------------------------------------
    def _atualizar_presets(self) -> None:
        self._menu_preset.configure(values=[_SEM_PRESET, *sorted(self._presets.enquadramentos())])

    def _escolheu_preset(self, nome: str) -> None:
        escolhido = self._presets.enquadramentos().get(nome)
        if escolhido is None:
            return
        self._preencher(escolhido)
        self._ao_mudar()

    def salvar_preset(self) -> None:
        nome = dialogos.pedir_texto(
            self.winfo_toplevel(),
            "Salvar preset de zoom",
            "Dê um nome a este enquadramento (se o nome já existir, o preset é substituído).",
            inicial=self.preset.get() if self.preset.get() != _SEM_PRESET else "",
        )
        if not nome:
            return
        self._presets.salvar_enquadramento(nome, self.enquadramento())
        self._atualizar_presets()
        self.preset.set(nome)

    def excluir_preset(self) -> None:
        nome = self.preset.get()
        if nome == _SEM_PRESET or nome not in self._presets.enquadramentos():
            return
        if dialogos.confirmar(
            self.winfo_toplevel(), "Excluir preset", f"Excluir o preset “{nome}”?",
            sim="Excluir", nao="Manter",
        ):  # fmt: skip
            self._presets.excluir_enquadramento(nome)
            self.preset.set(_SEM_PRESET)
            self._atualizar_presets()

    # --- habilitação --------------------------------------------------------------------------
    def habilitar(self, ativo: bool, *, shorts: bool = True) -> None:
        estado = "normal" if ativo else "disabled"
        for controle in self._filhos:
            controle.configure(state=estado)
        self._check_shorts.configure(state="normal" if ativo and shorts else "disabled")
