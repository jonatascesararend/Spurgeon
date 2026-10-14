"""Painel "Português": mostra os avisos do verificador sobre um campo de texto e corrige no clique.

Funciona em cima de qualquer `tkinter.Text` (a revisão da transcrição e a legenda de um Short).
Cada aviso ganha um sublinhado colorido no texto e uma linha na lista; ao escolher um aviso, o
painel mostra as sugestões e os botões de ação. Os trechos são acompanhados por *tags* do próprio
Tk, então continuam certos mesmo que a pessoa edite o texto entre uma correção e outra.
"""

from __future__ import annotations

import contextlib
import queue
import re
import threading
import tkinter as tk
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import customtkinter as ctk

from spurgeon.transcricao.verificador import Problema, Verificador
from spurgeon.transcricao.vocabulario import acrescentar_ao_vocabulario

ROTULOS = {
    "ortografia": "Ortografia",
    "repeticao": "Repetição",
    "maiuscula": "Maiúscula",
    "pontuacao": "Pontuação",
}
CORES = {
    "ortografia": "#e05555",
    "repeticao": "#e0a030",
    "maiuscula": "#5aa0e0",
    "pontuacao": "#b070e0",
}
#: nestes tipos a primeira sugestão quase sempre serve, então há o botão "corrigir todos"
TIPOS_EM_LOTE = ("maiuscula", "pontuacao", "repeticao")
_COR_FUNDO_LISTA = "#2b2b2b"
_COR_TEXTO_LISTA = "#dce4ee"
_COR_SELECAO = "#1f538d"
_COR_ATUAL = "#5a3a3a"
_SUAVE = "gray60"

Estado = str  # "pendente" | "corrigido" | "ignorado" | "desatualizado"


def corpos_das_linhas(
    conteudo: str, prefixo: re.Pattern[str] | None = None
) -> tuple[list[str], list[int]]:
    """O texto de cada linha sem o prefixo (o `[2:05] ` da transcrição) e o tamanho do prefixo."""
    corpos, cortes = [], []
    for linha in conteudo.split("\n"):
        achado = prefixo.match(linha) if prefixo else None
        corte = achado.end() if achado else 0
        corpos.append(linha[corte:])
        cortes.append(corte)
    return corpos, cortes


def indice_do_tk(linha: int, coluna: int) -> str:
    """Linha começando em 0 e coluna em caracteres → índice do Tk (`"3.12"`)."""
    return f"{linha + 1}.{coluna}"


@dataclass
class _Item:
    problema: Problema
    sugestoes: list[str]
    etiqueta: str
    estado: Estado = "pendente"


