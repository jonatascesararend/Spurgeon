"""A janela "Ajuste fino da legenda" do Studio Shorts: cada palavra do Short com o seu tempo.

A pessoa ouve o trecho (no tempo normal ou mais devagar) com a palavra que toca acesa na lista
e acerta, palavra por palavra, o texto, o início e o fim; acrescenta a que a transcrição comeu e
tira a que sobrou. "⏱ Agora" põe o início (ou o fim) da palavra no instante que está tocando: é o
jeito mais rápido de sincronizar. Com "Mover as seguintes junto", acertar uma palavra empurra as
de depois pelo mesmo tanto (quando a legenda inteira está adiantada ou atrasada a partir de um
ponto).

O som é o do sermão, na linha do tempo do vídeo original (`render/trecho_de_audio.py`), tocado pelo
`winsound`; a posição vem do relógio desde o começo do som.
"""

from __future__ import annotations

import contextlib
import tempfile
import time
import wave
from bisect import bisect_right
from collections.abc import Callable
from pathlib import Path
from tkinter import TclError, ttk

import customtkinter as ctk

from spurgeon.core.formatacao import formatar_tempo_preciso, ler_tempo
from spurgeon.render.trecho_de_audio import VELOCIDADES, wav_do_trecho
from spurgeon.shorts.ajuste_fino import DURACAO_MINIMA_DA_PALAVRA_S, PalavraAjustada
from spurgeon.shorts.dados import Corte
from spurgeon.transcricao.formato import Palavra
from spurgeon.ui import dialogos
from spurgeon.ui.ajuda import dica_do_seletor
from spurgeon.ui.dicas import dica

try:
    import winsound
except ImportError:  # pragma: no cover - o aplicativo é só para Windows
    winsound = None  # type: ignore[assignment]

FOLGA_S = 1.0  # quanto de som antes e depois do Short entra no trecho
FOLGA_DA_PALAVRA_S = 0.25  # "Ouvir esta palavra" toca um pouco antes e depois dela
PASSOS_S = (0.1, 0.02)
_ATUALIZAR_MS = 40
_RECUO_DO_AGORA_S = 0.12  # quem aperta "Agora" reage um pouco depois de ouvir: desconta
_COR_SUAVE = ("gray35", "gray65")
_COR_ALERTA = "#e0a030"


def _tempo(segundos: float) -> str:
    return formatar_tempo_preciso(segundos, casas=2)


def recortar_wav(origem: Path, de_s: float, ate_s: float | None, destino: Path) -> None:
    """Copia de `origem` o som de `de_s` a `ate_s` (segundos do próprio arquivo)."""
    with wave.open(str(origem), "rb") as entrada:
        taxa = entrada.getframerate()
        total = entrada.getnframes()
        primeiro = max(0, min(total, int(de_s * taxa)))
        ultimo = total if ate_s is None else max(primeiro, min(total, int(ate_s * taxa)))
        entrada.setpos(primeiro)
        quadros = entrada.readframes(ultimo - primeiro)
        with wave.open(str(destino), "wb") as saida:
            saida.setparams(entrada.getparams())
            saida.writeframes(quadros)


def _tocar_com_winsound(arquivo: Path | None) -> None:
    if winsound is None:
        return
    with contextlib.suppress(RuntimeError, OSError):
        if arquivo is None:
            winsound.PlaySound(None, winsound.SND_PURGE)
        else:
            winsound.PlaySound(str(arquivo), winsound.SND_FILENAME | winsound.SND_ASYNC)


