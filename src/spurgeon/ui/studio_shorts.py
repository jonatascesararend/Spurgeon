"""Editor dos cortes dos Shorts: a lista (nota, título, trecho da legenda) e o detalhe de cada um.

É a peça que serve às duas telas: a janela de aprovação dos cortes (que pausa o processamento) e a
aba "Studio Shorts" da janela principal (que vale a qualquer hora depois do cálculo dos cortes).

Cada corte sempre começa e termina numa palavra inteira, com no máximo 60 s. A pessoa pode:
marcar ou desmarcar, corrigir título, descrição e hashtags, deslocar o início e o fim (digitando o
tempo, ou acrescentando e tirando palavras ou frases, com as palavras vizinhas à vista) e corrigir
a legenda que vai queimada no vídeo, com a ajuda do verificador de português.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from tkinter import BooleanVar, StringVar, TclError

import customtkinter as ctk

from spurgeon.core.config import Pastas
from spurgeon.core.projeto import PastasProjeto
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_NOVO,
    SITUACAO_PRONTO,
    ler_registro,
    situacao_do_short,
)
from spurgeon.shorts.analise import separar_frases
from spurgeon.shorts.dados import ROTULOS_CATEGORIA, Corte, Frase
from spurgeon.shorts.edicao import (
    DURACAO_MAXIMA_S,
    LimiteInvalido,
    contexto_das_pontas,
    definir_limite,
    faixa_da_nota,
    formatar_tempo_preciso,
    legenda_corrigida,
    legenda_do_corte,
    ler_hashtags,
    ler_tempo,
    mover_limite,
    mover_por_frase,
    resumir,
)
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.shorts.pontuacao import PARTES, ROTULO_ALERTA
from spurgeon.shorts.selecao import nome_de_arquivo
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo
from spurgeon.transcricao.verificador import verificador_compartilhado
from spurgeon.ui import dialogos
from spurgeon.ui.verificacao import PainelDePortugues

_COR_NOTA = {"alta": "#2e8b57", "media": "#b8860b", "baixa": "#a84444"}
_COR_ALERTA = "#e0a030"
_COR_SUAVE = "gray60"
_COR_OK = "#4caf72"
_COR_LINHA = ("gray86", "gray20")
_COR_LINHA_ATUAL = ("#cfe3f7", "#27415c")
_COR_FORA = "#7f7f7f"
_TRECHO_NA_LISTA = 120  # caracteres da legenda mostrados em cada linha da lista
_QUEBRA_NA_LISTA = 215  # largura útil do texto de cada linha da lista, em pixels

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


def trecho_da_legenda(palavras: list[Palavra], corte: Corte, limite: int = _TRECHO_NA_LISTA) -> str:
    """O começo da legenda do corte (a corrigida, se houver), para a lista."""
    texto = legenda_do_corte(palavras, corte) if palavras else ""
    texto = " ".join((texto or corte.texto).split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


class EditorDeCortes(ctk.CTkFrame):
    def __init__(
        self,
        master,
        pastas: PastasProjeto,
        *,
        pastas_do_app: Pastas | None = None,
        introducao: str | None = None,
        legenda_queimada: Callable[[], bool] | None = None,
        ao_mudar: Callable[[], None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._pastas = pastas
        self._pastas_do_app = pastas_do_app
        self._introducao = introducao
        self._legenda_queimada = legenda_queimada or (lambda: False)
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
        )

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

        self._lista = ctk.CTkScrollableFrame(corpo, label_text="")
        self._lista.grid(row=2, column=0, sticky="nsew", padx=(14, 7), pady=(0, 8))
        self._lista.columnconfigure(0, weight=1)

        self._detalhe = ctk.CTkScrollableFrame(corpo, label_text="")
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
        self._var_inicio = StringVar()
        self._var_fim = StringVar()
        self._campo_inicio = self._montar_ponta(d, 12, "Começa em", "inicio", self._var_inicio)
        self._campo_fim = self._montar_ponta(d, 14, "Termina em", "fim", self._var_fim)
        self._rotulo_ajuste = ctk.CTkLabel(
            d, anchor="w", text="", text_color=_COR_ALERTA, wraplength=420, justify="left"
        )
        self._rotulo_ajuste.grid(row=16, column=0, sticky="ew", padx=8)
        ctk.CTkLabel(
            d, text="Onde o corte começa e termina (cinza = fica de fora)", anchor="w"
        ).grid(row=17, column=0, sticky="ew", padx=8, pady=(8, 0))
        self._contexto = ctk.CTkTextbox(d, height=118, wrap="word")
        self._contexto.grid(row=18, column=0, sticky="ew", padx=8, pady=(0, 2))
        caixa = self._contexto._textbox
        caixa.tag_config("rotulo", foreground=_COR_SUAVE)
        caixa.tag_config("fora", foreground=_COR_FORA)
        caixa.tag_config("dentro", foreground="#f2f2f2", font=("Segoe UI", 11, "bold"))
        self._rotulo_avisos = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ALERTA, text=""
        )
        self._rotulo_avisos.grid(row=19, column=0, sticky="ew", padx=8, pady=(0, 4))

        cabeca_legenda = ctk.CTkFrame(d, fg_color="transparent")
        cabeca_legenda.grid(row=20, column=0, sticky="ew", padx=8, pady=(8, 0))
        ctk.CTkLabel(cabeca_legenda, text="Legenda do Short", anchor="w").pack(side="left")
        self._rotulo_editada = ctk.CTkLabel(cabeca_legenda, text="", text_color=_COR_ALERTA)
        self._rotulo_editada.pack(side="left", padx=8)
        self._texto = ctk.CTkTextbox(d, height=130, wrap="word", undo=True)
        self._texto.grid(row=21, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._texto.bind("<KeyRelease>", lambda _e: self._ao_editar_legenda())
        self._rotulo_queimada = ctk.CTkLabel(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_SUAVE, text=""
        )
        self._rotulo_queimada.grid(row=22, column=0, sticky="ew", padx=8)
        botoes = ctk.CTkFrame(d, fg_color="transparent")
        botoes.grid(row=23, column=0, sticky="ew", padx=8, pady=(4, 8))
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

    def _ajustar_quebras_do_detalhe(self, evento) -> None:
        largura = max(240, evento.width - 70)
        for rotulo in (
            self._rotulo_alerta,
            self._rotulo_motivo,
            self._rotulo_ajuste,
            self._rotulo_avisos,
            self._rotulo_queimada,
        ):
            rotulo.configure(wraplength=largura)

    def _alternar_nota(self) -> None:
        if self._quadro_nota.winfo_manager():
            self._quadro_nota.grid_remove()
            self._botao_nota.configure(text="▸ Como a nota foi calculada")
        else:
            self._quadro_nota.grid()
            self._botao_nota.configure(text="▾ Como a nota foi calculada")

    def _montar_ponta(self, pai, linha: int, titulo: str, ponta: str, variavel: StringVar):
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
        botoes = ctk.CTkFrame(pai, fg_color="transparent")
        botoes.grid(row=linha + 1, column=0, sticky="ew", padx=8, pady=(2, 0))
        if ponta == "inicio":
            acoes = [
                ("◀ +palavra", lambda: self.mover("inicio", -1)),
                ("−palavra ▶", lambda: self.mover("inicio", 1)),
                ("◀ +frase", lambda: self.mover_frase("inicio", -1)),
                ("−frase ▶", lambda: self.mover_frase("inicio", 1)),
            ]
        else:
            acoes = [
                ("◀ −palavra", lambda: self.mover("fim", -1)),
                ("+palavra ▶", lambda: self.mover("fim", 1)),
                ("◀ −frase", lambda: self.mover_frase("fim", -1)),
                ("+frase ▶", lambda: self.mover_frase("fim", 1)),
            ]
        for texto, comando in acoes:
            ctk.CTkButton(
                botoes,
                text=texto,
                width=82,
                height=24,
                fg_color=("gray70", "gray30"),
                command=comando,
            ).pack(side="left", padx=(0, 4))
        return campo

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
        info = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text_color=_COR_SUAVE,
            text=(
                f"{etiqueta} · {formatar_tempo(corte.inicio)}–{formatar_tempo(corte.fim)}"
                f" · {descrever_duracao(corte.duracao)}"
                f"{sufixo}"
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
        self._resumo.configure(text=texto)

    def _pintar_atual(self) -> None:
        for i, linha in self._linhas.items():
            with contextlib.suppress(TclError):
                linha.configure(fg_color=_COR_LINHA_ATUAL if i == self._atual else _COR_LINHA)

    # --- detalhe ------------------------------------------------------------------------------
    def selecionar(self, i: int) -> None:
        self._guardar_detalhe()
        self._atual = i
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
            f"{' · reserva: só entra se você marcar' if corte.reserva else ''}"
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

    def _mostrar_legenda(self, corte: Corte) -> None:
        self._texto.delete("1.0", "end")
        self._texto.insert("1.0", self._legenda_para_editar(corte))
        self._texto._textbox.edit_reset()
        self._rotulo_editada.configure(text="✎ legenda editada" if corte.legenda else "")
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

    def _legenda_para_editar(self, corte: Corte) -> str:
        return legenda_do_corte(self._palavras, corte) if self._palavras else corte.texto

    def _mostrar_limites(self, corte: Corte) -> None:
        self._rotulo_tempo.configure(
            text=(
                f"{formatar_tempo(corte.inicio)} a {formatar_tempo(corte.fim)} · "
                f"{corte.duracao:.1f} s (máximo {DURACAO_MAXIMA_S:g})"
            )
        )
        self._var_inicio.set(formatar_tempo_preciso(corte.inicio))
        self._var_fim.set(formatar_tempo_preciso(corte.fim))
        caixa = self._contexto._textbox
        self._contexto.configure(state="normal")
        caixa.delete("1.0", "end")
        avisos: tuple[str, ...] = ()
        try:
            contexto = contexto_das_pontas(self._palavras, corte, self._frases_do_sermao())
        except LimiteInvalido:
            caixa.insert("end", "(transcrição indisponível)", "fora")
        else:
            caixa.insert("end", "Começo  ", "rotulo")
            caixa.insert("end", f"…{contexto.antes} ", "fora")
            caixa.insert("end", f"▶ {contexto.comeco} …\n", "dentro")
            caixa.insert("end", "Fim  ", "rotulo")
            caixa.insert("end", f"… {contexto.fim} ◀ ", "dentro")
            caixa.insert("end", f"{contexto.depois}…", "fora")
            avisos = contexto.avisos
        self._contexto.configure(state="disabled")
        self._rotulo_avisos.configure(text="\n".join(f"⚠ {a}" for a in avisos))

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

    def desfazer_legenda(self) -> None:
        """Volta a legenda do corte atual para o texto da transcrição."""
        if self._atual is None:
            return
        corte = self._cortes[self._atual]
        corte.legenda = ""
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
            )
        if self._portugues_visivel:
            self._portugues.grid_remove()
            self._portugues_visivel = False
            return
        self._portugues.grid(row=24, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._portugues_visivel = True
        self._portugues.verificar()

    def _ao_corrigir_portugues(self) -> None:
        self._ao_editar_legenda()

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

    def salvar(self) -> bool:
        """Grava `cortes_shorts.json`. Devolve False se a pessoa desistiu ou há o que corrigir."""
        self._guardar_detalhe()
        janela = self.winfo_toplevel()
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
