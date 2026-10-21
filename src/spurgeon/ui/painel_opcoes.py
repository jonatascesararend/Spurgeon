"""Os cartões de opções: O que gerar, Corte e enquadramento, Revisão, Processamento e Avançado."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from spurgeon.core.config import INFO_MODELOS_WHISPER, PRESETS_X264, Pastas
from spurgeon.ui import dicas
from spurgeon.ui.ajuda import CabecalhoDeEtapa, dica_do_seletor
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.formulario import (
    DICAS_MODO,
    ROTULO_USO_75,
    ROTULO_USO_100,
    ROTULOS_FADE,
    ROTULOS_MODO,
    ValoresFormulario,
    aviso_qualidade,
    aviso_whisper,
    calcular_habilitacoes,
    rotulos_ollama,
    rotulos_qualidade,
    rotulos_whisper,
)
from spurgeon.ui.painel_marca import PainelMarca
from spurgeon.ui.painel_tratamento import PainelTratamento
from spurgeon.ui.presets import RepositorioDePresets
from spurgeon.ui.quadros import FonteDeQuadros

_COR_DICA = "gray60"
_COR_AVISO = "#e0a030"

#: os nomes dos botões que trocam de página, na ordem das páginas
NOMES_DAS_PAGINAS = ("1 · Vídeo e geração", "2 · Corte e imagem", "3 · Revisão e opções")

#: o que o "?" de cada cartão explica (o do cartão 1 é do `PainelEntrada`, os do 4 e do 5 dos
#: painéis de tratamento e de marca)
AJUDAS_DOS_CARTOES = {
    "gerar": (
        "Escolha o que o programa vai produzir. “Tudo” processa um sermão novo; “Só Shorts” e "
        "“Só vinhetas” reaproveitam um projeto já feito. Em “Tudo”, marque o que quer: limpar o "
        "áudio, o vídeo completo sem silêncios com legendas, a introdução e a vinheta, e os "
        "Shorts com legendas dinâmicas."
    ),
    "ajustes": (
        "Ajustes da imagem antes de renderizar: cortar o começo e o fim da gravação, aproximar a "
        "câmera (zoom) e acertar o zoom e a legenda dos Shorts. Clique no título de cada seção "
        "para abri-la. Tudo é opcional: se a gravação já está boa, deixe como está."
    ),
    "revisao": (
        "Marque onde o processamento deve parar para você conferir o trabalho: depois da "
        "transcrição, para corrigir o texto, e antes de renderizar os Shorts, para escolher os "
        "cortes. É mais cuidadoso e um pouco mais demorado; desmarque para processar tudo de uma "
        "vez."
    ),
    "processamento": (
        "Como o computador vai trabalhar: quanto do processador usar, qual modelo transcreve a "
        "fala (os maiores acertam mais, mas demoram) e a qualidade do vídeo final. As opções "
        "recomendadas para este PC já vêm marcadas."
    ),
    "avancado": (
        "Ajustes finos que quase nunca precisam mudar: pausa residual, duração e nota dos Shorts, "
        "modelo de IA, deslocamento do recorte, qualidade personalizada e uso do disco. Na "
        "dúvida, não mexa."
    ),
}
BALOES_DO_MODO = {
    "Tudo": (
        "Faz o processamento completo de um sermão novo: áudio, transcrição, vídeo sem "
        "silêncios, Shorts e vinhetas, conforme o que estiver marcado abaixo."
    ),
    "Só Shorts": (
        "Para um projeto que já foi transcrito: refaz só os Shorts (escolha dos cortes, "
        "legendas e vídeos verticais), sem renderizar o vídeo longo de novo."
    ),
    "Só vinhetas": (
        "Para um projeto que já tem o vídeo longo pronto: só emenda a introdução e a vinheta "
        "final, sem recodificar. É rápido."
    ),
}

#: o que o "O que gerar" mostra em cada modo (na ordem em que aparece)
_GRUPOS_POR_MODO = {
    "completo": ("audio", "longo", "vinhetas", "shorts"),
    "so_shorts": ("audio", "shorts"),
    "so_vinhetas": ("vinhetas",),
}


class PainelOpcoes(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        valores: ValoresFormulario,
        pastas: Pastas,
        ao_mudar: Callable[[], None],
        fonte: FonteDeQuadros,
        tarefas: Tarefas,
        presets: RepositorioDePresets,
        ao_listar_ollama: Callable[[], None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self._pastas = pastas
        self._ao_mudar = ao_mudar
        self._ao_listar_ollama = ao_listar_ollama
        self._ollama_instalados: list[str] = []
        self._rotulos_ollama: dict[str, str] = {}
        self._nomes_de_arquivo: dict[str, tuple[ctk.CTkLabel, str]] = {}
        self._recomendado: str | None = None
        self._cuda = False
        self._nvenc = False
        self._bloqueado = False
        self._rotulos_whisper = rotulos_whisper()
        self._rotulos_qualidade = rotulos_qualidade()
        self._todos: list[ctk.CTkBaseClass] = []
        #: os cartões deste painel por chave (gerar, ajustes, revisao, processamento)
        self.cartoes: dict[str, ctk.CTkFrame] = {}
        self._por_nome: dict[str, ctk.CTkBaseClass] = {}

        self._rotulos_modo = {rotulo: modo for modo, rotulo in ROTULOS_MODO.items()}
        self._estilos_do_fade = {rotulo: estilo for estilo, rotulo in ROTULOS_FADE.items()}
        self.modo = ctk.StringVar(value=ROTULOS_MODO[valores.modo])
        self.uso = ctk.IntVar(value=valores.uso_cpu)
        self.whisper = ctk.StringVar()
        self.qualidade = ctk.StringVar()
        self.bools = {
            nome: ctk.BooleanVar(value=getattr(valores, nome))
            for nome in (
                "limpar_audio",
                "normalizar_volume",
                "gerar_video_longo",
                "introducao",
                "vinheta_final",
                "gerar_shorts",
                "legendas_dinamicas",
                "revisar_transcricao",
                "aprovar_cortes",
                "manter_intermediarios",
                "impedir_bloqueio",
                "fade_vinhetas",
            )
        }
        self.textos = {
            nome: ctk.StringVar(value=getattr(valores, nome))
            for nome in (
                "limiar_pausa",
                "pausa_residual",
                "shorts_min",
                "shorts_max",
                "limite_shorts",
                "nota_minima",
                "ollama_modelo",
                "deslocamento_x",
                "x264_preset",
                "crf",
                "introducao_arquivo",
                "vinheta_arquivo",
                "fade_vinhetas_s",
            )
        }
        self.ollama = ctk.StringVar()
        self.fade_estilo = ctk.StringVar(value=ROTULOS_FADE[valores.fade_estilo])

        # Três páginas, uma de cada vez na tela: menos widgets para rolar e uma tela mais curta.
        self.paginas = [ctk.CTkFrame(self, fg_color="transparent") for _ in NOMES_DAS_PAGINAS]
        self._montando = 0
        self.espaco_do_video = ctk.CTkFrame(self.paginas[0], fg_color="transparent")
        self.espaco_do_video.pack(fill="x")  # a janela põe aqui o cartão "1. Vídeo"
        self._montar_gerar()
        self._montando = 1
        self._montar_ajustes_do_video()
        self.tratamento = PainelTratamento(
            self.paginas[1], valores, fonte=fonte, tarefas=tarefas, presets=presets,
            ao_mudar=self._ao_mudar,
        )  # fmt: skip
        self.tratamento.pack(fill="x", pady=(0, 10))
        self.marca = PainelMarca(
            self.paginas[1], valores, pasta_inicial=pastas.recursos, ao_mudar=self._mudou
        )
        self.marca.pack(fill="x", pady=(0, 10))
        self._montando = 2
        self._montar_revisao()
        self._montar_processamento()
        self._montar_avancado()
        self.pagina_atual = -1
        self.mostrar_pagina(0)
        self.selecionar_whisper(valores.whisper_modelo)
        self.selecionar_qualidade(valores.qualidade)
        self.definir_modelos_ollama(self._ollama_instalados)
        self.atualizar_habilitacoes()

    # --- montagem -----------------------------------------------------------------------------
    def mostrar_pagina(self, indice: int) -> None:
        """Mostra só a página `indice` (0 = vídeo e geração, 1 = corte e imagem, 2 = revisão)."""
        if indice == self.pagina_atual:
            return
        self.pagina_atual = indice
        for pagina in self.paginas:
            pagina.pack_forget()
        self.paginas[indice].pack(fill="x")

    def _cartao(self, titulo: str, ajuda: str = "", chave: str = "") -> ctk.CTkFrame:
        """Um cartão da coluna: o título à esquerda e, se há `ajuda`, o "?" à direita."""
        cartao = ctk.CTkFrame(self.paginas[self._montando])
        cartao.pack(fill="x", pady=(0, 10))
        cartao.cabecalho = CabecalhoDeEtapa(cartao, titulo, ajuda)
        cartao.cabecalho.pack(fill="x", padx=12, pady=(10, 4))
        if chave:
            self.cartoes[chave] = cartao
        return cartao

    @staticmethod
    def _balao(widget, texto: dicas.Texto) -> None:
        """A dica de ferramenta (o balão ao passar o mouse) de um widget."""
        if texto:
            dicas.dica(widget, texto)

    def _registrar(self, widget, nome: str | None = None):
        self._todos.append(widget)
        if nome:
            self._por_nome[nome] = widget
        return widget

    def _dica(self, pai, *, empacotar: bool = True) -> ctk.CTkLabel:
        dica = ctk.CTkLabel(
            pai,
            text="",
            text_color=_COR_DICA,
            anchor="w",
            justify="left",
            wraplength=430,
            font=ctk.CTkFont(size=12),
        )
        if empacotar:
            dica.pack(anchor="w", padx=12, pady=(0, 6))
        return dica

    @staticmethod
    def _definir_dica(rotulo: ctk.CTkLabel, texto: str, cor: str, depois_de) -> None:
        """Mostra o rótulo logo abaixo de `depois_de`, ou o esconde se não há texto."""
        if texto:
            rotulo.configure(text=texto, text_color=cor)
            rotulo.pack(anchor="w", padx=12, pady=(0, 6), after=depois_de)
        else:
            rotulo.pack_forget()

    def _ajuda(self, pai, texto: str, recuo: int = 0) -> ctk.CTkLabel:
        """A legendinha cinza que explica a opção logo acima dela."""
        rotulo = ctk.CTkLabel(
            pai,
            text=texto,
            text_color=_COR_DICA,
            anchor="w",
            justify="left",
            wraplength=430 - recuo,
            font=ctk.CTkFont(size=12),
        )
        rotulo.pack(anchor="w", padx=(12 + recuo, 12), pady=(0, 6))
        return rotulo

    def _check(self, pai, texto: str, nome: str, recuo: int = 0, ajuda: str = "", balao=""):
        caixa = ctk.CTkCheckBox(pai, text=texto, variable=self.bools[nome], command=self._mudou)
        caixa.pack(anchor="w", padx=(12 + recuo, 12), pady=3)
        self._balao(caixa, balao)
        if ajuda:
            self._ajuda(pai, ajuda, recuo + 28)
        return self._registrar(caixa, nome)

    def _campo(
        self, pai, rotulo: str, nome: str, *, largura: int = 80, dica: str = "", recuo=0,
        ajuda: str = "", balao: str = "",
    ):  # fmt: skip
        linha = ctk.CTkFrame(pai, fg_color="transparent")
        linha.pack(anchor="w", padx=(12 + recuo, 12), pady=3, fill="x")
        ctk.CTkLabel(linha, text=rotulo, anchor="w").pack(side="left")
        entrada = ctk.CTkEntry(linha, textvariable=self.textos[nome], width=largura)
        entrada.pack(side="left", padx=8)
        self._balao(entrada, balao)
        entrada.bind("<KeyRelease>", lambda _e: self._ao_mudar())
        if dica:
            ctk.CTkLabel(linha, text=dica, text_color=_COR_DICA, font=ctk.CTkFont(size=12)).pack(
                side="left"
            )
        if ajuda:
            self._ajuda(pai, ajuda, recuo)
        return self._registrar(entrada, nome)

    def _linha_de_arquivo(
        self,
        pai,
        rotulo: str,
        nome_check: str,
        nome_arquivo: str,
        padrao: str,
        *,
        balao: str = "",
        quando: str = "",
    ):
        """Caixa de marcar + o arquivo usado (o padrão de `recursos/` ou o escolhido)."""
        self._check(pai, rotulo, nome_check, recuo=28, balao=balao)
        linha = ctk.CTkFrame(pai, fg_color="transparent")
        linha.pack(anchor="w", padx=(12 + 56, 12), pady=(0, 4), fill="x")
        botao = ctk.CTkButton(
            linha,
            text="Escolher arquivo…",
            width=130,
            height=24,
            fg_color=("gray70", "gray30"),
            command=lambda: self._escolher_arquivo(nome_arquivo, rotulo),
        )
        botao.pack(side="left")
        self._balao(
            botao,
            f"Escolhe outro vídeo para {quando}, no lugar do padrão ({padrao}, na pasta recursos).",
        )
        padrao_btn = ctk.CTkButton(
            linha,
            text="Usar o padrão",
            width=100,
            height=24,
            fg_color=("gray70", "gray30"),
            command=lambda: self._definir_arquivo(nome_arquivo, ""),
        )
        padrao_btn.pack(side="left", padx=6)
        self._balao(
            padrao_btn,
            f"Volta a usar o arquivo padrão ({padrao}) da pasta recursos, do lado do programa.",
        )
        nome = ctk.CTkLabel(linha, text="", anchor="w", text_color=_COR_DICA)
        nome.pack(side="left", padx=4)
        self._registrar(botao, f"escolher_{nome_check}")
        self._registrar(padrao_btn, f"padrao_{nome_check}")
        self._nomes_de_arquivo[nome_arquivo] = (nome, padrao)

    def _escolher_arquivo(self, nome_arquivo: str, titulo: str) -> None:
        atual = self.textos[nome_arquivo].get().strip()
        inicial = Path(atual).parent if atual else self._pastas.recursos
        caminho = filedialog.askopenfilename(
            title=f"Escolha o arquivo: {titulo}",
            initialdir=str(inicial if inicial.exists() else Path.home()),
            filetypes=[("Vídeos", "*.mp4 *.mov *.mkv *.m4v *.avi *.webm"), ("Todos", "*.*")],
        )
        if caminho:
            self._definir_arquivo(nome_arquivo, caminho)

    def _definir_arquivo(self, nome_arquivo: str, caminho: str) -> None:
        self.textos[nome_arquivo].set(str(caminho))
        self._mudou()

    def _mostrar_arquivos(self) -> None:
        for nome_arquivo, (rotulo, padrao) in self._nomes_de_arquivo.items():
            escolhido = self.textos[nome_arquivo].get().strip()
            rotulo.configure(text=f"{Path(escolhido).name}" if escolhido else f"{padrao} (padrão)")

    # --- modelos do Ollama --------------------------------------------------------------------
    def definir_modelos_ollama(self, instalados: Sequence[str]) -> None:
        """Preenche a lista de modelos (quando o Ollama responde com o que tem instalado)."""
        self._ollama_instalados = list(instalados)
        atual = self.textos["ollama_modelo"].get().strip() or "qwen3:8b"
        self._rotulos_ollama = rotulos_ollama(self._ollama_instalados, atual)
        self._menu_ollama.configure(values=list(self._rotulos_ollama))
        self.selecionar_ollama(atual)

    def selecionar_ollama(self, modelo: str) -> None:
        for rotulo, nome in self._rotulos_ollama.items():
            if nome == modelo:
                self.ollama.set(rotulo)
                return

    def _mudou_ollama(self, rotulo: str) -> None:
        self.textos["ollama_modelo"].set(self._rotulos_ollama.get(rotulo, rotulo))
        self._mudou()

    def _montar_processamento(self) -> None:
        c = self._cartao("7. Processamento", AJUDAS_DOS_CARTOES["processamento"], "processamento")
        ctk.CTkLabel(c, text="Uso do processador", anchor="w").pack(anchor="w", padx=12)
        for rotulo, valor, balao in (
            (
                ROTULO_USO_75, 75,
                "Usa cerca de três quartos dos núcleos do processador: o computador continua "
                "utilizável para outras coisas enquanto o sermão é processado.",
            ),
            (
                ROTULO_USO_100, 100,
                "Usa o processador inteiro: termina mais cedo, mas o computador quase não "
                "responde a mais nada durante o processamento.",
            ),
        ):  # fmt: skip
            r = ctk.CTkRadioButton(c, text=rotulo, variable=self.uso, value=valor)
            r.pack(anchor="w", padx=(24, 12), pady=2)
            self._balao(r, balao)
            self._registrar(r)

        ctk.CTkLabel(c, text="Modelo do Whisper (transcrição)", anchor="w").pack(
            anchor="w", padx=12, pady=(10, 0)
        )
        self._menu_whisper = ctk.CTkOptionMenu(
            c,
            variable=self.whisper,
            values=list(self._rotulos_whisper),
            command=self._mudou_menu,
            dynamic_resizing=False,
            width=420,
        )
        self._menu_whisper.pack(anchor="w", padx=12, pady=(2, 2))
        self._balao(
            self._menu_whisper,
            "O modelo que transcreve a fala. Os maiores acertam mais o português e os nomes, mas "
            "são mais lentos e pesados. O marcado como “recomendado” é o melhor para este "
            "computador.",
        )
        self._registrar(self._menu_whisper)
        self._dica_whisper = self._dica(c)
        self._aviso_whisper = self._dica(c, empacotar=False)

        ctk.CTkLabel(c, text="Qualidade do vídeo final", anchor="w").pack(anchor="w", padx=12)
        self._menu_qualidade = ctk.CTkOptionMenu(
            c,
            variable=self.qualidade,
            values=list(self._rotulos_qualidade),
            command=self._mudou_menu,
            dynamic_resizing=False,
            width=420,
        )
        self._menu_qualidade.pack(anchor="w", padx=12, pady=(2, 2))
        self._balao(
            self._menu_qualidade,
            "O equilíbrio entre a qualidade do vídeo final e o tempo de renderização. “Máxima” dá "
            "a melhor imagem e leva muito mais tempo; “Rápido” serve para testes. Num PC sem "
            "placa de vídeo, a renderização pode levar horas.",
        )
        self._registrar(self._menu_qualidade)
        self._dica_qualidade = self._dica(c, empacotar=False)

    def _grupo(self, pai, titulo: str) -> ctk.CTkFrame:
        """Um bloco do "O que gerar": um título pequeno com fio e as opções dele logo abaixo."""
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        ctk.CTkLabel(
            quadro,
            text=titulo.upper(),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=_COR_DICA,
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkFrame(quadro, height=1, fg_color=("gray75", "gray30")).pack(
            fill="x", padx=12, pady=(2, 4)
        )
        return quadro

    def _montar_gerar(self) -> None:
        c = self._cartao("2. O que gerar", AJUDAS_DOS_CARTOES["gerar"], "gerar")
        self._seletor_modo = ctk.CTkSegmentedButton(
            c, values=list(ROTULOS_MODO.values()), variable=self.modo, command=self._mudou_modo
        )
        self._seletor_modo.pack(fill="x", padx=12, pady=(0, 2))
        dica_do_seletor(self._seletor_modo, BALOES_DO_MODO)
        self._dica_modo = self._dica(c)

        audio = self._grupo(c, "Áudio")
        self._check(
            audio,
            "Limpar áudio (DeepFilterNet)",
            "limpar_audio",
            ajuda="Tira ruído de fundo com IA. Deixa o som e a transcrição melhores, mas é lento.",
            balao="Use quando a gravação tem chiado, ventilador ou eco. Se o áudio já está "
            "limpo, desligue para ganhar bastante tempo.",
        )
        self._check(
            audio,
            "Normalizar volume",
            "normalizar_volume",
            ajuda="Deixa o volume parecido do começo ao fim (−16 LUFS no vídeo longo e −14 nos "
            "Shorts).",
            balao="Recomendado: põe o volume num nível que toca bem no YouTube e no celular, sem "
            "estourar. Só muda o volume; o conteúdo do áudio é o mesmo.",
        )

        longo = self._grupo(c, "Vídeo completo")
        self._check(
            longo,
            "Gerar vídeo completo sem silêncios + arquivo .srt",
            "gerar_video_longo",
            ajuda="Corta as pausas longas, renderiza o sermão limpo e escreve as legendas (.srt).",
            balao="Marque para ter o sermão inteiro, sem as pausas longas, e as legendas para "
            "subir no YouTube. Desmarque se você só quer os Shorts.",
        )
        self._campo(
            longo,
            "Tamanho mínimo da pausa a cortar (s)",
            "limiar_pausa",
            largura=64,
            dica="de 0,5 a 30",
            recuo=28,
            balao="Silêncios mais longos que isto são cortados do vídeo; os mais curtos ficam "
            "como estão. Quanto menor o número, mais corrido fica o sermão.",
        )

        vinhetas = self._grupo(c, "Introdução e vinheta final")
        self._linha_de_arquivo(
            vinhetas, "Introdução", "introducao", "introducao_arquivo", "introducao.mp4",
            balao="Emenda um vídeo de abertura (a vinheta da igreja, por exemplo) antes do "
            "sermão. Precisa do arquivo: veja o nome embaixo.",
            quando="a introdução",
        )  # fmt: skip
        self._linha_de_arquivo(
            vinhetas, "Vinheta final", "vinheta_final", "vinheta_arquivo", "vinheta_final.mp4",
            balao="Emenda um vídeo de encerramento depois do sermão. Precisa do arquivo: veja "
            "o nome embaixo.",
            quando="a vinheta final",
        )  # fmt: skip
        self._check(
            vinhetas,
            "Suavizar com fade (imagem e som) entre a introdução/vinheta e o vídeo",
            "fade_vinhetas",
            recuo=28,
            balao="Faz uma transição suave de imagem e som entre a introdução (ou a vinheta) e "
            "o sermão, em vez de um corte seco.",
        )
        linha_estilo = ctk.CTkFrame(vinhetas, fg_color="transparent")
        linha_estilo.pack(anchor="w", padx=(12 + 56, 12), pady=3, fill="x")
        ctk.CTkLabel(linha_estilo, text="Como o fade acontece", anchor="w").pack(side="left")
        self._seletor_fade = ctk.CTkSegmentedButton(
            linha_estilo,
            values=list(ROTULOS_FADE.values()),
            variable=self.fade_estilo,
            command=lambda _v: self._mudou(),
        )
        self._seletor_fade.pack(side="left", padx=8)
        dica_do_seletor(
            self._seletor_fade,
            {
                ROTULOS_FADE["mesclar"]: "A introdução se dissolve no vídeo, com as imagens e "
                "os sons se misturando. O vídeo final fica um pouco mais curto.",
                ROTULOS_FADE["preto"]: "A imagem escurece e volta, e o som some e volta. É o "
                "único estilo possível no modo Só vinhetas.",
            },
        )
        self._campo(
            vinhetas,
            "Duração do fade (s)",
            "fade_vinhetas_s",
            largura=56,
            dica="de 0,1 a 3",
            recuo=56,
            balao="Quanto dura a transição, em segundos. Mais curta é mais discreta.",
        )
        self._w_vinheta = self._ajuda(  # a dica de arquivo ausente vem logo depois deste texto
            vinhetas,
            "Mesclar: a introdução se dissolve no vídeo (e o vídeo na vinheta final), as duas "
            "imagens e os dois sons se misturam, sem trecho escuro nem silêncio; o vídeo final "
            "fica "
            "a duração do fade mais curto. "
            "Pelo preto: a imagem escurece e volta, e o som some e "
            "volta. No modo Só vinhetas o fade sempre passa pelo preto, e só do lado da "
            "introdução e da vinheta, porque o vídeo pronto não é recodificado.",
            56,
        )
        self._dica_recursos = self._dica(vinhetas, empacotar=False)

        shorts = self._grupo(c, "Shorts (vertical 9:16)")
        self._quadro_gerar_shorts = ctk.CTkFrame(shorts, fg_color="transparent")
        self._check(
            self._quadro_gerar_shorts,
            "Gerar Shorts automáticos (vertical 9:16)",
            "gerar_shorts",
            ajuda="A IA procura os melhores trechos, escreve título e hashtags, e você aprova "
            "antes de renderizar.",
            balao="Liga a escolha automática de cortes. Depois de processar, a aba Studio Shorts "
            "lista os trechos sugeridos para você revisar, ajustar e renderizar.",
        )
        self._quadro_legendas = ctk.CTkFrame(shorts, fg_color="transparent")
        self._check(
            self._quadro_legendas,
            "Legendas dinâmicas (palavra falada em amarelo)",
            "legendas_dinamicas",
            recuo=28,
            ajuda="Queima a legenda no vídeo dos Shorts. Sem isto o Short sai sem texto na tela.",
            balao="Grava a legenda dentro do vídeo dos Shorts, com a palavra falada em destaque. "
            "Desligue se você prefere pôr a legenda em outro programa.",
        )
        self._grupos = {"audio": audio, "longo": longo, "vinhetas": vinhetas, "shorts": shorts}
        self._modo_mostrado: str | None = None

    def _mostrar_grupos(self, modo: str) -> None:
        """Mostra só o que vale para o modo: Tudo mostra tudo; Só Shorts, o áudio e os Shorts;
        Só vinhetas, apenas a introdução e a vinheta final."""
        if modo == self._modo_mostrado:
            return
        self._modo_mostrado = modo
        for quadro in (*self._grupos.values(), self._quadro_gerar_shorts, self._quadro_legendas):
            quadro.pack_forget()
        for nome in _GRUPOS_POR_MODO[modo]:
            self._grupos[nome].pack(fill="x")
        if "shorts" in _GRUPOS_POR_MODO[modo]:
            if modo == "completo":  # no Só Shorts os Shorts são o produto: a caixa não faz sentido
                self._quadro_gerar_shorts.pack(fill="x")
            self._quadro_legendas.pack(fill="x")

    def _montar_ajustes_do_video(self) -> None:
        """O cartão onde a janela põe o corte do começo e do fim, o zoom e a legenda dos Shorts."""
        self.cartao_ajustes = self._cartao(
            "3. Corte e enquadramento", AJUDAS_DOS_CARTOES["ajustes"], "ajustes"
        )

    def _montar_revisao(self) -> None:
        c = self._cartao("6. Pontos de revisão", AJUDAS_DOS_CARTOES["revisao"], "revisao")
        self._check(
            c,
            "Pausar para revisar a transcrição (.srt) antes de continuar",
            "revisar_transcricao",
            balao="O processamento para logo depois de transcrever, para você ler e corrigir o "
            "texto (nomes, versículos, termos teológicos) antes de gerar as legendas e os "
            "Shorts. Recomendado, principalmente nos primeiros sermões.",
        )
        self._check(
            c,
            "Pausar para aprovar os cortes dos Shorts antes de renderizar",
            "aprovar_cortes",
            balao="O processamento para antes de renderizar os Shorts, para você escolher quais "
            "cortes quer e ajustar o começo e o fim. Desligado, os Shorts marcados pela IA são "
            "renderizados direto.",
        )
        ctk.CTkLabel(c, text="").pack(pady=1)

    def _montar_avancado(self) -> None:
        cabecalho_avancado = ctk.CTkFrame(self.paginas[2], fg_color="transparent")
        cabecalho_avancado.pack(fill="x", pady=(0, 4))
        cabecalho_avancado.columnconfigure(0, weight=1)
        self._botao_avancado = ctk.CTkButton(
            cabecalho_avancado,
            text="▸ Opções avançadas",
            anchor="w",
            fg_color="transparent",
            text_color=("gray20", "gray80"),
            hover_color=("gray80", "gray25"),
            command=self._alternar_avancado,
        )
        self._botao_avancado.grid(row=0, column=0, sticky="ew")
        self._balao(
            self._botao_avancado,
            lambda: (
                "Esconde os ajustes avançados."
                if self._avancado_visivel
                else "Mostra ajustes finos que quase nunca precisam mudar."
            ),
        )
        self.icone_do_avancado = dicas.IconeDeAjuda(
            cabecalho_avancado, AJUDAS_DOS_CARTOES["avancado"]
        )
        self.icone_do_avancado.grid(row=0, column=1, sticky="e", padx=(8, 12))
        self._avancado = ctk.CTkFrame(self.paginas[2])
        self._avancado_visivel = False
        a = self._avancado
        self._campo(
            a,
            "Pausa residual mantida ao cortar (s)",
            "pausa_residual",
            largura=64,
            ajuda="Quanto silêncio sobra em cada pausa cortada (metade em cada ponta), para a "
            "emenda não soar seca.",
            balao="Segundos de silêncio que ficam em cada pausa cortada. Zero faz um corte seco; "
            "cerca de 1 s deixa a fala respirar entre as frases.",
        )
        self._campo(
            a,
            "Duração mínima do Short (s)",
            "shorts_min",
            largura=64,
            ajuda="Trechos mais curtos que isto não viram Short. O padrão é 30 s.",
            balao="Duração mínima, em segundos, de um trecho para ser sugerido como Short.",
        )
        self._campo(
            a,
            "Duração máxima do Short (s)",
            "shorts_max",
            largura=64,
            dica="até 180",
            ajuda="Trechos mais longos que isto nem são avaliados. 180 s (3 min) é o limite do "
            "YouTube. A nota favorece os de cerca de 60 s, mas os longos ainda concorrem.",
            balao="Duração máxima, em segundos, de um trecho para ser avaliado como Short (até "
            "180, o limite do YouTube).",
        )
        self._campo(
            a,
            "Limite de segurança de Shorts",
            "limite_shorts",
            largura=64,
            ajuda="O máximo de cortes que vêm marcados; os demais bons viram reservas desmarcadas.",
            balao="Quantos Shorts, no máximo, já vêm marcados para renderizar. Os outros bons "
            "ficam na lista como reservas, desmarcados.",
        )
        self._campo(
            a,
            "Nota mínima para um corte vir marcado",
            "nota_minima",
            largura=64,
            dica="0 a 100",
            ajuda="Cortes abaixo desta nota aparecem na aprovação, mas desmarcados.",
            balao="A nota vai de 0 a 100. Suba este número para sugerir menos Shorts, só os "
            "melhores; desça para ter mais opções.",
        )
        linha_ollama = ctk.CTkFrame(a, fg_color="transparent")
        linha_ollama.pack(anchor="w", padx=12, pady=3, fill="x")
        ctk.CTkLabel(linha_ollama, text="Modelo do Ollama", anchor="w").pack(side="left")
        self._menu_ollama = ctk.CTkOptionMenu(
            linha_ollama,
            variable=self.ollama,
            values=[""],
            command=self._mudou_ollama,
            dynamic_resizing=False,
            width=230,
        )
        self._menu_ollama.pack(side="left", padx=8)
        self._balao(
            self._menu_ollama,
            "A IA local (Ollama) que avalia os trechos e escreve os títulos. Os modelos maiores "
            "entendem melhor o português, mas são mais lentos.",
        )
        self._registrar(self._menu_ollama, "ollama_menu")
        atualizar = ctk.CTkButton(
            linha_ollama,
            text="↻",
            width=28,
            height=28,
            fg_color=("gray70", "gray30"),
            command=lambda: self._ao_listar_ollama() if self._ao_listar_ollama else None,
        )
        atualizar.pack(side="left")
        self._balao(
            atualizar, "Pergunta ao Ollama quais modelos estão instalados e atualiza a lista."
        )
        self._registrar(atualizar, "ollama_atualizar")
        self._ajuda(
            a,
            "A IA local que avalia os trechos e escreve os títulos. A lista mostra o que o Ollama "
            "já tem; os marcados “não instalado” são baixados na primeira vez. Modelos maiores "
            "entendem melhor o português e são mais lentos. ↻ atualiza a lista.",
        )
        self._campo(
            a,
            "Deslocamento do recorte 9:16 (%)",
            "deslocamento_x",
            largura=64,
            dica="− esquerda / + direita",
            ajuda="Move o enquadramento vertical dos Shorts para o lado. +4 põe o pregador no meio "
            "com a câmera fixa do púlpito. O zoom e a altura da legenda dos Shorts ficam em "
            "“Ajustar o zoom e a legenda dos Shorts”, no começo da tela.",
            balao="Move o recorte vertical dos Shorts para o lado, em % da largura: negativo "
            "para a esquerda, positivo para a direita. Use se o pregador não ficar no centro do "
            "Short.",
        )
        linha = ctk.CTkFrame(a, fg_color="transparent")
        linha.pack(anchor="w", padx=12, pady=3)
        ctk.CTkLabel(linha, text="Qualidade personalizada: preset").pack(side="left")
        menu = ctk.CTkOptionMenu(
            linha,
            variable=self.textos["x264_preset"],
            values=list(PRESETS_X264),
            width=110,
            command=self._mudou_menu,
        )
        menu.pack(side="left", padx=8)
        self._balao(
            menu,
            "Velocidade de compressão do vídeo: quanto mais lento, menor o arquivo para a mesma "
            "qualidade. Só vale quando a Qualidade é “Personalizado”.",
        )
        self._registrar(menu, "x264_preset")
        ctk.CTkLabel(linha, text="  CRF").pack(side="left")
        crf = ctk.CTkEntry(linha, textvariable=self.textos["crf"], width=50)
        crf.pack(side="left", padx=8)
        self._balao(
            crf,
            "O nível de qualidade: números menores dão mais qualidade e arquivos maiores (de 16 a "
            "23 é o usual). Só vale quando a Qualidade é “Personalizado”.",
        )
        self._registrar(crf, "crf")
        self._ajuda(
            a,
            "Só vale com “Personalizado” em Qualidade. Preset mais lento gera arquivo menor com a "
            "mesma qualidade; CRF menor dá mais qualidade e arquivo maior (16 a 23 é o usual).",
        )
        self._check(
            a,
            "Manter arquivos intermediários ao terminar",
            "manter_intermediarios",
            ajuda="Não apaga os áudios e partes pesadas de _work/ ao concluir. Ocupam vários GB; "
            "só vale a pena para investigar um problema.",
            balao="Deixe desligado no uso normal: ao terminar, os arquivos de trabalho pesados "
            "são apagados para liberar disco (a análise da IA é guardada).",
        )
        self._check(
            a,
            "Impedir que o PC bloqueie ou apague a tela durante o processamento",
            "impedir_bloqueio",
            ajuda="As renderizações levam horas. Com isto ligado o Windows não suspende nem "
            "bloqueia o PC enquanto o aplicativo trabalha (volta ao normal ao terminar).",
            balao="Recomendado: evita que o computador durma no meio de uma renderização longa.",
        )
        ctk.CTkLabel(a, text="").pack(pady=1)

    def _alternar_avancado(self) -> None:
        self._avancado_visivel = not self._avancado_visivel
        if self._avancado_visivel:
            self._avancado.pack(fill="x", pady=(0, 10))
            self._botao_avancado.configure(text="▾ Opções avançadas")
        else:
            self._avancado.pack_forget()
            self._botao_avancado.configure(text="▸ Opções avançadas")

    # --- leitura e estado ---------------------------------------------------------------------
    def valores(self) -> ValoresFormulario:
        return ValoresFormulario(
            modo=self._rotulos_modo.get(self.modo.get(), "completo"),
            uso_cpu=self.uso.get(),
            whisper_modelo=self._rotulos_whisper.get(self.whisper.get(), "large-v3-turbo"),
            qualidade=self._rotulos_qualidade.get(self.qualidade.get(), "maxima"),
            **{nome: var.get() for nome, var in self.bools.items()},
            **{nome: var.get() for nome, var in self.textos.items()},
            fade_estilo=self._estilos_do_fade.get(self.fade_estilo.get(), "mesclar"),
            **self.marca.valores(),
            **self.tratamento.valores(),
        )

    def aplicar_valores(self, v: ValoresFormulario) -> None:
        """Põe na tela as opções de um projeto aberto (ou as padrão)."""
        self.modo.set(ROTULOS_MODO[v.modo])
        self.uso.set(v.uso_cpu)
        for nome, var in self.bools.items():
            var.set(getattr(v, nome))
        for nome, var in self.textos.items():
            var.set(getattr(v, nome))
        self.fade_estilo.set(ROTULOS_FADE.get(v.fade_estilo, ROTULOS_FADE["mesclar"]))
        self.selecionar_whisper(v.whisper_modelo)
        self.selecionar_qualidade(v.qualidade)
        self.definir_modelos_ollama(self._ollama_instalados)
        self.marca.aplicar(v)
        self.tratamento.aplicar(v)
        self.atualizar_habilitacoes()
        self._ao_mudar()

    def selecionar_whisper(self, modelo: str) -> None:
        for rotulo, nome in self._rotulos_whisper.items():
            if nome == modelo:
                self.whisper.set(rotulo)

    def selecionar_qualidade(self, chave: str) -> None:
        for rotulo, nome in self._rotulos_qualidade.items():
            if nome == chave:
                self.qualidade.set(rotulo)

    def definir_perfil(self, *, recomendado: str, cuda_utilizavel: bool, nvenc: bool) -> None:
        """Chamado quando a detecção de hardware termina: marca o modelo recomendado."""
        atual = self.valores().whisper_modelo
        self._recomendado, self._cuda, self._nvenc = recomendado, cuda_utilizavel, nvenc
        self._rotulos_whisper = rotulos_whisper(recomendado)
        self._menu_whisper.configure(values=list(self._rotulos_whisper))
        self.selecionar_whisper(atual)
        self.atualizar_habilitacoes()

    def bloquear(self, bloqueado: bool) -> None:
        self._bloqueado = bloqueado
        self.atualizar_habilitacoes()

    def _mudou(self) -> None:
        self.atualizar_habilitacoes()
        self._ao_mudar()

    def _mudou_menu(self, _valor: str) -> None:
        self._mudou()

    def _mudou_modo(self, _valor: str) -> None:
        if self.valores().modo == "so_shorts":
            self.bools["gerar_shorts"].set(True)  # neste modo os Shorts são o produto
        self._mudou()

    def definir_modo(self, modo: str) -> None:
        self.modo.set(ROTULOS_MODO[modo])
        self._mudou_modo("")

    def atualizar_habilitacoes(self) -> None:
        v = self.valores()
        arquivo_intro = self._pastas.arquivo_introducao(v.introducao_arquivo)
        arquivo_vinheta = self._pastas.arquivo_vinheta(v.vinheta_arquivo)
        intro, vinheta = arquivo_intro.exists(), arquivo_vinheta.exists()
        self._mostrar_arquivos()
        h = calcular_habilitacoes(v, introducao_existe=intro, vinheta_existe=vinheta)
        livre = not self._bloqueado

        for widget in self._todos:
            widget.configure(state="normal" if livre else "disabled")
        self._seletor_modo.configure(state="normal" if livre else "disabled")
        self._mostrar_grupos(v.modo)
        self._seletor_fade.configure(state="normal" if livre and h.fade_estilo else "disabled")
        self._dica_modo.configure(text=DICAS_MODO[v.modo])
        self.marca.aplicar_habilitacoes(h, livre=livre)
        self.tratamento.aplicar_habilitacoes(h, livre=livre)
        regras = {
            "limpar_audio": h.limpar_audio,
            "gerar_video_longo": h.gerar_video_longo,
            "gerar_shorts": h.gerar_shorts,
            "revisar_transcricao": h.revisar_transcricao,
            "limiar_pausa": h.limiar_pausa,
            "introducao": h.introducao,
            "vinheta_final": h.vinheta_final,
            "escolher_introducao": h.escolher_introducao,
            "padrao_introducao": h.escolher_introducao,
            "escolher_vinheta_final": h.escolher_vinheta,
            "padrao_vinheta_final": h.escolher_vinheta,
            "fade_vinhetas": h.fade_vinhetas,
            "fade_vinhetas_s": h.fade_vinhetas_s,
            "ollama_menu": h.campos_shorts,
            "ollama_atualizar": h.campos_shorts,
            "legendas_dinamicas": h.legendas_dinamicas,
            "aprovar_cortes": h.aprovar_cortes,
            "shorts_min": h.campos_shorts,
            "shorts_max": h.campos_shorts,
            "limite_shorts": h.campos_shorts,
            "x264_preset": h.qualidade_personalizada,
            "crf": h.qualidade_personalizada,
        }
        for nome, ativo in regras.items():
            self._por_nome[nome].configure(state="normal" if livre and ativo else "disabled")

        faltam = [
            caminho.name
            for caminho, existe in ((arquivo_intro, intro), (arquivo_vinheta, vinheta))
            if not existe
        ]
        ausentes = f"Arquivo não encontrado: {', '.join(faltam)}" if faltam else ""
        self._definir_dica(
            self._dica_recursos,
            ausentes
            if v.modo != "so_shorts" and (v.gerar_video_longo or v.modo == "so_vinhetas")
            else "",
            _COR_DICA,
            self._w_vinheta,
        )

        peso, observacao = INFO_MODELOS_WHISPER[v.whisper_modelo]
        self._dica_whisper.configure(
            text=f"{observacao[0].upper()}{observacao[1:]} (peso: {peso})."
        )
        self._definir_dica(
            self._aviso_whisper,
            aviso_whisper(v.whisper_modelo, cuda_utilizavel=self._cuda) or "",
            _COR_AVISO,
            self._dica_whisper,
        )
        self._definir_dica(
            self._dica_qualidade,
            aviso_qualidade(v.qualidade, nvenc=self._nvenc) or "",
            _COR_AVISO if v.qualidade == "maxima" else _COR_DICA,
            self._menu_qualidade,
        )