class PainelDePortugues(ctk.CTkFrame):
    def __init__(
        self,
        master,
        caixa: tk.Text,
        obter_verificador: Callable[[], Verificador],
        *,
        vocabulario: Path | None = None,
        prefixo: re.Pattern[str] | None = None,
        ao_corrigir: Callable[[], None] | None = None,
        largura: int = 310,
    ):
        super().__init__(master, width=largura)
        self._caixa = caixa
        self._obter_verificador = obter_verificador
        self._vocabulario = vocabulario
        self._prefixo = prefixo
        self._ao_corrigir = ao_corrigir
        self._itens: list[_Item] = []
        self._visiveis: list[int] = []  # item de cada linha da lista
        self._atual: int | None = None
        self._ocupado = False
        self._geracao = 0  # cada verificação nova invalida o que a anterior ainda traria
        self._montar(largura)
        self._configurar_tags()

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self, largura: int) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        cabeca = ctk.CTkFrame(self, fg_color="transparent")
        cabeca.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        ctk.CTkLabel(cabeca, text="Português", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self._botao = ctk.CTkButton(cabeca, text="Verificar", width=84, command=self.verificar)
        self._botao.pack(side="right")
        self._situacao = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", wraplength=largura - 24, text_color=_SUAVE
        )
        self._situacao.grid(row=1, column=0, sticky="ew", padx=10)

        moldura = ctk.CTkFrame(self, fg_color="transparent")
        moldura.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 4))
        moldura.columnconfigure(0, weight=1)
        moldura.rowconfigure(0, weight=1)
        self._lista = tk.Listbox(
            moldura,
            activestyle="none",
            exportselection=False,
            bg=_COR_FUNDO_LISTA,
            fg=_COR_TEXTO_LISTA,
            selectbackground=_COR_SELECAO,
            highlightthickness=0,
            borderwidth=0,
            font=("Segoe UI", 10),
            height=8,
        )
        self._lista.grid(row=0, column=0, sticky="nsew")
        rolagem = ctk.CTkScrollbar(moldura, command=self._lista.yview)
        rolagem.grid(row=0, column=1, sticky="ns")
        self._lista.configure(yscrollcommand=rolagem.set)
        self._lista.bind("<<ListboxSelect>>", self._ao_escolher)

        self._detalhe = ctk.CTkFrame(self, fg_color="transparent")
        self._detalhe.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._detalhe.columnconfigure(0, weight=1)
        self._mensagem = ctk.CTkLabel(
            self._detalhe, text="", anchor="w", justify="left", wraplength=largura - 24
        )
        self._mensagem.grid(row=0, column=0, sticky="ew")
        self._quadro_sugestoes = ctk.CTkFrame(self._detalhe, fg_color="transparent")
        self._quadro_sugestoes.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._quadro_acoes = ctk.CTkFrame(self._detalhe, fg_color="transparent")
        self._quadro_acoes.grid(row=2, column=0, sticky="ew", pady=(4, 0))

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        ctk.CTkButton(rodape, text="◀", width=36, command=lambda: self.proximo(-1)).pack(
            side="left"
        )
        ctk.CTkButton(rodape, text="▶", width=36, command=lambda: self.proximo(1)).pack(
            side="left", padx=4
        )
        ctk.CTkLabel(rodape, text="F8 vai ao próximo", text_color=_SUAVE).pack(side="left", padx=6)

    def _configurar_tags(self) -> None:
        for tipo, cor in CORES.items():
            etiqueta = f"pt_{tipo}"
            try:
                self._caixa.tag_config(etiqueta, underline=True, underlinefg=cor)
            except tk.TclError:  # Tk antigo, sem cor própria no sublinhado
                self._caixa.tag_config(etiqueta, underline=True)
        self._caixa.tag_config("pt_atual", background=_COR_ATUAL)
        self._caixa.tag_raise("pt_atual")

    # --- verificação --------------------------------------------------------------------------
    def verificar(self, *, sincrono: bool = False) -> None:
        """Lê o texto e procura problemas (em uma thread, porque o dicionário demora a carregar)."""
        if self._ocupado:
            return
        corpos, cortes = corpos_das_linhas(self._caixa.get("1.0", "end-1c"), self._prefixo)
        self._geracao += 1
        geracao = self._geracao
        self._ocupado = True
        self._botao.configure(state="disabled")
        self._situacao.configure(
            text="Verificando… na primeira vez o dicionário leva alguns segundos."
        )

        def trabalho() -> list[Problema]:
            verificador = self._obter_verificador()
            verificador.preparar()
            return verificador.verificar(corpos)

        def concluir(resultado: tuple[bool, Any]) -> None:
            self._ocupado = False
            with contextlib.suppress(tk.TclError):
                self._botao.configure(state="normal")
            ok, valor = resultado
            if geracao != self._geracao:
                return
            if not ok:
                self._situacao.configure(text=f"Não foi possível verificar: {valor}")
                return
            self._mostrar(valor, cortes)

        self._executar(trabalho, concluir, sincrono)

    def _executar(self, trabalho: Callable[[], Any], concluir: Callable, sincrono: bool) -> None:
        def seguro() -> tuple[bool, Any]:
            try:
                return True, trabalho()
            except Exception as erro:  # a interface mostra o motivo em vez de travar
                return False, erro

        if sincrono:
            concluir(seguro())
            return
        fila: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: fila.put(seguro()), daemon=True).start()

        def esperar() -> None:
            try:
                resultado = fila.get_nowait()
            except queue.Empty:
                with contextlib.suppress(tk.TclError):
                    self.after(80, esperar)
                return
            with contextlib.suppress(tk.TclError):
                concluir(resultado)

        self.after(80, esperar)

    def _mostrar(self, problemas: list[Problema], cortes: list[int]) -> None:
        self._limpar_marcas()
        self._itens = []
        for numero, problema in enumerate(problemas):
            etiqueta = f"pt_item_{self._geracao}_{numero}"
            inicio = indice_do_tk(problema.linha, cortes[problema.linha] + problema.inicio)
            fim = indice_do_tk(problema.linha, cortes[problema.linha] + problema.fim)
            self._caixa.tag_add(f"pt_{problema.tipo}", inicio, fim)
            self._caixa.tag_add(etiqueta, inicio, fim)
            self._itens.append(_Item(problema, list(problema.sugestoes), etiqueta))
        self._atual = None
        self._atualizar_lista()
        if self._itens:
            self.proximo(1, a_partir_do_cursor=True)

    def _limpar_marcas(self) -> None:
        for tipo in CORES:
            self._caixa.tag_remove(f"pt_{tipo}", "1.0", "end")
        self._caixa.tag_remove("pt_atual", "1.0", "end")
        for item in self._itens:
            self._caixa.tag_delete(item.etiqueta)

    def limpar(self) -> None:
        """Tira os sublinhados e esvazia a lista (o texto foi trocado por outro)."""
        self._geracao += 1
        self._limpar_marcas()
        self._itens = []
        self._atual = None
        self._atualizar_lista()

    # --- lista --------------------------------------------------------------------------------
    def _atualizar_lista(self) -> None:
        pendentes = [i for i, it in enumerate(self._itens) if it.estado == "pendente"]
        self._visiveis = pendentes
        self._lista.delete(0, "end")
        for i in pendentes:
            p = self._itens[i].problema
            self._lista.insert(
                "end", f"{p.linha + 1:>4}  {ROTULOS[p.tipo][:4]}.  {p.trecho.strip()}"
            )
            self._lista.itemconfig("end", foreground=CORES[p.tipo])
        contagem = Counter(self._itens[i].problema.tipo for i in pendentes)
        if not self._itens:
            texto = "Nenhum aviso. ✔"
        elif not pendentes:
            texto = "Tudo resolvido. ✔ Verifique de novo depois de mexer no texto."
        else:
            partes = ", ".join(f"{n} {ROTULOS[t].lower()}" for t, n in contagem.most_common())
            texto = f"{len(pendentes)} aviso(s): {partes}."
        self._situacao.configure(text=texto)
        if not pendentes:
            self._mostrar_detalhe(None)

    def _ao_escolher(self, _evento=None) -> None:
        escolhida = self._lista.curselection()
        if escolhida:
            self._selecionar(self._visiveis[escolhida[0]])

    def proximo(self, sentido: int = 1, *, a_partir_do_cursor: bool = False) -> None:
        """Vai ao próximo (ou anterior) aviso pendente, a partir do atual ou do cursor."""
        if not self._itens:
            if not self._ocupado:
                self.verificar()
            return
        if not self._visiveis:
            return
        if self._atual in self._visiveis and not a_partir_do_cursor:
            posicao = self._visiveis.index(self._atual) + sentido
        else:
            posicao = self._posicao_do_cursor()
            if sentido < 0:
                posicao -= 1
        self._selecionar(self._visiveis[posicao % len(self._visiveis)])

    def _posicao_do_cursor(self) -> int:
        """Primeiro aviso pendente que está no cursor ou depois dele."""
        cursor = self._caixa.index("insert")
        for k, i in enumerate(self._visiveis):
            faixa = self._faixa(self._itens[i])
            if faixa and self._caixa.compare(faixa[0], ">=", cursor):
                return k
        return 0

    def _selecionar(self, i: int) -> None:
        self._atual = i
        item = self._itens[i]
        self._caixa.tag_remove("pt_atual", "1.0", "end")
        faixa = self._faixa(item)
        if faixa:
            self._caixa.tag_add("pt_atual", *faixa)
            self._caixa.mark_set("insert", faixa[0])
            self._caixa.see(faixa[0])
        if i in self._visiveis:
            linha = self._visiveis.index(i)
            self._lista.selection_clear(0, "end")
            self._lista.selection_set(linha)
            self._lista.see(linha)
        self._mostrar_detalhe(item)

    def _faixa(self, item: _Item) -> tuple[str, str] | None:
        faixa = self._caixa.tag_ranges(item.etiqueta)
        return (str(faixa[0]), str(faixa[1])) if len(faixa) >= 2 else None

    # --- detalhe e ações ----------------------------------------------------------------------
    def _mostrar_detalhe(self, item: _Item | None) -> None:
        for quadro in (self._quadro_sugestoes, self._quadro_acoes):
            for filho in quadro.winfo_children():
                filho.destroy()
        if item is None:
            self._mensagem.configure(text="")
            return
        p = item.problema
        self._mensagem.configure(text=p.mensagem, text_color=CORES[p.tipo])
        for sugestao in item.sugestoes[:5]:
            rotulo = sugestao if len(sugestao.strip()) > 2 else f"“{sugestao.strip()}”"
            ctk.CTkButton(
                self._quadro_sugestoes,
                text=rotulo,
                anchor="w",
                height=26,
                fg_color=("gray70", "gray28"),
                command=lambda s=sugestao: self.aplicar_sugestao(s),
            ).pack(fill="x", pady=1)
        acoes = [("Ignorar", self.ignorar_atual)]
        if p.tipo == "ortografia":
            if self._vocabulario is not None:
                acoes.append(("Está certa: guardar no vocabulário", self.aceitar_atual))
            acoes.append(("Mais sugestões", self.mais_sugestoes))
        pendentes_do_tipo = [
            1 for it in self._itens if it.estado == "pendente" and it.problema.tipo == p.tipo
        ]
        if p.tipo in TIPOS_EM_LOTE and len(pendentes_do_tipo) > 1 and item.sugestoes:
            acoes.append(
                (f"Corrigir os {len(pendentes_do_tipo)} de “{ROTULOS[p.tipo].lower()}”",
                 lambda: self.corrigir_todos(p.tipo)),
            )  # fmt: skip
        for texto, comando in acoes:
            ctk.CTkButton(
                self._quadro_acoes,
                text=texto,
                anchor="w",
                height=26,
                fg_color="transparent",
                border_width=1,
                command=comando,
            ).pack(fill="x", pady=1)

    def _marcar(self, i: int, estado: Estado) -> None:
        item = self._itens[i]
        item.estado = estado
        self._caixa.tag_remove(f"pt_{item.problema.tipo}", *(self._faixa(item) or ("1.0", "1.0")))
        self._caixa.tag_delete(item.etiqueta)

    def aplicar_sugestao(self, texto: str, *, ir_ao_proximo: bool = True) -> bool:
        """Troca o trecho do aviso atual por `texto`; False se o texto mudou antes."""
        if self._atual is None:
            return False
        i = self._atual
        ok = self._trocar(i, texto)
        self._depois_de_resolver(i, ir_ao_proximo)
        return ok

    def _trocar(self, i: int, texto: str, *, separar: bool = True) -> bool:
        item = self._itens[i]
        faixa = self._faixa(item)
        if faixa is None or self._caixa.get(*faixa) != item.problema.trecho:
            self._marcar(i, "desatualizado")
            return False
        caixa = self._caixa
        if separar:
            caixa.edit_separator()
        caixa.delete(*faixa)
        caixa.insert(faixa[0], texto)
        caixa.tag_remove(f"pt_{item.problema.tipo}", faixa[0], f"{faixa[0]}+{len(texto)}c")
        if separar:
            caixa.edit_separator()
        self._marcar(i, "corrigido")
        if self._ao_corrigir:
            self._ao_corrigir()
        return True

    def _depois_de_resolver(self, i: int, ir_ao_proximo: bool) -> None:
        posicao = self._visiveis.index(i) if i in self._visiveis else 0
        self._atualizar_lista()
        if ir_ao_proximo and self._visiveis:
            self._selecionar(self._visiveis[min(posicao, len(self._visiveis) - 1)])

    def corrigir_todos(self, tipo: str) -> int:
        """Aplica a primeira sugestão de todos os avisos pendentes do tipo. Devolve quantos."""
        feitos = 0
        self._caixa.edit_separator()
        for i, item in enumerate(self._itens):
            if item.estado == "pendente" and item.problema.tipo == tipo and item.sugestoes:
                feitos += bool(self._trocar(i, item.sugestoes[0], separar=False))
        self._caixa.edit_separator()
        self._atual = None
        self._atualizar_lista()
        if self._visiveis:
            self._selecionar(self._visiveis[0])
        return feitos

    def ignorar_atual(self) -> None:
        if self._atual is None:
            return
        verificador = self._obter_verificador()
        problema = self._itens[self._atual].problema
        verificador.ignorar(problema)
        i = self._atual
        for k, item in enumerate(self._itens):  # os iguais a este também somem da lista
            if item.estado == "pendente" and item.problema.chave == problema.chave:
                self._marcar(k, "ignorado")
        self._depois_de_resolver(i, True)

    def aceitar_atual(self) -> None:
        """A palavra está certa: entra no vocabulário (arquivo) e some de todos os avisos."""
        if self._atual is None or self._vocabulario is None:
            return
        i = self._atual
        palavra = self._itens[i].problema.trecho
        acrescentar_ao_vocabulario(self._vocabulario, palavra)
        self._obter_verificador().aceitar_palavra(palavra)
        for k, item in enumerate(self._itens):
            if item.estado == "pendente" and item.problema.trecho.casefold() == palavra.casefold():
                self._marcar(k, "ignorado")
        self._depois_de_resolver(i, True)

    def mais_sugestoes(self) -> None:
        """Pede ao Hunspell sugestões melhores (leva alguns segundos, em thread)."""
        if self._atual is None:
            return
        i = self._atual
        trecho = self._itens[i].problema.trecho
        self._mensagem.configure(text=f"{self._itens[i].problema.mensagem}\nBuscando sugestões…")

        def concluir(resultado: tuple[bool, Any]) -> None:
            ok, valor = resultado
            if not ok or i >= len(self._itens):
                return
            item = self._itens[i]
            novas = [s for s in valor if s not in item.sugestoes]
            item.sugestoes = [*item.sugestoes, *novas][:8]
            if self._atual == i:
                self._mostrar_detalhe(item)

        self._executar(
            lambda: list(self._obter_verificador().sugerir(trecho, a_fundo=True)), concluir, False
        )
