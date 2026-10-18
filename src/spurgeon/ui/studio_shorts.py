"""Editor dos cortes dos Shorts: a lista (nota, título, trecho da legenda) e o detalhe de cada um.

É a peça que serve às duas telas: a janela de aprovação dos cortes (que pausa o processamento) e a
aba "Studio Shorts" da janela principal (que vale a qualquer hora depois do cálculo dos cortes).

Cada corte sempre começa e termina numa palavra inteira. Passar de 180 s é permitido, mas avisa
(acima disso o corte não pode ser Short). A pessoa pode:
marcar ou desmarcar, corrigir título, descrição e hashtags, deslocar o início e o fim (digitando o
tempo, ou acrescentando e tirando palavras ou frases, com as palavras vizinhas à vista) e corrigir
a legenda que vai queimada no vídeo, com a ajuda do verificador de português.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, StringVar, TclError

import customtkinter as ctk

from spurgeon.core.config import OpcoesProcessamento, Pastas
from spurgeon.core.erros import Cancelado
from spurgeon.core.ffmpeg import InfoMidia
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render import previa
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_NOVO,
    SITUACAO_PRONTO,
    ler_registro,
    situacao_do_short,
)
from spurgeon.shorts.analise import palavras_da_janela, separar_frases
from spurgeon.shorts.dados import (
    ORIGEM_AUTOMATICA,
    ORIGEM_MANUAL,
    ROTULOS_CATEGORIA,
    ROTULOS_ORIGEM,
    Corte,
    Frase,
)
from spurgeon.shorts.edicao import (
    AVISO_ACIMA_DO_LIMITE,
    DURACAO_MAXIMA_S,
    LimiteInvalido,
    MudancaDeLimite,
    comparar_cortes,
    contexto_da_legenda,
    contexto_das_pontas,
    definir_limite,
    faixa_da_nota,
    formatar_tempo_preciso,
    indices_das_palavras,
    janela_do_corte,
    legenda_corrigida,
    legenda_do_corte,
    legenda_em_blocos,
    ler_hashtags,
    ler_tempo,
    mover_limite,
    mover_por_frase,
    palavras_do_corte,
    passa_do_limite,
    resumir,
)
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.shorts.pontuacao import PARTES, ROTULO_ALERTA
from spurgeon.shorts.selecao import nome_de_arquivo
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo
from spurgeon.transcricao.verificador import verificador_compartilhado
from spurgeon.ui import dialogos
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.miniplayer import MiniPlayer
from spurgeon.ui.rolagem import QuadroRolavel
from spurgeon.ui.verificacao import PainelDePortugues

_COR_NOTA = {"alta": "#2e8b57", "media": "#b8860b", "baixa": "#a84444"}
_COR_ALERTA = "#e0a030"
_COR_ERRO = "#e05555"
_COR_SUAVE = "gray60"
_COR_OK = "#4caf72"
_COR_MANUAL = "#6ab0f3"
_COR_LINHA = ("gray86", "gray20")
_COR_LINHA_ATUAL = ("#cfe3f7", "#27415c")
_COR_FORA = "#7f7f7f"
_TRECHO_NA_LISTA = 120  # caracteres da legenda mostrados em cada linha da lista
_QUEBRA_NA_LISTA = 215  # largura útil do texto de cada linha da lista, em pixels
_ALTURA_DA_JANELA = 300  # a caixa com o corte e o que há ao redor dele
_PALAVRAS_FORA = 220  # quanto da transcrição, além da ponta, a janela deixa rolar (≈ 80 s)
_FIM_DE_FRASE = (".", "?", "!", "…")

ORDEM_NOTA = "Maior nota"
ORDEM_HORARIO = "Horário"
_ROTULO_SITUACAO = {
    SITUACAO_PRONTO: "✔ renderizado",
    SITUACAO_ALTERADO: "⟳ alterado: renderizar de novo",
    SITUACAO_NOVO: "",
}


def formatar_nota(nota: float) -> str:
    return f"{nota:.0f}"


def formatar_tempo(segundos: float) -> str:
    return segundos_para_rotulo(segundos)


def descrever_duracao(segundos: float) -> str:
    return f"{segundos:.0f} s"


def formatar_total(segundos: float) -> str:
    minutos, seg = divmod(round(segundos), 60)
    return f"{minutos} min {seg:02d} s" if minutos else f"{seg} s"


def resumir_palavras(palavras, limite: int = 150) -> str:
    """As palavras juntas; se passam do limite, o começo e o fim com reticências no meio."""
    texto = " ".join(palavras)
    if len(texto) <= limite:
        return texto
    cabeca = limite * 2 // 3
    return f"{texto[:cabeca].rstrip()} … {texto[-(limite - cabeca - 3) :].lstrip()}"


def rotulo_da_origem(corte: Corte) -> str:
    """`gerado automaticamente` ou `gerado manualmente` (o que a análise ou a pessoa criou)."""
    return ROTULOS_ORIGEM.get(corte.origem, ROTULOS_ORIGEM[ORIGEM_AUTOMATICA])


def trecho_da_legenda(palavras: list[Palavra], corte: Corte, limite: int = _TRECHO_NA_LISTA) -> str:
    """O começo da legenda do corte (a corrigida, se houver), para a lista."""
    texto = legenda_do_corte(palavras, corte) if palavras else ""
    texto = " ".join((texto or corte.texto).split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


@dataclass(frozen=True)
class OrigemDaPrevia:
    """O que a prévia de um Short precisa saber do que está na tela: o vídeo e as opções."""

    video: Path
    info: InfoMidia
    opcoes: OpcoesProcessamento


class EditorDeCortes(ctk.CTkFrame):
    def __init__(
        self,
        master,
        pastas: PastasProjeto,
        *,
        pastas_do_app: Pastas | None = None,
        introducao: str | None = None,
        legenda_queimada: Callable[[], bool] | None = None,
        janela_do_video: Callable[[], tuple[float, float | None]] | None = None,
        aparencia: Callable[[], str | None] | None = None,
        ao_mudar: Callable[[], None] | None = None,
        tarefas: Tarefas | None = None,
        origem_da_previa: Callable[[], OrigemDaPrevia | None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._pastas = pastas
        self._tarefas = tarefas
        self._origem_da_previa = origem_da_previa
        self._pastas_do_app = pastas_do_app
        self._introducao = introducao
        self._legenda_queimada = legenda_queimada or (lambda: False)
        self._janela_do_video = janela_do_video
        self._aparencia = aparencia or (lambda: None)
        self._mudanca: MudancaDeLimite | None = None  # o último ajuste, para mostrar na legenda
        self._ao_mudar = ao_mudar
        self._cortes: list[Corte] = []
        self._palavras: list[Palavra] = []
        self._frases: list[Frase] | None = None
        self._marcas: list[BooleanVar] = []
        self._registro: dict[str, dict] = {}
        self._ordem: list[int] = []
        self._atual: int | None = None
        self._linhas: dict[int, ctk.CTkFrame] = {}
        self._mudou = False
        self._carregando = False  # True enquanto a tela é preenchida (não conta como edição)
        self._portugues: PainelDePortugues | None = None
        self._portugues_visivel = False
        self._bloqueado: str | None = None
        self._botoes_das_pontas: dict[str, list[ctk.CTkButton]] = {}
        self._pasta_das_previas = Path(tempfile.gettempdir()) / "spurgeon_previas" / "shorts"
        self._previas: dict[str, tuple[str, previa.PreviaDoShort]] = {}  # por id do corte
        self._gerando: str | None = None  # id do corte cuja prévia está sendo gerada
        self._cancelar_previa = threading.Event()
        self._progresso_previa = 0.0
        self._montar()
        self.carregar()

    # --- carga --------------------------------------------------------------------------------
    @property
    def mudou(self) -> bool:
        return self._mudou

    def carregar(self) -> None:
        """Lê `cortes_shorts.json`, a transcrição e o registro dos Shorts já renderizados."""
        self._cortes = (
            carregar_cortes(self._pastas.cortes_shorts)
            if self._pastas.cortes_shorts.exists()
            else []
        )
        self._palavras = (
            Transcricao.carregar(self._pastas.transcricao).palavras
            if self._pastas.transcricao.exists()
            else []
        )
        if self._janela_do_video is not None:  # só a fala do trecho do vídeo que o projeto usa
            inicio, fim = self._janela_do_video()
            self._palavras = palavras_da_janela(self._palavras, inicio, fim)
        self._frases = None
        self._marcas = [BooleanVar(value=c.incluir) for c in self._cortes]
        self._atual = None
        self._mudou = False
        self.recarregar_registro(reordenar=False)
        self._atualizar_texto_introducao()
        self._reordenar()
        if self._cortes:
            self.selecionar(self._ordem[0])
        else:
            self._limpar_detalhe()

    def recarregar_registro(self, *, reordenar: bool = True) -> None:
        """Relê o que já foi renderizado (depois de uma renderização)."""
        self._registro = ler_registro(self._pastas.shorts_renderizados)
        self._existem = {
            id_: (self._pastas.shorts / e["arquivo"]).exists() for id_, e in self._registro.items()
        }
        if reordenar and self._cortes:
            self._reordenar_mantendo()

    def _frases_do_sermao(self) -> list[Frase]:
        if self._frases is None:
            self._frases = separar_frases(self._palavras) if self._palavras else []
        return self._frases

    def situacao(self, corte: Corte) -> str:
        id_ = str(corte.id)
        return situacao_do_short(
            corte,
            self._registro.get(id_),
            self._existem.get(id_, False),
            com_legenda=self._legenda_queimada(),
            visual=self._aparencia(),
        )

    @property
    def cortes(self) -> list[Corte]:
        """Os cortes da lista, já com o que está digitado no detalhe."""
        self._guardar_detalhe()
        return list(self._cortes)

    def adicionar_corte(self, corte: Corte) -> None:
        """Põe na lista um corte novo (o do Shorts livre), marcado, e o abre no detalhe."""
        self._guardar_detalhe()
        self._cortes.append(corte)
        self._marcas.append(BooleanVar(value=corte.incluir))
        self._houve_mudanca()
        self._reordenar()
        self.selecionar(len(self._cortes) - 1)
        self.after_idle(self._rolar_ate_o_atual, self._ordem.index(self._atual))

    def pendentes(self) -> list[Corte]:
        """Os cortes marcados que ainda não têm Short renderizado (ou mudaram depois)."""
        self._guardar_detalhe()
        return [c for c in self._cortes if c.incluir and self.situacao(c) != SITUACAO_PRONTO]

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.grid(row=0, column=0, sticky="nsew")
        self._corpo = corpo
        corpo.columnconfigure(0, weight=5, minsize=330)
        corpo.columnconfigure(1, weight=6, minsize=400)
        corpo.rowconfigure(2, weight=1)
        self._rotulo_intro = ctk.CTkLabel(
            corpo, anchor="w", justify="left", wraplength=1080, text=""
        )
        self._rotulo_intro.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        corpo.bind(
            "<Configure>",
            lambda e: self._rotulo_intro.configure(wraplength=max(300, e.width - 40)),
        )  # o texto acompanha a largura da área

        barra = ctk.CTkFrame(corpo, fg_color="transparent")
        barra.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))
        ctk.CTkButton(
            barra, text="Marcar todos", width=110, command=lambda: self.marcar_todos(True)
        ).pack(side="left")
        ctk.CTkButton(
            barra, text="Desmarcar todos", width=120, command=lambda: self.marcar_todos(False)
        ).pack(side="left", padx=6)
        ctk.CTkLabel(barra, text="Ordenar por:", text_color=_COR_SUAVE).pack(
            side="left", padx=(18, 6)
        )
        self._seletor_ordem = ctk.CTkSegmentedButton(
            barra, values=[ORDEM_NOTA, ORDEM_HORARIO], command=lambda _v: self._reordenar()
        )
        self._seletor_ordem.set(ORDEM_NOTA)
        self._seletor_ordem.pack(side="left")
        self._resumo = ctk.CTkLabel(corpo, text="", anchor="w", text_color=_COR_SUAVE)
        self._resumo.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 4))

        self._lista = QuadroRolavel(corpo, label_text="")
        self._lista.grid(row=2, column=0, sticky="nsew", padx=(14, 7), pady=(0, 8))
        self._lista.columnconfigure(0, weight=1)

        self._detalhe = QuadroRolavel(corpo, label_text="")
        self._detalhe.grid(row=2, column=1, sticky="nsew", padx=(7, 14), pady=(0, 8))
        self._detalhe.columnconfigure(0, weight=1)
        self._montar_detalhe()
        self._detalhe.bind("<Configure>", self._ajustar_quebras_do_detalhe, add="+")

        self._aviso_bloqueio = ctk.CTkLabel(
            self, text="", wraplength=700, justify="center", text_color=_COR_ALERTA
        )

    def _atualizar_texto_introducao(self) -> None:
        principais = sum(1 for c in self._cortes if not c.reserva)
        reservas = len(self._cortes) - principais
        marcados = f"{principais} cortes sugeridos já vêm marcados"
        if reservas:
            marcados += f", mais {reservas} reservas desmarcadas"
        self._rotulo_intro.configure(
            text=self._introducao
            or (
                f"{marcados}. A nota (0 a 100) soma o que a IA achou com o que se mede no texto: "
                "veja o detalhe ao lado. Desmarque o que não quiser e, se precisar, ajuste o "
                "início e o fim de um corte e corrija a legenda."
            )
        )

    def _montar_detalhe(self) -> None:
        d = self._detalhe
        self._rotulo_nota = ctk.CTkLabel(d, anchor="w", font=ctk.CTkFont(size=16, weight="bold"))
        self._rotulo_nota.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        linha_categoria = ctk.CTkFrame(d, fg_color="transparent")
        linha_categoria.grid(row=1, column=0, sticky="ew", padx=8)
        linha_categoria.columnconfigure(0, weight=1)
        self._rotulo_categoria = ctk.CTkLabel(linha_categoria, anchor="w", text_color=_COR_SUAVE)
        self._rotulo_categoria.grid(row=0, column=0, sticky="ew")
        self._botao_nota = ctk.CTkButton(
            linha_categoria,
            text="▸ Como a nota foi calculada",
            width=170,
            height=22,
            fg_color="transparent",
            text_color=_COR_SUAVE,
            hover_color=("gray80", "gray25"),
            command=self._alternar_nota,
        )
        self._botao_nota.grid(row=0, column=1, sticky="e")

        self._partes: dict[str, tuple[ctk.CTkProgressBar, ctk.CTkLabel]] = {}
        quadro = ctk.CTkFrame(d, fg_color="transparent")
        quadro.grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 2))
        quadro.columnconfigure(1, weight=1)
        for linha, (chave, (rotulo, _maximo)) in enumerate(PARTES.items()):
            ctk.CTkLabel(quadro, text=rotulo, anchor="w", width=190).grid(
                row=linha, column=0, sticky="w", pady=1
            )
            barra = ctk.CTkProgressBar(quadro, height=9)
            barra.grid(row=linha, column=1, sticky="ew", padx=8)
            valor = ctk.CTkLabel(quadro, text="", width=70, anchor="e")
            valor.grid(row=linha, column=2, sticky="e")
            self._partes[chave] = (barra, valor)
        quadro.grid_remove()  # a decomposição da nota só aparece quando pedida
        self._quadro_nota = quadro
        self._rotulo_alerta = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ALERTA
        )
        self._rotulo_alerta.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 0))
        self._rotulo_motivo = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_SUAVE
        )
        self._rotulo_motivo.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 8))

        self._var_titulo = StringVar()
        self._var_hashtags = StringVar()
        ctk.CTkLabel(d, text="Título", anchor="w").grid(row=5, column=0, sticky="ew", padx=8)
        self._campo_titulo = ctk.CTkEntry(d, textvariable=self._var_titulo)
        self._campo_titulo.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 6))
        ctk.CTkLabel(d, text="Descrição", anchor="w").grid(row=7, column=0, sticky="ew", padx=8)
        self._campo_descricao = ctk.CTkTextbox(d, height=64, wrap="word")
        self._campo_descricao.grid(row=8, column=0, sticky="ew", padx=8, pady=(0, 6))
        ctk.CTkLabel(d, text="Hashtags", anchor="w").grid(row=9, column=0, sticky="ew", padx=8)
        self._campo_hashtags = ctk.CTkEntry(d, textvariable=self._var_hashtags)
        self._campo_hashtags.grid(row=10, column=0, sticky="ew", padx=8, pady=(0, 8))
        for variavel in (self._var_titulo, self._var_hashtags):
            variavel.trace_add("write", lambda *_a: self._marcar_mudanca())
        self._campo_descricao.bind("<KeyRelease>", lambda _e: self._marcar_mudanca())

        self._rotulo_tempo = ctk.CTkLabel(d, anchor="w", font=ctk.CTkFont(weight="bold"))
        self._rotulo_tempo.grid(row=11, column=0, sticky="ew", padx=8)
        self._rotulo_limite = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ERRO, text=""
        )
        self._rotulo_limite.grid(row=12, column=0, sticky="ew", padx=8)
        self._var_inicio = StringVar()
        self._var_fim = StringVar()
        self._campo_inicio = self._montar_campo_do_tempo(
            d, 13, "Começa em", "inicio", self._var_inicio
        )
        self._montar_botoes_da_ponta(d, 14, "inicio")
        self._montar_janela_do_corte(d, 15)
        self._montar_botoes_da_ponta(d, 17, "fim")
        self._campo_fim = self._montar_campo_do_tempo(d, 18, "Termina em", "fim", self._var_fim)
        self._rotulo_ajuste = ctk.CTkLabel(
            d, anchor="w", text="", text_color=_COR_ALERTA, wraplength=420, justify="left"
        )
        self._rotulo_ajuste.grid(row=21, column=0, sticky="ew", padx=8, pady=(6, 0))
        self._rotulo_avisos = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ALERTA, text=""
        )
        self._rotulo_avisos.grid(row=22, column=0, sticky="ew", padx=8, pady=(0, 4))

        cabeca_legenda = ctk.CTkFrame(d, fg_color="transparent")
        cabeca_legenda.grid(row=23, column=0, sticky="ew", padx=8, pady=(8, 0))
        ctk.CTkLabel(cabeca_legenda, text="Legenda do Short", anchor="w").pack(side="left")
        self._rotulo_editada = ctk.CTkLabel(cabeca_legenda, text="", text_color=_COR_ALERTA)
        self._rotulo_editada.pack(side="left", padx=8)
        self._dica_legenda = ctk.CTkLabel(
            d,
            text="Cada bloco de até 2 linhas é o que aparece na tela de uma vez, como no vídeo. "
            "As quebras se refazem sozinhas ao sair do campo.",
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._dica_legenda.grid(row=24, column=0, sticky="ew", padx=8)
        moldura = ctk.CTkFrame(d, fg_color="transparent")
        moldura.grid(row=25, column=0, sticky="ew", padx=8, pady=(2, 4))
        moldura.columnconfigure(0, weight=1)
        self._rotulo_mudanca = ctk.CTkLabel(
            moldura, anchor="w", justify="left", wraplength=420, text=""
        )
        self._rotulo_mudanca.grid(row=0, column=0, sticky="ew")
        self._contexto_antes = ctk.CTkLabel(
            moldura, anchor="w", justify="left", wraplength=420, text="", text_color=_COR_FORA,
            font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._contexto_antes.grid(row=1, column=0, sticky="ew")
        self._texto = ctk.CTkTextbox(
            moldura, height=190, wrap="word", undo=True,
            font=ctk.CTkFont(size=15, weight="bold"),
        )  # fmt: skip
        self._texto.grid(row=2, column=0, sticky="ew", pady=(2, 2))
        self._texto._textbox.tag_config("novo", background="#1f5f3a", foreground="#ffffff")
        self._contexto_depois = ctk.CTkLabel(
            moldura, anchor="w", justify="left", wraplength=420, text="", text_color=_COR_FORA,
            font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._contexto_depois.grid(row=3, column=0, sticky="ew")
        self._texto.bind("<KeyRelease>", lambda _e: self._ao_editar_legenda())
        self._texto.bind("<FocusOut>", lambda _e: self._reorganizar_legenda())
        self._rotulo_queimada = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_SUAVE, text=""
        )
        self._rotulo_queimada.grid(row=26, column=0, sticky="ew", padx=8)
        botoes = ctk.CTkFrame(d, fg_color="transparent")
        botoes.grid(row=27, column=0, sticky="ew", padx=8, pady=(4, 8))
        ctk.CTkButton(
            botoes, text="Verificar português", width=140, command=self._alternar_portugues
        ).pack(side="left")
        ctk.CTkButton(
            botoes,
            text="Desfazer correções",
            width=130,
            fg_color=("gray70", "gray30"),
            command=self.desfazer_legenda,
        ).pack(side="left", padx=6)
        self._montar_previa(d, 29)

    def _montar_previa(self, pai, linha: int) -> None:
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=0, sticky="ew", padx=8, pady=(6, 12))
        quadro.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            quadro, text="Prévia do Short", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="ew")
        self._dica_previa = ctk.CTkLabel(
            quadro,
            text="Uma miniatura de qualidade bem baixa, só para ver como o Short vai ficar: "
            "recorte, zoom, cor e legenda (a legenda aparece sempre na prévia; no vídeo final "
            "ela só é queimada com “Legendas dinâmicas” ligado). O som é o do vídeo original "
            "(sem limpeza nem volume ajustado) e a marca d'água não aparece.",
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._dica_previa.grid(row=1, column=0, sticky="ew")
        linha_botoes = ctk.CTkFrame(quadro, fg_color="transparent")
        linha_botoes.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._botao_previa = ctk.CTkButton(
            linha_botoes, text="Gerar prévia", width=130, command=self.gerar_previa
        )
        self._botao_previa.pack(side="left")
        self._botao_cancelar_previa = ctk.CTkButton(
            linha_botoes,
            text="Cancelar",
            width=90,
            fg_color=("gray70", "gray30"),
            command=self._clicou_em_cancelar,
        )
        self._barra_previa = ctk.CTkProgressBar(quadro, height=8)
        self._barra_previa.set(0)
        self._situacao_previa = ctk.CTkLabel(
            quadro, anchor="w", justify="left", wraplength=420, text="", text_color=_COR_SUAVE
        )
        self._situacao_previa.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        self._player = MiniPlayer(quadro)
        self._player.grid(row=5, column=0, pady=(6, 0))
        self._player.grid_remove()

    def _ajustar_quebras_do_detalhe(self, evento) -> None:
        largura = max(240, evento.width - 70)
        for rotulo in (
            self._rotulo_alerta,
            self._rotulo_motivo,
            self._rotulo_ajuste,
            self._rotulo_avisos,
            self._rotulo_queimada,
            self._rotulo_limite,
            self._dica_legenda,
            self._dica_previa,
            self._situacao_previa,
            self._dica_janela,
            self._rotulo_mudanca,
            self._contexto_antes,
            self._contexto_depois,
        ):
            with contextlib.suppress(TclError):  # o editor pode ter sido refeito (projeto reaberto)
                rotulo.configure(wraplength=largura)

    def _alternar_nota(self) -> None:
        if self._quadro_nota.winfo_manager():
            self._quadro_nota.grid_remove()
            self._botao_nota.configure(text="▸ Como a nota foi calculada")
        else:
            self._quadro_nota.grid()
            self._botao_nota.configure(text="▾ Como a nota foi calculada")

    def _montar_campo_do_tempo(self, pai, linha: int, titulo: str, ponta: str, variavel: StringVar):
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=0, sticky="ew", padx=8, pady=(4, 0))
        ctk.CTkLabel(quadro, text=titulo, anchor="w", width=80).pack(side="left")
        campo = ctk.CTkEntry(quadro, textvariable=variavel, width=88, justify="center")
        campo.pack(side="left")
        campo.bind("<Return>", lambda _e, p=ponta: self.aplicar_tempo(p))
        campo.bind("<FocusOut>", lambda _e, p=ponta: self.aplicar_tempo(p))
        ctk.CTkLabel(quadro, text="min:s,décimos", text_color=_COR_SUAVE).pack(
            side="left", padx=(6, 0)
        )
        return campo

    def _montar_botoes_da_ponta(self, pai, linha: int, ponta: str) -> None:
        """Os quatro botões de uma ponta, na mesma ordem no começo e no fim.

        A seta mostra para onde a ponta anda: no começo, tirar palavras leva o começo para a direita
        e pôr palavras o leva para a esquerda; no fim é o contrário. A ordem é a mesma nos dois.
        """
        botoes = ctk.CTkFrame(pai, fg_color="transparent")
        botoes.grid(row=linha, column=0, sticky="ew", padx=8, pady=(2, 2))
        sinal = 1 if ponta == "inicio" else -1  # o que "tirar" faz com o número da palavra
        if ponta == "inicio":
            rotulos = ("−palavra ▶", "−frase ▶", "◀ +frase", "◀ +palavra")
        else:
            rotulos = ("◀ −palavra", "◀ −frase", "+frase ▶", "+palavra ▶")
        comandos = (
            lambda: self.mover(ponta, sinal),
            lambda: self.mover_frase(ponta, sinal),
            lambda: self.mover_frase(ponta, -sinal),
            lambda: self.mover(ponta, -sinal),
        )
        self._botoes_das_pontas[ponta] = []
        for texto, comando in zip(rotulos, comandos, strict=True):
            botao = ctk.CTkButton(
                botoes,
                text=texto,
                width=82,
                height=24,
                fg_color=("gray70", "gray30"),
                command=comando,
            )
            botao.pack(side="left", padx=(0, 4))
            self._botoes_das_pontas[ponta].append(botao)

    def _montar_janela_do_corte(self, pai, linha: int) -> None:
        """Uma caixa só: o que vem antes (cinza), o corte inteiro, e o que vem depois (cinza)."""
        cabeca = ctk.CTkFrame(pai, fg_color="transparent")
        cabeca.grid(row=linha, column=0, sticky="ew", padx=8, pady=(2, 0))
        cabeca.columnconfigure(0, weight=1)
        self._dica_janela = ctk.CTkLabel(
            cabeca,
            text="O cinza fica de fora do Short. Duplo clique numa palavra leva até ela o começo "
            "ou o fim, o que estiver mais perto.",
            anchor="w",
            justify="left",
            wraplength=300,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._dica_janela.grid(row=0, column=0, sticky="ew")
        for coluna, (texto, ponta) in enumerate((("↥ começo", "inicio"), ("↧ fim", "fim")), 1):
            ctk.CTkButton(
                cabeca,
                text=texto,
                width=64,
                height=22,
                fg_color="transparent",
                text_color=_COR_SUAVE,
                hover_color=("gray80", "gray25"),
                command=lambda p=ponta: self._ir_a_ponta(p),
            ).grid(row=0, column=coluna, padx=(4, 0))
        janela = ctk.CTkTextbox(pai, height=_ALTURA_DA_JANELA, wrap="word", state="disabled")
        janela.grid(row=linha + 1, column=0, sticky="ew", padx=8, pady=(0, 2))
        caixa = janela._textbox
        caixa.tag_config("fora", foreground=_COR_FORA)
        caixa.tag_config("dentro", foreground="#f2f2f2", font=("Segoe UI", 11, "bold"))
        caixa.tag_config("limite", foreground="#6ab0f3", font=("Segoe UI", 10, "bold"))
        caixa.bind("<Double-Button-1>", self._duplo_clique)
        self._janela = janela
        self._marcas_da_janela: dict[str, str] = {}

    # --- lista --------------------------------------------------------------------------------
    def _reordenar(self) -> None:
        self._guardar_detalhe()
        indices = range(len(self._cortes))
        if self._seletor_ordem.get() == ORDEM_HORARIO:
            self._ordem = sorted(indices, key=lambda i: self._cortes[i].inicio)
        else:
            self._ordem = sorted(
                indices, key=lambda i: (-self._cortes[i].nota, self._cortes[i].inicio)
            )
        for filho in self._lista.winfo_children():
            filho.destroy()
        self._linhas.clear()
        for posicao, i in enumerate(self._ordem):
            self._linhas[i] = self._criar_linha(posicao, i)
        self._atualizar_resumo()
        if self._atual is not None:
            self._pintar_atual()
            self.after_idle(self._rolar_ate_o_atual, self._ordem.index(self._atual))

    def _criar_linha(self, posicao: int, i: int) -> ctk.CTkFrame:
        corte = self._cortes[i]
        linha = ctk.CTkFrame(self._lista, fg_color=_COR_LINHA, corner_radius=6)
        linha.grid(row=posicao, column=0, sticky="ew", pady=2)
        linha.columnconfigure(2, weight=1)
        ctk.CTkCheckBox(
            linha, text="", width=24, variable=self._marcas[i], command=lambda: self._ao_marcar(i)
        ).grid(row=0, column=0, rowspan=3, padx=(8, 0), pady=6)
        nota = ctk.CTkLabel(
            linha,
            text=formatar_nota(corte.nota),
            width=38,
            height=26,
            corner_radius=6,
            text_color="white",
            fg_color=_COR_NOTA[faixa_da_nota(corte.nota)],
            font=ctk.CTkFont(weight="bold"),
        )
        nota.grid(row=0, column=1, rowspan=3, padx=8, pady=6)
        etiqueta = ROTULOS_CATEGORIA.get(corte.categoria, corte.categoria)
        sufixo = " · reserva" if corte.reserva else ""
        sufixo += " · editado" if corte.editado else ""
        sufixo += " · ✎ legenda" if corte.legenda else ""
        sufixo += " · ⚠ contexto" if corte.alerta_contexto else ""
        sufixo += f" · ⚠ mais de {DURACAO_MAXIMA_S:g} s" if passa_do_limite(corte) else ""
        info = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text_color=_COR_SUAVE,
            text=(
                f"{etiqueta} · {formatar_tempo(corte.inicio)}–{formatar_tempo(corte.fim)}"
                f" · {descrever_duracao(corte.duracao)}"
                f"{sufixo}\n{rotulo_da_origem(corte)}"
            ),
        )
        info.grid(row=0, column=2, sticky="ew")
        titulo = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text=corte.titulo or corte.texto[:60],
            font=ctk.CTkFont(weight="bold"),
        )
        titulo.grid(row=1, column=2, sticky="ew")
        situacao = _ROTULO_SITUACAO[self.situacao(corte)]
        trecho = trecho_da_legenda(self._palavras, corte)
        legenda = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text_color=_COR_SUAVE,
            text=f"“{trecho}”" + (f"\n{situacao}" if situacao else ""),
        )
        legenda.grid(row=2, column=2, sticky="ew", pady=(0, 6))
        for widget in (linha, nota, info, titulo, legenda):
            widget.bind("<Button-1>", lambda _e, i=i: self.selecionar(i))
        return linha

    def _ao_marcar(self, i: int) -> None:
        self._cortes[i].incluir = self._marcas[i].get()
        self._houve_mudanca()
        self._atualizar_resumo()

    def _andar(self, passo: int) -> None:
        """Seleciona o corte seguinte (ou anterior) da lista, na ordem em que ela aparece."""
        if self._atual is None or not self._ordem:
            return
        posicao = self._ordem.index(self._atual) + passo
        if 0 <= posicao < len(self._ordem):
            self.selecionar(self._ordem[posicao])
            self._rolar_ate_o_atual(posicao)

    def _rolar_ate_o_atual(self, posicao: int) -> None:
        with contextlib.suppress(TclError, AttributeError):
            canvas = self._lista._parent_canvas
            canvas.yview_moveto(max(0.0, (posicao - 2) / max(1, len(self._ordem))))

    def alternar_atual(self) -> None:
        """Marca ou desmarca o corte que está aberto."""
        if self._atual is not None:
            self._marcas[self._atual].set(not self._marcas[self._atual].get())
            self._ao_marcar(self._atual)

    def marcar_todos(self, valor: bool) -> None:
        for i, corte in enumerate(self._cortes):
            self._marcas[i].set(valor)
            corte.incluir = valor
        self._houve_mudanca()
        self._atualizar_resumo()

    def _atualizar_resumo(self) -> None:
        r = resumir(self._cortes)
        texto = f"{r.marcados} de {r.total} marcados · {formatar_total(r.duracao_s)} de vídeo"
        pendentes = sum(
            1 for c in self._cortes if c.incluir and self.situacao(c) != SITUACAO_PRONTO
        )
        if self._registro:
            texto += f" · {pendentes} por renderizar"
        longos = sum(1 for c in self._cortes if c.incluir and passa_do_limite(c))
        if longos:
            texto += f" · ⚠ {longos} com mais de {DURACAO_MAXIMA_S:g} s"
        self._resumo.configure(text=texto)

    def _pintar_atual(self) -> None:
        for i, linha in self._linhas.items():
            with contextlib.suppress(TclError):
                linha.configure(fg_color=_COR_LINHA_ATUAL if i == self._atual else _COR_LINHA)

    # --- detalhe ------------------------------------------------------------------------------
    def selecionar(self, i: int) -> None:
        self._guardar_detalhe()
        self._atual = i
        self._mudanca = None
        self._pintar_atual()
        self._mostrar_detalhe()

    def _limpar_detalhe(self) -> None:
        self._carregando = True
        try:
            self._rotulo_nota.configure(text="Nenhum corte calculado ainda.")
            for rotulo in (self._rotulo_categoria, self._rotulo_alerta, self._rotulo_motivo):
                rotulo.configure(text="")
        finally:
            self._carregando = False

    def _mostrar_detalhe(self) -> None:
        self._carregando = True
        try:
            self._preencher_detalhe(self._cortes[self._atual])
        finally:
            self._carregando = False

    def _preencher_detalhe(self, corte: Corte) -> None:
        faixa = faixa_da_nota(corte.nota)
        self._rotulo_nota.configure(
            text=f"Nota {formatar_nota(corte.nota)} de 100"
            + ("  (do corte original)" if corte.editado else ""),
            text_color=_COR_NOTA[faixa],
        )
        self._rotulo_categoria.configure(
            text=f"{ROTULOS_CATEGORIA.get(corte.categoria, corte.categoria)}"
            f" · {rotulo_da_origem(corte)}"
            f"{' · reserva: só entra se você marcar' if corte.reserva else ''}",
            text_color=_COR_MANUAL if corte.origem == ORIGEM_MANUAL else _COR_SUAVE,
        )
        for chave, (barra, valor) in self._partes.items():
            maximo = PARTES[chave][1]
            pontos = corte.detalhe.get(chave, 0.0)
            barra.set(max(0.0, min(1.0, pontos / maximo)))
            valor.configure(text=f"{pontos:g} de {maximo:g}")
        penalidade = corte.detalhe.get("alerta_de_contexto")
        self._rotulo_alerta.configure(
            text=(
                f"⚠ {ROTULO_ALERTA} ({penalidade:g} pontos): {corte.alerta_contexto}"
                if corte.alerta_contexto
                else ""
            )
        )
        self._rotulo_motivo.configure(text=f"IA: {corte.motivo}" if corte.motivo else "")
        self._var_titulo.set(corte.titulo)
        self._campo_descricao.delete("1.0", "end")
        self._campo_descricao.insert("1.0", corte.descricao)
        self._var_hashtags.set(" ".join(corte.hashtags))
        self._mostrar_legenda(corte)
        self._mostrar_limites(corte)
        self._rotulo_ajuste.configure(text="")
        self._mostrar_previa(corte)

    def _mostrar_legenda(self, corte: Corte) -> None:
        self._texto.delete("1.0", "end")
        self._texto.insert("1.0", self._legenda_para_editar(corte))
        self._texto._textbox.edit_reset()
        self._rotulo_editada.configure(text="✎ legenda editada" if corte.legenda else "")
        self._mostrar_mudanca_na_legenda(corte)
        queimada = self._legenda_queimada()
        self._rotulo_queimada.configure(
            text=(
                "A legenda é queimada no vídeo: o que você corrigir aqui aparece no Short."
                if queimada
                else "As “Legendas dinâmicas” estão desligadas nas opções: a correção fica "
                "guardada, mas só aparece no vídeo se você ligá-las."
            )
        )
        if self._portugues is not None:
            self._portugues.limpar()

    def _mostrar_mudanca_na_legenda(self, corte: Corte) -> None:
        """Mostra na legenda o que o último ajuste acrescentou ou tirou, e o que fica de fora.

        As palavras novas ganham fundo verde, a caixa rola até o ponto que mudou (o começo ou o
        fim, que numa legenda de 3 minutos ficaria fora de vista) e, acima e abaixo, o texto que
        vem antes e depois do corte corre junto, para ver o que ainda há para trás e para a frente.
        """
        antes, depois = contexto_da_legenda(self._palavras, corte)
        if self._palavras:
            self._contexto_antes.configure(
                text=f"◀ antes do começo (fica de fora): …{antes}"
                if antes
                else "◀ início do sermão"
            )
            self._contexto_depois.configure(
                text=f"depois do fim (fica de fora) ▶: {depois}…" if depois else "fim do sermão ▶"
            )
        else:
            self._contexto_antes.configure(text="")
            self._contexto_depois.configure(text="")
        caixa = self._texto._textbox
        caixa.tag_remove("novo", "1.0", "end")
        mudanca = self._mudanca
        if mudanca is None or not mudanca.houve:
            self._rotulo_mudanca.configure(text="")
            return
        linhas = []
        for rotulo, palavras in (
            ("➕ acrescentou no começo", mudanca.inicio_acrescentou),
            ("➖ tirou do começo", mudanca.inicio_tirou),
            ("➕ acrescentou no fim", mudanca.fim_acrescentou),
            ("➖ tirou do fim", mudanca.fim_tirou),
        ):
            if palavras:
                linhas.append(f"{rotulo}: “{resumir_palavras(palavras)}”")
        so_acrescentou = not (mudanca.inicio_tirou or mudanca.fim_tirou)
        self._rotulo_mudanca.configure(
            text="\n".join(linhas),
            text_color=_COR_OK if so_acrescentou else _COR_ALERTA,
        )
        texto = caixa.get("1.0", "end-1c")
        achadas = list(re.finditer(r"\S+", texto))
        novas = ((len(mudanca.inicio_acrescentou), False), (len(mudanca.fim_acrescentou), True))
        for quantas, do_fim in novas:
            alvo = achadas[-quantas:] if do_fim else achadas[:quantas]
            for achada in alvo if quantas else ():
                caixa.tag_add("novo", f"1.0+{achada.start()}c", f"1.0+{achada.end()}c")
        ponto = "end-1c" if mudanca.ponta == "fim" else "1.0"
        self.after_idle(lambda: self._rolar_legenda_ate(ponto))

    def _rolar_legenda_ate(self, ponto: str) -> None:
        with contextlib.suppress(TclError):
            self._texto._textbox.see(ponto)

    def _legenda_para_editar(self, corte: Corte) -> str:
        """A legenda dividida em blocos de até 2 linhas, como vai aparecer no vídeo."""
        if self._palavras and palavras_do_corte(self._palavras, corte):
            return legenda_em_blocos(self._palavras, corte)
        return corte.texto

    def _mostrar_limites(self, corte: Corte) -> None:
        acima = passa_do_limite(corte)
        self._rotulo_tempo.configure(
            text=(
                f"{formatar_tempo(corte.inicio)} a {formatar_tempo(corte.fim)} · "
                f"{corte.duracao:.1f} s"
            ),
            text_color=_COR_ERRO if acima else ("gray10", "gray90"),
        )
        self._rotulo_limite.configure(
            text=(
                f"⚠ {AVISO_ACIMA_DO_LIMITE} Encurte o corte (ou desmarque-o) antes de "
                f"renderizar; o limite é {DURACAO_MAXIMA_S:g} s."
                if acima
                else ""
            )
        )
        self._var_inicio.set(formatar_tempo_preciso(corte.inicio))
        self._var_fim.set(formatar_tempo_preciso(corte.fim))
        self._preencher_janela(corte)
        avisos: tuple[str, ...] = ()
        with contextlib.suppress(LimiteInvalido):
            avisos = contexto_das_pontas(self._palavras, corte, self._frases_do_sermao()).avisos
        self._rotulo_avisos.configure(text="\n".join(f"⚠ {a}" for a in avisos))

    def _preencher_janela(self, corte: Corte) -> None:
        """A transcrição ao redor do corte: o que fica fora em cinza, o corte em destaque."""
        caixa_ctk = self._janela
        caixa = caixa_ctk._textbox
        caixa_ctk.configure(state="normal")
        caixa.delete("1.0", "end")
        self._marcas_da_janela.clear()
        try:
            janela = janela_do_corte(self._palavras, corte, fora=_PALAVRAS_FORA)
        except LimiteInvalido:
            caixa.insert("end", "(transcrição indisponível)", "fora")
            caixa_ctk.configure(state="disabled")
            return

        def marcar(nome: str, texto: str) -> None:
            if caixa.index("end-1c") != "1.0" and caixa.get("end-2c", "end-1c") != "\n":
                caixa.insert("end", "\n")
            self._marcas_da_janela[nome] = caixa.index("end-1c")
            caixa.insert("end", texto, "limite")

        for item in janela.palavras:
            if item.indice == janela.primeira:
                marcar("inicio", "▶ COMEÇA AQUI ▶\n")
            caixa.insert(
                "end", item.texto, (f"pal_{item.indice}", "dentro" if item.dentro else "fora")
            )
            fecha_frase = item.texto.rstrip().endswith(_FIM_DE_FRASE)
            caixa.insert("end", "\n" if fecha_frase else " ")
            if item.indice == janela.ultima:
                marcar("fim", "◀ TERMINA AQUI ◀\n")
        caixa_ctk.configure(state="disabled")
        ponta = (self._mudanca.ponta if self._mudanca is not None else None) or "inicio"
        self.after_idle(self._centralizar, caixa, self._marcas_da_janela.get(ponta, "1.0"))

    def _ir_a_ponta(self, ponta: str) -> None:
        marca = self._marcas_da_janela.get(ponta)
        if marca is not None:
            self._centralizar(self._janela._textbox, marca)

    @staticmethod
    def _centralizar(caixa, marca: str) -> None:
        """Rola a janela para a marca ficar no meio, com contexto de um lado e do outro."""
        with contextlib.suppress(TclError):
            caixa.see(f"{marca} - 5 display lines")
            caixa.see(f"{marca} + 5 display lines")

    def _duplo_clique(self, evento) -> str:
        """Duplo clique numa palavra: o começo ou o fim (o que estiver mais perto) vai até ela."""
        if self._atual is None:
            return "break"
        caixa = self._janela._textbox
        posicao = caixa.index(f"@{evento.x},{evento.y}")
        for etiqueta in caixa.tag_names(posicao):
            if etiqueta.startswith("pal_"):
                indice = int(etiqueta[4:])
                primeira, ultima = indices_das_palavras(self._palavras, self._cortes[self._atual])
                ponta = "inicio" if indice - primeira <= ultima - indice else "fim"
                palavra = self._palavras[indice]
                segundos = palavra.inicio if ponta == "inicio" else palavra.fim
                self._ajustar(
                    lambda c, s=segundos, p=ponta: definir_limite(self._palavras, c, p, s)
                )
                break
        return "break"

    # --- ajustes do início e do fim -----------------------------------------------------------
    def mover(self, ponta: str, passo: int) -> bool:
        """Desloca o início ou o fim do corte atual `passo` palavras. False se não foi possível."""
        return self._ajustar(lambda c: mover_limite(self._palavras, c, ponta, passo))

    def mover_frase(self, ponta: str, sentido: int) -> bool:
        """Leva o início ou o fim à fronteira de frase anterior (−1) ou seguinte (+1)."""
        return self._ajustar(
            lambda c: mover_por_frase(self._palavras, c, ponta, sentido, self._frases_do_sermao())
        )

    def aplicar_tempo(self, ponta: str) -> bool:
        """Leva o início ou o fim ao limite de palavra mais perto do tempo digitado."""
        if self._atual is None or self._carregando:
            return False
        texto = (self._var_inicio if ponta == "inicio" else self._var_fim).get()
        corte = self._cortes[self._atual]
        atual = corte.inicio if ponta == "inicio" else corte.fim
        try:
            segundos = ler_tempo(texto)
        except ValueError as erro:
            self._rotulo_ajuste.configure(text=str(erro))
            self._mostrar_limites(corte)
            return False
        if abs(segundos - atual) < 0.05:
            self._mostrar_limites(corte)  # nada mudou: só arruma a formatação do campo
            return True
        return self._ajustar(lambda c: definir_limite(self._palavras, c, ponta, segundos))

    def _ajustar(self, operacao: Callable[[Corte], Corte]) -> bool:
        if self._atual is None:
            return False
        self._guardar_detalhe()
        antes = self._cortes[self._atual]
        try:
            novo = operacao(antes)
        except LimiteInvalido as erro:
            self._rotulo_ajuste.configure(text=str(erro))
            self._mostrar_limites(antes)
            return False
        self._cortes[self._atual] = novo
        self._mudanca = comparar_cortes(self._palavras, antes, novo)
        self._houve_mudanca()
        self._mostrar_detalhe()
        if antes.legenda:
            self._rotulo_ajuste.configure(
                text="As correções da legenda foram descartadas, porque o trecho mudou. "
                "Corrija a legenda de novo, se precisar."
            )
        self._reordenar_mantendo()
        return True

    def _reordenar_mantendo(self) -> None:
        atual = self._atual
        self._reordenar()
        self._atual = atual
        self._pintar_atual()

    # --- legenda ------------------------------------------------------------------------------
    def _ao_editar_legenda(self) -> None:
        if self._carregando or self._atual is None:
            return
        self._marcar_mudanca()
        corte = self._cortes[self._atual]
        editada = bool(self._legenda_digitada(corte))
        self._rotulo_editada.configure(text="✎ legenda editada" if editada else "")

    def _legenda_digitada(self, corte: Corte) -> str:
        """O que vai para `Corte.legenda`: vazio se o texto é o mesmo da transcrição."""
        texto = self._texto.get("1.0", "end-1c")
        if not self._palavras:
            return "" if " ".join(texto.split()) == " ".join(corte.texto.split()) else texto.strip()
        return legenda_corrigida(self._palavras, corte, texto)

    def _reorganizar_legenda(self) -> None:
        """Refaz as quebras de linha como o vídeo vai mostrar, sem mexer nas palavras.

        Só os espaços entre as palavras são trocados (por Enter ou por espaço), então as marcas do
        verificador de português continuam nas palavras certas.
        """
        if self._atual is None or self._carregando or not self._palavras:
            return
        corte = self._cortes[self._atual]
        if not palavras_do_corte(self._palavras, corte):
            return
        atual = self._texto.get("1.0", "end-1c")
        desejado = legenda_em_blocos(self._palavras, corte, atual)
        if desejado == atual or atual.split() != desejado.split():
            return
        palavras_atuais = list(re.finditer(r"\S+", atual))
        separadores = [m.group() for m in re.finditer(r"\s+", desejado)]
        caixa = self._texto._textbox
        for k in range(len(palavras_atuais) - 2, -1, -1):  # de trás para frente: as posições valem
            de, ate = palavras_atuais[k].end(), palavras_atuais[k + 1].start()
            if atual[de:ate] != separadores[k]:
                caixa.delete(f"1.0+{de}c", f"1.0+{ate}c")
                caixa.insert(f"1.0+{de}c", separadores[k])

    def desfazer_legenda(self) -> None:
        """Volta a legenda do corte atual para o texto da transcrição."""
        if self._atual is None:
            return
        corte = self._cortes[self._atual]
        corte.legenda = ""
        self._mudanca = None
        self._carregando = True
        try:
            self._mostrar_legenda(corte)
        finally:
            self._carregando = False
        self._houve_mudanca()

    def _alternar_portugues(self) -> None:
        if self._portugues is None:
            pastas_app = self._pastas_do_app or Pastas()
            self._portugues = PainelDePortugues(
                self._detalhe,
                self._texto._textbox,
                lambda: verificador_compartilhado(pastas_app),
                vocabulario=pastas_app.vocabulario,
                ao_corrigir=self._ao_corrigir_portugues,
                largura=400,
                compacto=True,
            )
        if self._portugues_visivel:
            self._portugues.grid_remove()
            self._portugues_visivel = False
            return
        self._portugues.grid(row=28, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._portugues_visivel = True
        self._portugues.verificar()

    def _ao_corrigir_portugues(self) -> None:
        self._ao_editar_legenda()
        self.after_idle(self._reorganizar_legenda)  # corrigir pode mudar onde a linha quebra

    # --- prévia -------------------------------------------------------------------------------
    def _assinatura_da_previa(self, corte: Corte, origem: OrigemDaPrevia) -> str:
        """O que decide se a prévia guardada ainda mostra este corte com esta aparência."""
        o = origem.opcoes
        dados = [
            str(origem.video),
            round(corte.inicio, 3),
            round(corte.fim, 3),
            corte.legenda,
            o.legenda_posicao_vertical,
            o.recorte_deslocamento_x_pct,
            o.enquadramento_nos_curtos.model_dump(),
            o.enquadramento_do_short.model_dump(),
            o.tratamento_nos_curtos.model_dump(),
        ]
        return hashlib.sha1(json.dumps(dados, sort_keys=True, default=str).encode()).hexdigest()[
            :12
        ]

    def _origem(self) -> OrigemDaPrevia | None:
        return self._origem_da_previa() if self._origem_da_previa is not None else None

    def _mostrar_previa(self, corte: Corte) -> None:
        """Põe no detalhe a prévia deste corte (se já foi gerada e ainda vale) ou o botão."""
        id_ = str(corte.id)
        if self._gerando is not None and self._gerando != id_:
            self._cancelar_a_previa()  # outro corte foi aberto: a prévia que corria não interessa
        gerando = self._gerando == id_
        guardada = self._previas.get(id_)
        origem = self._origem() if guardada and not gerando else None
        vale = bool(
            guardada and origem and self._assinatura_da_previa(corte, origem) == guardada[0]
        )
        if vale:
            if self._player.previa is not guardada[1]:
                self._player.carregar(guardada[1])
            self._player.grid()
        else:
            self._player.limpar()
            self._player.grid_remove()
        poder = self._tarefas is not None and self._origem_da_previa is not None and not gerando
        self._botao_previa.configure(
            state="normal" if poder else "disabled",
            text="Gerando…" if gerando else ("Gerar de novo" if guardada else "Gerar prévia"),
        )
        if gerando:
            return
        if self._tarefas is None or self._origem_da_previa is None:
            texto = "A prévia fica disponível na aba Studio Shorts."
        elif guardada and not vale:
            texto = (
                "⟳ A prévia é de uma versão anterior: o trecho, a legenda ou a aparência mudou. "
                "Gere de novo."
            )
        else:
            texto = ""
        self._situacao_previa.configure(
            text=texto, text_color=_COR_ALERTA if guardada else _COR_SUAVE
        )

    def gerar_previa(self) -> None:
        """Renderiza o corte aberto em miniatura (em segundo plano) e mostra no mini player."""
        if self._atual is None or self._gerando is not None or self._tarefas is None:
            return
        janela = self.winfo_toplevel()
        origem = self._origem()
        if origem is None:
            dialogos.aviso(
                janela,
                "Prévia do Short",
                "Escolha o vídeo do sermão (e confira se as opções estão completas) para ver a "
                "prévia: ela é feita a partir do vídeo original.",
            )
            return
        self._guardar_detalhe()
        corte = Corte.de_dict(self._cortes[self._atual].para_dict())  # a thread usa uma cópia
        id_ = str(corte.id)
        assinatura = self._assinatura_da_previa(corte, origem)
        palavras = list(self._palavras)
        o = origem.opcoes
        fontes = (self._pastas_do_app or Pastas()).fontes
        destino = self._pasta_das_previas / f"previa_{id_}.mp4"
        cancelar = threading.Event()
        self._cancelar_previa = cancelar
        self._gerando = id_
        self._progresso_previa = 0.0
        self._player.limpar()
        self._player.grid_remove()
        self._previas.pop(id_, None)
        self._botao_previa.configure(state="disabled", text="Gerando…")
        self._botao_cancelar_previa.pack(side="left", padx=(6, 0))
        self._barra_previa.set(0)
        self._barra_previa.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self._situacao_previa.configure(text="Gerando a prévia…", text_color=_COR_SUAVE)
        self.after(300, self._acompanhar_previa)

        def definir_progresso(fracao: float) -> None:
            self._progresso_previa = fracao

        def pronta(resultado: previa.PreviaDoShort) -> None:
            if cancelar.is_set() or not self.winfo_exists():  # cancelada: outro corte já mandou
                return
            self._previas[id_] = (assinatura, resultado)
            self._terminou_a_previa()
            self._atualizar_previa_atual()

        def falhou(erro: Exception) -> None:
            if cancelar.is_set() or isinstance(erro, Cancelado) or not self.winfo_exists():
                return  # a tela já foi arrumada por quem cancelou
            self._terminou_a_previa()
            self._atualizar_previa_atual()
            self._situacao_previa.configure(
                text="Não foi possível gerar a prévia.", text_color=_COR_ERRO
            )
            dialogos.erro(
                self.winfo_toplevel(), "Prévia do Short", f"Não foi possível gerar a prévia: {erro}"
            )

        self._tarefas.rodar(
            lambda: previa.previa_do_corte(
                origem.video, origem.info, corte, palavras, destino,
                deslocamento_pct=o.recorte_deslocamento_x_pct,
                posicao_legenda=o.legenda_posicao_vertical,
                com_legenda=True,
                enquadramento=o.enquadramento_nos_curtos,
                zoom_do_short=o.enquadramento_do_short,
                tratamento=o.tratamento_nos_curtos,
                pasta_fontes=fontes,
                ao_progresso=definir_progresso,
                cancelar=cancelar,
            ),
            pronta,
            falhou,
            chave=f"previa_do_short_{id_}",
        )  # fmt: skip

    def _acompanhar_previa(self) -> None:
        if self._gerando is None:
            return
        with contextlib.suppress(TclError):
            self._barra_previa.set(self._progresso_previa)
            self._situacao_previa.configure(
                text=f"Gerando a prévia… {self._progresso_previa * 100:.0f}%"
            )
            self.after(300, self._acompanhar_previa)

    def _atualizar_previa_atual(self) -> None:
        if self._atual is not None:
            self._mostrar_previa(self._cortes[self._atual])

    def _clicou_em_cancelar(self) -> None:
        self._cancelar_a_previa()
        self._atualizar_previa_atual()
        self._situacao_previa.configure(text="Prévia cancelada.", text_color=_COR_SUAVE)

    def _cancelar_a_previa(self) -> None:
        self._cancelar_previa.set()
        self._terminou_a_previa()

    def _terminou_a_previa(self) -> None:
        self._gerando = None
        with contextlib.suppress(TclError):
            self._botao_cancelar_previa.pack_forget()
            self._barra_previa.grid_remove()
            self._botao_previa.configure(state="normal")

    def destroy(self) -> None:
        self._cancelar_previa.set()
        super().destroy()

    # --- estado e gravação --------------------------------------------------------------------
    def _marcar_mudanca(self) -> None:
        if self._atual is not None and not self._carregando:
            self._houve_mudanca()

    def _houve_mudanca(self) -> None:
        if not self._carregando:
            self._mudou = True
        if self._ao_mudar is not None:
            self._ao_mudar()

    def _guardar_detalhe(self) -> None:
        """Copia título, descrição, hashtags e legenda da tela para o corte que estava aberto."""
        if self._atual is None or self._carregando or not self._cortes:
            return
        corte = self._cortes[self._atual]
        corte.titulo = self._var_titulo.get().strip()
        corte.descricao = self._campo_descricao.get("1.0", "end-1c").strip()
        corte.hashtags = ler_hashtags(self._var_hashtags.get())
        corte.legenda = self._legenda_digitada(corte)

    def cortes_acima_do_limite(self) -> list[Corte]:
        """Os cortes marcados que passam do limite e por isso não podem ser Shorts."""
        self._guardar_detalhe()
        return [c for c in self._cortes if c.incluir and passa_do_limite(c)]

    def salvar(self, *, exigir_ate_o_limite: bool = True) -> bool:
        """Grava `cortes_shorts.json`. Devolve False se a pessoa desistiu ou há o que corrigir.

        Com `exigir_ate_o_limite`, um corte marcado acima do limite de duração impede de salvar
        (quem salva para
        renderizar não pode deixá-lo passar); sem isso, vale como trabalho em andamento.
        """
        self._guardar_detalhe()
        janela = self.winfo_toplevel()
        longos = self.cortes_acima_do_limite() if exigir_ate_o_limite else []
        if longos:
            lista = "\n".join(f"• {c.titulo or c.texto[:40]} ({c.duracao:.0f} s)" for c in longos)
            dialogos.aviso(
                janela,
                f"Corte com mais de {DURACAO_MAXIMA_S:g} segundos",
                f"{AVISO_ACIMA_DO_LIMITE}\n\n{lista}\n\nEncurte o corte ou desmarque-o.",
            )
            return False
        r = resumir(self._cortes)
        if r.marcados == 0 and not dialogos.confirmar(
            janela,
            "Nenhum corte marcado",
            "Sem cortes marcados, nenhum Short será gerado. Salvar assim mesmo?",
            sim="Salvar",
            nao="Voltar",
        ):
            return False
        if r.sem_titulo:
            dialogos.aviso(
                janela,
                "Corte sem título",
                f"{r.sem_titulo} corte(s) marcado(s) estão sem título. Escreva um título ou "
                "desmarque o corte.",
            )
            return False
        usados: set[str] = set()
        for corte in self._cortes:
            corte.slug = nome_de_arquivo(corte.slug or corte.titulo, usados)
            usados.add(corte.slug)
        gravar_cortes(self._pastas.cortes_shorts, self._cortes)
        self._mudou = False
        self._atualizar_resumo()
        return True

    # --- bloqueio -----------------------------------------------------------------------------
    def bloquear(self, motivo: str | None) -> None:
        """Esconde o editor e mostra o motivo (processamento em andamento); `None` libera."""
        if motivo == self._bloqueado:
            return
        self._bloqueado = motivo
        if motivo:
            self._corpo.grid_remove()
            self._aviso_bloqueio.configure(text=motivo)
            self._aviso_bloqueio.grid(row=0, column=0, pady=60)
        else:
            self._aviso_bloqueio.grid_remove()
            self._corpo.grid()
