"""Tela de revisão da transcrição: um trecho por linha, com busca e troca em todo o texto.

As funções de cima não usam janela e são testadas à parte. Cada linha da tela tem o formato
`[2:05] texto do trecho`; o horário só orienta (não dá para tocar o vídeo aqui) e precisa ficar
no começo da linha. As palavras em que o Whisper teve pouca confiança aparecem em laranja.
"""

from __future__ import annotations

import contextlib
import re
from tkinter import TclError

import customtkinter as ctk

from spurgeon.core.config import Pastas
from spurgeon.core.projeto import PastasProjeto
from spurgeon.transcricao.edicao import aplicar_edicoes, separar_pontuacao
from spurgeon.transcricao.formato import Transcricao, segundos_para_rotulo
from spurgeon.transcricao.verificador import verificador_compartilhado
from spurgeon.ui import dialogos
from spurgeon.ui.verificacao import PainelDePortugues

PROB_BAIXA = 0.5
# Palavrinhas ("e", "o", "se") têm confiança baixa o tempo todo sem estarem erradas.
TAMANHO_MINIMO_DESTAQUE = 4
_COR_BAIXA = "#e0a030"
_COR_MARCA = "#7a7a7a"
_COR_ACHADO = "#5a4a00"
_MARCA = re.compile(r"^\[(\d+:\d{2}(?::\d{2})?)\] ?")


class ErroDeLinhas(ValueError):
    """O texto editado não corresponde mais aos trechos da transcrição."""


def _duvidosa(palavra) -> bool:
    nucleo = separar_pontuacao(palavra.texto)[1]
    return (
        palavra.prob < PROB_BAIXA
        and len(nucleo) >= TAMANHO_MINIMO_DESTAQUE
        and palavra.original is None
    )


def montar_linhas(transcricao: Transcricao) -> list[tuple[str, list[tuple[str, bool]]]]:
    """Uma entrada por segmento: o horário (`[2:05] `) e as palavras com a marca de dúvida."""
    return [
        (
            f"[{segundos_para_rotulo(segmento.inicio)}] ",
            [(p.texto, _duvidosa(p)) for p in segmento.palavras],
        )
        for segmento in transcricao.segmentos
    ]


def ler_linhas(conteudo: str, quantidade: int) -> list[str]:
    """O texto de cada trecho, sem o horário. Levanta `ErroDeLinhas` se a estrutura mudou."""
    linhas = conteudo.split("\n")
    while linhas and linhas[-1] == "":  # o widget acrescenta uma quebra no fim
        linhas.pop()
    if len(linhas) != quantidade:
        raise ErroDeLinhas(
            f"A transcrição tem {quantidade} trechos, mas o texto tem {len(linhas)} linhas. "
            "Cada linha é um trecho: não divida nem junte linhas (Ctrl+Z desfaz). Para apagar "
            "um trecho, apague só o texto e deixe o [horário]."
        )
    textos = []
    for numero, linha in enumerate(linhas, start=1):
        achado = _MARCA.match(linha)
        if achado is None:
            raise ErroDeLinhas(f"A linha {numero} perdeu o [horário] do começo; devolva-o.")
        textos.append(linha[achado.end() :])
    return textos


def trocar_no_texto(conteudo: str, procurar: str, trocar_por: str) -> tuple[str, int]:
    """Troca `procurar` (sem diferenciar maiúsculas) em todas as linhas, exceto no horário."""
    procurar = procurar.strip()
    if not procurar:
        return conteudo, 0
    padrao = re.compile(rf"(?<!\w){re.escape(procurar)}(?!\w)", re.IGNORECASE)
    total = 0
    saida = []
    for linha in conteudo.split("\n"):
        marca = _MARCA.match(linha)
        corte = marca.end() if marca else 0
        corpo, n = padrao.subn(lambda m: _manter_inicial(m.group(0), trocar_por), linha[corte:])
        total += n
        saida.append(linha[:corte] + corpo)
    return "\n".join(saida), total


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _manter_inicial(original: str, novo: str) -> str:
    if original[:1].isupper() and novo[:1].islower():
        return novo[:1].upper() + novo[1:]
    return novo


