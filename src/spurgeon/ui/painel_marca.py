"""Cartão "Marca d'água": imagem, fundo, canto, tamanho e onde a marca entra.

A imagem pode ser PNG com fundo transparente (usada como está) ou qualquer outra (JPG, WebP…, ou PNG
sem transparência): nestas, a pessoa pode pedir que o fundo seja removido. Duas prévias mostram o
resultado: a imagem sobre um xadrez (o que é transparente aparece) e um quadro 16:9 com a marca no
canto e no tamanho escolhidos.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageDraw

from spurgeon.core.config import CantoMarca
from spurgeon.render import filtros as f
from spurgeon.render.marca import (
    EXTENSOES,
    MARGEM_PCT,
    ROTULOS_DOS_CANTOS,
    InfoImagem,
    ler_info,
    posicao_em_pixels,
    preparar_imagem,
)
from spurgeon.ui.formulario import ValoresFormulario

_COR_DICA = "gray60"
_COR_AVISO = "#e0a030"
_COR_ERRO = "#e05555"
_LADO_MINIATURA = 104
_TAMANHO_PREVIA = (192, 108)
_PNG_SUFIXOS = " ".join(f"*{e}" for e in EXTENSOES)
_MAIOR_LADO_DA_PREVIA = (
    320  # a prévia é feita numa cópia pequena: remover o fundo é lento em 1200 px
)


def miniatura_sobre_xadrez(imagem: Image.Image, lado: int = _LADO_MINIATURA) -> Image.Image:
    """A imagem sobre um xadrez cinza, para a transparência aparecer."""
    quadro = Image.new("RGB", (lado, lado), "#3a3a3a")
    desenho = ImageDraw.Draw(quadro)
    for y in range(0, lado, 8):
        for x in range(0, lado, 8):
            if (x // 8 + y // 8) % 2:
                desenho.rectangle((x, y, x + 7, y + 7), fill="#2c2c2c")
    copia = imagem.copy()
    copia.thumbnail((lado - 8, lado - 8), Image.Resampling.LANCZOS)
    quadro.paste(copia, ((lado - copia.width) // 2, (lado - copia.height) // 2), copia)
    return quadro


def previa_da_posicao(
    marca: Image.Image | None,
    canto: CantoMarca,
    tamanho_pct: float,
    *,
    tamanho: tuple[int, int] = _TAMANHO_PREVIA,
) -> Image.Image:
    """Um quadro 16:9 escuro com a marca no canto e no tamanho que o vídeo terá."""
    largura, altura = tamanho
    quadro = Image.new("RGB", tamanho, "#1e1e1e")
    ImageDraw.Draw(quadro).rectangle((0, 0, largura - 1, altura - 1), outline="#555555")
    largura_marca = max(4, round(largura * tamanho_pct / 100))
    if marca is None:
        marca = Image.new("RGBA", (40, 40), "#1f6aa5")
    escala = largura_marca / marca.width
    pronta = marca.resize((largura_marca, max(2, round(marca.height * escala))), Image.LANCZOS)
    x, y = posicao_em_pixels(
        canto, largura, altura, pronta.width, pronta.height, round(largura * MARGEM_PCT / 100)
    )
    quadro.paste(pronta, (x, y), pronta if pronta.mode == "RGBA" else None)
    return quadro


def texto_do_formato(info: InfoImagem) -> tuple[str, bool]:
    """(o que dizer sobre a imagem, se o fundo pode ser removido)."""
    if info.tem_transparencia:
        return (
            f"{info.formato} com fundo transparente: será usado como está "
            f"({info.largura}×{info.altura}).",
            False,
        )
    qual = "Este PNG não tem" if info.eh_png else f"{info.formato or 'Esta imagem'} não tem"
    return (
        f"{qual} fundo transparente. Se tiver um fundo liso, marque “Remover o fundo da imagem”.",
        True,
    )


class PainelMarca(ctk.CTkFrame):
    def __init__(
        self,
        master,
        valores: ValoresFormulario,
        *,
        pasta_inicial: Path,
        ao_mudar: Callable[[], None],
    ):
        super().__init__(master)
        self._ao_mudar = ao_mudar
        self._pasta_inicial = pasta_inicial
        self._info: InfoImagem | None = None
        self._imagens: dict = {}  # a imagem de cada rótulo, para o Python não a descartar
        self._vazia = ctk.CTkImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), size=(1, 1))
        self._livre = True
        self._erro = ""
        self._pode_remover = False
        self._marca_pronta: Image.Image | None = None

        self.ativa = ctk.BooleanVar(value=valores.marca_dagua)
        self.imagem = ctk.StringVar(value=valores.marca_imagem)
        self.remover = ctk.BooleanVar(value=valores.marca_remover_fundo)
        self.canto = ctk.StringVar(value=valores.marca_canto)
        self.tamanho = ctk.DoubleVar(value=valores.marca_tamanho_pct)
        self.no_longo = ctk.BooleanVar(value=valores.marca_no_video_longo)
        self.nos_shorts = ctk.BooleanVar(value=valores.marca_nos_shorts)
        self._filhos: list[ctk.CTkBaseClass] = []

        ctk.CTkLabel(self, text="4. Marca d'água", font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        self._check_ativa = ctk.CTkCheckBox(
            self,
            text="Colocar uma marca d'água (logotipo) no vídeo",
            variable=self.ativa,
            command=self._mudou,
        )
        self._check_ativa.pack(anchor="w", padx=12, pady=3)
        self._corpo = ctk.CTkFrame(self, fg_color="transparent")
        self._montar_corpo(self._corpo)
        self._corpo.pack(fill="x")
        self.atualizar_imagem(refazer_info=True)

    # --- montagem -----------------------------------------------------------------------------
    def _montar_corpo(self, c: ctk.CTkFrame) -> None:
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(anchor="w", fill="x", padx=(40, 12), pady=3)
        self._botao_imagem = ctk.CTkButton(
            linha, text="Escolher imagem…", width=130, command=self.escolher_imagem
        )
        self._botao_imagem.pack(side="left")
        self._filhos.append(self._botao_imagem)
        self._nome = ctk.CTkLabel(linha, text="", anchor="w", text_color=_COR_DICA)
        self._nome.pack(side="left", padx=8)

        meio = ctk.CTkFrame(c, fg_color="transparent")
        meio.pack(anchor="w", fill="x", padx=(40, 12), pady=3)
        self._miniatura = ctk.CTkLabel(meio, text="sem imagem", width=_LADO_MINIATURA, height=60)
        self._miniatura.pack(side="left")
        texto = ctk.CTkFrame(meio, fg_color="transparent")
        texto.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._info_texto = ctk.CTkLabel(
            texto, text="", anchor="w", justify="left", wraplength=290, text_color=_COR_DICA
        )
        self._info_texto.pack(anchor="w")
        self._check_remover = ctk.CTkCheckBox(
            texto, text="Remover o fundo da imagem", variable=self.remover, command=self._mudou
        )
        self._check_remover.pack(anchor="w", pady=(4, 0))
        self._filhos.append(self._check_remover)

        posicao = ctk.CTkFrame(c, fg_color="transparent")
        posicao.pack(anchor="w", fill="x", padx=(40, 12), pady=(6, 3))
        ctk.CTkLabel(posicao, text="Canto do vídeo", anchor="w").grid(row=0, column=0, sticky="w")
        for k, (canto, rotulo) in enumerate(ROTULOS_DOS_CANTOS.items()):
            botao = ctk.CTkRadioButton(
                posicao, text=rotulo, variable=self.canto, value=canto, command=self._mudou
            )
            botao.grid(row=1 + k, column=0, sticky="w", padx=(0, 14), pady=2)
            self._filhos.append(botao)
        self._previa = ctk.CTkLabel(posicao, text="")
        self._previa.grid(row=0, column=1, rowspan=5, padx=(10, 0))

        tamanho = ctk.CTkFrame(c, fg_color="transparent")
        tamanho.pack(anchor="w", fill="x", padx=(40, 12), pady=(6, 3))
        ctk.CTkLabel(tamanho, text="Tamanho", anchor="w").pack(side="left")
        self._slider = ctk.CTkSlider(
            tamanho, from_=3, to=50, number_of_steps=47, variable=self.tamanho, width=190,
            command=lambda _v: self._mudou(),
        )  # fmt: skip
        self._slider.pack(side="left", padx=10)
        self._filhos.append(self._slider)
        self._rotulo_tamanho = ctk.CTkLabel(tamanho, text="", anchor="w", text_color=_COR_DICA)
        self._rotulo_tamanho.pack(side="left")

        onde = ctk.CTkFrame(c, fg_color="transparent")
        onde.pack(anchor="w", fill="x", padx=(40, 12), pady=3)
        ctk.CTkLabel(onde, text="Aplicar em", anchor="w").pack(side="left")
        self._check_longo = ctk.CTkCheckBox(
            onde, text="Vídeo longo", variable=self.no_longo, command=self._mudou, width=100
        )
        self._check_longo.pack(side="left", padx=(10, 6))
        self._check_shorts = ctk.CTkCheckBox(
            onde, text="Shorts", variable=self.nos_shorts, command=self._mudou, width=80
        )
        self._check_shorts.pack(side="left")
        self._filhos += [self._check_longo, self._check_shorts]
        self._dica = ctk.CTkLabel(
            c,
            text="A marca é gravada na imagem do vídeo: se o vídeo longo ou um Short já foi "
            "renderizado sem ela, será renderizado de novo.",
            anchor="w",
            justify="left",
            wraplength=430,
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
        )
        self._dica.pack(anchor="w", padx=(40, 12), pady=(2, 8))

    # --- imagem -------------------------------------------------------------------------------
    def escolher_imagem(self) -> None:
        atual = Path(self.imagem.get()) if self.imagem.get() else None
        inicial = atual.parent if atual and atual.parent.exists() else self._pasta_inicial
        caminho = filedialog.askopenfilename(
            title="Escolha a imagem da marca d'água",
            initialdir=str(inicial if inicial.exists() else Path.home()),
            filetypes=[("Imagens", _PNG_SUFIXOS), ("Todos os arquivos", "*.*")],
        )
        if caminho:
            self.definir_imagem(Path(caminho))

    def definir_imagem(self, caminho: Path | str) -> None:
        self.imagem.set(str(caminho))
        self.remover.set(False)
        self.atualizar_imagem(refazer_info=True)
        self._mudou()

    def atualizar_imagem(self, *, refazer_info: bool = False) -> None:
        """Lê a imagem escolhida e refaz o texto, a miniatura e a prévia."""
        caminho = Path(self.imagem.get()) if self.imagem.get().strip() else None
        if refazer_info:
            self._info = None
            self._erro = ""
            if caminho is not None:
                try:
                    self._info = ler_info(caminho)
                except (OSError, ValueError) as erro:
                    self._erro = f"Não foi possível abrir a imagem: {erro}"
        self._nome.configure(text=caminho.name if caminho else "nenhuma imagem escolhida")
        self._pode_remover = False
        if caminho is None:
            self._info_texto.configure(
                text="Escolha um PNG com fundo transparente, ou outra imagem."
            )
        elif self._info is None:
            self._info_texto.configure(
                text=self._erro or "Imagem não encontrada.", text_color=_COR_ERRO
            )
        else:
            texto, self._pode_remover = texto_do_formato(self._info)
            self._info_texto.configure(
                text=texto, text_color=_COR_AVISO if self._pode_remover else _COR_DICA
            )
        self._atualizar_miniatura(caminho)
        self._atualizar_previa()
        self._atualizar_rotulo_tamanho()

    def _imagem_pronta(self, caminho: Path | None) -> Image.Image | None:
        """A marca como ficará (com o fundo removido, se pedido), numa cópia pequena."""
        if caminho is None or self._info is None:
            return None
        try:
            with Image.open(caminho) as aberta:
                aberta.load()
                pequena = aberta.copy()
            pequena.thumbnail((_MAIOR_LADO_DA_PREVIA, _MAIOR_LADO_DA_PREVIA), Image.LANCZOS)
            return preparar_imagem(pequena, remover=self.remover.get() and self._pode_remover)
        except (OSError, ValueError):
            return None

    def _trocar_imagem(self, rotulo: ctk.CTkLabel, nova: ctk.CTkImage, texto: str = "") -> None:
        """Põe a imagem nova e só depois solta a antiga (o Tk quebra se ela some antes)."""
        rotulo.configure(image=nova, text=texto)
        self._imagens[rotulo] = nova

    def _atualizar_miniatura(self, caminho: Path | None) -> None:
        pronta = self._imagem_pronta(caminho)
        self._marca_pronta = pronta
        if pronta is None:
            self._trocar_imagem(self._miniatura, self._vazia, texto="sem imagem")
            return
        quadro = miniatura_sobre_xadrez(pronta)
        self._trocar_imagem(self._miniatura, ctk.CTkImage(quadro, size=quadro.size))

    def _atualizar_previa(self) -> None:
        previa = previa_da_posicao(self._marca_pronta, self.canto.get(), self.tamanho.get())
        self._trocar_imagem(self._previa, ctk.CTkImage(previa, size=previa.size))

    def _atualizar_rotulo_tamanho(self) -> None:
        pct = round(self.tamanho.get())
        self._rotulo_tamanho.configure(
            text=f"{pct}% da largura ({round(f.LARGURA_SHORT * pct / 100)} px no Short)"
        )

    # --- interface com o painel de opções -----------------------------------------------------
    def aplicar(self, v: ValoresFormulario) -> None:
        """Põe na tela as escolhas de marca d'água de um projeto aberto."""
        self.ativa.set(v.marca_dagua)
        self.imagem.set(v.marca_imagem)
        self.canto.set(v.marca_canto)
        self.tamanho.set(v.marca_tamanho_pct)
        self.no_longo.set(v.marca_no_video_longo)
        self.nos_shorts.set(v.marca_nos_shorts)
        self.atualizar_imagem(refazer_info=True)
        self.remover.set(v.marca_remover_fundo and self._pode_remover)
        self.atualizar_imagem()

    def _mudou(self) -> None:
        self._atualizar_miniatura(Path(self.imagem.get()) if self.imagem.get().strip() else None)
        self._atualizar_previa()
        self._atualizar_rotulo_tamanho()
        self._ao_mudar()

    def valores(self) -> dict:
        return {
            "marca_dagua": self.ativa.get(),
            "marca_imagem": self.imagem.get().strip(),
            "marca_remover_fundo": self.remover.get() and self._pode_remover,
            "marca_canto": self.canto.get(),
            "marca_tamanho_pct": float(round(self.tamanho.get())),
            "marca_no_video_longo": self.no_longo.get(),
            "marca_nos_shorts": self.nos_shorts.get(),
        }

    def aplicar_habilitacoes(self, h, *, livre: bool) -> None:
        """Liga e desliga os controles conforme o modo e o que será gerado."""
        self._livre = livre
        self._check_ativa.configure(state="normal" if livre and h.marca_dagua else "disabled")
        campos = livre and h.marca_campos
        for widget in self._filhos:
            widget.configure(state="normal" if campos else "disabled")
        self._check_remover.configure(
            state="normal" if campos and self._pode_remover else "disabled"
        )
        self._check_longo.configure(
            state="normal" if campos and h.marca_no_video_longo else "disabled"
        )
        self._check_shorts.configure(
            state="normal" if campos and h.marca_nos_shorts else "disabled"
        )
        if h.marca_campos:  # o aviso só vale quando a marca está ligada
            self._dica.pack(anchor="w", padx=(40, 12), pady=(2, 8))
        else:
            self._dica.pack_forget()