class Tocador:
    """Toca o trecho do sermão a partir de qualquer instante e diz onde está (segundos do vídeo)."""

    def __init__(
        self,
        fonte: Path | None,
        de: float,
        ate: float,
        *,
        tocar_som: Callable[[Path | None], None] = _tocar_com_winsound,
        relogio: Callable[[], float] = time.monotonic,
        gerar=wav_do_trecho,
    ):
        self.fonte, self.de, self.ate = fonte, de, ate
        self.velocidade = 1.0
        self._tocar_som, self._relogio, self._gerar = tocar_som, relogio, gerar
        self._pasta = Path(tempfile.mkdtemp(prefix="spurgeon_ajuste_"))
        self._wavs: dict[float, Path] = {}
        self._inicio_relogio: float | None = None
        self._desde = de
        self._fim = ate
        self._vez = 0

    @property
    def tocando(self) -> bool:
        return self._inicio_relogio is not None and self.posicao < self._fim

    @property
    def posicao(self) -> float:
        if self._inicio_relogio is None:
            return self._desde
        return self._desde + (self._relogio() - self._inicio_relogio) * self.velocidade

    def _wav(self) -> Path:
        if self.velocidade not in self._wavs:
            destino = self._pasta / f"trecho_{self.velocidade:g}.wav"
            self._gerar(self.fonte, self.de, self.ate, destino, velocidade=self.velocidade)
            self._wavs[self.velocidade] = destino
        return self._wavs[self.velocidade]

    def tocar(self, desde: float, ate: float | None = None) -> None:
        """Toca de `desde` até `ate` (ou até o fim do trecho), em segundos do vídeo original."""
        if self.fonte is None:
            return
        self.parar()
        desde = min(max(desde, self.de), self.ate)
        fim = self.ate if ate is None else min(max(ate, desde), self.ate)
        self._vez += 1
        pedaco = self._pasta / f"tocando_{self._vez % 2}.wav"
        escala = 1 / self.velocidade
        recortar_wav(self._wav(), (desde - self.de) * escala, (fim - self.de) * escala, pedaco)
        self._desde, self._fim = desde, fim
        self._inicio_relogio = self._relogio()
        self._tocar_som(pedaco)

    def parar(self) -> None:
        if self._inicio_relogio is not None:
            self._desde = min(self.posicao, self._fim)
        self._inicio_relogio = None
        self._tocar_som(None)

    def limpar(self) -> None:
        self.parar()
        for arquivo in self._pasta.glob("*"):
            arquivo.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self._pasta.rmdir()