class PainelRevisaoTranscricao(ctk.CTkToplevel):
    def __init__(self, janela: ctk.CTk, pastas: PastasProjeto, *, modal: bool = True):
        super().__init__(janela)
        self._janela = janela
        self._pastas = pastas
        self._transcricao = Transcricao.carregar(pastas.transcricao)
        self._linhas = montar_linhas(self._transcricao)
        self.alteracoes = 0
        self.title("Revisão da transcrição")
        self._dimensionar(janela)
        self._montar()
        self._preencher()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Control-s>", lambda _e: self.salvar_e_fechar())
        self.bind("<Control-f>", lambda _e: self._busca.focus())
        self.bind("<F3>", lambda _e: self._proximo())
        self.bind("<F8>", lambda _e: self._portugues.proximo(1))
        self.bind("<Shift-F8>", lambda _e: self._portugues.proximo(-1))
        if modal:
            self.after(150, self.focus)
            self.grab_set()

    # --- montagem -----------------------------------------------------------------------------
    def _dimensionar(self, janela: ctk.CTk) -> None:
        escala = self._get_window_scaling()
        tela_l, tela_a = self.winfo_screenwidth() / escala, self.winfo_screenheight() / escala
        largura, altura = int(min(1000, tela_l * 0.92)), int(min(700, tela_a * 0.88))
        self.geometry(
            f"{largura}x{altura}+{int((tela_l - largura) / 2)}+{int((tela_a - altura) / 3)}"
        )
        self.minsize(640, 420)

    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        duvidosas = sum(1 for _, ps in self._linhas for _, baixa in ps if baixa)
        ctk.CTkLabel(
            self,
            anchor="w",
            justify="left",
            wraplength=940,
            text=(
                f"{len(self._linhas)} trechos. Cada linha é um trecho da fala: corrija o texto "
                "direto aqui. Palavras em laranja são as que o Whisper teve menos certeza "
                f"({duvidosas}). Não divida nem junte linhas e mantenha o [horário] no começo."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))

        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))
        self._busca = ctk.CTkEntry(barra, width=200, placeholder_text="Localizar…")
        self._busca.pack(side="left")
        self._busca.bind("<Return>", lambda _e: self._proximo())
        self._busca.bind("<KeyRelease>", lambda _e: self._marcar_achados())
        ctk.CTkButton(barra, text="Próximo", width=80, command=self._proximo).pack(
            side="left", padx=6
        )
        self._contagem = ctk.CTkLabel(barra, text="", text_color="gray60", width=90, anchor="w")
        self._contagem.pack(side="left")
        self._troca = ctk.CTkEntry(barra, width=180, placeholder_text="Trocar por…")
        self._troca.pack(side="left", padx=(16, 6))
        ctk.CTkButton(barra, text="Trocar em tudo", width=120, command=self._trocar_em_tudo).pack(
            side="left"
        )
        ctk.CTkButton(
            barra, text="Verificar português", width=140, command=self._verificar_portugues
        ).pack(side="right")

        self._texto = ctk.CTkTextbox(self, wrap="word", undo=True, font=ctk.CTkFont(size=14))
        self._texto.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 8))
        caixa = self._texto._textbox
        caixa.tag_config("marca", foreground=_COR_MARCA)
        caixa.tag_config("baixa", foreground=_COR_BAIXA)
        caixa.tag_config("achado", background=_COR_ACHADO)

        pastas_do_app = self._pastas_do_aplicativo()
        self._portugues = PainelDePortugues(
            self,
            caixa,
            lambda: verificador_compartilhado(pastas_do_app),
            vocabulario=pastas_do_app.vocabulario,
            prefixo=_MARCA,
        )  # aparece (coluna 1) na primeira verificação
        self._portugues_visivel = False

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
            text="Ctrl+S salva · Ctrl+F busca · F3 próximo achado · F8 próximo aviso de português",
            text_color="gray60",
        ).pack(side="left")

    def _pastas_do_aplicativo(self) -> Pastas:
        config = getattr(self._janela, "config_app", None)
        return config.pastas if config is not None else Pastas()

    def _verificar_portugues(self) -> None:
        if not self._portugues_visivel:
            self._portugues.grid(row=2, column=1, sticky="ns", padx=(0, 14), pady=(0, 8))
            self._portugues_visivel = True
            self._ampliar_para_o_painel()
        self._portugues.verificar()

    def _ampliar_para_o_painel(self) -> None:
        """Dá ao painel de português o espaço que ele ocupa, se a tela comportar."""
        escala = self._get_window_scaling()
        self.update_idletasks()
        largura = int(self.winfo_width() / escala) + 330
        maxima = int(self.winfo_screenwidth() / escala * 0.98)
        altura = int(self.winfo_height() / escala)
        x, y = int(self.winfo_x() / escala), int(self.winfo_y() / escala)
        largura = min(largura, maxima)
        self.geometry(f"{largura}x{altura}+{max(0, min(x, maxima - largura))}+{y}")

    def _preencher(self) -> None:
        caixa = self._texto._textbox
        for marca, palavras in self._linhas:
            caixa.insert("end", marca, "marca")
            for i, (texto, baixa) in enumerate(palavras):
                if i:
                    caixa.insert("end", " ")
                caixa.insert("end", texto, "baixa" if baixa else ())
            caixa.insert("end", "\n")
        caixa.edit_reset()  # o preenchimento inicial não entra no histórico do Ctrl+Z
        caixa.edit_modified(False)  # e não conta como edição (senão Cancelar sempre perguntaria)
        caixa.mark_set("insert", "1.0")

    # --- busca e troca ------------------------------------------------------------------------
    def _marcar_achados(self) -> int:
        caixa = self._texto._textbox
        caixa.tag_remove("achado", "1.0", "end")
        termo = self._busca.get().strip()
        if not termo:
            self._contagem.configure(text="")
            return 0
        total = 0
        inicio = "1.0"
        while True:
            posicao = caixa.search(termo, inicio, stopindex="end", nocase=True)
            if not posicao:
                break
            fim = f"{posicao}+{len(termo)}c"
            caixa.tag_add("achado", posicao, fim)
            total += 1
            inicio = fim
        self._contagem.configure(text=_plural(total, "achado", "achados") if total else "nenhum")
        return total

    def _proximo(self) -> None:
        if not self._marcar_achados():
            return
        caixa = self._texto._textbox
        termo = self._busca.get().strip()
        posicao = caixa.search(termo, "insert+1c", stopindex="end", nocase=True) or caixa.search(
            termo, "1.0", stopindex="end", nocase=True
        )
        if posicao:
            caixa.mark_set("insert", posicao)
            caixa.see(posicao)

    def _trocar_em_tudo(self) -> None:
        procurar, trocar_por = self._busca.get().strip(), self._troca.get().strip()
        if not procurar or not trocar_por:
            dialogos.aviso(
                self, "Trocar em tudo", "Preencha os dois campos: o que procurar e por que trocar."
            )
            return
        caixa = self._texto._textbox
        novo, total = trocar_no_texto(caixa.get("1.0", "end-1c"), procurar, trocar_por)
        if not total:
            dialogos.aviso(self, "Trocar em tudo", f"“{procurar}” não aparece no texto.")
            return
        # troca só o corpo das linhas, para manter as cores; recarrega o texto todo
        caixa.edit_separator()
        self._portugues.limpar()  # as marcas do verificador não sobrevivem ao texto novo
        caixa.delete("1.0", "end")
        for numero, linha in enumerate(novo.split("\n")):
            if numero:
                caixa.insert("end", "\n")
            marca = _MARCA.match(linha)
            corte = marca.end() if marca else 0
            caixa.insert("end", linha[:corte], "marca")
            caixa.insert("end", linha[corte:])
        caixa.edit_separator()
        self._contagem.configure(text=_plural(total, "troca", "trocas"))

    # --- salvar -------------------------------------------------------------------------------
    def salvar_e_fechar(self) -> bool:
        """Grava as correções em `transcricao.json`. Devolve False se o texto não pôde ser lido."""
        try:
            textos = ler_linhas(self._texto._textbox.get("1.0", "end-1c"), len(self._linhas))
            self.alteracoes = aplicar_edicoes(self._transcricao, textos)
        except ErroDeLinhas as erro:
            dialogos.aviso(self, "Não foi possível salvar", str(erro))
            return False
        if self.alteracoes:
            self._transcricao.salvar(self._pastas.transcricao)
        self._fechar()
        return True

    def _cancelar(self) -> None:
        if self._houve_edicao() and not dialogos.confirmar(
            self,
            "Descartar as correções?",
            "Há alterações que ainda não foram salvas.",
            sim="Descartar",
            nao="Continuar editando",
        ):
            return
        self._fechar()

    def _houve_edicao(self) -> bool:
        try:
            return bool(self._texto._textbox.edit_modified())
        except TclError:
            return False

    def _fechar(self) -> None:
        with contextlib.suppress(TclError):
            self.grab_release()
        self.destroy()


def abrir_painel_transcricao(janela: ctk.CTk, pastas: PastasProjeto) -> None:
    if not pastas.transcricao.exists():
        dialogos.aviso(janela, "Revisão da transcrição", "A transcrição ainda não foi gerada.")
        return
    painel = PainelRevisaoTranscricao(janela, pastas)
    janela.wait_window(painel)
