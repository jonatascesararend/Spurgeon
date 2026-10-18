"""Seção "Ajustar o zoom e a legenda dos Shorts": como o corte vertical vai ficar, sem renderizar.

A prévia é um quadro do vídeo passando pelo MESMO filtro do render do Short (recorte 9:16 dentro do
zoom do vídeo, zoom só dos Shorts, tratamento de cor, ampliação para 1080×1920) com duas linhas de
legenda de exemplo, montadas pelo mesmo gerador das legendas de verdade: o tamanho e a altura que se
veem aqui são os que saem no arquivo, sem precisar gerar legenda nenhuma ainda.
"""

from __future__ import annotations

import tkinter
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from spurgeon.core.aparencia import Enquadramento
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.render import previa
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.composicao import ajustar_ao_tamanho
from spurgeon.ui.formulario import ValoresFormulario
from spurgeon.ui.quadros import FonteDeQuadros, QuadroDeExemplo
from spurgeon.ui.youtube_shorts import conflitos_da_legenda, sobrepor_elementos

_COR_DICA = "gray60"
_COR_AVISO = "#e0a030"
_COR_ERRO = "#e05555"
_COR_OK = "#4caf72"
_TEXTO_ESCONDER = "Esconder elementos do YouTube"
_TEXTO_MOSTRAR = "Mostrar elementos do YouTube"
_LARGURA_DA_PREVIA = 234  # 9:16 → 416 de altura
_ALTURA_DA_PREVIA = 416
_ATRASO_DA_PREVIA_MS = 160
_ZOOM_MAXIMO_NA_TELA = 200.0
#: o 9:16 já é uma ampliação de ~1,8× do vídeo em 1080p; um zoom a mais borra depressa
_NITIDEZ_PERDIDA_ACIMA_DE = 125.0
_LEGENDA_MINIMA, _LEGENDA_MAXIMA = 30.0, 90.0  # % da altura, contada de cima


