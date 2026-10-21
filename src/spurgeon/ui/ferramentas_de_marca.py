"""Os três botões de "ferramenta do mouse" do texto: seta, marca-texto e borracha.

É um interruptor de três posições (uma só fica ligada). A tabela de frases do Studio e o texto
do Shorts livre perguntam `ferramenta` (ou recebem `ao_trocar`) para saber o que o mouse faz.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from spurgeon.ui.dicas import dica
from spurgeon.ui.tabela_de_frases import FERRAMENTA_BORRACHA, FERRAMENTA_MARCA, FERRAMENTA_SETA

_COR_DESLIGADO = ("gray70", "gray30")
FERRAMENTAS = (
    (
        FERRAMENTA_SETA,
        "↖ Seta",
        "Seleciona e edita como sempre. No texto cinza, duplo clique leva o começo ou o fim do "
        "Short até a palavra; botão direito numa palavra abre mais opções (começar aqui, "
        "terminar aqui, pôr ou cortar a palavra ou a frase).",
        "#1f6aa5",
    ),
    (
        FERRAMENTA_MARCA,
        "✎ Marca-texto",
        "Pinta, palavra por palavra, o que entra no Short: arraste o mouse sobre palavras cinzas "
        "(ou riscadas) para acrescentá-las. Dá para pintar longe do Short e montar um corte com "
        "pedaços separados.",
        "#a67c00",
    ),
    (
        FERRAMENTA_BORRACHA,
        "⌫ Borracha",
        "Apaga, palavra por palavra, o que sai do Short: arraste sobre palavras brancas para "
        "tirá-las e o Short continua logo depois. Serve para cortar um erro de pronúncia ou uma "
        "frase do meio sem ter que parar antes.",
        "#a03a3a",
    ),
)


class FerramentasDeMarca(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        ao_trocar: Callable[[str], None] | None = None,
        inicial: str = FERRAMENTA_SETA,
    ):
        super().__init__(master, fg_color="transparent")
        self._ao_trocar = ao_trocar
        self._ferramenta = inicial
        self._botoes: dict[str, ctk.CTkButton] = {}
        self._cores = {nome: cor for nome, _t, _d, cor in FERRAMENTAS}
        for nome, texto, ajuda, _cor in FERRAMENTAS:
            botao = ctk.CTkButton(
                self,
                text=texto,
                width=116,
                height=28,
                corner_radius=6,
                command=lambda n=nome: self.definir(n),
            )
            botao.pack(side="left", padx=(0, 8))
            dica(botao, ajuda)
            self._botoes[nome] = botao
        self._pintar()

    @property
    def ferramenta(self) -> str:
        return self._ferramenta

    def definir(self, nome: str, *, avisar: bool = True) -> None:
        if nome not in self._botoes:
            raise ValueError(f"ferramenta desconhecida: {nome}")
        mudou = nome != self._ferramenta
        self._ferramenta = nome
        self._pintar()
        if avisar and mudou and self._ao_trocar is not None:
            self._ao_trocar(nome)

    def _pintar(self) -> None:
        for nome, botao in self._botoes.items():
            ligado = nome == self._ferramenta
            botao.configure(
                fg_color=self._cores[nome] if ligado else _COR_DESLIGADO,
                hover_color=self._cores[nome] if ligado else ("gray62", "gray38"),
                text_color="white" if ligado else ("gray10", "gray90"),
            )

    def habilitar(self, ativo: bool) -> None:
        for botao in self._botoes.values():
            botao.configure(state="normal" if ativo else "disabled")
