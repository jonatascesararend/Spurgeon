"""Janela de aprovação dos cortes dos Shorts: lista com a nota de cada um e o detalhe ao lado.

Todos os cortes escolhidos já vêm marcados; as reservas (nota baixa ou acima do limite) vêm
desmarcadas. A pessoa pode desmarcar, corrigir título, descrição e hashtags e deslocar o início
ou o fim de um corte de palavra em palavra (o corte sempre começa e termina numa palavra inteira,
com no máximo 60 s). O que sobra marcado é o que vira Short.
"""

from __future__ import annotations

import contextlib
from tkinter import BooleanVar, StringVar, TclError

import customtkinter as ctk

from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts.dados import ROTULOS_CATEGORIA, Corte
from spurgeon.shorts.edicao import (
    DURACAO_MAXIMA_S,
    LimiteInvalido,
    faixa_da_nota,
    ler_hashtags,
    mover_limite,
    palavras_das_pontas,
    resumir,
)
from spurgeon.shorts.etapas import carregar_cortes, gravar_cortes
from spurgeon.shorts.pontuacao import PARTES, ROTULO_ALERTA
from spurgeon.shorts.selecao import nome_de_arquivo
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo
from spurgeon.ui import dialogos

_COR_NOTA = {"alta": "#2e8b57", "media": "#b8860b", "baixa": "#a84444"}
_COR_ALERTA = "#e0a030"
_COR_SUAVE = "gray60"
_COR_LINHA = ("gray86", "gray20")
_COR_LINHA_ATUAL = ("#cfe3f7", "#27415c")

ORDEM_NOTA = "Maior nota"
ORDEM_HORARIO = "Horário"


def formatar_nota(nota: float) -> str:
    return f"{nota:.0f}"


def formatar_tempo(segundos: float) -> str:
    return segundos_para_rotulo(segundos)


def descrever_duracao(segundos: float) -> str:
    return f"{segundos:.0f} s"


def formatar_total(segundos: float) -> str:
    minutos, seg = divmod(round(segundos), 60)
    return f"{minutos} min {seg:02d} s" if minutos else f"{seg} s"


