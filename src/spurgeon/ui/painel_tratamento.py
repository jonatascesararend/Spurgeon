"""Cartão "Tratamento de imagem": brilho, contraste, cor, brancos e sombras, com o antes e o depois.

Mostra um quadro do vídeo escolhido com uma divisória que se arrasta: à esquerda o original, à
direita o quadro com os ajustes. O quadro tratado sai do FFmpeg, com os mesmos filtros do render,
então o que se vê aqui é o que vai para o arquivo. A sugestão automática mede alguns quadros do
vídeo e propõe um ponto de partida; os presets guardam o estilo para os próximos sermões.
"""

from __future__ import annotations

import tkinter
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from spurgeon.core.aparencia import AJUSTES_DO_TRATAMENTO, TratamentoImagem
from spurgeon.render import previa
from spurgeon.render.imagem import sugerir_tratamento
from spurgeon.ui import dialogos, dicas
from spurgeon.ui.ajuda import CabecalhoDeEtapa
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.composicao import ajustar_ao_tamanho, compor_antes_depois
from spurgeon.ui.formulario import ValoresFormulario
from spurgeon.ui.presets import RepositorioDePresets
from spurgeon.ui.quadros import FonteDeQuadros, QuadroDeExemplo

_COR_DICA = "gray60"
_COR_ERRO = "#e05555"
_LARGURA_DA_PREVIA = 440
_ATRASO_DA_PREVIA_MS = 160
_SEM_PRESET = "(nenhum preset)"
AJUDA_DA_ETAPA = (
    "Corrige a cor e a luz quando a imagem saiu escura, lavada ou amarelada. A sugestão "
    "automática mede o vídeo e propõe um ponto de partida; a divisória mostra o antes e o depois "
    "com os mesmos filtros do arquivo final. Se a imagem já está boa, deixe desligado."
)


