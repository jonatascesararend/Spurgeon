"""Seções "Processamento", "O que gerar", "Pontos de revisão" e "Avançado"."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from spurgeon.core.config import INFO_MODELOS_WHISPER, PRESETS_X264, Pastas
from spurgeon.ui.formulario import (
    ROTULO_USO_75,
    ROTULO_USO_100,
    ValoresFormulario,
    aviso_qualidade,
    aviso_whisper,
    calcular_habilitacoes,
    rotulos_qualidade,
    rotulos_whisper,
)

_COR_DICA = "gray60"
_COR_AVISO = "#e0a030"


class PainelOpcoes(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        valores: ValoresFormulario,
        pastas: Pastas,
        ao_mudar: Callable[[], None],
    ):
        super().__init__(master, fg_color="transparent")
        self._pastas = pastas
        self._ao_mudar = ao_mudar
        self._recomendado: str | None = None
        self._cuda = False
        self._nvenc = False
        self._bloqueado = False
        self._rotulos_whisper = rotulos_whisper()
        self._rotulos_qualidade = rotulos_qualidade()
        self._todos: list[ctk.CTkBaseClass] = []
        self._por_nome: dict[str, ctk.CTkBaseClass] = {}

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
                "posicao_legenda",
                "x264_preset",
                "crf",
            )
        }

        self._montar_processamento()
        self._montar_gerar()
        self._montar_revisao()
        self._montar_avancado()
        self.selecionar_whisper(valores.whisper_modelo)
        self.selecionar_qualidade(valores.qualidade)
        self.atualizar_habilitacoes()

    # --- montagem -----------------------------------------------------------------------------
    def _cartao(self, titulo: str) -> ctk.CTkFrame:
        cartao = ctk.CTkFrame(self)
        cartao.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(cartao, text=titulo, font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        return cartao

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

    def _check(self, pai, texto: str, nome: str, recuo: int = 0):
        caixa = ctk.CTkCheckBox(pai, text=texto, variable=self.bools[nome], command=self._mudou)
        caixa.pack(anchor="w", padx=(12 + recuo, 12), pady=3)
        return self._registrar(caixa, nome)

    def _campo(self, pai, rotulo: str, nome: str, *, largura: int = 80, dica: str = "", recuo=0):
        linha = ctk.CTkFrame(pai, fg_color="transparent")
        linha.pack(anchor="w", padx=(12 + recuo, 12), pady=3, fill="x")
        ctk.CTkLabel(linha, text=rotulo, anchor="w").pack(side="left")
        entrada = ctk.CTkEntry(linha, textvariable=self.textos[nome], width=largura)
        entrada.pack(side="left", padx=8)
        entrada.bind("<KeyRelease>", lambda _e: self._ao_mudar())
        if dica:
            ctk.CTkLabel(linha, text=dica, text_color=_COR_DICA, font=ctk.CTkFont(size=12)).pack(
                side="left"
            )
        return self._registrar(entrada, nome)

    def _montar_processamento(self) -> None:
        c = self._cartao("2. Processamento")
        ctk.CTkLabel(c, text="Uso do processador", anchor="w").pack(anchor="w", padx=12)
        for rotulo, valor in ((ROTULO_USO_75, 75), (ROTULO_USO_100, 100)):
            r = ctk.CTkRadioButton(c, text=rotulo, variable=self.uso, value=valor)
            r.pack(anchor="w", padx=(24, 12), pady=2)
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
        self._registrar(self._menu_qualidade)
        self._dica_qualidade = self._dica(c, empacotar=False)

    def _montar_gerar(self) -> None:
        c = self._cartao("3. O que gerar")
        self._check(c, "Limpar áudio (DeepFilterNet)", "limpar_audio")
        self._check(c, "Normalizar volume", "normalizar_volume")
        self._check(c, "Gerar vídeo completo sem silêncios + arquivo .srt", "gerar_video_longo")
        self._campo(
            c,
            "Tamanho mínimo da pausa a cortar (s)",
            "limiar_pausa",
            largura=64,
            dica="de 0,5 a 30",
            recuo=28,
        )
        self._check(c, "Introdução", "introducao", recuo=28)
        self._w_vinheta = self._check(c, "Vinheta final", "vinheta_final", recuo=28)
        self._dica_recursos = self._dica(c, empacotar=False)
        self._check(c, "Gerar Shorts automáticos (vertical 9:16)", "gerar_shorts")
        self._check(c, "Legendas dinâmicas", "legendas_dinamicas", recuo=28)

    def _montar_revisao(self) -> None:
        c = self._cartao("4. Pontos de revisão")
        self._check(
            c, "Pausar para revisar a transcrição (.srt) antes de continuar", "revisar_transcricao"
        )
        self._check(
            c, "Pausar para aprovar os cortes dos Shorts antes de renderizar", "aprovar_cortes"
        )
        ctk.CTkLabel(c, text="").pack(pady=1)

    def _montar_avancado(self) -> None:
        self._botao_avancado = ctk.CTkButton(
            self,
            text="▸ Opções avançadas",
            anchor="w",
            fg_color="transparent",
            text_color=("gray20", "gray80"),
            hover_color=("gray80", "gray25"),
            command=self._alternar_avancado,
        )
        self._botao_avancado.pack(fill="x", pady=(0, 4))
        self._avancado = ctk.CTkFrame(self)
        self._avancado_visivel = False
        a = self._avancado
        self._campo(a, "Pausa residual mantida ao cortar (s)", "pausa_residual", largura=64)
        self._campo(a, "Duração mínima do Short (s)", "shorts_min", largura=64)
        self._campo(a, "Duração máxima do Short (s)", "shorts_max", largura=64, dica="até 60")
        self._campo(a, "Limite de segurança de Shorts", "limite_shorts", largura=64)
        self._campo(
            a,
            "Nota mínima para um corte vir marcado",
            "nota_minima",
            largura=64,
            dica="0 a 100",
        )
        self._campo(a, "Modelo do Ollama", "ollama_modelo", largura=160)
        self._campo(
            a,
            "Deslocamento do recorte 9:16 (%)",
            "deslocamento_x",
            largura=64,
            dica="− esquerda / + direita",
        )
        self._campo(
            a,
            "Posição vertical da legenda (%)",
            "posicao_legenda",
            largura=64,
            dica="da altura, de cima",
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
        self._registrar(menu, "x264_preset")
        ctk.CTkLabel(linha, text="  CRF").pack(side="left")
        crf = ctk.CTkEntry(linha, textvariable=self.textos["crf"], width=50)
        crf.pack(side="left", padx=8)
        self._registrar(crf, "crf")
        self._check(a, "Manter arquivos intermediários ao terminar", "manter_intermediarios")
        self._check(
            a,
            "Impedir que o PC bloqueie ou apague a tela durante o processamento",
            "impedir_bloqueio",
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
            uso_cpu=self.uso.get(),
            whisper_modelo=self._rotulos_whisper.get(self.whisper.get(), "large-v3-turbo"),
            qualidade=self._rotulos_qualidade.get(self.qualidade.get(), "maxima"),
            **{nome: var.get() for nome, var in self.bools.items()},
            **{nome: var.get() for nome, var in self.textos.items()},
        )

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

    def atualizar_habilitacoes(self) -> None:
        v = self.valores()
        intro = self._pastas.introducao.exists()
        vinheta = self._pastas.vinheta_final.exists()
        h = calcular_habilitacoes(v, introducao_existe=intro, vinheta_existe=vinheta)
        livre = not self._bloqueado

        for widget in self._todos:
            widget.configure(state="normal" if livre else "disabled")
        regras = {
            "limiar_pausa": h.limiar_pausa,
            "introducao": h.introducao,
            "vinheta_final": h.vinheta_final,
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
            nome
            for nome, existe in (("introducao.mp4", intro), ("vinheta_final.mp4", vinheta))
            if not existe
        ]
        ausentes = f"Arquivo ausente em recursos/: {', '.join(faltam)}" if faltam else ""
        self._definir_dica(
            self._dica_recursos,
            ausentes if v.gerar_video_longo else "",
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