class PainelShorts(ctk.CTkFrame):
    def __init__(
        self,
        master,
        valores: ValoresFormulario,
        *,
        fonte: FonteDeQuadros,
        tarefas: Tarefas,
        opcoes_atuais: Callable[[], OpcoesProcessamento | None],
        ao_mudar: Callable[[], None],
        pasta_fontes: Path | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._fonte = fonte
        self._tarefas = tarefas
        self._opcoes_atuais = opcoes_atuais
        self._ao_mudar = ao_mudar
        self._pasta_fontes = pasta_fontes
        self._quadro: QuadroDeExemplo | None = None
        self._imagem: Image.Image | None = None  # a prévia pronta, no tamanho da tela
        self._foto: ImageTk.PhotoImage | None = None
        self._indice_do_quadro = 0
        self._adiado: str | None = None
        self._chave: tuple | None = None  # o que a prévia mostrada representa
        self._sequencia = 0  # cada prévia grava num arquivo próprio (duas podem se sobrepor)
        self._aplicando = False

        self.zoom = ctk.DoubleVar(value=valores.shorts_zoom_pct)
        self.zoom_texto = ctk.StringVar(value=f"{valores.shorts_zoom_pct:g}")
        self.centro_x = ctk.DoubleVar(value=valores.shorts_zoom_centro_x_pct)
        self.centro_y = ctk.DoubleVar(value=valores.shorts_zoom_centro_y_pct)
        self.legenda = ctk.DoubleVar(value=_legenda_do_texto(valores.posicao_legenda))
        self._filhos: list[ctk.CTkBaseClass] = []

        self._montar()
        fonte.ao_mudar_o_video(self._video_mudou)
        self._atualizar_textos()

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self) -> None:
        self.rotulo_titulo = ctk.CTkLabel(
            self, text="Ajustar o zoom e a legenda dos Shorts", font=ctk.CTkFont(weight="bold")
        )  # a janela o esconde quando o painel vem dentro de uma seção recolhível
        self.rotulo_titulo.pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(
            self,
            text="O quadro mostra como o corte vertical (9:16) vai ficar, já com o zoom do vídeo, "
            "a cor e o deslocamento do recorte. As duas linhas de texto simulam a legenda: elas só "
            "aparecem no Short se “Legendas dinâmicas” estiver marcado.",
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
        self._tela = tkinter.Canvas(
            self, width=_LARGURA_DA_PREVIA, height=_ALTURA_DA_PREVIA, bg="#1c1c1c",
            highlightthickness=0, cursor="sb_v_double_arrow",
        )  # fmt: skip
        self._tela.pack(padx=12, pady=(2, 2))
        self._imagem_na_tela = self._tela.create_image(0, 0, anchor="nw")
        self._tela.bind("<Button-1>", self._clicou)
        self._tela.bind("<B1-Motion>", self._clicou)
        self.mostrar_youtube = ctk.BooleanVar(value=True)
        linha_youtube = ctk.CTkFrame(self, fg_color="transparent")
        linha_youtube.pack(fill="x", padx=12, pady=(0, 2))
        self._botao_youtube = ctk.CTkButton(
            linha_youtube,
            text=_TEXTO_ESCONDER,
            width=250,
            height=26,
            command=self._alternar_youtube,
        )
        self._botao_youtube.pack(side="left")
        self._conflito = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", wraplength=430, font=ctk.CTkFont(size=12)
        )
        self._conflito.pack(anchor="w", padx=12)
        ctk.CTkLabel(
            self,
            text="Clique ou arraste no quadro para pôr a legenda na altura que quiser.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(anchor="w", padx=12)

        linha_zoom = ctk.CTkFrame(self, fg_color="transparent")
        linha_zoom.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(linha_zoom, text="Zoom do Short", width=130, anchor="w").pack(side="left")
        slider = ctk.CTkSlider(
            linha_zoom, from_=100, to=_ZOOM_MAXIMO_NA_TELA, number_of_steps=100,
            variable=self.zoom, width=170, command=self._moveu_zoom,
        )  # fmt: skip
        slider.pack(side="left", padx=6)
        campo = ctk.CTkEntry(linha_zoom, textvariable=self.zoom_texto, width=52, justify="center")
        campo.pack(side="left")
        campo.bind("<Return>", lambda _e: self._digitou_zoom())
        campo.bind("<FocusOut>", lambda _e: self._digitou_zoom())
        ctk.CTkLabel(linha_zoom, text="%").pack(side="left", padx=(2, 0))
        self._filhos += [slider, campo]

        for rotulo, variavel in (
            ("Posição horizontal", self.centro_x),
            ("Posição vertical", self.centro_y),
        ):
            linha = ctk.CTkFrame(self, fg_color="transparent")
            linha.pack(fill="x", padx=12, pady=(2, 0))
            ctk.CTkLabel(linha, text=rotulo, width=130, anchor="w").pack(side="left")
            controle = ctk.CTkSlider(
                linha, from_=0, to=100, number_of_steps=100, variable=variavel, width=170,
                command=lambda _v: self._moveu_posicao(),
            )  # fmt: skip
            controle.pack(side="left", padx=6)
            self._filhos.append(controle)

        linha_legenda = ctk.CTkFrame(self, fg_color="transparent")
        linha_legenda.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(linha_legenda, text="Altura da legenda", width=130, anchor="w").pack(
            side="left"
        )
        controle = ctk.CTkSlider(
            linha_legenda, from_=_LEGENDA_MINIMA, to=_LEGENDA_MAXIMA,
            number_of_steps=int(_LEGENDA_MAXIMA - _LEGENDA_MINIMA), variable=self.legenda,
            width=170, command=lambda _v: self._moveu_legenda(),
        )  # fmt: skip
        controle.pack(side="left", padx=6)
        self._rotulo_legenda = ctk.CTkLabel(linha_legenda, text="", width=44, anchor="w")
        self._rotulo_legenda.pack(side="left")
        self._filhos.append(controle)
        ctk.CTkLabel(
            self,
            text="Contada de cima, até o centro da legenda. Deixe os elementos do YouTube ligados "
            "na prévia para conferir que a legenda e o pregador não ficam atrás dos botões, do "
            "nome do canal ou do título.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12)

        self._info = ctk.CTkLabel(
            self, text="", text_color=_COR_DICA, anchor="w", justify="left", wraplength=430
        )
        self._info.pack(anchor="w", padx=12, pady=(4, 0))

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.pack(fill="x", padx=12, pady=(4, 8))
        for texto, comando, largura in (
            ("Outro quadro", self.outro_quadro, 110),
            ("Sem zoom", self.zerar_zoom, 90),
            ("Legenda a 62%", self.legenda_padrao, 120),
        ):
            botao = ctk.CTkButton(botoes, text=texto, width=largura, command=comando)
            botao.pack(side="left", padx=(0, 6))
            self._filhos.append(botao)

    # --- valores ------------------------------------------------------------------------------
    def zoom_do_short(self) -> Enquadramento:
        return Enquadramento(
            zoom_pct=min(max(round(self.zoom.get()), 100), 300),
            centro_x_pct=round(self.centro_x.get(), 1),
            centro_y_pct=round(self.centro_y.get(), 1),
        )

    def posicao_da_legenda(self) -> float:
        """A altura da legenda em % da altura do Short, de cima."""
        return min(max(round(self.legenda.get()), _LEGENDA_MINIMA), _LEGENDA_MAXIMA)

    def valores(self) -> dict:
        e = self.zoom_do_short()
        return {
            "shorts_zoom_pct": e.zoom_pct,
            "shorts_zoom_centro_x_pct": e.centro_x_pct,
            "shorts_zoom_centro_y_pct": e.centro_y_pct,
            "posicao_legenda": f"{self.posicao_da_legenda():g}".replace(".", ","),
        }

    def aplicar(self, v: ValoresFormulario) -> None:
        self._aplicando = True
        try:
            self.zoom.set(v.shorts_zoom_pct)
            self.zoom_texto.set(f"{v.shorts_zoom_pct:g}")
            self.centro_x.set(v.shorts_zoom_centro_x_pct)
            self.centro_y.set(v.shorts_zoom_centro_y_pct)
            self.legenda.set(_legenda_do_texto(v.posicao_legenda))
        finally:
            self._aplicando = False
        self._atualizar_textos()
        self._pedir_previa()

    # --- mudanças -----------------------------------------------------------------------------
    def _depois_de_mexer(self) -> None:
        if self._aplicando:
            return
        self._atualizar_textos()
        self._pedir_previa()
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

    def _moveu_posicao(self) -> None:
        self._depois_de_mexer()

    def _moveu_legenda(self) -> None:
        self._depois_de_mexer()

    def _clicou(self, evento) -> None:
        """Clicar no quadro põe a legenda naquela altura."""
        fracao = min(max(evento.y / _ALTURA_DA_PREVIA, 0.0), 1.0) * 100
        self.legenda.set(min(max(round(fracao), _LEGENDA_MINIMA), _LEGENDA_MAXIMA))
        self._depois_de_mexer()

    def zerar_zoom(self) -> None:
        self._aplicando = True
        try:
            self.zoom.set(100)
            self.zoom_texto.set("100")
            self.centro_x.set(50)
            self.centro_y.set(50)
        finally:
            self._aplicando = False
        self._depois_de_mexer()

    def legenda_padrao(self) -> None:
        self.legenda.set(OpcoesProcessamento.model_fields["legenda_posicao_vertical"].default * 100)
        self._depois_de_mexer()

    def _alternar_youtube(self) -> None:
        """Esconde ou mostra os botões, o canal e o título do YouTube por cima da prévia."""
        self.mostrar_youtube.set(not self.mostrar_youtube.get())
        self._botao_youtube.configure(
            text=_TEXTO_ESCONDER if self.mostrar_youtube.get() else _TEXTO_MOSTRAR
        )
        self._atualizar_conflito()
        self._desenhar()

    def _atualizar_conflito(self) -> None:
        """Diz, em palavras, se a legenda nesta altura toca nos elementos do YouTube."""
        if not self.mostrar_youtube.get():
            self._conflito.configure(text="")
            return
        posicao = self.posicao_da_legenda()
        achados = conflitos_da_legenda(posicao / 100)
        if achados:
            self._conflito.configure(
                text=f"⚠ A legenda a {posicao:g}% pode ficar atrás {_lista(achados)}. Suba ou "
                "desça a legenda até ela sair dessas áreas.",
                text_color=_COR_AVISO,
            )
        else:
            self._conflito.configure(
                text=f"✔ A legenda a {posicao:g}% fica livre dos elementos do YouTube.",
                text_color=_COR_OK,
            )

    def _atualizar_textos(self) -> None:
        self._rotulo_legenda.configure(text=f"{self.posicao_da_legenda():g}%")
        self._atualizar_conflito()
        e = self.zoom_do_short()
        if not e.ativo:
            self._info.configure(
                text="Sem zoom extra: o Short usa o recorte vertical inteiro.", text_color=_COR_DICA
            )
            return
        texto = f"Zoom de {e.zoom_pct:g}% dentro do recorte vertical."
        perde = e.zoom_pct > _NITIDEZ_PERDIDA_ACIMA_DE
        if perde:
            texto += " Acima de 125% a imagem perde bastante nitidez."
        self._info.configure(text=texto, text_color=_COR_AVISO if perde else _COR_DICA)

    # --- quadro e prévia ----------------------------------------------------------------------
    def _video_mudou(self) -> None:
        self._quadro = self._imagem = None
        self._chave = None
        self._indice_do_quadro = 0
        self._desenhar()
        self._carregar_quadro()

    def outro_quadro(self) -> None:
        self._indice_do_quadro += 1
        self._carregar_quadro()

    def _carregar_quadro(self) -> None:
        if not self._fonte.pronta:
            self._aviso.configure(
                text="Escolha um vídeo para ver como o Short vai ficar.", text_color=_COR_DICA
            )
            return
        self._aviso.configure(text="Carregando um quadro do vídeo…", text_color=_COR_DICA)
        self._fonte.quadro(
            self._fonte.instante_de_exemplo(self._indice_do_quadro),
            self._quadro_pronto,
            self._falhou,
            chave="shorts_quadro",
        )

    def _quadro_pronto(self, quadro: QuadroDeExemplo) -> None:
        self._quadro = quadro
        self._chave = None
        self._pedir_previa()

    def _falhou(self, erro: Exception) -> None:
        self._aviso.configure(
            text=f"Não foi possível montar a prévia do Short: {erro}", text_color=_COR_ERRO
        )

    def atualizar_previa(self) -> None:
        """Chamada pela janela quando qualquer ajuste muda (zoom do vídeo, cor, deslocamento…)."""
        self._pedir_previa()

    def _pedir_previa(self) -> None:
        if self._aplicando or self._quadro is None:
            return
        if self._adiado is not None:
            self.after_cancel(self._adiado)
        self._adiado = self.after(_ATRASO_DA_PREVIA_MS, self._gerar_previa)

    def _gerar_previa(self) -> None:
        self._adiado = None
        quadro, info = self._quadro, self._fonte.info
        opcoes = self._opcoes_atuais()
        if quadro is None or info is None or opcoes is None:
            return  # sem quadro ainda, ou há um campo incompleto: a prévia anterior continua
        ao_vivo = self._opcoes_deste_painel(opcoes)
        chave = (
            str(quadro.arquivo),
            ao_vivo.enquadramento_nos_curtos.model_dump_json(),
            ao_vivo.enquadramento_do_short.model_dump_json(),
            ao_vivo.tratamento_nos_curtos.model_dump_json(),
            ao_vivo.recorte_deslocamento_x_pct,
            ao_vivo.legenda_posicao_vertical,
        )
        if chave == self._chave and self._imagem is not None:
            return  # nada que apareça no Short mudou
        self._sequencia += 1
        destino = self._fonte.caminho_temporario(f"short_{self._sequencia}.png")
        self._tarefas.rodar(
            lambda: previa.previa_do_short(
                quadro.arquivo, info, quadro.imagem.size, destino,
                deslocamento_pct=ao_vivo.recorte_deslocamento_x_pct,
                posicao_legenda=ao_vivo.legenda_posicao_vertical,
                enquadramento=ao_vivo.enquadramento_nos_curtos,
                zoom_do_short=ao_vivo.enquadramento_do_short,
                tratamento=ao_vivo.tratamento_nos_curtos,
                pasta_fontes=self._pasta_fontes,
            ),
            lambda png: self._previa_pronta(png, quadro, chave),
            self._falhou,
            chave="shorts_previa",
        )  # fmt: skip

    def _opcoes_deste_painel(self, opcoes: OpcoesProcessamento) -> OpcoesProcessamento:
        """As opções da janela, com o zoom e a legenda que estão neste painel agora."""
        e = self.zoom_do_short()
        return opcoes.model_copy(
            update={
                "shorts_zoom_pct": e.zoom_pct,
                "shorts_zoom_centro_x_pct": e.centro_x_pct,
                "shorts_zoom_centro_y_pct": e.centro_y_pct,
                "legenda_posicao_vertical": self.posicao_da_legenda() / 100,
            }
        )

    def _previa_pronta(self, png, quadro: QuadroDeExemplo, chave: tuple) -> None:
        if quadro is not self._quadro:
            return  # outro quadro foi escolhido enquanto esta prévia saía
        with Image.open(png) as aberta:
            pronta = aberta.convert("RGB")
        Path(png).unlink(missing_ok=True)
        self._imagem = ajustar_ao_tamanho(pronta, (_LARGURA_DA_PREVIA, _ALTURA_DA_PREVIA))
        self._chave = chave
        self._aviso.configure(text="")
        self._desenhar()

    def _desenhar(self) -> None:
        if self._imagem is None:
            self._tela.itemconfigure(self._imagem_na_tela, image="")
            self._foto = None
            return
        imagem = sobrepor_elementos(self._imagem) if self.mostrar_youtube.get() else self._imagem
        nova = ImageTk.PhotoImage(imagem)
        self._tela.itemconfigure(self._imagem_na_tela, image=nova)  # a nova antes de soltar a velha
        self._foto = nova

    # --- habilitação --------------------------------------------------------------------------
    def habilitar(self, ativo: bool) -> None:
        estado = "normal" if ativo else "disabled"
        for controle in self._filhos:
            controle.configure(state=estado)


def _lista(nomes: list[str]) -> str:
    """`os botões` + `o título` → `dos botões e do título` (a preposição entra em cada item)."""
    itens = [f"d{n}" for n in nomes]
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def _legenda_do_texto(texto: str) -> float:
    try:
        valor = float(texto.replace(",", ".").strip())
    except ValueError:
        return 62.0
    return min(max(valor, _LEGENDA_MINIMA), _LEGENDA_MAXIMA)