class JanelaDoAjusteFino(ctk.CTkToplevel):
    """`ao_salvar(palavras)` recebe a lista acertada; `ao_desfazer()` (quando o Short já tem um
    ajuste) volta aos tempos da transcrição."""

    def __init__(
        self,
        master,
        *,
        corte: Corte,
        palavras: list[Palavra],
        fonte: Path | None,
        ao_salvar: Callable[[list[PalavraAjustada]], None],
        ao_desfazer: Callable[[], None] | None = None,
        aviso_do_som: str = "",
        tocador: Tocador | None = None,
    ):
        super().__init__(master)
        self.title(f"Ajuste fino da legenda · {corte.titulo or 'Short'}")
        self.geometry("880x640")
        self.minsize(760, 520)
        self.corte = corte
        self.itens: list[list] = [[p.texto, p.inicio, p.fim] for p in palavras]
        self._originais = [list(i) for i in self.itens]
        self._ao_salvar, self._ao_desfazer = ao_salvar, ao_desfazer
        de = max(0.0, corte.inicio - FOLGA_S)
        self.tocador = tocador or Tocador(fonte, de, corte.fim + FOLGA_S)
        self._atual: int | None = None
        self._acompanhando: str | None = None
        self._var_seguintes = ctk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._montar_barra(aviso_do_som if fonte is None else "")
        self._montar_lista()
        self._montar_editor()
        self._montar_rodape()
        self._encher_lista()
        if self.itens:
            self.selecionar(0)
        self.protocol("WM_DELETE_WINDOW", self.cancelar)
        self.bind("<space>", self._espaco)
        self.after(50, self._trazer_para_frente)

    def _trazer_para_frente(self) -> None:
        with contextlib.suppress(TclError):
            self.lift()
            self.focus_force()

    # --- montagem ------------------------------------------------------------------------------
    def _montar_barra(self, aviso: str) -> None:
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self.botao_tocar = ctk.CTkButton(barra, text="▶ Tocar", width=110, command=self.alternar)
        self.botao_tocar.pack(side="left")
        dica(
            self.botao_tocar,
            "Toca o Short a partir da palavra escolhida, com a palavra que toca acesa na lista "
            "(barra de espaço também toca e para).",
        )
        self.velocidade = ctk.CTkSegmentedButton(
            barra, values=[_rotulo_da_velocidade(v) for v in VELOCIDADES],
            command=self._trocar_velocidade,
        )  # fmt: skip
        self.velocidade.set(_rotulo_da_velocidade(1.0))
        self.velocidade.pack(side="left", padx=10)
        dica_do_seletor(
            self.velocidade, "Mais devagar fica mais fácil ouvir onde cada palavra começa."
        )
        self.posicao = ctk.CTkLabel(barra, text="", text_color=_COR_SUAVE, width=110, anchor="w")
        self.posicao.pack(side="left")
        explicacao = (
            aviso
            or "Escolha uma palavra na lista, ouça e acerte o texto e os tempos. Nada muda no "
            "Short até você clicar em Salvar (e depois salvar o Studio)."
        )
        self.aviso = ctk.CTkLabel(
            self, text=explicacao, anchor="w", justify="left", wraplength=840,
            text_color=_COR_ALERTA if aviso else _COR_SUAVE,
        )  # fmt: skip
        self.aviso.grid(row=1, column=0, sticky="ew", padx=12)
        if self.tocador.fonte is None:
            self.botao_tocar.configure(state="disabled")
            self.velocidade.configure(state="disabled")

    def _montar_lista(self) -> None:
        quadro = ctk.CTkFrame(self, fg_color="transparent")
        quadro.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        quadro.columnconfigure(0, weight=1)
        quadro.rowconfigure(0, weight=1)
        colunas = ("n", "palavra", "inicio", "fim", "duracao")
        self.lista = ttk.Treeview(quadro, columns=colunas, show="headings", selectmode="browse")
        for coluna, titulo, largura in (
            ("n", "#", 50), ("palavra", "Palavra", 300), ("inicio", "Começa", 120),
            ("fim", "Termina", 120), ("duracao", "Dura", 90),
        ):  # fmt: skip
            self.lista.heading(coluna, text=titulo)
            self.lista.column(coluna, width=largura, anchor="w" if coluna == "palavra" else "e")
        self.lista.tag_configure("tocando", background="#2b6cb0", foreground="white")
        self.lista.tag_configure("mudou", foreground="#e0a030")
        rolagem = ttk.Scrollbar(quadro, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=rolagem.set)
        self.lista.grid(row=0, column=0, sticky="nsew")
        rolagem.grid(row=0, column=1, sticky="ns")
        self.lista.bind("<<TreeviewSelect>>", self._ao_escolher)
        self.lista.bind("<Double-1>", lambda _e: self.ouvir_a_palavra())

    def _montar_editor(self) -> None:
        editor = ctk.CTkFrame(self)
        editor.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 6))
        editor.columnconfigure(1, weight=1)
        ctk.CTkLabel(editor, text="Palavra", anchor="w", width=70).grid(
            row=0, column=0, sticky="w", padx=(10, 6), pady=(8, 4)
        )
        self.campo_texto = ctk.CTkEntry(editor)
        self.campo_texto.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(8, 4), padx=(0, 10))
        self.campo_texto.bind("<Return>", lambda _e: self._confirmar_texto())
        self.campo_texto.bind("<FocusOut>", lambda _e: self._confirmar_texto())
        dica(
            self.campo_texto,
            "O texto da palavra como aparece na legenda (Enter confirma). Com espaço vira duas "
            "palavras, que dividem o tempo; apagar tudo tira a palavra.",
        )
        self.campos_tempo: dict[str, ctk.CTkEntry] = {}
        for linha, (ponta, rotulo) in enumerate((("inicio", "Começa"), ("fim", "Termina")), 1):
            self._montar_ponta(editor, linha, ponta, rotulo)
        acoes = ctk.CTkFrame(editor, fg_color="transparent")
        acoes.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 8))
        botoes = (
            ("▶ Ouvir esta palavra", self.ouvir_a_palavra,
             "Toca só esta palavra, com um respiro antes e depois (duplo clique na lista também)."),
            ("＋ Palavra depois", self.acrescentar_depois,
             "Acrescenta uma palavra logo depois desta (a que a transcrição comeu). Ela ocupa o "
             "silêncio até a seguinte ou, sem silêncio, a segunda metade desta."),
            ("✕ Tirar a palavra", self.tirar,
             "Tira a palavra da legenda (o som do Short não muda)."),
        )  # fmt: skip
        for texto, comando, ajuda in botoes:
            botao = ctk.CTkButton(acoes, text=texto, width=150, command=comando)
            botao.pack(side="left", padx=(0, 8))
            dica(botao, ajuda)
        seguintes = ctk.CTkCheckBox(
            acoes, text="Mover as seguintes junto", variable=self._var_seguintes
        )
        seguintes.pack(side="left", padx=(8, 0))
        dica(
            seguintes,
            "Ligado, acertar o tempo de uma palavra empurra todas as de depois pelo mesmo tanto: "
            "para quando a legenda inteira fica adiantada ou atrasada a partir de um ponto.",
        )

    def _montar_ponta(self, pai, linha: int, ponta: str, rotulo: str) -> None:
        ctk.CTkLabel(pai, text=rotulo, anchor="w", width=70).grid(
            row=linha, column=0, sticky="w", padx=(10, 6), pady=3
        )
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=1, sticky="w", pady=3)
        for passo in PASSOS_S:
            botao = ctk.CTkButton(
                quadro, text=f"−{passo:g}".replace(".", ","), width=48,
                command=lambda p=-passo: self.mover(ponta, p),
            )  # fmt: skip
            botao.pack(side="left", padx=(0, 4))
            dica(botao, f"{rotulo} {passo:g} s antes.".replace(".", ","))
        campo = ctk.CTkEntry(quadro, width=100, justify="center")
        campo.pack(side="left", padx=4)
        campo.bind("<Return>", lambda _e: self._confirmar_tempo(ponta))
        campo.bind("<FocusOut>", lambda _e: self._confirmar_tempo(ponta))
        dica(campo, "O tempo no vídeo original (minutos:segundos,centésimos). Enter confirma.")
        self.campos_tempo[ponta] = campo
        for passo in reversed(PASSOS_S):
            botao = ctk.CTkButton(
                quadro, text=f"+{passo:g}".replace(".", ","), width=48,
                command=lambda p=passo: self.mover(ponta, p),
            )  # fmt: skip
            botao.pack(side="left", padx=(4, 0))
            dica(botao, f"{rotulo} {passo:g} s depois.".replace(".", ","))
        agora = ctk.CTkButton(
            quadro, text="⏱ Agora", width=80, command=lambda: self.marcar_agora(ponta)
        )
        agora.pack(side="left", padx=(12, 0))
        dica(
            agora,
            f"Com o som tocando: põe o “{rotulo.lower()}” desta palavra no instante que está "
            "tocando (descontado o tempo de reação).",
        )

    def _montar_rodape(self) -> None:
        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        salvar = ctk.CTkButton(rodape, text="Salvar", width=110, command=self.salvar)
        salvar.pack(side="right")
        dica(salvar, "Leva as palavras acertadas para o Short (vale quando você salvar o Studio).")
        cancelar = ctk.CTkButton(
            rodape, text="Cancelar", width=100, fg_color=("gray70", "gray30"),
            command=self.cancelar,
        )  # fmt: skip
        cancelar.pack(side="right", padx=8)
        dica(cancelar, "Fecha sem mudar nada.")
        if self._ao_desfazer is not None:
            desfazer = ctk.CTkButton(
                rodape, text="Desfazer o ajuste fino", width=170,
                fg_color=("gray70", "gray30"), command=self.desfazer,
            )  # fmt: skip
            desfazer.pack(side="left")
            dica(
                desfazer,
                "Volta os tempos das palavras aos da transcrição (o texto corrigido continua).",
            )

    # --- a lista -------------------------------------------------------------------------------
    def _valores(self, k: int) -> tuple:
        texto, inicio, fim = self.itens[k]
        return (
            k + 1,
            texto,
            _tempo(inicio),
            _tempo(fim),
            f"{fim - inicio:.2f} s".replace(".", ","),
        )

    def _etiquetas(self, k: int) -> tuple[str, ...]:
        original = self._originais[k] if k < len(self._originais) else None
        return () if original == self.itens[k] else ("mudou",)

    def _encher_lista(self) -> None:
        self.lista.delete(*self.lista.get_children())
        for k in range(len(self.itens)):
            self.lista.insert(
                "", "end", iid=str(k), values=self._valores(k), tags=self._etiquetas(k)
            )

    def _repintar(self, k: int) -> None:
        if 0 <= k < len(self.itens):
            tocando = "tocando" in self.lista.item(str(k), "tags")
            etiquetas = self._etiquetas(k) + (("tocando",) if tocando else ())
            self.lista.item(str(k), values=self._valores(k), tags=etiquetas)

    def selecionar(self, k: int) -> None:
        if not self.itens:
            self._atual = None
            return
        k = max(0, min(k, len(self.itens) - 1))
        self._atual = k
        self.lista.selection_set(str(k))
        self.lista.see(str(k))
        self._preencher_editor()

    def _ao_escolher(self, _evento=None) -> None:
        escolhidos = self.lista.selection()
        if escolhidos and int(escolhidos[0]) != self._atual:
            self._atual = int(escolhidos[0])
            self._preencher_editor()

    def _preencher_editor(self) -> None:
        if self._atual is None:
            return
        texto, inicio, fim = self.itens[self._atual]
        self.campo_texto.delete(0, "end")
        self.campo_texto.insert(0, texto)
        for ponta, valor in (("inicio", inicio), ("fim", fim)):
            self.campos_tempo[ponta].delete(0, "end")
            self.campos_tempo[ponta].insert(0, _tempo(valor))

    # --- edição --------------------------------------------------------------------------------
    def _confirmar_texto(self) -> None:
        if self._atual is None:
            return
        texto = " ".join(self.campo_texto.get().split())
        if texto != self.itens[self._atual][0]:
            self.itens[self._atual][0] = texto
            self._repintar(self._atual)

    def _confirmar_tempo(self, ponta: str) -> None:
        if self._atual is None:
            return
        try:
            valor = ler_tempo(self.campos_tempo[ponta].get().strip())
        except ValueError:
            self._preencher_editor()
            return
        indice = 1 if ponta == "inicio" else 2
        self.mover(ponta, valor - self.itens[self._atual][indice])

    def mover(self, ponta: str, delta: float) -> None:
        """Anda o início ou o fim da palavra escolhida `delta` segundos (com as seguintes junto,
        se a caixa está ligada)."""
        if self._atual is None or not delta:
            return
        self._confirmar_texto()
        k = self._atual
        item = self.itens[k]
        if self._var_seguintes.get():
            # pelo início, a palavra inteira anda (com a mesma duração); pelo fim, só o fim dela.
            # Em ambos, todas as de depois andam o mesmo tanto
            if ponta == "inicio":
                item[1] += delta
            item[2] += delta
            for seguinte in self.itens[k + 1 :]:
                seguinte[1] += delta
                seguinte[2] += delta
        elif ponta == "inicio":
            item[1] = min(item[1] + delta, item[2] - DURACAO_MINIMA_DA_PALAVRA_S)
        else:
            item[2] = max(item[2] + delta, item[1] + DURACAO_MINIMA_DA_PALAVRA_S)
        limite_de, limite_ate = self.corte.inicio, self.corte.fim
        for outro in self.itens[k:]:
            outro[1] = min(max(outro[1], limite_de), limite_ate)
            outro[2] = min(max(outro[2], outro[1]), limite_ate)
        for j in range(k, len(self.itens) if self._var_seguintes.get() else k + 1):
            self._repintar(j)
        self._preencher_editor()

    def marcar_agora(self, ponta: str) -> None:
        if self._atual is None or not self.tocador.tocando:
            return
        agora = self.tocador.posicao - _RECUO_DO_AGORA_S * self.tocador.velocidade
        indice = 1 if ponta == "inicio" else 2
        self.mover(ponta, agora - self.itens[self._atual][indice])

    def acrescentar_depois(self) -> None:
        if self._atual is None:
            return
        self._confirmar_texto()
        k = self._atual
        _texto, inicio, fim = self.itens[k]
        seguinte = self.itens[k + 1][1] if k + 1 < len(self.itens) else self.corte.fim
        if seguinte - fim >= 0.15:  # cabe no silêncio até a seguinte
            novo = ["…", fim, min(seguinte, fim + 0.6)]
        else:  # divide esta palavra ao meio
            meio = (inicio + fim) / 2
            self.itens[k][2] = meio
            novo = ["…", meio, fim]
        self.itens.insert(k + 1, novo)
        self._originais.insert(k + 1, None)
        self._encher_lista()
        self.selecionar(k + 1)
        self.campo_texto.focus_set()
        self.campo_texto.select_range(0, "end")

    def tirar(self) -> None:
        if self._atual is None or len(self.itens) <= 1:
            return
        k = self._atual
        del self.itens[k]
        del self._originais[k]
        self._encher_lista()
        self.selecionar(min(k, len(self.itens) - 1))

    # --- som -----------------------------------------------------------------------------------
    def _espaco(self, evento) -> str | None:
        if evento.widget in (self.campo_texto, *self.campos_tempo.values()) or str(
            evento.widget
        ).startswith(str(self.campo_texto)):
            return None  # o espaço do campo de texto é espaço
        self.alternar()
        return "break"

    def alternar(self) -> None:
        if self.tocador.tocando:
            self.parar()
        else:
            inicio = self.itens[self._atual][1] if self._atual is not None else self.corte.inicio
            self._tocar(inicio - 0.3)

    def ouvir_a_palavra(self) -> None:
        if self._atual is None:
            return
        _texto, inicio, fim = self.itens[self._atual]
        self._tocar(inicio - FOLGA_DA_PALAVRA_S, fim + FOLGA_DA_PALAVRA_S, seguir=False)

    def _tocar(self, desde: float, ate: float | None = None, *, seguir: bool = True) -> None:
        try:
            self.tocador.tocar(desde, ate)
        except Exception as erro:  # o FFmpeg ou o arquivo de som
            self.aviso.configure(text=f"Não deu para tocar o som: {erro}", text_color=_COR_ALERTA)
            return
        self.botao_tocar.configure(text="⏸ Parar")
        self._seguir = seguir
        if self._acompanhando is None:
            self._acompanhar()

    def parar(self) -> None:
        self.tocador.parar()
        self._parou()

    def _parou(self) -> None:
        if self._acompanhando is not None:
            with contextlib.suppress(TclError):
                self.after_cancel(self._acompanhando)
            self._acompanhando = None
        with contextlib.suppress(TclError):
            self.botao_tocar.configure(text="▶ Tocar")
            for iid in self.lista.tag_has("tocando"):
                self._tirar_tocando(int(iid))

    def _tirar_tocando(self, k: int) -> None:
        self.lista.item(str(k), tags=self._etiquetas(k))

    def palavra_em(self, segundos: float) -> int | None:
        inicios = [item[1] for item in self.itens]
        k = bisect_right(inicios, segundos) - 1
        if k >= 0 and segundos <= self.itens[k][2] + 0.05:
            return k
        return None

    def _acompanhar(self) -> None:
        self._acompanhando = None
        if not self.tocador.tocando:
            self._parou()
            return
        posicao = self.tocador.posicao
        self.posicao.configure(text=_tempo(posicao))
        k = self.palavra_em(posicao)
        acesas = [int(i) for i in self.lista.tag_has("tocando")]
        for j in acesas:
            if j != k:
                self._tirar_tocando(j)
        if k is not None and k not in acesas:
            self.lista.item(str(k), tags=(*self._etiquetas(k), "tocando"))
            if getattr(self, "_seguir", True):
                self.lista.see(str(k))
        self._acompanhando = self.after(_ATUALIZAR_MS, self._acompanhar)

    def _trocar_velocidade(self, rotulo: str) -> None:
        tocava = self.tocador.tocando
        posicao = self.tocador.posicao
        self.tocador.parar()
        self.tocador.velocidade = next(v for v in VELOCIDADES if _rotulo_da_velocidade(v) == rotulo)
        if tocava:
            self._tocar(posicao)

    # --- sair ----------------------------------------------------------------------------------
    @property
    def mudou(self) -> bool:
        return self.itens != self._originais

    def _fechar(self) -> None:
        self.parar()
        self.tocador.limpar()
        self.destroy()

    def salvar(self) -> None:
        self._confirmar_texto()
        palavras = [PalavraAjustada(t, i, f) for t, i, f in self.itens if t.strip()]
        if not palavras:
            dialogos.aviso(self, "Ajuste fino", "A legenda ficaria sem nenhuma palavra.")
            return
        self._ao_salvar(palavras)
        self._fechar()

    def cancelar(self) -> None:
        self._confirmar_texto()
        if self.mudou and not dialogos.confirmar(
            self, "Ajuste fino", "Fechar sem levar as mudanças para o Short?",
            sim="Fechar sem salvar", nao="Voltar",
        ):  # fmt: skip
            return
        self._fechar()

    def desfazer(self) -> None:
        if self._ao_desfazer is None or not dialogos.confirmar(
            self, "Desfazer o ajuste fino",
            "Voltar os tempos das palavras deste Short aos da transcrição? O texto corrigido "
            "continua.", sim="Desfazer", nao="Voltar",
        ):  # fmt: skip
            return
        self._ao_desfazer()
        self._fechar()


def _rotulo_da_velocidade(v: float) -> str:
    return f"{v:g}×".replace(".", ",")
