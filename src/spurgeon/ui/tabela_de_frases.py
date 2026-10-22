"""Tabela de frases: o corte do Short e o que há ao redor, uma frase por linha, com a hora ao lado.

É uma caixa de texto do Tk (rápida mesmo com centenas de palavras) que se comporta como tabela:
cada linha é uma frase (ou o pedaço dela que fica de um lado do corte), com a hora em que foi dita
numa coluna e o texto em outra (as linhas que quebram se alinham sob o texto). As linhas de fora
do corte ficam em cinza e só se lê; as do corte podem ser editadas ali mesmo, e quem usa a tabela
fica sabendo pelo `ao_editar`.

O começo e o fim do Short não têm marcador: é onde o texto passa de cinza para branco. Um
pedaço que a pessoa cortou de dentro dele aparece riscado, entre dois marcadores (“corta aqui” e
“continua aqui”). Há três ferramentas do mouse, todas com o cursor de texto: a seta (seleciona e
edita como sempre), o marca-texto (pinta, palavra por palavra, o que entra no Short; só as
palavras de fora acendem) e a borracha (apaga, palavra por palavra, o que sai dele; só as
palavras do Short acendem); quem usa a tabela fica sabendo pelo `ao_pintar`.

A edição é protegida no nível do Tcl: o comando do widget é trocado por um filtro que só deixa
inserir e apagar dentro do texto das linhas do corte (nunca na hora, nas linhas de fora, nos
marcadores nem nas quebras de linha). Assim a estrutura da tabela não se desfaz, venha a mudança
de um teclado, de colar ou de recortar.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from tkinter import Menu, TclError

import customtkinter as ctk

from spurgeon.shorts.edicao import LinhaDaTabela
from spurgeon.transcricao.formato import segundos_para_rotulo
from spurgeon.ui.legenda_de_categorias import BARRA, LOSANGO, configurar_tags_de_categoria
from spurgeon.ui.pincel import FERRAMENTA_BORRACHA, FERRAMENTA_MARCA, FERRAMENTA_SETA, Pincel

COR_FORA = "#7f7f7f"
_COR_HORA = "#7a7a7a"
_COR_DENTRO = "#f2f2f2"
_COR_CORTE = "#e0a030"
_COR_CORTADO = "#a07070"
_COR_SELECAO = "#2a6fb0"
_MARCADOR = {
    "corta": "✂ CORTA AQUI ✂",
    "volta": "↩ CONTINUA AQUI ↩",
}
_QUEBRAS = re.compile(r"[\r\n\t]+")
_MARGEM_DA_LINHA = 3  # a barra da categoria, o losango dos outros Shorts e um espaço


@dataclass(frozen=True)
class MargemDaLinha:
    """O que a margem de uma linha mostra, como no texto do Shorts livre.

    `categoria` é a que a IA viu no melhor trecho que cobre a frase (a barra colorida); `feito`, a
    categoria do primeiro outro Short que fala algo da frase (o losango); `sublinhadas`, as palavras
    da linha que esses Shorts falam (palavra → categoria do Short), que ficam sublinhadas.
    """

    categoria: str | None = None
    feito: str | None = None
    sublinhadas: Mapping[int, str] = field(default_factory=dict)


class TabelaDeFrases(ctk.CTkTextbox):
    def __init__(
        self,
        master,
        *,
        height: int,
        ao_editar: Callable[[], None],
        ao_duplo_clique: Callable[[int], None],
        ao_menu: Callable[[int, str], None],
        ao_pintar: Callable[[list[int], str], None] | None = None,
    ):
        """`ao_pintar(indices, ferramenta)` recebe as palavras (posições na transcrição) que o
        marca-texto pintou (`marca`) ou a borracha apagou (`borracha`) num arrasto do mouse."""
        super().__init__(master, height=height, wrap="word", undo=True)
        self._ao_editar = ao_editar
        self._ao_duplo_clique = ao_duplo_clique
        self._ao_menu = ao_menu
        self._ao_pintar = ao_pintar
        self._onde: dict[int, tuple[int, int, int]] = {}  # palavra → (linha, coluna, tamanho)
        self._linhas: list[LinhaDaTabela] = []
        self._colunas: list[int] = []  # onde, em cada linha do widget, o texto começa
        self._marcas: dict[str, str] = {}
        self._ultimo_texto = ""
        self._interno = False  # True enquanto o próprio código escreve na tabela
        self._configurar_marcas()
        self._instalar_filtro()
        caixa = self._textbox
        caixa.bind("<KeyRelease>", lambda _e: self._conferir_edicao(), add="+")
        for evento in ("<<Paste>>", "<<Cut>>", "<<Undo>>", "<<Redo>>"):
            caixa.bind(evento, lambda _e: self.after_idle(self._conferir_edicao), add="+")
        caixa.bind("<Double-Button-1>", self._duplo_clique)
        caixa.bind("<Button-3>", self._abrir_menu)
        self._pincel = Pincel(
            caixa,
            palavra_em=self.palavra_em,
            onde=lambda i: self._onde.get(i),
            ao_pintar=ao_pintar,
            vale=self._pode_pintar,
        )

    # --- aparência ----------------------------------------------------------------------------
    def _configurar_marcas(self) -> None:
        caixa = self._textbox
        # em pontos, para acompanhar a escala da tela: a coluna da hora e o recuo das linhas que
        # quebram (o texto continua sob o texto, não sob a hora)
        caixa.tag_config(
            "linha", lmargin1="6p", lmargin2="52p", tabs=("52p",), spacing1="2p", spacing3="2p"
        )
        # com a margem das categorias a hora anda um pouco para a direita: o texto também
        caixa.tag_config(
            "linha_m", lmargin1="6p", lmargin2="66p", tabs=("66p",), spacing1="2p", spacing3="2p"
        )
        configurar_tags_de_categoria(caixa)
        caixa.tag_config("marcador", lmargin1="6p", lmargin2="6p", spacing1="4p", spacing3="2p")
        caixa.tag_config("hora", foreground=_COR_HORA, font=("Segoe UI", 9))
        caixa.tag_config("fora", foreground=COR_FORA)
        caixa.tag_config("dentro", foreground=_COR_DENTRO, font=("Segoe UI", 11, "bold"))
        caixa.tag_config("limite_corte", foreground=_COR_CORTE, font=("Segoe UI", 10, "bold"))
        caixa.tag_config("cortado", foreground=_COR_CORTADO, overstrike=True)
        # a seleção fica visível mesmo quando o foco sai da tabela, e vale sobre as cores do texto
        caixa.configure(
            selectbackground=_COR_SELECAO,
            inactiveselectbackground=_COR_SELECAO,
            selectforeground="#ffffff",
        )
        caixa.tag_raise("sel")  # depois de criar as etiquetas de categoria, que nascem por cima

    # --- o filtro de edição -------------------------------------------------------------------
    def _instalar_filtro(self) -> None:
        caixa = self._textbox
        self._nome = str(caixa)
        self._original = f"{self._nome}_original"
        caixa.tk.call("rename", self._nome, self._original)
        caixa.tk.createcommand(self._nome, self._despachar)

    def _chamar(self, *argumentos):
        return self._textbox.tk.call(self._original, *argumentos)

    def _despachar(self, operacao, *argumentos):
        """O comando do widget: tudo passa direto, menos inserir e apagar feitos pela pessoa."""
        try:
            if not self._interno:
                if operacao == "insert":
                    return self._inserir(*argumentos)
                if operacao == "delete":
                    return self._apagar(*argumentos)
                if operacao == "replace":
                    return ""
            return self._chamar(operacao, *argumentos)
        except TclError:
            if operacao in ("insert", "delete", "replace"):
                return ""
            raise

    def _posicao(self, indice) -> tuple[int, int]:
        linha, coluna = str(self._chamar("index", indice)).split(".")
        return int(linha), int(coluna)

    def _fim_da_linha(self, linha: int) -> int:
        return self._posicao(f"{linha}.end")[1]

    def _editavel(self, linha: int, coluna: int) -> bool:
        if not 1 <= linha <= len(self._linhas):
            return False
        return self._linhas[linha - 1].tipo == "dentro" and coluna >= self._colunas[linha - 1]

    def _inserir(self, indice, texto, *_marcas_do_pedido) -> str:
        linha, coluna = self._posicao(indice)
        if not self._editavel(linha, coluna):
            return ""
        texto = str(texto)
        if "\n" in texto and not texto.strip("\r\n"):
            return ""  # Enter: a frase não se parte em duas
        texto = _QUEBRAS.sub(" ", texto)
        if not texto:
            return ""
        self._chamar("insert", f"{linha}.{coluna}", texto, ("dentro",))
        return ""

    def _apagar(self, inicio, fim=None) -> str:
        de = self._posicao(inicio)
        ate = self._posicao(fim) if fim not in (None, "") else self._posicao(f"{inicio} +1c")
        if ate <= de:
            return ""
        (linha_de, coluna_de), (linha_ate, coluna_ate) = de, ate
        # só o texto das linhas do corte sai; as horas e as quebras de linha ficam onde estão
        for linha in range(min(linha_ate, len(self._linhas)), linha_de - 1, -1):
            if self._linhas[linha - 1].tipo != "dentro":
                continue
            primeira = max(coluna_de if linha == linha_de else 0, self._colunas[linha - 1])
            ultima = coluna_ate if linha == linha_ate else self._fim_da_linha(linha)
            if ultima > primeira:
                self._chamar("delete", f"{linha}.{primeira}", f"{linha}.{ultima}")
        return ""

    def destroy(self) -> None:
        with contextlib.suppress(TclError):
            self._textbox.tk.deletecommand(self._nome)
        super().destroy()

    # --- o conteúdo ---------------------------------------------------------------------------
    @property
    def linhas(self) -> list[LinhaDaTabela]:
        return list(self._linhas)

    def mostrar(
        self,
        linhas: Sequence[LinhaDaTabela],
        *,
        centralizar: str | None = None,
        margens: Sequence[MargemDaLinha | None] | None = None,
    ) -> None:
        """Põe as linhas na tabela. Com `centralizar` (`inicio` ou `fim`) leva a vista até o começo
        ou o fim do Short; sem ele a vista fica onde estava (a mesma frase no alto).

        `margens` (uma por linha) liga a margem das categorias: a barra colorida, o losango dos
        outros Shorts e o sublinhado das palavras deles. Sem `margens` as linhas começam na hora.
        """
        caixa = self._textbox
        ancora = self._tempo_do_alto() if centralizar is None else None
        self._interno = True
        try:
            caixa.delete("1.0", "end")
            self._linhas = list(linhas)
            self._colunas = []
            self._marcas = {}
            self._onde = {}
            for numero, linha in enumerate(self._linhas, start=1):
                if linha.e_marcador:
                    self._marcas.setdefault(linha.tipo, f"{numero}.0")
                    caixa.insert("end", _MARCADOR[linha.tipo] + "\n", ("limite_corte", "marcador"))
                    self._colunas.append(0)
                    continue
                hora = segundos_para_rotulo(linha.tempo)
                molde = "linha_m" if margens is not None else "linha"
                prefixo = 0
                if margens is not None:
                    margem = margens[numero - 1] if numero <= len(margens) else None
                    categoria = margem.categoria if margem else None
                    feito = margem.feito if margem else None
                    caixa.insert(
                        "end",
                        BARRA if categoria else " ",
                        (f"cat_{categoria}", molde) if categoria else (molde,),
                    )
                    caixa.insert(
                        "end",
                        LOSANGO if feito else " ",
                        (f"feito_{feito}", molde) if feito else (molde,),
                    )
                    caixa.insert("end", " ", (molde,))
                    prefixo = _MARGEM_DA_LINHA
                caixa.insert("end", hora, ("hora", molde))
                caixa.insert("end", "\t", (molde,))
                caixa.insert("end", linha.texto, (linha.tipo, molde))
                caixa.insert("end", "\n", (molde,))
                self._colunas.append(prefixo + len(hora) + 1)
                coluna = prefixo + len(hora) + 1
                for texto, indice in zip(linha.textos, linha.indices, strict=False):
                    if indice >= 0:  # -1: palavra que a pessoa escreveu, sem par na transcrição
                        self._onde[indice] = (numero, coluna, len(texto))
                    coluna += len(texto) + 1
                if margens is not None and margem is not None:
                    for indice, categoria_do_short in margem.sublinhadas.items():
                        onde = self._onde.get(indice)
                        if onde is not None and onde[0] == numero:
                            caixa.tag_add(
                                f"sub_{categoria_do_short}",
                                f"{numero}.{onde[1]}",
                                f"{numero}.{onde[1] + onde[2]}",
                            )
                if linha.tipo == "dentro":  # onde o Short começa e termina (sem marcador)
                    self._marcas.setdefault("inicio", f"{numero}.0")
                    self._marcas["fim"] = f"{numero}.0"
            caixa.edit_reset()
            caixa.edit_modified(False)
            self._ultimo_texto = self.texto_dentro()
            if ancora is not None:
                caixa.yview(f"{self._linha_mais_perto(ancora)}.0")
        finally:
            self._interno = False
        if centralizar is not None:
            self.after_idle(self.ir_a, centralizar)

    def mostrar_aviso(self, texto: str) -> None:
        """Esvazia a tabela e escreve só um aviso (a transcrição não está disponível)."""
        caixa = self._textbox
        self._interno = True
        try:
            caixa.delete("1.0", "end")
            self._linhas, self._colunas, self._marcas, self._onde = [], [], {}, {}
            caixa.insert("end", texto, ("fora",))
            caixa.edit_reset()
            self._ultimo_texto = ""
        finally:
            self._interno = False

    def texto_dentro(self) -> str:
        """O texto das linhas do corte, como está agora (com o que a pessoa editou)."""
        partes = [
            str(self._chamar("get", f"{numero}.{self._colunas[numero - 1]}", f"{numero}.end"))
            for numero, linha in enumerate(self._linhas, start=1)
            if linha.tipo == "dentro"
        ]
        return " ".join(" ".join(partes).split())

    def _conferir_edicao(self) -> None:
        atual = self.texto_dentro()
        if atual != self._ultimo_texto:
            self._ultimo_texto = atual
            self._ao_editar()

    # --- a vista ------------------------------------------------------------------------------
    def ir_para_linha(self, numero: int) -> None:
        """Leva a vista da tabela até a linha `numero` (de 1 a N), deixando-a no alto."""
        with contextlib.suppress(TclError):
            self._textbox.yview(f"{numero}.0")

    def ir_a(self, ponta: str, *, so_se_preciso: bool = False) -> None:
        """Rola até a primeira ou a última linha do Short, deixando contexto de um lado e do outro.

        Com `so_se_preciso` a vista só anda se a linha não estiver à vista (sem recentralizar).
        """
        marca = self._marcas.get(ponta)
        if marca is None:
            return
        with contextlib.suppress(TclError):
            caixa = self._textbox
            if so_se_preciso:
                caixa.see(marca)
                return
            caixa.see(f"{marca} - 5 display lines")
            caixa.see(f"{marca} + 5 display lines")

    def _tempo_do_alto(self) -> float | None:
        if not self._linhas:
            return None
        with contextlib.suppress(TclError):
            numero = self._posicao("@0,0")[0]
            for linha in self._linhas[numero - 1 :] or self._linhas[-1:]:
                if not linha.e_marcador:
                    return linha.tempo
        return None

    def _linha_mais_perto(self, tempo: float) -> int:
        candidatas = [
            (abs(linha.tempo - tempo), numero)
            for numero, linha in enumerate(self._linhas, start=1)
            if not linha.e_marcador
        ]
        return min(candidatas)[1] if candidatas else 1

    # --- palavras sob o mouse -----------------------------------------------------------------
    def _sob(self, x: int, y: int) -> tuple[LinhaDaTabela | None, int | None]:
        """(a linha da tabela sob o ponto, a posição na transcrição da palavra que está ali).

        A palavra é None quando não dá para dizer: a hora, um marcador, o espaço entre as palavras
        ou uma frase que a pessoa já reescreveu (as palavras já não são as da transcrição).
        """
        try:
            numero, coluna = self._posicao(f"@{x},{y}")
        except TclError:
            return None, None
        if not 1 <= numero <= len(self._linhas):
            return None, None
        linha, inicio_do_texto = self._linhas[numero - 1], self._colunas[numero - 1]
        if linha.e_marcador or not linha.indices or coluna < inicio_do_texto:
            return linha, None
        if str(self._chamar("get", f"{numero}.{inicio_do_texto}", f"{numero}.end")) != linha.texto:
            return linha, None
        posicao = coluna - inicio_do_texto
        comeco = 0
        for texto, indice in zip(linha.textos, linha.indices, strict=True):
            if comeco <= posicao < comeco + len(texto):
                return linha, (indice if indice >= 0 else None)
            comeco += len(texto) + 1
        return linha, None

    def palavra_em(self, x: int, y: int) -> int | None:
        """A posição, na transcrição, da palavra sob o ponto (x, y); None se não dá para dizer."""
        return self._sob(x, y)[1]

    def posicao_da_palavra(self, indice: int) -> str | None:
        """O índice do Tk (`linha.coluna`) onde começa a palavra `indice` da transcrição."""
        onde = self._onde.get(indice)
        return f"{onde[0]}.{onde[1]}" if onde else None

    def linha_da_palavra(self, indice: int) -> LinhaDaTabela | None:
        """A linha da tabela (a frase) a que a palavra `indice` pertence."""
        onde = self._onde.get(indice)
        return self._linhas[onde[0] - 1] if onde else None

    def selecionar_palavra(self, indice: int) -> bool:
        """Deixa a palavra `indice` selecionada (azul) na tabela, como um duplo clique faria."""
        onde = self._onde.get(indice)
        if onde is None:
            return False
        linha, coluna, tamanho = onde
        with contextlib.suppress(TclError):
            self._textbox.tag_remove("sel", "1.0", "end")
            self._textbox.tag_add("sel", f"{linha}.{coluna}", f"{linha}.{coluna + tamanho}")
            return True
        return False

    # --- marca-texto e borracha -----------------------------------------------------------------
    def _pode_pintar(self, indice: int, ferramenta: str) -> bool:
        """O marca-texto só mexe nas palavras que estão de fora (ou cortadas); a borracha, só nas
        que estão no Short: assim o que acende é sempre o que muda."""
        linha = self.linha_da_palavra(indice)
        if linha is None:
            return False
        if ferramenta == FERRAMENTA_MARCA:
            return linha.tipo in ("fora", "cortado")
        return linha.tipo == "dentro"

    @property
    def ferramenta(self) -> str:
        return self._pincel.ferramenta

    def definir_ferramenta(self, nome: str) -> None:
        """`seta` (o gesto normal), `marca` (marca-texto) ou `borracha`."""
        self._pincel.definir(nome)

    def _duplo_clique(self, evento) -> str | None:
        """Duplo clique numa palavra de fora: leva o começo ou o fim do corte até ela. Dentro do
        corte vale o gesto normal (selecionar a palavra), porque ali se edita o texto."""
        if self.ferramenta != FERRAMENTA_SETA:
            return "break"  # com o marca-texto ou a borracha o duplo clique é só mais um arrasto
        linha, indice = self._sob(evento.x, evento.y)
        if indice is None or linha is None or linha.tipo in ("dentro", "cortado"):
            return None  # dentro do corte o duplo clique seleciona a palavra, para editar
        self._ao_duplo_clique(indice)
        return "break"

    def _abrir_menu(self, evento) -> str | None:
        linha, indice = self._sob(evento.x, evento.y)
        if indice is None or linha is None:
            return None
        menu = Menu(self, tearoff=0)
        menu.add_command(
            label="Começar o Short nesta palavra", command=lambda: self._ao_menu(indice, "inicio")
        )
        menu.add_command(
            label="Terminar o Short nesta palavra", command=lambda: self._ao_menu(indice, "fim")
        )
        if self._ao_pintar is not None:
            menu.add_separator()
            frase = [i for i in linha.indices if i >= 0]
            if linha.tipo == "dentro":
                menu.add_command(
                    label="Cortar esta palavra do Short",
                    command=lambda: self._ao_pintar([indice], FERRAMENTA_BORRACHA),
                )
                menu.add_command(
                    label="Cortar esta frase do Short",
                    command=lambda: self._ao_pintar(frase, FERRAMENTA_BORRACHA),
                )
            else:
                menu.add_command(
                    label="Pôr esta palavra no Short",
                    command=lambda: self._ao_pintar([indice], FERRAMENTA_MARCA),
                )
                menu.add_command(
                    label="Pôr esta frase no Short",
                    command=lambda: self._ao_pintar(frase, FERRAMENTA_MARCA),
                )
        try:
            menu.tk_popup(evento.x_root, evento.y_root)
        finally:
            menu.grab_release()
        return "break"
