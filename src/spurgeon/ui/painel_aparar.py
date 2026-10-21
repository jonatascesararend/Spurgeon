"""Seção "Cortar o começo e o fim" do cartão do vídeo: onde o vídeo realmente começa e termina.

Para cada ponta há o campo de tempo, o quadro exato onde o corte cai e um botão que gera uma prévia
de 15 segundos (o trecho que começa no corte inicial, ou os 15 s que terminam no corte final) e a
abre no player do Windows. Tudo o que fica antes do começo e depois do fim é ignorado em todo o
processamento: vídeo longo, legendas e Shorts.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from spurgeon.core.aparencia import Enquadramento, TratamentoImagem
from spurgeon.core.formatacao import formatar_duracao, formatar_tempo_preciso
from spurgeon.render import previa
from spurgeon.ui import dialogos, dicas
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.composicao import ajustar_ao_tamanho
from spurgeon.ui.formulario import ValoresFormulario, ler_tempo_do_video
from spurgeon.ui.quadros import FonteDeQuadros, QuadroDeExemplo

_COR_DICA = "gray60"
_COR_ERRO = "#e05555"
_MINIATURA = (176, 99)
_ATRASO_MS = 350
_FOLGA_DO_ULTIMO_QUADRO_S = 0.2


def abrir_no_player(caminho: Path) -> None:
    """Abre o arquivo no programa padrão do Windows."""
    if sys.platform == "win32":
        os.startfile(caminho)


class PainelAparar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        valores: ValoresFormulario,
        *,
        fonte: FonteDeQuadros,
        tarefas: Tarefas,
        ao_mudar: Callable[[], None],
        estilo: Callable[[], tuple[Enquadramento, TratamentoImagem]] | None = None,
        abrir: Callable[[Path], None] = abrir_no_player,
    ):
        super().__init__(master, fg_color="transparent")
        self._fonte = fonte
        self._tarefas = tarefas
        self._ao_mudar = ao_mudar
        self._estilo = estilo or (lambda: (Enquadramento(), TratamentoImagem()))
        self._abrir = abrir
        self._adiado: str | None = None
        self._miniaturas: dict[str, ctk.CTkImage] = {}
        self._vazia = ctk.CTkImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), size=(1, 1))
        self._pasta_das_previas = Path(tempfile.gettempdir()) / "spurgeon_previas" / "cortes"
        self.var_inicio = ctk.StringVar(value=valores.video_inicio)
        self.var_fim = ctk.StringVar(value=valores.video_fim)
        self._botoes: list[ctk.CTkBaseClass] = []
        self._campos: dict[str, ctk.CTkEntry] = {}
        self._rotulos_quadro: dict[str, ctk.CTkLabel] = {}

        self.rotulo_titulo = ctk.CTkLabel(
            self, text="Cortar o começo e o fim do vídeo", font=ctk.CTkFont(weight="bold")
        )  # a janela o esconde quando o painel vem dentro de uma seção recolhível
        self.rotulo_titulo.pack(anchor="w", padx=12, pady=(4, 0))
        ctk.CTkLabel(
            self,
            text="Tudo antes do começo e depois do fim é ignorado: vídeo, legendas e Shorts.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(anchor="w", padx=12)
        self._montar_ponta(
            "inicio", "Começar em", self.var_inicio, "▶ Ver 15 s do começo",
            campo_dica="O instante em que o vídeo passa a valer, no formato min:s (por exemplo "
            "0:07 ou 1:04:56,5). Tudo antes dele é ignorado. Vazio: desde o começo.",
            botao_dica="Gera 15 segundos a partir do ponto de corte inicial e abre no player do "
            "Windows, já com o zoom e a cor escolhidos. Serve para conferir se o corte caiu "
            "no lugar certo.",
            quadro_dica="O quadro exato onde o vídeo passa a começar.",
        )  # fmt: skip
        self._montar_ponta(
            "fim", "Terminar em", self.var_fim, "▶ Ver 15 s do fim",
            campo_dica="O instante em que o vídeo termina. Vazio: vai até o fim. Escreva -2:04 "
            "para tirar os últimos 2 min 04 s.",
            botao_dica="Gera os 15 segundos que terminam no ponto de corte final e abre no "
            "player do Windows, para conferir onde o vídeo acaba.",
            quadro_dica="O último quadro que fica no vídeo.",
        )  # fmt: skip
        self._resumo = ctk.CTkLabel(
            self, text="", text_color=_COR_DICA, anchor="w", justify="left", wraplength=430
        )
        self._resumo.pack(anchor="w", padx=12, pady=(2, 0))
        ctk.CTkLabel(
            self,
            text="Formato min:s (ex.: 0:07 ou 1:04:56,5). Deixe “Terminar em” vazio para ir até o "
            "fim, ou escreva -2:04 para tirar 2 min 04 s do final. A prévia abre no player do "
            "Windows e usa o zoom e o tratamento de cor escolhidos.",
            text_color=_COR_DICA,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12, pady=(0, 6))
        fonte.ao_mudar_o_video(self._video_mudou)

    # --- montagem -----------------------------------------------------------------------------
    def _montar_ponta(
        self,
        ponta: str,
        titulo: str,
        variavel: ctk.StringVar,
        botao: str,
        *,
        campo_dica: str = "",
        botao_dica: str = "",
        quadro_dica: str = "",
    ) -> None:
        linha = ctk.CTkFrame(self, fg_color="transparent")
        linha.pack(fill="x", padx=12, pady=(6, 0))
        esquerda = ctk.CTkFrame(linha, fg_color="transparent")
        esquerda.pack(side="left", anchor="n")
        ctk.CTkLabel(esquerda, text=titulo, anchor="w").grid(row=0, column=0, sticky="w")
        campo = ctk.CTkEntry(esquerda, textvariable=variavel, width=110, justify="center")
        campo.grid(row=1, column=0, sticky="w", pady=(2, 4))
        campo.bind("<Return>", lambda _e, p=ponta: self._confirmar(p))
        campo.bind("<FocusOut>", lambda _e, p=ponta: self._confirmar(p))
        self._campos[ponta] = campo
        ver = ctk.CTkButton(
            esquerda,
            text=botao,
            width=150,
            height=26,
            command=lambda p=ponta: self.gerar_previa(p),
        )
        ver.grid(row=2, column=0, sticky="w")
        self._botoes += [campo, ver]
        rotulo = ctk.CTkLabel(
            linha, text="", width=_MINIATURA[0], height=_MINIATURA[1],
            fg_color=("gray80", "gray20"),
            corner_radius=4,
        )  # fmt: skip
        rotulo.pack(side="right")
        for widget, texto in ((campo, campo_dica), (ver, botao_dica), (rotulo, quadro_dica)):
            if texto:
                dicas.dica(widget, texto)
        self._rotulos_quadro[ponta] = rotulo

    # --- valores ------------------------------------------------------------------------------
    def valores(self) -> dict:
        # "-2:04" ainda no campo (o foco não saiu dele, por exemplo ao clicar em Iniciar): o tempo
        # absoluto é calculado agora, com a duração do vídeo
        duracao = self._fonte.info.duracao_s if self._fonte.info else None
        for variavel in (self.var_inicio, self.var_fim):
            texto = variavel.get().strip()
            if texto.startswith(("-", "−")) and duracao:
                with contextlib.suppress(
                    ValueError
                ):  # o erro aparece quando validarem o formulário
                    variavel.set(
                        formatar_tempo_preciso(ler_tempo_do_video(texto, duracao_s=duracao))
                    )
        return {
            "video_inicio": self.var_inicio.get().strip(),
            "video_fim": self.var_fim.get().strip(),
        }

    def aplicar(self, v: ValoresFormulario) -> None:
        self.var_inicio.set(v.video_inicio)
        self.var_fim.set(v.video_fim)
        self._atualizar_resumo()
        self._pedir_quadros()

    def janela(self) -> tuple[float, float | None]:
        """(começo, fim) em segundos; o fim é None quando vai até o fim do vídeo."""
        inicio = fim = 0.0
        try:
            texto_i = self.var_inicio.get().strip()
            texto_f = self.var_fim.get().strip()
            duracao = self._fonte.info.duracao_s if self._fonte.info else None
            inicio = ler_tempo_do_video(texto_i, duracao_s=duracao) if texto_i else 0.0
            fim = ler_tempo_do_video(texto_f, duracao_s=duracao) if texto_f else 0.0
        except ValueError:
            pass
        return inicio, (fim or None)

    # --- campos -------------------------------------------------------------------------------
    def _confirmar(self, ponta: str) -> None:
        """Lê o campo, converte "-2:04" para o tempo certo, limita ao vídeo e arruma o formato."""
        variavel = self.var_inicio if ponta == "inicio" else self.var_fim
        texto = variavel.get().strip()
        duracao = self._fonte.info.duracao_s if self._fonte.info else None
        if not texto:
            self._mostrar_erro("")
        else:
            try:
                segundos = ler_tempo_do_video(texto, duracao_s=duracao)
            except ValueError as erro:
                self._mostrar_erro(str(erro))
                return
            if duracao:
                segundos = min(segundos, duracao)
            if ponta == "inicio" and segundos <= 0.05:
                variavel.set("")
            elif ponta == "fim" and duracao and segundos >= duracao - 0.05:
                variavel.set("")  # até o fim, mesmo
            else:
                variavel.set(formatar_tempo_preciso(segundos))
            self._mostrar_erro("")
        self._atualizar_resumo()
        self._pedir_quadros()
        self._ao_mudar()

    def _mostrar_erro(self, texto: str) -> None:
        if texto:
            self._resumo.configure(text=texto, text_color=_COR_ERRO)

    def _atualizar_resumo(self) -> None:
        info = self._fonte.info
        inicio, fim = self.janela()
        if info is None:
            self._resumo.configure(text="", text_color=_COR_DICA)
            return
        fim_real = min(fim, info.duracao_s) if fim else info.duracao_s
        if fim_real <= inicio:
            self._resumo.configure(
                text="O vídeo precisa terminar depois do ponto em que começa.", text_color=_COR_ERRO
            )
            return
        partes = []
        if inicio:
            partes.append(f"{formatar_duracao(inicio)} do começo")
        if fim_real < info.duracao_s - 0.05:
            partes.append(f"{formatar_duracao(info.duracao_s - fim_real)} do fim")
        texto = (
            f"O vídeo usado terá {formatar_duracao(fim_real - inicio)} (tira {' e '.join(partes)})."
            if partes
            else f"Sem cortes: o vídeo inteiro ({formatar_duracao(info.duracao_s)})."
        )
        self._resumo.configure(text=texto, text_color=_COR_DICA)

    # --- quadros e prévia ---------------------------------------------------------------------
    def _video_mudou(self) -> None:
        for ponta in self._rotulos_quadro:
            self._trocar(ponta, self._vazia, "")
        self._atualizar_resumo()
        self._pedir_quadros()

    def _instantes(self) -> dict[str, float]:
        info = self._fonte.info
        if info is None:
            return {}
        inicio, fim = self.janela()
        fim_real = min(fim, info.duracao_s) if fim else info.duracao_s
        return {
            "inicio": inicio,
            "fim": max(inicio, fim_real - _FOLGA_DO_ULTIMO_QUADRO_S),
        }

    def _pedir_quadros(self) -> None:
        if not self._fonte.pronta:
            return
        if self._adiado is not None:
            self.after_cancel(self._adiado)
        self._adiado = self.after(_ATRASO_MS, self._buscar_quadros)

    def _buscar_quadros(self) -> None:
        self._adiado = None
        for ponta, instante in self._instantes().items():
            self._fonte.quadro(
                instante,
                lambda q, p=ponta: self._quadro_pronto(p, q),
                lambda _e, p=ponta: self._trocar(p, self._vazia, "sem quadro"),
                chave=f"aparar_{ponta}",
            )

    def _quadro_pronto(self, ponta: str, quadro: QuadroDeExemplo) -> None:
        pequena = ajustar_ao_tamanho(quadro.imagem, _MINIATURA)
        self._trocar(ponta, ctk.CTkImage(pequena, size=pequena.size), "")

    def _trocar(self, ponta: str, imagem: ctk.CTkImage, texto: str) -> None:
        """Põe a imagem nova e só depois solta a antiga (o Tk quebra se ela some antes)."""
        rotulo = self._rotulos_quadro[ponta]
        rotulo.configure(image=imagem, text=texto)
        self._miniaturas[ponta] = imagem

    def gerar_previa(self, ponta: str) -> None:
        """Gera os 15 s de prévia (em segundo plano) e abre no player."""
        info, video = self._fonte.info, self._fonte.video
        if not self._fonte.pronta or info is None or video is None:
            dialogos.aviso(
                self.winfo_toplevel(), "Prévia", "Escolha um vídeo para ver a prévia do corte."
            )
            return
        self._confirmar(ponta)
        inicio, fim = self.janela()
        fim_real = min(fim, info.duracao_s) if fim else info.duracao_s
        if fim_real <= inicio:
            return
        duracao = min(previa.DURACAO_DA_PREVIA_S, fim_real - inicio)
        comeco = inicio if ponta == "inicio" else fim_real - duracao
        enquadramento, tratamento = self._estilo()
        destino = self._pasta_das_previas / f"previa_{ponta}.mp4"
        botao = self._botoes[1 if ponta == "inicio" else 3]
        botao.configure(state="disabled", text="Gerando a prévia…")
        rotulo = "começo" if ponta == "inicio" else "fim"

        def restaurar() -> None:
            botao.configure(
                state="normal",
                text="▶ Ver 15 s do começo" if ponta == "inicio" else "▶ Ver 15 s do fim",
            )

        def pronta(caminho: Path) -> None:
            restaurar()
            self._abrir(caminho)

        def falhou(erro: Exception) -> None:
            restaurar()
            dialogos.erro(
                self.winfo_toplevel(),
                "Prévia",
                f"Não foi possível gerar a prévia do {rotulo}: {erro}",
            )

        self._tarefas.rodar(
            lambda: previa.gerar_previa(
                video, info, comeco, destino, duracao_s=duracao,
                enquadramento=enquadramento, tratamento=tratamento,
            ),
            pronta,
            falhou,
            chave=f"previa_{ponta}",
        )  # fmt: skip

    # --- habilitação --------------------------------------------------------------------------
    def habilitar(self, ativo: bool) -> None:
        estado = "normal" if ativo else "disabled"
        for controle in self._botoes:
            controle.configure(state=estado)