class PainelTratamento(ctk.CTkFrame):
    def __init__(
        self,
        master,
        valores: ValoresFormulario,
        *,
        fonte: FonteDeQuadros,
        tarefas: Tarefas,
        presets: RepositorioDePresets,
        ao_mudar: Callable[[], None],
    ):
        super().__init__(master)
        self._fonte = fonte
        self._tarefas = tarefas
        self._presets = presets
        self._ao_mudar = ao_mudar
        self._livre = True
        self._indice_do_quadro = 0
        self._quadro: QuadroDeExemplo | None = None
        self._original: Image.Image | None = None  # já no tamanho da prévia
        self._tratado: Image.Image | None = None
        self._divisao = 0.5
        self._foto: ImageTk.PhotoImage | None = None
        self._adiado: str | None = None
        self._aplicando = False  # True enquanto os ajustes são preenchidos por código
        self._sequencia = 0  # cada prévia grava num arquivo próprio (duas podem se sobrepor)

        self.ativo = ctk.BooleanVar(value=valores.tratamento_ativo)
        self.nos_shorts = ctk.BooleanVar(value=valores.tratamento_nos_shorts)
        self.preset = ctk.StringVar(value=_SEM_PRESET)
        self.ajustes = {
            nome: ctk.DoubleVar(value=getattr(valores, f"tratamento_{nome}"))
            for nome in AJUSTES_DO_TRATAMENTO
        }
        self._filhos: list[ctk.CTkBaseClass] = []
        self._rotulos_valor: dict[str, ctk.CTkLabel] = {}

        self.cabecalho = CabecalhoDeEtapa(self, "4. Tratamento de imagem", AJUDA_DA_ETAPA)
        self.cabecalho.pack(fill="x", padx=12, pady=(10, 4))
        self._check_ativo = ctk.CTkCheckBox(
            self,
            text="Aplicar um tratamento de cor e luz ao vídeo",
            variable=self.ativo,
            command=self._mudou,
        )
        self._check_ativo.pack(anchor="w", padx=12, pady=3)
        dicas.dica(
            self._check_ativo,
            "Liga ou desliga o tratamento. Desligado, a cor e a luz do vídeo ficam como foram "
            "gravadas. Mexer em qualquer ajuste liga sozinho.",
        )
        self._corpo = ctk.CTkFrame(self, fg_color="transparent")
        self._corpo.pack(fill="x")
        self._montar_corpo(self._corpo)

        fonte.ao_mudar_o_video(self._video_mudou)
        self._atualizar_presets()
        self._desenhar()

    # --- montagem -----------------------------------------------------------------------------
    def _montar_corpo(self, c: ctk.CTkFrame) -> None:
        self._aviso = ctk.CTkLabel(
            c,
            text="Escolha um vídeo para ver o antes e o depois.",
            text_color=_COR_DICA,
            anchor="w",
            justify="left",
            wraplength=430,
        )
        self._aviso.pack(anchor="w", padx=12, pady=(2, 2))
        altura = round(_LARGURA_DA_PREVIA * 9 / 16)
        self._tela = tkinter.Canvas(
            c, width=_LARGURA_DA_PREVIA, height=altura, bg="#1c1c1c", highlightthickness=0,
            cursor="sb_h_double_arrow",
        )  # fmt: skip
        self._tela.pack(padx=12, pady=(0, 2))
        self._imagem_na_tela = self._tela.create_image(0, 0, anchor="nw")
        self._tela.bind("<Button-1>", self._arrastar)
        self._tela.bind("<B1-Motion>", self._arrastar)
        dicas.dica(
            self._tela,
            "Clique ou arraste para mover a linha branca: à esquerda está o quadro original e à "
            "direita o mesmo quadro com os ajustes.",
        )
        legenda = ctk.CTkFrame(c, fg_color="transparent")
        legenda.pack(fill="x", padx=12)
        ctk.CTkLabel(legenda, text="◀ original", text_color=_COR_DICA).pack(side="left")
        ctk.CTkLabel(legenda, text="tratado ▶", text_color=_COR_DICA).pack(side="right")
        ctk.CTkLabel(
            c,
            text="Arraste a linha branca para comparar. O quadro é um de vários do vídeo.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(anchor="w", padx=12)

        botoes = ctk.CTkFrame(c, fg_color="transparent")
        botoes.pack(fill="x", padx=12, pady=(6, 2))
        for texto, comando, largura, ajuda in (
            (
                "✨ Sugestão automática", self.sugerir, 170,
                "Mede alguns quadros do vídeo e propõe brilho, contraste e cor para equilibrar a "
                "imagem. É um ponto de partida: ajuste a seu gosto depois.",
            ),
            (
                "Outro quadro", self.outro_quadro, 110,
                "Mostra outro quadro do vídeo no antes e depois, para você ver o efeito em "
                "cenas diferentes.",
            ),
            (
                "Zerar", self.zerar, 70,
                "Volta os sete ajustes para 0 (sem tratamento). O preset escolhido não é apagado.",
            ),
        ):  # fmt: skip
            botao = ctk.CTkButton(botoes, text=texto, width=largura, command=comando)
            botao.pack(side="left", padx=(0, 6))
            dicas.dica(botao, ajuda)
            self._filhos.append(botao)

        for nome, (rotulo, ajuda) in AJUSTES_DO_TRATAMENTO.items():
            linha = ctk.CTkFrame(c, fg_color="transparent")
            linha.pack(fill="x", padx=12, pady=(4, 0))
            ctk.CTkLabel(linha, text=rotulo, width=150, anchor="w").pack(side="left")
            controle = ctk.CTkSlider(
                linha, from_=-100, to=100, number_of_steps=200, variable=self.ajustes[nome],
                width=190, command=lambda _v, n=nome: self._moveu(n),
            )  # fmt: skip
            controle.pack(side="left", padx=6)
            dicas.dica(
                controle,
                f"{rotulo}: arraste para a direita para aumentar e para a esquerda para diminuir. "
                "No 0, nada muda.",
            )
            self._filhos.append(controle)
            valor = ctk.CTkLabel(linha, text="0", width=36, anchor="e")
            valor.pack(side="left")
            self._rotulos_valor[nome] = valor
            ctk.CTkLabel(
                c, text=ajuda, text_color=_COR_DICA, anchor="w", justify="left", wraplength=420,
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=12)  # fmt: skip

        linha_preset = ctk.CTkFrame(c, fg_color="transparent")
        linha_preset.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(linha_preset, text="Preset", width=60, anchor="w").pack(side="left")
        self._menu_preset = ctk.CTkOptionMenu(
            linha_preset, variable=self.preset, values=[_SEM_PRESET], width=170,
            command=self._escolheu_preset, dynamic_resizing=False,
        )  # fmt: skip
        self._menu_preset.pack(side="left", padx=6)
        self._filhos.append(self._menu_preset)
        dicas.dica(
            self._menu_preset,
            "Escolha um estilo que você salvou antes: os sete ajustes são aplicados na hora.",
        )
        for texto, comando, largura, ajuda in (
            (
                "Salvar preset…", self.salvar_preset, 110,
                "Guarda os sete ajustes atuais com um nome, para repetir o estilo nos próximos "
                "vídeos. Se o nome já existe, o preset é substituído.",
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
            c,
            text="O preset guarda estes sete ajustes com um nome, para repetir o estilo nos "
            "próximos vídeos. O que está na tela também é lembrado da próxima vez.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12)
        self._check_shorts = ctk.CTkCheckBox(
            c, text="Aplicar também aos Shorts", variable=self.nos_shorts, command=self._mudou
        )
        self._check_shorts.pack(anchor="w", padx=12, pady=(8, 4))
        dicas.dica(
            self._check_shorts,
            "Marcado: os Shorts também recebem o tratamento. Desmarcado: só o vídeo longo.",
        )
        self._filhos.append(self._check_shorts)
        ctk.CTkLabel(
            c,
            text="Vale para o vídeo longo e, se marcado, para os Shorts. A introdução e a vinheta "
            "não são tratadas. Mudar o tratamento refaz o que já foi renderizado.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12, pady=(0, 10))

    # --- valores ------------------------------------------------------------------------------
    def tratamento(self) -> TratamentoImagem:
        return TratamentoImagem(**{nome: round(var.get()) for nome, var in self.ajustes.items()})

    def valores(self) -> dict:
        t = self.tratamento()
        return {
            "tratamento_ativo": self.ativo.get(),
            "tratamento_nos_shorts": self.nos_shorts.get(),
            **{f"tratamento_{nome}": float(valor) for nome, valor in t.model_dump().items()},
        }

    def aplicar(self, v: ValoresFormulario) -> None:
        """Põe na tela o tratamento de um projeto aberto."""
        self.ativo.set(v.tratamento_ativo)
        self.nos_shorts.set(v.tratamento_nos_shorts)
        self._preencher(
            TratamentoImagem(**{n: getattr(v, f"tratamento_{n}") for n in AJUSTES_DO_TRATAMENTO})
        )

    def _preencher(self, t: TratamentoImagem) -> None:
        self._aplicando = True
        try:
            for nome, valor in t.model_dump().items():
                self.ajustes[nome].set(valor)
        finally:
            self._aplicando = False
        self._atualizar_rotulos()
        self._pedir_previa()

    def _atualizar_rotulos(self) -> None:
        for nome, rotulo in self._rotulos_valor.items():
            valor = round(self.ajustes[nome].get())
            rotulo.configure(text=f"{valor:+d}" if valor else "0")

    # --- mudanças -----------------------------------------------------------------------------
    def _moveu(self, _nome: str) -> None:
        self._atualizar_rotulos()
        if not self.tratamento().neutro:
            self.ativo.set(True)  # mexer num ajuste liga o tratamento
        self.preset.set(_SEM_PRESET)
        self._pedir_previa()
        self._ao_mudar()

    def _mudou(self) -> None:
        self._ao_mudar()

    def zerar(self) -> None:
        self._preencher(TratamentoImagem())
        self.preset.set(_SEM_PRESET)
        self._ao_mudar()

    # --- quadro e prévia ----------------------------------------------------------------------
    def _video_mudou(self) -> None:
        self._quadro = self._original = self._tratado = None
        self._indice_do_quadro = 0
        self._desenhar()
        self._carregar_quadro()

    def outro_quadro(self) -> None:
        self._indice_do_quadro += 1
        self._carregar_quadro()

    def _carregar_quadro(self) -> None:
        if not self._fonte.pronta:
            self._aviso.configure(text="Escolha um vídeo para ver o antes e o depois.")
            return
        self._aviso.configure(text="Carregando um quadro do vídeo…", text_color=_COR_DICA)
        self._fonte.quadro(
            self._fonte.instante_de_exemplo(self._indice_do_quadro),
            self._quadro_pronto,
            self._quadro_falhou,
            chave="tratamento_quadro",
        )

    def _quadro_pronto(self, quadro: QuadroDeExemplo) -> None:
        self._quadro = quadro
        self._original = ajustar_ao_tamanho(quadro.imagem, (_LARGURA_DA_PREVIA, _LARGURA_DA_PREVIA))
        self._tratado = None
        self._aviso.configure(text="")
        self._tela.configure(height=self._original.height)
        self._desenhar()
        self._pedir_previa()

    def _quadro_falhou(self, erro: Exception) -> None:
        self._aviso.configure(
            text=f"Não foi possível ler um quadro do vídeo: {erro}", text_color=_COR_ERRO
        )

    def _pedir_previa(self) -> None:
        if self._aplicando or self._quadro is None:
            return
        if self._adiado is not None:
            self.after_cancel(self._adiado)
        self._adiado = self.after(_ATRASO_DA_PREVIA_MS, self._gerar_previa)

    def _gerar_previa(self) -> None:
        self._adiado = None
        quadro = self._quadro
        if quadro is None:
            return
        t = self.tratamento()
        if t.neutro:
            self._tratado = self._original
            self._desenhar()
            return
        self._sequencia += 1
        destino = self._fonte.caminho_temporario(f"tratado_{self._sequencia}.png")
        self._tarefas.rodar(
            lambda: previa.quadro_para_png(quadro.arquivo, destino, tratamento=t),
            lambda png: self._previa_pronta(png, quadro),
            self._quadro_falhou,
            chave="tratamento_previa",
        )

    def _previa_pronta(self, png, quadro: QuadroDeExemplo) -> None:
        if quadro is not self._quadro:
            return  # outro quadro foi escolhido enquanto esta prévia saía
        with Image.open(png) as aberta:
            tratado = aberta.convert("RGB")
        Path(png).unlink(missing_ok=True)
        self._tratado = ajustar_ao_tamanho(tratado, (_LARGURA_DA_PREVIA, _LARGURA_DA_PREVIA))
        self._desenhar()

    def _arrastar(self, evento) -> None:
        if self._original is None:
            return
        self._divisao = min(max(evento.x / max(self._original.width, 1), 0.0), 1.0)
        self._desenhar()

    def _desenhar(self) -> None:
        if self._original is None:
            self._tela.itemconfigure(self._imagem_na_tela, image="")
            self._foto = None
            return
        tratado = self._tratado or self._original
        quadro = compor_antes_depois(self._original, tratado, self._divisao)
        nova = ImageTk.PhotoImage(quadro)
        self._tela.itemconfigure(self._imagem_na_tela, image=nova)  # a nova antes de soltar a velha
        self._foto = nova

    # --- sugestão automática ------------------------------------------------------------------
    def sugerir(self) -> None:
        if not self._fonte.pronta:
            dialogos.aviso(
                self.winfo_toplevel(),
                "Sugestão automática",
                "Escolha um vídeo primeiro: a sugestão é medida em alguns quadros dele.",
            )
            return
        self._aviso.configure(text="Medindo a imagem do vídeo…", text_color=_COR_DICA)
        self._fonte.quadros_para_sugestao(self._sugestao_pronta, self._quadro_falhou)

    def _sugestao_pronta(self, quadros: list[Image.Image]) -> None:
        sugerido = sugerir_tratamento(quadros)
        self._aviso.configure(
            text="Sugestão aplicada: é um ponto de partida, ajuste a seu gosto."
            if not sugerido.neutro
            else "A imagem já está equilibrada: a sugestão não mudou nada.",
            text_color=_COR_DICA,
        )
        self._preencher(sugerido)
        if not sugerido.neutro:
            self.ativo.set(True)
        self.preset.set(_SEM_PRESET)
        self._ao_mudar()

    # --- presets ------------------------------------------------------------------------------
    def _atualizar_presets(self) -> None:
        nomes = sorted(self._presets.tratamentos())
        self._menu_preset.configure(values=[_SEM_PRESET, *nomes])

    def _escolheu_preset(self, nome: str) -> None:
        if nome == _SEM_PRESET:
            return
        escolhido = self._presets.tratamentos().get(nome)
        if escolhido is None:
            return
        self._preencher(escolhido)
        self.ativo.set(not escolhido.neutro)
        self._ao_mudar()

    def salvar_preset(self) -> None:
        nome = dialogos.pedir_texto(
            self.winfo_toplevel(),
            "Salvar preset de tratamento",
            "Dê um nome a este estilo (se o nome já existir, o preset é substituído).",
            inicial=self.preset.get() if self.preset.get() != _SEM_PRESET else "",
        )
        if not nome:
            return
        self._presets.salvar_tratamento(nome, self.tratamento())
        self._atualizar_presets()
        self.preset.set(nome)

    def excluir_preset(self) -> None:
        nome = self.preset.get()
        if nome == _SEM_PRESET or nome not in self._presets.tratamentos():
            return
        if dialogos.confirmar(
            self.winfo_toplevel(),
            "Excluir preset",
            f"Excluir o preset “{nome}”?",
            sim="Excluir",
            nao="Manter",
        ):
            self._presets.excluir_tratamento(nome)
            self.preset.set(_SEM_PRESET)
            self._atualizar_presets()

    # --- interface com o painel de opções -----------------------------------------------------
    def aplicar_habilitacoes(self, h, *, livre: bool) -> None:
        self._livre = livre
        pode = livre and h.tratamento
        self._check_ativo.configure(state="normal" if pode else "disabled")
        for widget in self._filhos:
            widget.configure(state="normal" if pode else "disabled")
        self._check_shorts.configure(
            state="normal" if pode and h.tratamento_nos_shorts else "disabled"
        )
