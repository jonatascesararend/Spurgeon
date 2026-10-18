"""Aba "Shorts livre" do Studio: escolher a dedo um trecho do sermão para virar Short.

À esquerda está o texto inteiro da transcrição (uma frase por linha, com a hora); à direita, a
busca (por palavras parecidas ou por tema) e o trecho escolhido. A pessoa marca o começo e o fim
(arrastando o mouse no texto, ou clicando numa palavra e usando "Começar aqui" / "Terminar aqui"),
afina com os botões de palavra e de frase e manda criar: a IA avalia o trecho, dá a nota e escreve
título, descrição e hashtags, e o corte vai para a lista do Studio como "gerado manualmente".
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from tkinter import TclError

import customtkinter as ctk

from spurgeon.audio.nivel import carregar_niveis
from spurgeon.core.erros import Cancelado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts.analise import palavras_da_janela, separar_frases
from spurgeon.shorts.busca import (
    Achado,
    carregar_modelo_de_vetores,
    procurar_palavras,
    procurar_tema,
)
from spurgeon.shorts.dados import Corte, Frase
from spurgeon.shorts.etapas import nome_do_sermao
from spurgeon.shorts.livre import (
    ContextoDaIA,
    Trecho,
    duracao_do_trecho,
    gerar_corte_livre,
    mover_ponta,
    preparar_cliente,
    problema_do_trecho,
    proximo_id,
    resumo_das_pontas,
)
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo
from spurgeon.ui import dialogos
from spurgeon.ui.assincrono import Tarefas

_COR_SUAVE = "gray60"
_COR_OK = "#4caf72"
_COR_ALERTA = "#e0a030"
_COR_ERRO = "#e05555"
BUSCA_PALAVRAS = "Palavras parecidas"
BUSCA_TEMA = "Tema"
_AJUDA = {
    BUSCA_PALAVRAS: "Acha as frases com palavras parecidas, mesmo com erro de digitação, sem "
    "acento ou em outra forma (perdoar, perdoou…).",
    BUSCA_TEMA: "Acha trechos que falam do assunto, mesmo sem a palavra exata (perdão acha "
    "misericórdia e culpa). É uma aproximação: confira lendo.",
}


class PainelShortsLivre(ctk.CTkFrame):
    def __init__(
        self,
        master,
        pastas: PastasProjeto,
        *,
        tarefas: Tarefas,
        contexto_da_ia: Callable[[], ContextoDaIA | None],
        cortes_existentes: Callable[[], Sequence[Corte]],
        ao_criar: Callable[[Corte], None],
        janela_do_video: Callable[[], tuple[float, float | None]] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._pastas = pastas
        self._tarefas = tarefas
        self._contexto_da_ia = contexto_da_ia
        self._cortes_existentes = cortes_existentes
        self._ao_criar = ao_criar
        self._janela_do_video = janela_do_video
        self._palavras: list[Palavra] = []
        self._frases: list[Frase] = []
        self._posicoes: list[
            tuple[int, int, int]
        ] = []  # por palavra: linha, coluna inicial e final
        self._primeira: int | None = None
        self._ultima: int | None = None
        self._clique: int | None = None  # a última palavra em que a pessoa clicou
        self._achados: list[Achado] = []
        self._achado: Achado | None = None
        self._nlp = None
        self._criando = False
        self._andamento = {"texto": ""}
        self._bloqueado: str | None = None
        self._botoes_do_trecho: list[ctk.CTkButton] = []

        self._montar()
        self.carregar()

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.grid(row=0, column=0, sticky="nsew")
        self._corpo = corpo
        corpo.columnconfigure(0, weight=3, minsize=380)
        corpo.columnconfigure(1, weight=2, minsize=340)
        corpo.rowconfigure(2, weight=1)

        self._intro = ctk.CTkLabel(
            corpo,
            anchor="w",
            justify="left",
            wraplength=1000,
            text="Escolha você mesmo onde um Short começa e termina. Procure uma palavra ou um "
            "assunto, ou role o texto; marque o começo e o fim arrastando o mouse sobre as "
            "palavras. Ao criar, a IA dá a nota e escreve título, descrição e hashtags, como nos "
            "cortes automáticos.",
        )
        self._intro.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        corpo.bind(
            "<Configure>", lambda e: self._intro.configure(wraplength=max(300, e.width - 40))
        )

        topo = ctk.CTkFrame(corpo, fg_color="transparent")
        topo.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))
        busca = ctk.CTkFrame(topo, fg_color="transparent")
        busca.pack(fill="x")
        self._var_busca = ctk.StringVar()
        entrada = ctk.CTkEntry(
            busca,
            textvariable=self._var_busca,
            width=300,
            placeholder_text="Palavra, frase ou tema",
        )
        entrada.pack(side="left")
        entrada.bind("<Return>", lambda _e: self.buscar())
        self._modo = ctk.CTkSegmentedButton(
            busca, values=[BUSCA_PALAVRAS, BUSCA_TEMA], command=lambda _v: self._mudou_modo()
        )
        self._modo.set(BUSCA_PALAVRAS)
        self._modo.pack(side="left", padx=8)
        ctk.CTkButton(busca, text="Buscar", width=80, command=self.buscar).pack(side="left")
        self._situacao_busca = ctk.CTkLabel(busca, text="", text_color=_COR_SUAVE, anchor="w")
        self._situacao_busca.pack(side="left", padx=10)
        self._ajuda_da_busca = ctk.CTkLabel(
            topo, text=_AJUDA[BUSCA_PALAVRAS], text_color=_COR_SUAVE, anchor="w",
            font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._ajuda_da_busca.pack(anchor="w", pady=(3, 0))

        self._caixa = ctk.CTkTextbox(
            corpo, wrap="word", font=ctk.CTkFont(size=14), state="disabled"
        )
        self._caixa.grid(row=2, column=0, sticky="nsew", padx=(14, 7), pady=(0, 6))
        texto = self._caixa._textbox
        texto.tag_config("hora", foreground="#7f7f7f", font=("Consolas", 10))
        texto.tag_config("achado", background="#5a4a12")
        texto.tag_config("palavra_achada", background="#9a7b10", foreground="#ffffff")
        texto.tag_config("escolhido", background="#1f4f7f", foreground="#ffffff")
        texto.tag_raise("escolhido")
        texto.bind("<ButtonRelease-1>", self._soltou_o_mouse)

        lado = ctk.CTkFrame(corpo, fg_color="transparent")
        lado.grid(row=2, column=1, sticky="nsew", padx=(7, 14), pady=(0, 6))
        lado.columnconfigure(0, weight=1)
        lado.rowconfigure(1, weight=1)
        ctk.CTkLabel(
            lado, text="Resultados da busca", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="ew")
        self._lista = ctk.CTkScrollableFrame(lado, height=170)
        self._lista.grid(row=1, column=0, sticky="nsew", pady=(2, 4))
        self._lista.columnconfigure(0, weight=1)
        linha_achado = ctk.CTkFrame(lado, fg_color="transparent")
        linha_achado.grid(row=2, column=0, sticky="ew")
        self._botao_comecar_no_achado = ctk.CTkButton(
            linha_achado, text="↥ Começar neste achado", width=150, height=26,
            fg_color=("gray70", "gray30"), command=self._comecar_no_achado, state="disabled",
        )  # fmt: skip
        self._botao_comecar_no_achado.pack(side="left")
        self._botao_usar_achado = ctk.CTkButton(
            linha_achado, text="Usar o achado todo", width=130, height=26,
            fg_color=("gray70", "gray30"), command=self._usar_o_achado, state="disabled",
        )  # fmt: skip
        self._botao_usar_achado.pack(side="left", padx=6)

        self._montar_trecho(lado, 3)

    def _montar_trecho(self, lado, linha: int) -> None:
        quadro = ctk.CTkFrame(lado)
        quadro.grid(row=linha, column=0, sticky="ew", pady=(10, 0))
        quadro.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            quadro, text="Trecho escolhido", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        self._rotulo_trecho = ctk.CTkLabel(
            quadro, anchor="w", justify="left", wraplength=320, text=""
        )
        self._rotulo_trecho.grid(row=1, column=0, sticky="ew", padx=10)
        self._rotulo_pontas = ctk.CTkLabel(
            quadro, anchor="w", justify="left", wraplength=320, text="", text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._rotulo_pontas.grid(row=2, column=0, sticky="ew", padx=10)

        marcar = ctk.CTkFrame(quadro, fg_color="transparent")
        marcar.grid(row=3, column=0, sticky="ew", padx=10, pady=(6, 0))
        for texto, comando in (
            ("↥ Começar aqui", self._marcar_inicio),
            ("↧ Terminar aqui", self._marcar_fim),
        ):
            ctk.CTkButton(marcar, text=texto, width=120, height=26, command=comando).pack(
                side="left", padx=(0, 6)
            )
        ctk.CTkLabel(
            quadro,
            text="Clique numa palavra do texto e use um dos botões, ou arraste o mouse sobre o "
            "trecho todo.",
            anchor="w", justify="left", wraplength=320, text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        ).grid(row=4, column=0, sticky="ew", padx=10)  # fmt: skip

        for n, (ponta, rotulos) in enumerate(
            (
                ("inicio", ("−palavra ▶", "−frase ▶", "◀ +frase", "◀ +palavra")),
                ("fim", ("◀ −palavra", "◀ −frase", "+frase ▶", "+palavra ▶")),
            )
        ):
            titulo = "Começo" if ponta == "inicio" else "Fim"
            linha_ponta = ctk.CTkFrame(quadro, fg_color="transparent")
            linha_ponta.grid(row=5 + n, column=0, sticky="ew", padx=10, pady=(4, 0))
            ctk.CTkLabel(linha_ponta, text=titulo, width=48, anchor="w").pack(side="left")
            sinal = 1 if ponta == "inicio" else -1  # o que "tirar" faz com o número da palavra
            comandos = (
                lambda p=ponta, s=sinal: self._mover(p, s, False),
                lambda p=ponta, s=sinal: self._mover(p, s, True),
                lambda p=ponta, s=sinal: self._mover(p, -s, True),
                lambda p=ponta, s=sinal: self._mover(p, -s, False),
            )
            for rotulo, comando in zip(rotulos, comandos, strict=True):
                botao = ctk.CTkButton(
                    linha_ponta, text=rotulo, width=70, height=24, fg_color=("gray70", "gray30"),
                    command=comando, state="disabled",
                )  # fmt: skip
                botao.pack(side="left", padx=(0, 3))
                self._botoes_do_trecho.append(botao)

        acoes = ctk.CTkFrame(quadro, fg_color="transparent")
        acoes.grid(row=7, column=0, sticky="ew", padx=10, pady=(10, 4))
        self._botao_criar = ctk.CTkButton(
            acoes, text="Criar Short deste trecho", height=34, font=ctk.CTkFont(weight="bold"),
            command=self.criar, state="disabled",
        )  # fmt: skip
        self._botao_criar.pack(side="left")
        ctk.CTkButton(
            acoes, text="Limpar", width=70, height=34, fg_color=("gray70", "gray30"),
            command=self.limpar_trecho,
        ).pack(side="left", padx=6)  # fmt: skip
        self._situacao = ctk.CTkLabel(
            quadro, anchor="w", justify="left", wraplength=320, text="", text_color=_COR_SUAVE
        )
        self._situacao.grid(row=8, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._aviso_bloqueio = ctk.CTkLabel(
            self, text="", wraplength=700, justify="center", text_color=_COR_ALERTA
        )

    # --- carga e texto ------------------------------------------------------------------------
    def carregar(self) -> None:
        """Lê a transcrição do projeto e põe o texto inteiro na caixa."""
        self._palavras = (
            Transcricao.carregar(self._pastas.transcricao).palavras
            if self._pastas.transcricao.exists()
            else []
        )
        if self._janela_do_video is not None:  # só a fala do trecho do vídeo que o projeto usa
            inicio, fim = self._janela_do_video()
            self._palavras = palavras_da_janela(self._palavras, inicio, fim)
        self._frases = separar_frases(self._palavras) if self._palavras else []
        self.limpar_trecho()
        self._preencher_texto()

    def _preencher_texto(self) -> None:
        texto = self._caixa._textbox
        texto.configure(state="normal")
        texto.delete("1.0", "end")
        self._posicoes = [(0, 0, 0)] * len(self._palavras)
        if not self._frases:
            texto.insert("1.0", "Este projeto ainda não tem a transcrição do sermão.")
        for numero, frase in enumerate(self._frases, start=1):
            hora = f"{segundos_para_rotulo(frase.inicio)}  "
            texto.insert("end", hora, "hora")
            coluna = len(hora)
            partes = []
            for k in range(frase.primeira, frase.ultima + 1):
                palavra = self._palavras[k].texto
                self._posicoes[k] = (numero, coluna, coluna + len(palavra))
                partes.append(palavra)
                coluna += len(palavra) + 1
            texto.insert("end", " ".join(partes) + "\n")
        texto.configure(state="disabled")

    def _indice_da_palavra(self, k: int, *, fim: bool = False) -> str:
        linha, de, ate = self._posicoes[k]
        return f"{linha}.{ate if fim else de}"

    def _palavra_no_indice(self, indice: str) -> int | None:
        """A palavra sob uma posição do texto (a mais próxima, se caiu na hora ou no vão)."""
        if not self._frases:
            return None
        linha, coluna = (int(x) for x in indice.split("."))
        linha = max(1, min(len(self._frases), linha))
        frase = self._frases[linha - 1]
        for k in range(frase.primeira, frase.ultima + 1):
            if coluna <= self._posicoes[k][2]:
                return k
        return frase.ultima

    # --- marcar o trecho ----------------------------------------------------------------------
    def _soltou_o_mouse(self, evento) -> None:
        texto = self._caixa._textbox
        clicada = self._palavra_no_indice(texto.index(f"@{evento.x},{evento.y}"))
        selecionado = None
        with contextlib.suppress(TclError):
            selecionado = (texto.index("sel.first"), texto.index("sel.last"))
        if selecionado is None:
            self._clique = clicada
            return
        texto.tag_remove("sel", "1.0", "end")
        primeira = self._palavra_no_indice(selecionado[0])
        ultima = self._palavra_no_indice(texto.index(f"{selecionado[1]}-1c"))
        if primeira is None or ultima is None or primeira >= ultima:
            # um duplo clique seleciona uma palavra: vale como clique nela
            self._clique = primeira if primeira is not None else clicada
            return
        self._clique = primeira
        self._primeira, self._ultima = primeira, ultima
        self._atualizar_trecho()

    def _marcar_inicio(self) -> None:
        if self._clique is None:
            self._situacao.configure(
                text="Clique primeiro numa palavra do texto.", text_color=_COR_ALERTA
            )
            return
        self._primeira = self._clique
        if self._ultima is not None and self._ultima < self._primeira:
            self._ultima = None
        self._atualizar_trecho()

    def _marcar_fim(self) -> None:
        if self._clique is None:
            self._situacao.configure(
                text="Clique primeiro numa palavra do texto.", text_color=_COR_ALERTA
            )
            return
        self._ultima = self._clique
        if self._primeira is not None and self._primeira > self._ultima:
            self._primeira = None
        self._atualizar_trecho()

    def limpar_trecho(self) -> None:
        self._primeira = self._ultima = None
        self._atualizar_trecho()

    def trecho(self) -> Trecho | None:
        if self._primeira is None or self._ultima is None or self._primeira > self._ultima:
            return None
        return Trecho(self._primeira, self._ultima)

    def definir_trecho(self, primeira: int, ultima: int) -> None:
        self._primeira, self._ultima = primeira, ultima
        self._atualizar_trecho()

    def _mover(self, ponta: str, sentido: int, por_frase: bool) -> None:
        atual = self.trecho()
        if atual is None:
            return
        novo = mover_ponta(self._palavras, self._frases, atual, ponta, sentido, por_frase=por_frase)
        self._primeira, self._ultima = novo.primeira, novo.ultima
        self._atualizar_trecho(rolar_para=ponta)

    def _atualizar_trecho(self, rolar_para: str | None = None) -> None:
        texto = self._caixa._textbox
        texto.tag_remove("escolhido", "1.0", "end")
        trecho = self.trecho()
        for botao in self._botoes_do_trecho:
            botao.configure(state="normal" if trecho and not self._criando else "disabled")
        if not self._palavras:
            self._rotulo_trecho.configure(text="")
            self._rotulo_pontas.configure(text="")
            self._botao_criar.configure(state="disabled")
            return
        if trecho is None:
            if self._primeira is not None:
                texto.tag_add(
                    "escolhido",
                    self._indice_da_palavra(self._primeira),
                    self._indice_da_palavra(self._primeira, fim=True),
                )
            falta = "o fim" if self._primeira is not None else "o começo e o fim"
            self._rotulo_trecho.configure(
                text=f"Marque {falta} do trecho no texto.", text_color=_COR_SUAVE
            )
            self._rotulo_pontas.configure(text="")
            self._botao_criar.configure(state="disabled")
            return
        texto.tag_add(
            "escolhido",
            self._indice_da_palavra(trecho.primeira),
            self._indice_da_palavra(trecho.ultima, fim=True),
        )
        problema = problema_do_trecho(self._palavras, trecho)
        duracao = duracao_do_trecho(self._palavras, trecho)
        inicio = self._palavras[trecho.primeira].inicio
        fim = self._palavras[trecho.ultima].fim
        resumo = (
            f"{segundos_para_rotulo(inicio)} a {segundos_para_rotulo(fim)} · {duracao:.0f} s · "
            f"{trecho.ultima - trecho.primeira + 1} palavras"
        )
        self._rotulo_trecho.configure(
            text=resumo + (f"\n⚠ {problema}" if problema else ""),
            text_color=_COR_ALERTA if problema else _COR_OK,
        )
        self._rotulo_pontas.configure(text=resumo_das_pontas(self._palavras, trecho))
        self._botao_criar.configure(
            state="normal"
            if problema is None and not self._criando and not self._bloqueado
            else "disabled"
        )
        if rolar_para:
            alvo = trecho.primeira if rolar_para == "inicio" else trecho.ultima
            texto.see(self._indice_da_palavra(alvo))

    # --- busca --------------------------------------------------------------------------------
    def _mudou_modo(self) -> None:
        self._ajuda_da_busca.configure(text=_AJUDA[self._modo.get()])
        if self._var_busca.get().strip():
            self.buscar()

    def buscar(self) -> None:
        consulta = self._var_busca.get().strip()
        if not consulta:
            self._situacao_busca.configure(text="Digite o que procurar.", text_color=_COR_SUAVE)
            return
        if not self._frases:
            return
        if self._modo.get() == BUSCA_PALAVRAS:
            self._mostrar_achados(procurar_palavras(self._palavras, self._frases, consulta))
            return
        self._situacao_busca.configure(
            text="Procurando pelo assunto…" if self._nlp else "Carregando o modelo de português…",
            text_color=_COR_SUAVE,
        )

        def trabalho() -> list[Achado]:
            if self._nlp is None:
                self._nlp = carregar_modelo_de_vetores()
            return procurar_tema(self._palavras, self._frases, consulta, self._nlp)

        self._tarefas.rodar(
            trabalho, self._mostrar_achados, self._busca_falhou, chave="livre_busca"
        )

    def _busca_falhou(self, erro: Exception) -> None:
        self._situacao_busca.configure(
            text=f"A busca por tema não funcionou: {erro}", text_color=_COR_ERRO
        )

    def _mostrar_achados(self, achados: list[Achado]) -> None:
        if not self.winfo_exists():
            return
        self._achados, self._achado = achados, None
        for filho in self._lista.winfo_children():
            filho.destroy()
        texto = self._caixa._textbox
        texto.tag_remove("achado", "1.0", "end")
        texto.tag_remove("palavra_achada", "1.0", "end")
        self._botao_comecar_no_achado.configure(state="disabled")
        self._botao_usar_achado.configure(state="disabled")
        if not achados:
            self._situacao_busca.configure(text="Nada encontrado.", text_color=_COR_ALERTA)
            return
        self._situacao_busca.configure(text=f"{len(achados)} achados.", text_color=_COR_SUAVE)
        for posicao, achado in enumerate(achados):
            resumo = " ".join(achado.texto.split())
            if len(resumo) > 110:
                resumo = resumo[:109].rstrip() + "…"
            botao = ctk.CTkButton(
                self._lista,
                anchor="w",
                height=40,
                fg_color=("gray86", "gray20"),
                text_color=("gray10", "gray90"),
                hover_color=("gray78", "gray28"),
                text=f"{segundos_para_rotulo(achado.inicio)} · {achado.pontos:g}\n{resumo}",
                command=lambda a=achado: self._abrir_achado(a),
            )
            botao.grid(row=posicao, column=0, sticky="ew", pady=1)
        self._abrir_achado(achados[0])

    def _abrir_achado(self, achado: Achado) -> None:
        """Leva o texto até o achado, destacando a frase (e as palavras que casaram)."""
        self._achado = achado
        texto = self._caixa._textbox
        texto.tag_remove("achado", "1.0", "end")
        texto.tag_remove("palavra_achada", "1.0", "end")
        texto.tag_add(
            "achado",
            self._indice_da_palavra(achado.primeira),
            self._indice_da_palavra(achado.ultima, fim=True),
        )
        for k in achado.palavras:
            texto.tag_add(
                "palavra_achada", self._indice_da_palavra(k), self._indice_da_palavra(k, fim=True)
            )
        texto.see(self._indice_da_palavra(achado.primeira))
        self._botao_comecar_no_achado.configure(state="normal")
        self._botao_usar_achado.configure(state="normal")

    def _comecar_no_achado(self) -> None:
        if self._achado is None:
            return
        self._primeira = self._achado.primeira
        if self._ultima is not None and self._ultima < self._primeira:
            self._ultima = None
        self._atualizar_trecho()

    def _usar_o_achado(self) -> None:
        if self._achado is not None:
            self.definir_trecho(self._achado.primeira, self._achado.ultima)

    # --- criar o Short ------------------------------------------------------------------------
    def criar(self) -> None:
        """Manda a IA avaliar o trecho e criar título, descrição e hashtags (em segundo plano)."""
        trecho = self.trecho()
        janela = self.winfo_toplevel()
        if (problema := problema_do_trecho(self._palavras, trecho)) is not None:
            dialogos.aviso(janela, "Shorts livre", problema)
            return
        ia = self._contexto_da_ia()
        if ia is None:
            dialogos.aviso(
                janela,
                "Shorts livre",
                "Confira as opções da janela (o modelo do Ollama está nas opções avançadas): "
                "há um campo incompleto.",
            )
            return
        existentes = list(self._cortes_existentes())
        id_ = proximo_id(existentes)
        titulos = [c.titulo for c in existentes if c.titulo]
        nomes = [c.slug for c in existentes if c.slug]
        palavras, frases = list(self._palavras), list(self._frases)
        sermao = nome_do_sermao(self._pastas.raiz)
        pastas = self._pastas
        andamento = self._andamento
        andamento["texto"] = "Abrindo o Ollama…"

        def trabalho() -> Corte:
            cliente = preparar_cliente(ia.modelo, threads=ia.threads, usar_gpu=ia.usar_gpu)
            niveis = None
            if pastas.niveis.exists():
                medido = carregar_niveis(pastas.niveis)
                niveis = (medido.medidas, medido.limiar_db)
            try:
                return gerar_corte_livre(
                    cliente, palavras=palavras, frases=frases, trecho=trecho, id_=id_,
                    sermao=sermao, titulos_usados=titulos, nomes_usados=nomes, niveis=niveis,
                    ao_andamento=lambda texto: andamento.__setitem__("texto", texto),
                )  # fmt: skip
            finally:
                cliente.descarregar()  # a renderização precisa da memória que a IA ocupa

        self._criando = True
        self._atualizar_trecho()
        self._botao_criar.configure(state="disabled", text="Criando…")
        self.after(400, self._acompanhar)
        self._tarefas.rodar(trabalho, self._criado, self._falhou, chave="livre_criar")

    def _acompanhar(self) -> None:
        if not self._criando:
            return
        with contextlib.suppress(TclError):
            self._situacao.configure(
                text=f"{self._andamento['texto']} (leva cerca de 2 minutos na CPU)",
                text_color=_COR_SUAVE,
            )
            self.after(400, self._acompanhar)

    def _terminou(self) -> None:
        self._criando = False
        with contextlib.suppress(TclError):
            self._botao_criar.configure(text="Criar Short deste trecho")
            self._atualizar_trecho()

    def _criado(self, corte: Corte) -> None:
        if not self.winfo_exists():
            return
        self._terminou()
        self._situacao.configure(
            text=f"Short criado: “{corte.titulo}” (nota {corte.nota:.0f}). Ele já está na lista "
            "de cortes, marcado para renderizar.",
            text_color=_COR_OK,
        )
        self.limpar_trecho()
        self._ao_criar(corte)

    def _falhou(self, erro: Exception) -> None:
        if not self.winfo_exists() or isinstance(erro, Cancelado):
            return
        self._terminou()
        self._situacao.configure(text="Não foi possível criar o Short.", text_color=_COR_ERRO)
        dialogos.erro(
            self.winfo_toplevel(), "Shorts livre", f"Não foi possível criar o Short: {erro}"
        )

    # --- bloqueio -----------------------------------------------------------------------------
    def bloquear(self, motivo: str | None) -> None:
        """Esconde o painel e mostra o motivo (processamento em andamento); `None` libera."""
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
            self._atualizar_trecho()