class PainelAprovacaoCortes(ctk.CTkToplevel):
    def __init__(self, janela: ctk.CTk, pastas: PastasProjeto, *, modal: bool = True):
        super().__init__(janela)
        self._pastas = pastas
        self._cortes: list[Corte] = carregar_cortes(pastas.cortes_shorts)
        self._palavras: list[Palavra] = (
            Transcricao.carregar(pastas.transcricao).palavras if pastas.transcricao.exists() else []
        )
        self._marcas: list[BooleanVar] = [BooleanVar(value=c.incluir) for c in self._cortes]
        self._ordem: list[int] = []
        self._atual: int | None = None
        self._linhas: dict[int, ctk.CTkFrame] = {}
        self._mudou = False
        self._carregando = False  # True enquanto a tela é preenchida (não conta como edição)
        self.salvo = False
        self.title("Aprovação dos cortes dos Shorts")
        self._dimensionar()
        self._montar()
        self._reordenar()
        if self._cortes:
            self.selecionar(self._ordem[0])
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Control-s>", lambda _e: self.salvar_e_fechar())
        if modal:
            self.after(150, self.focus)
            self.grab_set()

    # --- montagem -----------------------------------------------------------------------------
    def _dimensionar(self) -> None:
        escala = self._get_window_scaling()
        tela_l, tela_a = self.winfo_screenwidth() / escala, self.winfo_screenheight() / escala
        largura, altura = int(min(1180, tela_l * 0.95)), int(min(760, tela_a * 0.9))
        self.geometry(
            f"{largura}x{altura}+{int((tela_l - largura) / 2)}+{int((tela_a - altura) / 4)}"
        )
        self.minsize(900, 520)

    def _montar(self) -> None:
        self.columnconfigure(0, weight=5, minsize=430)
        self.columnconfigure(1, weight=6, minsize=440)
        self.rowconfigure(2, weight=1)
        principais = sum(1 for c in self._cortes if not c.reserva)
        reservas = len(self._cortes) - principais
        marcados = f"{principais} cortes sugeridos já vêm marcados"
        if reservas:
            marcados += f", mais {reservas} reservas desmarcadas"
        ctk.CTkLabel(
            self,
            anchor="w",
            justify="left",
            wraplength=1080,
            text=(
                f"{marcados}. A nota (0 a 100) soma o que a IA achou com o que se mede no texto: "
                "veja o detalhe ao lado. Desmarque o que não quiser e, se precisar, ajuste o "
                "início e o fim de um corte."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))

        barra = ctk.CTkFrame(self, fg_color="transparent")
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
        self._resumo = ctk.CTkLabel(barra, text="", anchor="e")
        self._resumo.pack(side="right")

        self._lista = ctk.CTkScrollableFrame(self, label_text="")
        self._lista.grid(row=2, column=0, sticky="nsew", padx=(14, 7), pady=(0, 8))
        self._lista.columnconfigure(0, weight=1)

        self._detalhe = ctk.CTkScrollableFrame(self, label_text="")
        self._detalhe.grid(row=2, column=1, sticky="nsew", padx=(7, 14), pady=(0, 8))
        self._detalhe.columnconfigure(0, weight=1)
        self._montar_detalhe()

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))
        ctk.CTkButton(rodape, text="Salvar e fechar", command=self.salvar_e_fechar).pack(
            side="right"
        )
        ctk.CTkButton(
            rodape, text="Cancelar", width=90, fg_color=("gray70", "gray30"), command=self._cancelar
        ).pack(side="right", padx=8)
        ctk.CTkLabel(
            rodape,
            text="Depois de salvar, clique em “Aprovar e continuar” na janela principal.",
            text_color=_COR_SUAVE,
        ).pack(side="left")

    def _montar_detalhe(self) -> None:
        d = self._detalhe
        self._rotulo_nota = ctk.CTkLabel(d, anchor="w", font=ctk.CTkFont(size=16, weight="bold"))
        self._rotulo_nota.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self._rotulo_categoria = ctk.CTkLabel(d, anchor="w", text_color=_COR_SUAVE)
        self._rotulo_categoria.grid(row=1, column=0, sticky="ew", padx=8)

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
        self._inicio_texto = self._montar_ponta(d, 12, "Começa em", "inicio")
        self._fim_texto = self._montar_ponta(d, 14, "Termina em", "fim")
        self._rotulo_ajuste = ctk.CTkLabel(
            d, anchor="w", text="", text_color=_COR_ALERTA, wraplength=420, justify="left"
        )
        self._rotulo_ajuste.grid(row=16, column=0, sticky="ew", padx=8)
        ctk.CTkLabel(d, text="Texto do corte", anchor="w").grid(
            row=17, column=0, sticky="ew", padx=8, pady=(8, 0)
        )
        self._texto = ctk.CTkTextbox(d, height=130, wrap="word")
        self._texto.grid(row=18, column=0, sticky="ew", padx=8, pady=(0, 8))

    def _montar_ponta(self, pai, linha: int, titulo: str, ponta: str) -> ctk.CTkLabel:
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=0, sticky="ew", padx=8, pady=(4, 0))
        ctk.CTkLabel(quadro, text=titulo, anchor="w", width=80).pack(side="left")
        for texto, passo in (("◀ −1", -1), ("+1 ▶", 1)):
            ctk.CTkButton(
                quadro,
                text=texto,
                width=56,
                height=24,
                fg_color=("gray70", "gray30"),
                command=lambda p=ponta, s=passo: self.mover(p, s),
            ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(quadro, text="palavra", text_color=_COR_SUAVE).pack(side="left", padx=(2, 0))
        rotulo = ctk.CTkLabel(
            pai, anchor="w", justify="left", wraplength=420, text_color=_COR_SUAVE
        )
        rotulo.grid(row=linha + 1, column=0, sticky="ew", padx=8)
        return rotulo

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

    def _criar_linha(self, posicao: int, i: int) -> ctk.CTkFrame:
        corte = self._cortes[i]
        linha = ctk.CTkFrame(self._lista, fg_color=_COR_LINHA, corner_radius=6)
        linha.grid(row=posicao, column=0, sticky="ew", pady=2)
        linha.columnconfigure(2, weight=1)
        ctk.CTkCheckBox(
            linha, text="", width=24, variable=self._marcas[i], command=lambda: self._ao_marcar(i)
        ).grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=6)
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
        nota.grid(row=0, column=1, rowspan=2, padx=8, pady=6)
        etiqueta = ROTULOS_CATEGORIA.get(corte.categoria, corte.categoria)
        sufixo = " · reserva" if corte.reserva else ""
        sufixo += " · editado" if corte.editado else ""
        sufixo += " · ⚠ contexto" if corte.alerta_contexto else ""
        info = ctk.CTkLabel(
            linha,
            anchor="w",
            text_color=_COR_SUAVE,
            text=(
                f"{etiqueta} · {formatar_tempo(corte.inicio)} · {descrever_duracao(corte.duracao)}"
                f"{sufixo}"
            ),
        )
        info.grid(row=0, column=2, sticky="ew")
        titulo = ctk.CTkLabel(
            linha, anchor="w", justify="left", wraplength=300, text=corte.titulo or corte.texto[:60]
        )
        titulo.grid(row=1, column=2, sticky="ew", pady=(0, 6))
        for widget in (linha, nota, info, titulo):
            widget.bind("<Button-1>", lambda _e, i=i: self.selecionar(i))
        return linha

    def _ao_marcar(self, i: int) -> None:
        self._cortes[i].incluir = self._marcas[i].get()
        self._mudou = True
        self._atualizar_resumo()

    def marcar_todos(self, valor: bool) -> None:
        for i, corte in enumerate(self._cortes):
            self._marcas[i].set(valor)
            corte.incluir = valor
        self._mudou = True
        self._atualizar_resumo()

    def _atualizar_resumo(self) -> None:
        r = resumir(self._cortes)
        self._resumo.configure(
            text=f"{r.marcados} de {r.total} marcados · {formatar_total(r.duracao_s)} de vídeo"
        )

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
        self._texto.configure(state="normal")
        self._texto.delete("1.0", "end")
        self._texto.insert("1.0", corte.texto)
        self._texto.configure(state="disabled")
        self._mostrar_limites(corte)
        self._rotulo_ajuste.configure(text="")

    def _mostrar_limites(self, corte: Corte) -> None:
        self._rotulo_tempo.configure(
            text=(
                f"{formatar_tempo(corte.inicio)} a {formatar_tempo(corte.fim)} · "
                f"{corte.duracao:.1f} s (máximo {DURACAO_MAXIMA_S:g})"
            )
        )
        try:
            comeco, fim = palavras_das_pontas(self._palavras, corte)
        except LimiteInvalido:
            comeco = fim = "(transcrição indisponível)"
        self._inicio_texto.configure(text=f"“{comeco} …”")
        self._fim_texto.configure(text=f"“… {fim}”")

    def mover(self, ponta: str, passo: int) -> bool:
        """Desloca o início ou o fim do corte atual `passo` palavras. False se não foi possível."""
        if self._atual is None:
            return False
        self._guardar_detalhe()
        try:
            novo = mover_limite(self._palavras, self._cortes[self._atual], ponta, passo)
        except LimiteInvalido as erro:
            self._rotulo_ajuste.configure(text=str(erro))
            return False
        self._cortes[self._atual] = novo
        self._mudou = True
        self._mostrar_detalhe()
        self._reordenar_mantendo()
        return True

    def _reordenar_mantendo(self) -> None:
        atual = self._atual
        self._reordenar()
        self._atual = atual
        self._pintar_atual()

    def _marcar_mudanca(self) -> None:
        if self._atual is not None and not self._carregando:
            self._mudou = True

    def _guardar_detalhe(self) -> None:
        """Copia título, descrição e hashtags da tela para o corte que estava aberto."""
        if self._atual is None:
            return
        corte = self._cortes[self._atual]
        corte.titulo = self._var_titulo.get().strip()
        corte.descricao = self._campo_descricao.get("1.0", "end-1c").strip()
        corte.hashtags = ler_hashtags(self._var_hashtags.get())

    # --- salvar -------------------------------------------------------------------------------
    def salvar_e_fechar(self) -> bool:
        """Grava `cortes_shorts.json`. Devolve False se a pessoa desistiu de salvar."""
        self._guardar_detalhe()
        r = resumir(self._cortes)
        if r.marcados == 0 and not dialogos.confirmar(
            self,
            "Nenhum corte marcado",
            "Sem cortes marcados, nenhum Short será gerado. Salvar assim mesmo?",
            sim="Salvar",
            nao="Voltar",
        ):
            return False
        if r.sem_titulo:
            dialogos.aviso(
                self,
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
        self.salvo = True
        self._fechar()
        return True

    def _cancelar(self) -> None:
        self._guardar_detalhe()
        if self._mudou and not dialogos.confirmar(
            self,
            "Descartar as alterações?",
            "Há alterações nos cortes que ainda não foram salvas.",
            sim="Descartar",
            nao="Continuar editando",
        ):
            return
        self._fechar()

    def _fechar(self) -> None:
        with contextlib.suppress(TclError):
            self.grab_release()
        self.destroy()


def abrir_painel_cortes(janela: ctk.CTk, pastas: PastasProjeto) -> None:
    if not pastas.cortes_shorts.exists():
        dialogos.aviso(janela, "Aprovação dos cortes", "Os cortes ainda não foram escolhidos.")
        return
    painel = PainelAprovacaoCortes(janela, pastas)
    janela.wait_window(painel)
