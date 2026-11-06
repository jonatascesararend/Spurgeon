"""Janela principal do Studio Spurgeon."""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
import tkinter
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

import customtkinter as ctk

from spurgeon.core.aparencia import TratamentoImagem
from spurgeon.core.atualizacao import (
    URL_DA_PAGINA,
    InfoAtualizacao,
    ResultadoDaAtualizacao,
    baixar_e_atualizar,
    consultar_atualizacao,
    motivo_de_nao_atualizar_sozinho,
    reabrir_programa,
)
from spurgeon.core.config import (
    ConfigApp,
    OpcoesProcessamento,
    carregar_config,
    raiz_repositorio,
    salvar_config,
)
from spurgeon.core.erros import ErroSpurgeon, ProjetoConflito
from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.core.executor import ExecutorProjeto
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import resumir_linha_log
from spurgeon.core.hardware import (
    PerfilHardware,
    Recomendacao,
    detectar_hardware,
    recomendar,
    threads_para_uso,
)
from spurgeon.core.ollama import listar_modelos_instalados
from spurgeon.core.preflight import Nivel, Verificacao, executar_preflight, tem_erro
from spurgeon.core.projeto import (
    PastasProjeto,
    abrir_pasta,
    criar_ou_retomar_projeto,
    data_gravacao,
    descrever_projeto,
    ler_nome_e_data,
    nome_pasta_projeto,
)
from spurgeon.publicacao.agenda import criar_plataformas
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.fila import projetos_com_fila, rodar_fila
from spurgeon.publicacao.modelo import ERRO
from spurgeon.render.shorts import assinatura_visual
from spurgeon.shorts.livre import ContextoDaIA
from spurgeon.ui import dialogos, dicas
from spurgeon.ui.aba_studio import PAINEL_CORTES, PAINEL_LIVRE, AbaStudioShorts
from spurgeon.ui.ajuda import dica_do_seletor
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.aviso_de_atualizacao import AvisoDeAtualizacao
from spurgeon.ui.contato import AvisoDeContato, abrir_link
from spurgeon.ui.estimativa import (
    EstimadorTempo,
    estimar_render_s,
    estimar_tempo_total,
    formatar_duracao,
    formatar_estimativa,
    formatar_restante,
)
from spurgeon.ui.formulario import (
    ErroFormulario,
    ValoresFormulario,
    formatar_data,
    ler_data,
    para_opcoes,
    shorts_do_formulario,
)
from spurgeon.ui.integracoes import AbaIntegracoes
from spurgeon.ui.painel_entrada import PainelEntrada
from spurgeon.ui.painel_execucao import EstadoUI, PainelExecucao
from spurgeon.ui.painel_opcoes import NOMES_DAS_PAGINAS, PainelOpcoes
from spurgeon.ui.postagem import ContextoDePublicacao
from spurgeon.ui.presets import RepositorioDePresets
from spurgeon.ui.quadros import FonteDeQuadros
from spurgeon.ui.revisao import abrir_revisao
from spurgeon.ui.rolagem import QuadroRolavel
from spurgeon.ui.studio_shorts import OrigemDaPrevia
from spurgeon.ui.tutorial import (
    PASSOS,
    VISTA_LIVRE,
    VISTA_STUDIO,
    Destino,
    OfertaDoTutorial,
    Passo,
    Tutorial,
)

_INTERVALO_MS = 100
_CICLOS_POR_SEGUNDO = 1000 // _INTERVALO_MS
ABA_PROCESSAMENTO = "Processamento"
ABA_STUDIO = "Studio Shorts"
ABA_INTEGRACOES = "Integrações"
_FILA_PRIMEIRA_MS = 8000  # a fila de postagens começa pouco depois de a janela aparecer
_FILA_MS = 60_000  # e anda a cada minuto
_ABERTURA_MS = 600  # a janela monta e aparece antes da reabertura e da oferta do passo a passo
_ENTRE_REABRIR_E_OFERTAR_MS = 300
_ATUALIZACAO_MS = 1500  # a consulta ao GitHub começa depois de a janela aparecer
_NOTA_SEM_STUDIO = (
    "Esta tela só aparece quando o projeto tem Shorts calculados. Para vê-la de verdade, abra um "
    "projeto que já tenha Shorts (“Abrir projeto…”) ou processe um vídeo."
)
_NOTA_ESCONDIDO = (
    "Esta parte da tela está escondida no modo escolhido. Troque o modo para “Tudo” para vê-la."
)
_ESTADOS_OCUPADOS = (
    EstadoUI.VERIFICANDO,
    EstadoUI.EXECUTANDO,
    EstadoUI.PAUSANDO,
    EstadoUI.PAUSADO,
)
_ESTADOS_RODANDO = (EstadoUI.EXECUTANDO, EstadoUI.PAUSANDO, EstadoUI.PAUSADO)  # com o trabalhador
#: as etapas que só renderizam: os cortes dos Shorts já estão decididos e nenhuma delas os reescreve
#: (a `selecionar_shorts`, mais atrás, é quem escreve o `cortes_shorts.json`). Nelas o Studio Shorts
#: fica à mão, mesmo que as prévias e a edição deixem a renderização um pouco mais lenta.
_ETAPAS_DE_RENDER = frozenset(
    {"renderizar_video_longo", "adicionar_vinhetas", "renderizar_shorts", "finalizar"}
)
_ESPERA_PARA_FECHAR_S = 10  # entre o fim do processamento e o fechamento do programa (a pedido)
_AVISO_DA_RENDERIZACAO = (
    "Renderização em andamento: o Studio segue à mão (editar, salvar, marcar como postado, "
    "agendar). O “Renderizar” volta quando ela terminar."
)


class JanelaPrincipal(ctk.CTk):
    def __init__(
        self,
        config: ConfigApp | None = None,
        *,
        oferecer_tutorial: bool = False,
        reabrir_ultimo: bool = False,
        verificar_atualizacao: bool = False,
    ):
        """`oferecer_tutorial`, `reabrir_ultimo` e `verificar_atualizacao` valem só na abertura de
        verdade do programa (`executar`): ao abrir, reabre o último projeto, oferece o passo a
        passo e pergunta ao GitHub se há versão nova (se a pessoa não desligou). Por padrão
        (testes, janelas escondidas) nada disso acontece, e nenhum teste toca a internet."""
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.config_app = config or carregar_config()
        self.config_app.pastas.garantir()
        self.executor = ExecutorProjeto()
        self.tarefas = Tarefas(self._na_interface)
        self.fonte = FonteDeQuadros(self.tarefas)
        self.presets = RepositorioDePresets(self.config_app)
        self.estimador = EstimadorTempo()
        self._fila_ui: queue.Queue[Callable[[], None]] = queue.Queue()
        self._pastas: PastasProjeto | None = None
        self._config_execucao: ConfigApp | None = None
        self._etapas: list[dict] = []
        self._revisao: dict | None = None
        self._recebeu_fim = False
        self._total = 0.0
        self._estimador_iniciado = False
        self._ciclos = 0
        self._encerrando = False
        self._info_video: InfoMidia | None = None
        self._perfil_hardware: PerfilHardware | None = None
        self._chave_do_studio: tuple[str, bool] | None = None
        self._abrindo_projeto = False
        self.tutorial: Tutorial | None = None
        self._oferta: OfertaDoTutorial | None = None
        self._abertura_agendada: str | None = None
        self._secoes_do_tutorial: set[str] = set()  # seções que o passo a passo abriu
        self._vista_antes: tuple[str, str] | None = None  # a aba e a página de antes dele
        self.cofre = Cofre()  # as chaves das redes (criptografadas pelo Windows)
        self._plataformas = criar_plataformas(self.cofre)
        self._fila_agendada: str | None = None
        self._fila_rodando = False
        self._etapa_atual: str | None = None  # a última etapa que começou nesta execução
        self._motivo_do_studio_antes: str | None = None  # por que o Studio estava travado antes
        self._fechamento_agendado: str | None = None  # o `after` do "fechar ao terminar"

        self.title("Studio Spurgeon")
        self._ajustar_tamanho()
        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.bind("<FocusIn>", self._ao_ganhar_foco)
        self.after(_INTERVALO_MS, self._ciclo)
        threading.Thread(target=self._detectar_hardware, daemon=True).start()
        self._listar_ollama()
        self._agendar_abertura(oferecer_tutorial, reabrir_ultimo)
        if verificar_atualizacao and self.config_app.atualizacao_ao_abrir:
            self.after(_ATUALIZACAO_MS, self._verificar_atualizacao)
        if verificar_atualizacao:  # a abertura de verdade também anda a fila de postagens
            self._fila_agendada = self.after(_FILA_PRIMEIRA_MS, self._rodar_a_fila)

    # --- montagem -----------------------------------------------------------------------------
    def _ajustar_tamanho(self) -> None:
        """Tamanho inicial que cabe na tela (800 px de altura cortaria o rodapé em telas de 768)."""
        escala = self._get_window_scaling()
        tela_l, tela_a = self.winfo_screenwidth() / escala, self.winfo_screenheight() / escala
        largura, altura = int(min(1560, tela_l * 0.96)), int(min(860, tela_a * 0.88))
        x, y = int((tela_l - largura) / 2), max(0, int((tela_a - altura) / 2) - 24)
        self.geometry(f"{largura}x{altura}+{x}+{y}")
        self.minsize(min(980, largura), min(620, altura))

    def _montar(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=0)  # a faixa das abas
        self.rowconfigure(1, weight=1)  # o conteúdo da aba

        # As abas ficam sempre no mesmo lugar, no alto da janela: Processamento mostra as opções à
        # esquerda e o andamento à direita; o Studio Shorts toma a janela inteira.
        faixa = ctk.CTkFrame(self, fg_color="transparent")
        faixa.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 0))
        # o aviso de atualização, quando há, fica por cima das abas (empacotado só ao aparecer)
        self.aviso_de_atualizacao = AvisoDeAtualizacao(faixa, ao_atualizar=self._atualizar_programa)
        linha_das_abas = ctk.CTkFrame(faixa, fg_color="transparent")
        linha_das_abas.pack(side="bottom", fill="x")
        self._abas = ctk.CTkSegmentedButton(
            linha_das_abas,
            values=[ABA_PROCESSAMENTO, ABA_STUDIO, ABA_INTEGRACOES],
            command=self._trocar_aba,
        )
        self._abas.pack(side="left")
        self.contato = AvisoDeContato(linha_das_abas)  # no canto direito, nas duas abas
        self.contato.pack(side="right", padx=(12, 2))
        self._faixa_das_abas = faixa
        dica_do_seletor(
            self._abas,
            {
                ABA_PROCESSAMENTO: "As opções do sermão, à esquerda, e o andamento do "
                "processamento, à direita.",
                ABA_STUDIO: lambda: self._dica_da_aba_studio(),
                ABA_INTEGRACOES: "Liga as contas do YouTube, do Instagram, do Facebook e do "
                "TikTok da igreja (as chaves ficam criptografadas neste computador) para o "
                "Studio Shorts postar e agendar cada Short em todas as redes de uma vez.",
            },
        )

        coluna = ctk.CTkFrame(self, fg_color="transparent")
        coluna.grid(row=1, column=0, sticky="ns", padx=(12, 6), pady=(8, 0))
        self._coluna = coluna
        coluna.rowconfigure(1, weight=1)
        self._paginas = ctk.CTkSegmentedButton(
            coluna, values=list(NOMES_DAS_PAGINAS), command=self._trocar_pagina
        )
        self._paginas.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        dica_do_seletor(
            self._paginas,
            {
                NOMES_DAS_PAGINAS[0]: "O vídeo do sermão e o que gerar: etapas 1 e 2.",
                NOMES_DAS_PAGINAS[1]: "Cortar o começo e o fim, zoom, tratamento de imagem e "
                "marca d'água: etapas 3 a 5.",
                NOMES_DAS_PAGINAS[2]: "Pontos de revisão, uso do computador e qualidade, mais as "
                "opções avançadas: etapas 6 e 7.",
            },
        )
        lateral = QuadroRolavel(coluna, width=500, fg_color="transparent")
        lateral.grid(row=1, column=0, sticky="ns")
        self.lateral = lateral
        pastas = self.config_app.pastas
        self.opcoes = PainelOpcoes(
            lateral,
            valores=ValoresFormulario.de_opcoes(self.config_app.padroes),
            pastas=pastas,
            ao_mudar=self._atualizar_estimativa,
            fonte=self.fonte,
            tarefas=self.tarefas,
            presets=self.presets,
            ao_listar_ollama=self._listar_ollama,
        )
        self.opcoes.pack(fill="x")
        self._paginas.set(NOMES_DAS_PAGINAS[0])
        self.entrada = PainelEntrada(
            self.opcoes.espaco_do_video,
            pasta_entrada=pastas.entrada,
            pasta_projetos=pastas.projetos,
            ao_escolher_video=self._ao_escolher_video,
            valores=ValoresFormulario.de_opcoes(self.config_app.padroes),
            fonte=self.fonte,
            tarefas=self.tarefas,
            presets=self.presets,
            ao_mudar=self._atualizar_estimativa,
            estilo=self._estilo,
            ao_abrir_projeto=self._abrir_projeto,
            opcoes_atuais=self._opcoes_da_tela,
            pasta_fontes=pastas.fontes,
        )
        self.entrada.pack(fill="x", pady=(0, 10))
        # o corte, o zoom e a legenda dos Shorts moram no cartão 3, entre "O que gerar" e o resto
        self.entrada.montar_ajustes(self.opcoes.cartao_ajustes)

        direita = ctk.CTkFrame(self, fg_color="transparent")
        direita.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(8, 0))
        direita.columnconfigure(0, weight=1)
        direita.rowconfigure(0, weight=1)
        self._direita = direita
        self.execucao = PainelExecucao(
            direita,
            ao_iniciar=self._iniciar,
            ao_pausar_retomar=self._pausar_retomar,
            ao_cancelar=self._cancelar,
            ao_abrir_pasta=self._abrir_pasta_projeto,
            ao_abrir_revisao=self._abrir_revisao,
            ao_aprovar=self._aprovar_revisao,
            ao_fechar_revisao=self._fechar_revisao,
        )
        self.execucao.grid(row=0, column=0, sticky="nsew")
        self.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)
        self.studio = AbaStudioShorts(
            self,  # filho da janela, não da coluna da direita: a aba ocupa a janela toda
            ao_renderizar=self._renderizar_do_studio,
            pastas_do_app=pastas,
            legenda_queimada=lambda: self.opcoes.valores().legendas_dinamicas,
            janela_do_video=lambda: self.entrada.aparar.janela(),
            aparencia=self._assinatura_visual,
            tarefas=self.tarefas,
            origem_da_previa=self.origem_da_previa,
            contexto_da_ia=self._contexto_da_ia,
            definir_ajustes=self._definir_ajustes_do_short,
            publicacao=ContextoDePublicacao(
                plataformas=lambda: self._plataformas,
                tarefas=self.tarefas,
                registrar=self._registrar_atividade,
            ),
        )
        self.integracoes = AbaIntegracoes(
            self, plataformas=self._plataformas, cofre=self.cofre, tarefas=self.tarefas
        )  # filho da janela, como o Studio: ocupa a janela toda quando a aba está aberta
        self._abas.set(ABA_PROCESSAMENTO)
        self._trocar_aba(ABA_PROCESSAMENTO)
        self._atualizar_aba_studio()

        # o rodapé: o hardware à esquerda e, à direita, os controles do passo a passo
        self._faixa_do_rodape = ctk.CTkFrame(self, fg_color="transparent")
        self._faixa_do_rodape.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 8))
        self._faixa_do_rodape.columnconfigure(0, weight=1)
        self._rodape = ctk.CTkLabel(
            self._faixa_do_rodape,
            text="Detectando o hardware…",
            anchor="w",
            text_color="gray55",
            font=ctk.CTkFont(size=12),
        )
        self._rodape.grid(row=0, column=0, sticky="ew")
        self._var_atualizacao = ctk.BooleanVar(value=self.config_app.atualizacao_ao_abrir)
        self._check_atualizacao = ctk.CTkCheckBox(
            self._faixa_do_rodape,
            text="Procurar atualizações ao abrir",
            variable=self._var_atualizacao,
            command=self._mudou_atualizacao_ao_abrir,
            font=ctk.CTkFont(size=12),
            checkbox_width=18,
            checkbox_height=18,
        )
        self._check_atualizacao.grid(row=0, column=1, sticky="e", padx=(12, 0))
        dicas.dica(
            self._check_atualizacao,
            "Marcado: ao abrir, o programa consulta o GitHub (precisa de internet) e avisa, no "
            "alto da janela, quando há uma versão nova. É a única coisa que o programa faz online "
            "sem você pedir; desmarcado, ele não consulta nada.",
        )
        # NÃO vai para o config.toml: um programa que se fecha sozinho tem que ser pedido de novo a
        # cada vez, senão um Short renderizado amanhã fecharia a janela de quem está olhando
        self._var_fechar = ctk.BooleanVar(value=False)
        self._check_fechar = ctk.CTkCheckBox(
            self._faixa_do_rodape,
            text="Fechar o programa ao terminar",
            variable=self._var_fechar,
            command=self._mudou_fechar_ao_terminar,
            font=ctk.CTkFont(size=12),
            checkbox_width=18,
            checkbox_height=18,
        )
        self._check_fechar.grid(row=0, column=2, sticky="e", padx=(12, 0))
        dicas.dica(
            self._check_fechar,
            "Para deixar o processamento rodando sem ninguém por perto: quando ele terminar "
            f"bem, o programa espera {_ESPERA_PARA_FECHAR_S} segundos e se fecha sozinho. Fica "
            "aberto se der erro, se você cancelar, se o Studio Shorts tiver alterações não "
            "salvas ou se houver postagens na fila do Spurgeon (o Instagram só publica com o "
            "programa aberto). Vale só para esta vez: ao abrir o programa de novo, volta "
            "desmarcado.",
        )
        self._var_tutorial = ctk.BooleanVar(value=self.config_app.tutorial_ao_abrir)
        self._check_tutorial = ctk.CTkCheckBox(
            self._faixa_do_rodape,
            text="Mostrar o passo a passo ao abrir",
            variable=self._var_tutorial,
            command=self._mudou_tutorial_ao_abrir,
            font=ctk.CTkFont(size=12),
            checkbox_width=18,
            checkbox_height=18,
        )
        self._check_tutorial.grid(row=0, column=3, sticky="e", padx=(12, 12))
        dicas.dica(
            self._check_tutorial,
            "Marcado: ao abrir, o programa pergunta se você quer ver o passo a passo. Desmarcado: "
            "ele não pergunta mais (você ainda pode abri-lo pelo botão “Como usar”).",
        )
        self._botao_tutorial = ctk.CTkButton(
            self._faixa_do_rodape,
            text="Como usar",
            width=96,
            height=26,
            command=self.abrir_tutorial,
        )
        self._botao_tutorial.grid(row=0, column=4, sticky="e")
        dicas.dica(
            self._botao_tutorial,
            "Abre agora o passo a passo que explica cada etapa, da escolha do vídeo até os "
            "Shorts prontos. Você pode sair dele quando quiser.",
        )

    # --- ciclo principal ----------------------------------------------------------------------
    def destroy(self) -> None:
        self._encerrando = True  # callbacks pendentes não devem mexer em widgets já destruídos
        self._cancelar_fechamento()
        tutorial = getattr(self, "tutorial", None)
        if tutorial is not None:
            tutorial.fechar()
        super().destroy()

    def _ciclo(self) -> None:
        if self._encerrando:
            return
        try:
            while True:
                self._fila_ui.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            logging.exception("Falha ao atualizar a interface")
        try:
            for evento in self.executor.eventos():
                self._tratar_evento(evento)
            self._ciclos += 1
            if self._ciclos % _CICLOS_POR_SEGUNDO == 0:
                self._atualizar_tempo()
                self._atualizar_aba_studio()
        except Exception:
            logging.exception("Falha ao tratar eventos do processamento")
        finally:
            if not self._encerrando:
                self.after(_INTERVALO_MS, self._ciclo)

    def _na_interface(self, funcao: Callable[[], None]) -> None:
        """Agenda `funcao` na thread da interface (as threads de trabalho não mexem nos widgets)."""
        self._fila_ui.put(funcao)

    def _trocar_pagina(self, nome: str) -> None:
        """Mostra uma das três páginas da coluna da esquerda e volta ao topo dela."""
        self.opcoes.mostrar_pagina(NOMES_DAS_PAGINAS.index(nome))
        with contextlib.suppress(tkinter.TclError):
            self.lateral._parent_canvas.yview_moveto(0.0)

    def _ao_ganhar_foco(self, evento) -> None:
        if evento.widget is self:
            self.opcoes.atualizar_habilitacoes()  # introdução/vinheta podem ter sido copiadas

    # --- hardware e vídeo ---------------------------------------------------------------------
    def _estilo(self):
        """O zoom e o tratamento de cor que estão na tela (as prévias de corte os usam)."""
        tratamento = (
            self.opcoes.tratamento.tratamento()
            if self.opcoes.tratamento.ativo.get()
            else TratamentoImagem()
        )
        return self.entrada.zoom.enquadramento(), tratamento

    def _valores(self) -> ValoresFormulario:
        """Tudo o que está na tela: as opções (à direita do vídeo) e o corte e o zoom do vídeo."""
        return replace(self.opcoes.valores(), **self.entrada.valores())

    def _opcoes_da_tela(self) -> OpcoesProcessamento | None:
        """As opções que a tela descreve agora, ou None se algum campo está incompleto."""
        try:
            return para_opcoes(self._valores(), self.config_app.padroes)
        except ErroFormulario:
            return None

    def origem_da_previa(self) -> OrigemDaPrevia | None:
        """O vídeo e as opções da tela, para a prévia de um Short; None se falta algo."""
        opcoes = self._opcoes_da_tela()
        if opcoes is None or not self.fonte.pronta:
            return None
        return OrigemDaPrevia(self.fonte.video, self.fonte.info, opcoes)

    def _contexto_da_ia(self) -> ContextoDaIA | None:
        """O modelo do Ollama e quanto da máquina usar no Shorts livre; None se falta algo."""
        opcoes = self._opcoes_da_tela()
        if opcoes is None:
            return None
        perfil = self._perfil_hardware or detectar_hardware(self.config_app.pastas.projetos)
        return ContextoDaIA(
            modelo=opcoes.ollama_modelo,
            threads=threads_para_uso(perfil, opcoes.uso_cpu_percentual),
            usar_gpu=perfil.cuda_utilizavel,
        )

    def _definir_ajustes_do_short(self, *, legenda: float | None = None) -> None:
        """A altura da legenda (de 0,3 a 0,9 da altura do Short) que o Studio Shorts acertou olhando
        a prévia vai para o campo da tela: vale para os Shorts todos e fica como opção salva."""
        if legenda is not None:
            self.entrada.shorts.definir_legenda(legenda * 100)

    def _assinatura_visual(self) -> str | None:
        """A aparência dos Shorts agora (zoom, cor, recorte, marca), para o Studio comparar."""
        opcoes = self._opcoes_da_tela()
        return None if opcoes is None else assinatura_visual(opcoes)

    def _listar_ollama(self) -> None:
        self.tarefas.rodar(listar_modelos_instalados, self.opcoes.definir_modelos_ollama)

    def _detectar_hardware(self) -> None:
        perfil = detectar_hardware(self.config_app.pastas.projetos)
        recomendacao = recomendar(perfil)
        nvenc = nvenc_funciona()
        self._na_interface(lambda: self._aplicar_hardware(perfil, recomendacao, nvenc))

    def _aplicar_hardware(self, perfil: PerfilHardware, rec: Recomendacao, nvenc: bool) -> None:
        self._perfil_hardware = perfil
        self.opcoes.definir_perfil(
            recomendado=rec.whisper_modelo, cuda_utilizavel=perfil.cuda_utilizavel, nvenc=nvenc
        )
        gpu = ", ".join(g.nome for g in perfil.gpus) or "nenhuma"
        cuda = "" if perfil.cuda_utilizavel else " (sem uso para IA)"
        self._rodape.configure(
            text=f"{perfil.cpu_nome} · {perfil.threads_logicos} threads · "
            f"{perfil.ram_total_gb:.1f} GB de RAM · GPU: {gpu}{cuda}"
        )

    def _ao_escolher_video(self, caminho: Path) -> None:
        self._info_video = None
        self.fonte.definir_video(None, None)
        self.entrada.mostrar_estimativa("")
        if self._pastas is not None and not self._abrindo_projeto:
            self._pastas = None  # outro vídeo: não é mais o projeto que estava aberto
            self.entrada.mostrar_projeto("")
            self._atualizar_aba_studio()

        def trabalho() -> None:
            try:
                info, dia = sondar(caminho), data_gravacao(caminho)
            except ErroSpurgeon as erro:
                # O Python apaga `erro` ao sair do except; o lambda roda depois, na interface.
                mensagem = str(erro)
                self._na_interface(lambda: self._video_lido(caminho, None, None, mensagem))
                return
            self._na_interface(lambda: self._video_lido(caminho, info, dia, None))

        threading.Thread(target=trabalho, daemon=True).start()

    def _video_lido(
        self, caminho: Path, info: InfoMidia | None, dia: date | None, erro: str | None
    ) -> None:
        if self.entrada.caminho_video() != caminho:
            return  # o usuário já escolheu outro
        if info is None or not info.tem_video:
            motivo = erro or "o arquivo não tem imagem"
            self.entrada.mostrar_info(f"Não foi possível usar este vídeo: {motivo}", erro=True)
            return
        self._info_video = info
        # a janela antes do vídeo: os quadros de exemplo saem de dentro do trecho usado
        self.fonte.definir_janela(*self.entrada.aparar.janela())
        self.fonte.definir_video(caminho, info)
        fps = f" · {info.fps:.0f} fps" if info.fps else ""
        self.entrada.mostrar_info(
            f"{formatar_duracao(info.duracao_s)} · {info.largura}×{info.altura}{fps}"
            f" · {info.codec_video}"
        )
        if (
            dia is not None and not self.entrada.var_data.get().strip()
        ):  # projeto aberto já tem a sua
            self.entrada.var_data.set(formatar_data(dia))
        self._atualizar_estimativa()

    def _atualizar_estimativa(self) -> None:
        """Mostra quanto tempo o processamento deve levar com o que está marcado na tela."""
        valores = self._valores()
        self.entrada.modo_so_vinhetas(
            valores.modo == "so_vinhetas", shorts=shorts_do_formulario(valores)
        )
        self.fonte.definir_janela(*self.entrada.aparar.janela())
        self.entrada.shorts.atualizar_previa()  # zoom, cor ou recorte podem ter mudado o Short
        if self._info_video is None:
            self.entrada.mostrar_estimativa("")
            return
        try:
            opcoes = para_opcoes(self._valores(), self.config_app.padroes)
        except ErroFormulario:
            return  # há um campo ainda incompleto; a estimativa anterior continua
        total, render = estimar_tempo_total(
            self._info_video.duracao_s, opcoes, hdr=self._info_video.hdr
        )
        self.entrada.mostrar_estimativa(formatar_estimativa(total, render))

    # --- iniciar ------------------------------------------------------------------------------
    def _coletar(
        self, ajustes: dict | None = None
    ) -> tuple[OpcoesProcessamento, Path, str, date] | None:
        """Lê e valida a tela; mostra os problemas e devolve None se houver algum.

        `ajustes` troca valores do formulário só para esta execução (o Studio Shorts força o
        modo só Shorts, sem as pausas de revisão).
        """
        erros: list[str] = []
        opcoes = video = data = None
        try:
            valores = replace(self._valores(), **(ajustes or {}))
            opcoes = para_opcoes(valores, self.config_app.padroes)
        except ErroFormulario as erro:
            erros += erro.mensagens
        video = self.entrada.caminho_video()
        if video is None or not video.is_file():
            erros.append("Escolha um vídeo que exista.")
        elif self._info_video is None:
            erros.append("Aguarde a leitura do vídeo terminar, ou escolha outro arquivo.")
        nome = self.entrada.var_nome.get().strip()
        if not nome:
            erros.append("Dê um nome ao sermão.")
        try:
            data = ler_data(self.entrada.var_data.get())
        except ErroFormulario as erro:
            erros += erro.mensagens
        if opcoes is not None and opcoes.so_vinhetas and nome and data is not None:
            falta = self._falta_o_video_longo(nome, data)
            if falta:
                erros.append(falta)
        if erros:
            dialogos.aviso(self, "Confira os campos", "• " + "\n• ".join(erros))
            return None
        return opcoes, video, nome, data

    def _falta_o_video_longo(self, nome: str, data: date) -> str:
        """O modo só vinhetas precisa de um projeto que já tem o vídeo longo pronto."""
        pasta = self.entrada.pasta_projetos() / nome_pasta_projeto(data, nome)
        if PastasProjeto(pasta).video_longo.exists():
            return ""
        return (
            "Este projeto ainda não tem o vídeo longo pronto, então não há onde juntar a "
            "introdução e a vinheta. Use “Abrir projeto…” e escolha um projeto que já gerou o "
            "vídeo, ou processe-o antes no modo Tudo."
        )

    def _iniciar(self, ajustes: dict | None = None) -> None:
        if self.studio.mudou and not self.studio.salvar():
            return  # há correção nos cortes que precisa de atenção antes de seguir
        coletado = self._coletar(ajustes)
        if coletado is None:
            return
        opcoes, video, nome, data = coletado
        config = self.config_app.model_copy(deep=True)
        config.padroes = opcoes
        config.pastas.projetos = self.entrada.pasta_projetos()

        self._bloquear(True)
        self.execucao.zerar_progresso()
        self.execucao.definir_estado(EstadoUI.VERIFICANDO)

        def trabalho() -> None:
            try:
                resultados = executar_preflight(config, video=video, opcoes=opcoes)
            except Exception as erro:
                resultados = [Verificacao("Verificação do ambiente", Nivel.ERRO, str(erro))]
            self._na_interface(
                lambda: self._verificacao_pronta(resultados, config, video, nome, data)
            )

        threading.Thread(target=trabalho, daemon=True).start()

    def _verificacao_pronta(
        self, resultados: list[Verificacao], config: ConfigApp, video: Path, nome: str, data: date
    ) -> None:
        for r in resultados:
            if r.nivel == Nivel.AVISO:
                self.execucao.adicionar_log(f"Aviso: {r.nome}: {r.detalhe}", logging.WARNING)
        if tem_erro(resultados):
            problemas = [f"• {r.nome}: {r.detalhe}" for r in resultados if r.nivel == Nivel.ERRO]
            dialogos.erro(
                self,
                "O ambiente não está pronto",
                "Corrija o que segue e tente de novo:\n\n" + "\n".join(problemas),
            )
            self._voltar_ao_ocioso()
            return
        try:
            criado = criar_ou_retomar_projeto(
                config.pastas.projetos, video, nome, data, config.padroes
            )
        except ProjetoConflito as erro:
            dialogos.erro(
                self,
                "Projeto já existe",
                f"{erro.motivo}.\n\nMude o nome do sermão ou a data da gravação, ou escolha o "
                f"vídeo original deste projeto.\n\n{erro.pasta}",
            )
            self._voltar_ao_ocioso()
            return
        except ErroSpurgeon as erro:
            dialogos.erro(self, "Não foi possível criar o projeto", str(erro))
            self._voltar_ao_ocioso()
            return

        ja_concluido = bool(criado.estado.etapas) and all(
            e.status == StatusEtapa.CONCLUIDA for e in criado.estado.etapas
        )
        if (
            criado.retomado
            and ja_concluido
            and config.padroes.modo == "completo"
            and not dialogos.confirmar(
                self,
                "Projeto já concluído",
                "Este projeto já foi processado até o fim, e os arquivos intermediários "
                "foram apagados. "
                "Processar de novo vai refazer as etapas que dependem deles.\n\nProcessar de novo?",
                sim="Processar de novo",
                nao="Cancelar",
            )
        ):
            self._voltar_ao_ocioso()
            return

        self._pastas, self._config_execucao = criado.pastas, config
        self._lembrar_preferencias(config, criado.pastas.raiz)
        self.execucao.separar_log()
        self.execucao.adicionar_log(
            ("Retomando o projeto" if criado.retomado else "Projeto criado")
            + f": {criado.pastas.raiz}"
        )
        if config.padroes.modo == "completo":
            abrir_pasta(criado.pastas.raiz)
        self._iniciar_execucao()

    def _preferencias(self, executadas: OpcoesProcessamento) -> OpcoesProcessamento:
        """O que a tela mostra (sem os ajustes só de uma execução), no modo Tudo."""
        try:
            da_tela = para_opcoes(self._valores(), self.config_app.padroes)
        except ErroFormulario:
            da_tela = executadas
        # o começo e o fim são de cada vídeo: não viram preferência para o próximo
        return da_tela.model_copy(
            update={"modo": "completo", "video_inicio_s": 0.0, "video_fim_s": 0.0}
        )

    def _lembrar_preferencias(self, config: ConfigApp, projeto: Path | None = None) -> None:
        self.config_app.padroes = self._preferencias(config.padroes)
        self.config_app.pastas.projetos = config.pastas.projetos
        if projeto is not None:
            self.config_app.ultimo_projeto = str(projeto)  # reaberto na próxima vez
        self._salvar_config("as preferências")

    def _salvar_config(self, o_que: str) -> None:
        """Grava o config.toml; se não der, avisa no registro e segue (não é motivo para parar)."""
        try:
            salvar_config(self.config_app)
        except OSError as erro:
            self.execucao.adicionar_log(f"Não foi possível salvar {o_que}: {erro}", logging.WARNING)

    def _lembrar_ultimo_projeto(self, pasta: Path) -> None:
        """Guarda a pasta do projeto aberto para o programa reabri-la ao iniciar."""
        if self.config_app.ultimo_projeto == str(pasta):
            return
        self.config_app.ultimo_projeto = str(pasta)
        self._salvar_config("o último projeto")

    def _iniciar_execucao(self, aprovar: str | None = None) -> None:
        self._recebeu_fim = False
        self._estimador_iniciado = False
        self._revisao = None
        self._etapa_atual = None  # o Studio só se solta quando uma etapa de renderização começa
        try:
            self.executor.iniciar(self._pastas.raiz, self._config_execucao, aprovar=aprovar)
        except (OSError, RuntimeError) as erro:
            dialogos.erro(self, "Não foi possível iniciar", str(erro))
            self._voltar_ao_ocioso()
            return
        self.execucao.definir_estado(EstadoUI.EXECUTANDO, tem_projeto=True)
        self._atualizar_aba_studio()

    def _voltar_ao_ocioso(self) -> None:
        self._bloquear(False)
        self.execucao.definir_estado(EstadoUI.OCIOSO)
        self._atualizar_aba_studio()

    def _bloquear(self, bloqueado: bool) -> None:
        self.opcoes.bloquear(bloqueado)
        self.entrada.habilitar(not bloqueado)

    # --- controle durante a execução ----------------------------------------------------------
    def _pausar_retomar(self) -> None:
        if self.execucao.estado == EstadoUI.EXECUTANDO:
            self.executor.enviar("pausar")
            self.execucao.definir_estado(EstadoUI.PAUSANDO)
        elif self.execucao.estado in (EstadoUI.PAUSANDO, EstadoUI.PAUSADO):
            self.executor.enviar("retomar")
            self.estimador.retomar()
            self.execucao.definir_estado(EstadoUI.EXECUTANDO)

    def _cancelar(self) -> None:
        if dialogos.confirmar(
            self,
            "Cancelar o processamento?",
            "O projeto fica salvo e pode ser retomado depois, a partir do ponto em que parou.",
            sim="Cancelar o processamento",
            nao="Continuar",
        ):
            self.executor.enviar("cancelar")
            self.execucao.adicionar_log(
                "Cancelamento pedido; aguardando um ponto seguro…", logging.WARNING
            )

    def _abrir_pasta_projeto(self) -> None:
        if self._pastas is not None:
            abrir_pasta(self._pastas.raiz)

    # --- revisão ------------------------------------------------------------------------------
    def _abrir_revisao(self) -> None:
        if self._revisao is None or self._pastas is None:
            return
        if not abrir_revisao(self, self._revisao["id"], self._pastas):
            dialogos.aviso(
                self,
                "Painel de revisão",
                "O painel de revisão desta etapa ainda não existe: ele chega junto com a etapa "
                "(os cortes dos Shorts, na Fase 4).\n\n"
                "Por enquanto você pode aprovar para o processamento seguir.",
            )

    def _aprovar_revisao(self) -> None:
        if self._revisao is None:
            return
        self._iniciar_execucao(aprovar=self._revisao["id"])

    def _fechar_revisao(self) -> None:
        self._voltar_ao_ocioso()

    # --- projetos abertos e Studio Shorts -----------------------------------------------------
    def _trocar_aba(self, nome: str) -> None:
        """Processamento: as opções à esquerda e o andamento à direita. Studio Shorts e
        Integrações: só eles, na janela inteira (mais espaço para editar as legendas e para as
        chaves das redes)."""
        if nome == ABA_STUDIO:
            self._coluna.grid_remove()
            self._direita.grid_remove()
            self.integracoes.grid_remove()
            self.studio.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=12, pady=(8, 0))
        elif nome == ABA_INTEGRACOES:
            self._coluna.grid_remove()
            self._direita.grid_remove()
            self.studio.grid_remove()
            self.integracoes.grid(
                row=1, column=0, columnspan=2, sticky="nsew", padx=12, pady=(8, 0)
            )
            self.integracoes.atualizar()
        else:
            self.studio.grid_remove()
            self.integracoes.grid_remove()
            self._coluna.grid()
            self._direita.grid()

    # --- fila de postagens ------------------------------------------------------------
    def _registrar_atividade(self, texto: str) -> None:
        self.integracoes.registrar(texto)

    def _rodar_a_fila(self) -> None:
        """De minuto em minuto: avança a fila de postagens de todos os projetos (o Instagram
        não agenda pela API: o Spurgeon publica na hora marcada, com o aplicativo aberto)."""
        self._fila_agendada = None
        if self._encerrando:
            return
        if self._fila_rodando:
            self._reagendar_a_fila()
            return
        self._fila_rodando = True
        projetos = self.config_app.pastas.projetos
        plataformas = self._plataformas

        def trabalho() -> list:
            mudancas: list = []
            rodar_fila(
                plataformas, projetos, ao_mudar=lambda p, i, r: mudancas.append((p.raiz.name, i, r))
            )
            return mudancas

        self.tarefas.rodar(trabalho, self._fila_andou, self._fila_falhou)

    def _reagendar_a_fila(self) -> None:
        if not self._encerrando:
            self._fila_agendada = self.after(_FILA_MS, self._rodar_a_fila)

    def _fila_andou(self, mudancas: list) -> None:
        self._fila_rodando = False
        for projeto, id_do_corte, resultado in mudancas:
            nome = self._plataformas[resultado.plataforma].nome
            linha = f"{projeto}, Short {id_do_corte}: {nome} {resultado.rotulo}"
            if resultado.estado == ERRO:
                linha += f". {resultado.detalhe}"
            elif resultado.aviso:
                linha += f". ⚠ {resultado.aviso}"
            self.integracoes.registrar(linha)
        if mudancas:
            self.studio.atualizar_redes()
        self._reagendar_a_fila()

    def _fila_falhou(self, erro: Exception) -> None:
        self._fila_rodando = False
        self.integracoes.registrar(f"Fila de postagens: {erro}")
        self._reagendar_a_fila()

    def _renderizando(self) -> bool:
        """O trabalhador está numa etapa que só renderiza: os cortes já estão decididos."""
        return self.execucao.estado in _ESTADOS_RODANDO and self._etapa_atual in _ETAPAS_DE_RENDER

    def _aviso_da_renderizacao(self) -> str | None:
        """O texto da barra do Studio enquanto há processamento (None se não há): é também o que
        diz ao editor para deixar os arquivos onde estão até o fim."""
        return _AVISO_DA_RENDERIZACAO if self.execucao.estado in _ESTADOS_OCUPADOS else None

    def _motivo_do_bloqueio(self) -> str | None:
        estado = self.execucao.estado
        if estado in _ESTADOS_OCUPADOS and not self._renderizando():
            return (
                "O processamento está em andamento. O Studio Shorts fica à mão quando ele chega "
                "à renderização (os cortes já estão decididos) e também quando terminar, for "
                "pausado e cancelado ou parar para a sua revisão."
            )
        if estado == EstadoUI.REVISAO:
            return (
                "O processamento parou esperando a sua revisão. Na aba Processamento, use "
                "“Abrir revisão” e “Aprovar e continuar”, ou “Fechar” para editar os cortes aqui."
            )
        return None

    def _atualizar_aba_studio(self, *, recarregar: bool = False) -> None:
        """Libera a aba quando há cortes, recarrega o editor e o trava durante o trabalho."""
        pastas = self._pastas
        liberada = pastas is not None and pastas.cortes_shorts.exists()
        botao = self._abas._buttons_dict[ABA_STUDIO]
        botao.configure(state="normal" if liberada else "disabled")
        if not liberada and self._abas.get() == ABA_STUDIO:
            self._abas.set(ABA_PROCESSAMENTO)
            self._trocar_aba(ABA_PROCESSAMENTO)
        chave = (str(pastas.raiz), liberada) if pastas is not None else None
        motivo = self._motivo_do_bloqueio()
        # saiu do bloqueio (chegou a renderização, a revisão fechou…): os cortes podem ter mudado
        # no disco enquanto o editor estava escondido
        liberou = self._motivo_do_studio_antes is not None and motivo is None
        self._motivo_do_studio_antes = motivo
        # antes de recarregar: se o processamento acabou, o editor velho leva os arquivos para as
        # pastas certas; se começou, o novo já nasce sabendo que não pode mexer neles
        self.studio.definir_processamento(self._aviso_da_renderizacao())
        if recarregar or ((chave != self._chave_do_studio or liberou) and not self.studio.mudou):
            self.studio.mostrar_projeto(pastas if liberada else None)
            self._chave_do_studio = chave
        self.studio.bloquear(motivo)

    def _abrir_projeto(self, pasta: Path) -> None:
        """Reabre um projeto: preenche a tela com o que ele tem e libera o Studio Shorts."""
        if self.execucao.estado in _ESTADOS_OCUPADOS or self.executor.ativo:
            dialogos.aviso(self, "Abrir projeto", "Espere o processamento terminar para abrir.")
            return
        if self.studio.mudou and not self.studio.salvar(exigir_ate_o_limite=False):
            return
        try:
            estado = carregar_estado(pasta)
        except (OSError, ValueError, ErroSpurgeon) as erro:
            dialogos.erro(self, "Abrir projeto", f"O estado do projeto não pôde ser lido: {erro}")
            return
        identificado = ler_nome_e_data(pasta)
        if estado is None or identificado is None:
            dialogos.aviso(
                self,
                "Abrir projeto",
                "Esta pasta não parece um projeto do Studio Spurgeon: ela precisa ter o "
                "estado.json e se chamar como `2026-10-04_Nome_do_sermao`.\n\n"
                "Escolha a pasta do projeto dentro da pasta de projetos.",
            )
            return
        nome, dia = identificado
        video = Path(estado.entrada.caminho)
        padroes = self.config_app.padroes
        opcoes = estado.opcoes.model_copy(
            update={
                "modo": "completo",  # os pontos de revisão são os preferidos agora, não os do
                "pausar_revisar_transcricao": padroes.pausar_revisar_transcricao,  # último
                "pausar_aprovar_cortes": padroes.pausar_aprovar_cortes,  # processamento
            }
        )
        self._abrindo_projeto = True
        try:
            self._pastas = PastasProjeto(pasta)
            self.entrada.abrir_projeto(video, nome, formatar_data(dia), pasta.parent)
            valores_do_projeto = ValoresFormulario.de_opcoes(opcoes)
            self.opcoes.aplicar_valores(valores_do_projeto)
            self.entrada.aplicar_valores(valores_do_projeto)
            self._info_video = None
            if video.is_file():
                self.entrada.mostrar_info("Lendo o vídeo…")
                self._ao_escolher_video(video)
            else:
                self.entrada.mostrar_info(
                    f"O vídeo original não está mais em {video}. Para processar ou renderizar de "
                    "novo, escolha-o em Escolher…; para só ver ou editar os cortes, não precisa.",
                    erro=True,
                )
        finally:
            self._abrindo_projeto = False
        self.entrada.mostrar_projeto(
            f"Projeto aberto: {pasta.name}. {descrever_projeto(self._pastas)}"
        )
        self.execucao.separar_log()
        self.execucao.adicionar_log(f"Projeto aberto: {pasta}")
        self.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=True)
        self._atualizar_aba_studio(recarregar=True)
        self._lembrar_ultimo_projeto(pasta)
        if self._pastas.cortes_shorts.exists():
            self._abas.set(ABA_STUDIO)
            self._trocar_aba(ABA_STUDIO)

    # --- a abertura do programa: reabrir o último projeto e oferecer o passo a passo ----------
    def _agendar_abertura(self, oferecer: bool, reabrir: bool) -> str | None:
        """Agenda, para depois de a janela estar montada, a reabertura do último projeto e a oferta
        do passo a passo. Sem nenhum dos dois pedidos, não agenda nada."""
        if not (oferecer or reabrir):
            return None
        self._abertura_agendada = self.after(
            _ABERTURA_MS, lambda: self._ao_abrir_o_programa(oferecer=oferecer, reabrir=reabrir)
        )
        return self._abertura_agendada

    def _ao_abrir_o_programa(self, *, oferecer: bool, reabrir: bool) -> None:
        """Primeiro o último projeto; só depois o passo a passo, para não haver duas janelas
        disputando a atenção (nem o projeto abrindo por baixo da pergunta)."""
        self._abertura_agendada = None
        if self._encerrando:
            return
        if reabrir:
            self._reabrir_ultimo_projeto()
        if oferecer and self.config_app.tutorial_ao_abrir and not self._encerrando:
            self._abertura_agendada = self.after(
                _ENTRE_REABRIR_E_OFERTAR_MS, self._oferecer_tutorial
            )

    def _reabrir_ultimo_projeto(self) -> bool:
        """Abre o projeto que estava aberto da última vez. Em silêncio: se a pasta sumiu ou não é
        mais um projeto, o campo é esquecido e a janela abre vazia, como na primeira vez."""
        texto = self.config_app.ultimo_projeto.strip()
        if not texto:
            return False
        if self.execucao.estado in _ESTADOS_OCUPADOS or self.executor.ativo:
            return False
        pasta = Path(texto)
        try:
            valido = (
                pasta.is_dir()
                and carregar_estado(pasta) is not None
                and ler_nome_e_data(pasta) is not None
            )
        except (OSError, ValueError, ErroSpurgeon):
            valido = False
        if not valido:
            self.config_app.ultimo_projeto = ""
            self._salvar_config("o último projeto")
            return False
        self._abrir_projeto(pasta)
        return self._pastas is not None

    def _mudou_tutorial_ao_abrir(self) -> None:
        self.config_app.tutorial_ao_abrir = bool(self._var_tutorial.get())
        self._salvar_config("a preferência do passo a passo")

    # --- atualização do programa --------------------------------------------------------------
    def _mudou_atualizacao_ao_abrir(self) -> None:
        self.config_app.atualizacao_ao_abrir = bool(self._var_atualizacao.get())
        self._salvar_config("a preferência de procurar atualizações")
        if not self.config_app.atualizacao_ao_abrir:
            self.aviso_de_atualizacao.ocultar()

    def _verificar_atualizacao(self) -> None:
        """Pergunta ao GitHub, em segundo plano, se há versão nova. Sem internet, em silêncio: o
        programa funciona offline e não tem por que reclamar disso."""
        if self._encerrando:
            return
        self.tarefas.rodar(
            consultar_atualizacao,
            self._achou_atualizacao,
            self._sem_consulta_de_atualizacao,
            chave="consultar_atualizacao",
        )

    def _achou_atualizacao(self, info: InfoAtualizacao | None) -> None:
        if info is not None and not self._encerrando:
            self.aviso_de_atualizacao.mostrar(info)

    @staticmethod
    def _sem_consulta_de_atualizacao(erro: Exception) -> None:
        logging.info("Não foi possível procurar atualização: %s", erro)

    def _atualizar_programa(self, info: InfoAtualizacao) -> None:
        """O clique no aviso: confirma, baixa a versão nova do GitHub, troca os arquivos, reabre."""
        if self.execucao.estado in _ESTADOS_OCUPADOS or self.executor.ativo:
            dialogos.aviso(
                self,
                "Atualizar o programa",
                "Espere o processamento terminar (ou cancele-o) para atualizar.",
            )
            return
        raiz = raiz_repositorio()
        if (motivo := motivo_de_nao_atualizar_sozinho(raiz)) is not None:
            if dialogos.confirmar(
                self,
                "Atualizar o programa",
                f"{motivo}\n\nAbrir a página do projeto no GitHub?",
                sim="Abrir a página",
                nao="Fechar",
            ):
                abrir_link(URL_DA_PAGINA)
            return
        if self.studio.mudou and not self.studio.salvar(exigir_ate_o_limite=False):
            return
        if not dialogos.confirmar(
            self,
            "Atualizar o programa",
            f"Atualizar da versão {info.versao_atual} para a {info.versao}?\n\n"
            "O programa baixa a versão nova do GitHub, troca os arquivos e reabre sozinho. Seus "
            "projetos, modelos e configurações não são mexidos. Leva um ou dois minutos.",
            sim="Atualizar agora",
            nao="Depois",
        ):
            return
        self.aviso_de_atualizacao.mostrar_andamento("Baixando a atualização… 0%")

        def trabalho() -> ResultadoDaAtualizacao:
            return baixar_e_atualizar(
                raiz, info, ao_progresso=lambda f: self._na_interface(lambda: self._baixando(f))
            )

        self.tarefas.rodar(trabalho, self._atualizado, self._atualizacao_falhou, chave="atualizar")

    def _baixando(self, fracao: float) -> None:
        if self._encerrando:
            return
        if fracao >= 1.0:
            texto = "Instalando a atualização…"
        elif fracao < 0:  # o GitHub não diz o tamanho do ZIP: sem porcentagem
            texto = "Baixando a atualização…"
        else:
            texto = f"Baixando a atualização… {fracao:.0%}"
        self.aviso_de_atualizacao.mostrar_andamento(texto)

    def _atualizado(self, resultado: ResultadoDaAtualizacao) -> None:
        if self._encerrando:
            return
        depois = (
            "Ela usa bibliotecas novas: o instalador vai rodar numa janela e o programa reabre "
            "quando ele terminar."
            if resultado.precisa_instalar
            else "O programa vai reabrir agora."
        )
        dialogos.aviso(
            self, "Atualização concluída", f"A versão {resultado.versao} foi instalada.\n\n{depois}"
        )
        reabrir_programa(raiz_repositorio(), instalar=resultado.precisa_instalar, pid=os.getpid())
        self._ao_fechar()

    def _atualizacao_falhou(self, erro: Exception) -> None:
        if self._encerrando:
            return
        self.aviso_de_atualizacao.voltar_ao_aviso()
        dialogos.erro(self, "Atualizar o programa", f"Não foi possível atualizar: {erro}")

    def _oferecer_tutorial(self) -> None:
        """A pergunta "Quer ver o passo a passo?" (uma só janela por vez)."""
        self._abertura_agendada = None
        if self._encerrando or self._oferta is not None:
            return
        if self.tutorial is not None and self.tutorial.aberto:
            return
        self._oferta = OfertaDoTutorial(self, ao_responder=self._oferta_respondida)

    def _oferta_respondida(self, ver: bool, nao_mostrar_mais: bool) -> None:
        self._oferta = None
        if nao_mostrar_mais:
            self.config_app.tutorial_ao_abrir = False
            self._var_tutorial.set(False)
            self._salvar_config("a preferência do passo a passo")
        if ver and not self._encerrando:
            self.abrir_tutorial()

    def abrir_tutorial(self, indice: int = 0) -> None:
        """Abre o passo a passo no passo `indice` (se já está aberto, só o traz para a frente)."""
        if self._oferta is not None:
            self._oferta.escolher(True)  # responde a pergunta; ela mesma abre o passo a passo
            return
        if self.tutorial is not None and self.tutorial.aberto:
            self.tutorial.iniciar(indice)
            return
        self._vista_antes = (self._abas.get(), self._paginas.get())
        self.tutorial = Tutorial(
            self, PASSOS, ir_para=self._ir_para_passo, ao_fechar=self._tutorial_fechado
        )
        self.tutorial.iniciar(indice)

    def _tutorial_fechado(self, _concluido: bool) -> None:
        """Devolve a tela ao que era antes do passo a passo (aba, página, seções que ele abriu)."""
        self.tutorial = None
        for nome in self._secoes_do_tutorial:
            self.entrada.secoes[nome].definir(False)
        self._secoes_do_tutorial.clear()
        if self._vista_antes is None or self._encerrando:
            return
        aba, pagina = self._vista_antes
        self._vista_antes = None
        if aba == ABA_STUDIO and not self.studio.liberada:
            aba = ABA_PROCESSAMENTO
        self._mostrar_aba(aba)
        if pagina in NOMES_DAS_PAGINAS:
            self._mostrar_pagina(NOMES_DAS_PAGINAS.index(pagina))

    def _dica_da_aba_studio(self) -> str:
        if self.studio.liberada:
            return (
                "Revise os Shorts sugeridos, corrija a legenda, ajuste o começo e o fim de cada "
                "um e mande renderizar."
            )
        return (
            "Fica liberada quando o projeto tem Shorts calculados: depois de processar um vídeo, "
            "ou ao abrir um projeto que já os tenha."
        )

    def _mostrar_aba(self, nome: str) -> None:
        if self._abas.get() != nome:
            self._abas.set(nome)
        self._trocar_aba(nome)

    def _mostrar_pagina(self, indice: int) -> None:
        nome = NOMES_DAS_PAGINAS[indice]
        if self._paginas.get() != nome:
            self._paginas.set(nome)
        self._trocar_pagina(nome)

    def _ir_para_passo(self, passo: Passo) -> Destino:
        """Prepara a tela para um passo do tutorial: mostra a aba e a página, abre a seção, rola
        até o que será explicado e devolve o que destacar."""
        nota = ""
        no_studio = passo.vista in (VISTA_STUDIO, VISTA_LIVRE)
        if no_studio and self.studio.liberada and self.studio.editor is not None:
            self._mostrar_aba(ABA_STUDIO)
            painel = PAINEL_LIVRE if passo.vista == VISTA_LIVRE else PAINEL_CORTES
            self.studio.mostrar_painel(painel)
            alvo = self._alvo_do_studio(passo.alvo)
        elif no_studio:  # sem cortes calculados a aba está desligada: só se mostra onde ela fica
            self._mostrar_aba(ABA_PROCESSAMENTO)
            alvo = self._abas._buttons_dict[ABA_STUDIO]
            nota = _NOTA_SEM_STUDIO
        else:
            self._mostrar_aba(ABA_PROCESSAMENTO)
            if passo.pagina is not None:
                self._mostrar_pagina(passo.pagina)
            alvo = self._alvo_do_passo(passo.alvo)
        self.update_idletasks()
        self._rolar_ate(alvo)
        if not nota and alvo is not None and self.winfo_viewable() and not _a_vista(alvo):
            nota = _NOTA_ESCONDIDO
        return Destino(alvo, nota)

    def _abrir_secao(self, nome: str) -> ctk.CTkFrame:
        secao = self.entrada.secoes[nome]
        if not secao.aberta:
            secao.definir(True)
            self._secoes_do_tutorial.add(nome)
            self.update_idletasks()
        return secao

    def _alvo_do_passo(self, chave: str):
        """O widget (ou a lista de widgets) que o passo `chave` explica, na aba Processamento."""
        o, e, x = self.opcoes, self.entrada, self.execucao
        alvos = {
            "como_usar": lambda: self._botao_tutorial,
            "video_arquivo": lambda: [e.campo_arquivo, e.faixa_de_botoes],
            "video_dados": lambda: [
                e.campo_nome,
                e.campo_data,
                e.campo_projetos,
                e.botao_alterar_pasta,
            ],
            "modo": lambda: o._seletor_modo,
            "audio": lambda: o._grupos["audio"],
            "longo": lambda: o._grupos["longo"],
            "vinhetas": lambda: o._grupos["vinhetas"],
            "shorts": lambda: o._grupos["shorts"],
            "aparar": lambda: self._abrir_secao("aparar"),
            "zoom": lambda: self._abrir_secao("zoom"),
            "shorts_zoom": lambda: self._abrir_secao("shorts"),
            "tratamento": lambda: o.tratamento,
            "marca": lambda: o.marca,
            "revisao": lambda: o.cartoes["revisao"],
            "processamento": lambda: o.cartoes["processamento"],
            "iniciar": lambda: [x.barra, x.botao_iniciar],
            "pausar_cancelar": lambda: [x.botao_pausar, x.botao_cancelar],
            "execucao": lambda: self._direita,
            "pasta": lambda: x.botao_pasta,
        }
        procurar = alvos.get(chave)
        return procurar() if procurar is not None else None

    def _alvo_do_studio(self, chave: str):
        """O widget que o passo `chave` explica no Studio Shorts. Se a aba oferece
        `alvos_do_tutorial()` (chave → widget), vale o que ela disser; senão procura os widgets
        conhecidos, e o que não achar vira a aba inteira."""
        estudio = self.studio
        oferecidos = getattr(estudio, "alvos_do_tutorial", None)
        if callable(oferecidos):
            try:
                achado = (oferecidos() or {}).get(chave)
            except Exception:
                logging.getLogger(__name__).exception("A aba Studio não soube dizer o alvo")
                achado = None
            if achado is not None:
                return achado
        editor = estudio.editor
        conhecidos = {
            "aba_studio": lambda: self._abas._buttons_dict[ABA_STUDIO],
            "studio_lista": lambda: editor._lista,
            "studio_tabela": lambda: _existentes(editor, "_ferramentas", "_janela"),
            "studio_edicao": lambda: (
                [
                    botao
                    for botoes in getattr(editor, "_botoes_do_historico", {}).values()
                    for botao in botoes
                ]
                or editor._texto
            ),
            "studio_texto": lambda: [
                editor._campo_titulo,
                editor._campo_descricao,
                editor._campo_hashtags,
            ],
            "studio_previa": lambda: editor._dica_previa.master,
            "studio_rodape": lambda: _existentes(
                estudio, "_botao_descartar", "_botao_salvar", "_botao_renderizar"
            ),
            "studio_livre": lambda: estudio._livre,
        }
        try:
            achado = conhecidos[chave]()
        except (AttributeError, KeyError, TypeError):
            achado = None
        return achado if achado is not None else estudio

    def _rolar_ate(self, alvo) -> None:
        """Rola a área rolável que contém o alvo até ele aparecer (no começo, se não cabe)."""
        if isinstance(alvo, (list, tuple)):
            alvo = alvo[0] if alvo else None
        if alvo is None:
            return
        # o quadro rolável que CONTÉM o alvo (um quadro rolável como alvo só rola a si mesmo)
        quadro = getattr(_visivel_de(alvo), "master", None)
        while quadro is not None and getattr(quadro, "_parent_canvas", None) is None:
            quadro = getattr(quadro, "master", None)
        if quadro is None:
            return
        try:
            tela = quadro._parent_canvas
            tela.configure(scrollregion=tela.bbox("all"))  # o Tk só o refaz no próximo evento
            visivel = _visivel_de(alvo)
            y_na_tela = visivel.winfo_rooty() - tela.winfo_rooty()
            altura_da_tela = tela.winfo_height()
            if y_na_tela >= 0 and y_na_tela + min(visivel.winfo_height(), altura_da_tela) <= (
                altura_da_tela
            ):
                return  # já está à vista
            total = max(1, quadro.winfo_height())
            y_no_conteudo = visivel.winfo_rooty() - quadro.winfo_rooty()
            tela.yview_moveto(max(0, y_no_conteudo - 12) / total)
            tela.update_idletasks()
        except tkinter.TclError:
            pass

    def _renderizar_do_studio(self) -> None:
        """Renderiza os Shorts marcados que ainda não existem (ou mudaram), sem o resto."""
        if self._pastas is None or not self.studio.liberada or self.studio.editor is None:
            return
        if self.execucao.estado in _ESTADOS_OCUPADOS or self.executor.ativo:
            dialogos.aviso(
                self,
                "Renderizar os Shorts",
                "Já há uma renderização em andamento. Quando ela terminar, o que você mudou "
                "nos Shorts nesse meio tempo pode ser renderizado.",
            )
            return
        if not self.studio.salvar():
            return
        pendentes = self.studio.editor.pendentes()
        if not pendentes:
            self.studio.aviso_sem_pendentes()
            return
        video = self.entrada.caminho_video()
        if video is None or not video.is_file():
            dialogos.aviso(
                self,
                "Vídeo original",
                "Para renderizar é preciso o vídeo original. Escolha-o em Arquivo → Escolher… "
                "(tem que ser o mesmo vídeo do projeto).",
            )
            return
        ajustes = {"modo": "so_shorts", "aprovar_cortes": False, "revisar_transcricao": False}
        coletado = self._coletar(ajustes)
        if coletado is None:
            return
        opcoes = coletado[0]
        if not dialogos.confirmar(
            self,
            "Renderizar os Shorts",
            self._texto_da_confirmacao(opcoes, pendentes),
            sim="Renderizar",
            nao="Voltar",
        ):
            return
        self._abas.set(ABA_PROCESSAMENTO)
        self._trocar_aba(ABA_PROCESSAMENTO)
        self._iniciar(ajustes)

    def _texto_da_confirmacao(self, opcoes: OpcoesProcessamento, pendentes: list) -> str:
        n = len(pendentes)
        total = sum(c.duracao for c in pendentes)
        linhas = [f"{n} Short{'s' if n != 1 else ''} ({total:.0f} s de vídeo no total)."]
        if self._info_video is not None:
            render = estimar_render_s(
                total, opcoes.preset_qualidade().preset, hdr=self._info_video.hdr
            )
            linhas.append(f"Tempo estimado nesta máquina: {formatar_restante(render)}.")
            if self._pastas is not None and not self._pastas.audio_final(opcoes).exists():
                refazer = self._info_video.duracao_s * 0.2
                linhas.append(
                    "O áudio do sermão será refeito antes (os arquivos temporários foram "
                    f"apagados ao terminar): mais {formatar_restante(refazer)}."
                )
        if opcoes.marca_nos_curtos:
            linhas.append("A marca d'água será colocada nos Shorts renderizados agora.")
        linhas.append(
            "Os Shorts que já estão prontos e não mudaram não são refeitos; os desmarcados vão "
            "para a pasta “desmarcados” (nada é apagado)."
        )
        return "\n\n".join(linhas)

    # --- eventos do processamento -------------------------------------------------------------
    def _tratar_evento(self, ev: dict) -> None:
        tipo = ev["tipo"]
        if tipo == "etapas":
            self._etapas = ev["etapas"]
        elif tipo == "log":
            self.execucao.adicionar_log(
                resumir_linha_log(ev["texto"]), ev.get("nivel", logging.INFO)
            )
        elif tipo == "progresso":
            self._ao_progresso(ev)
        elif tipo == "pausa":
            self._ao_pausa(ev["ativa"])
        elif tipo == "fim":
            self._ao_fim(ev)
        elif tipo == "processo_encerrado":
            self._ao_processo_encerrado(ev)

    def _ao_progresso(self, ev: dict) -> None:
        self._etapa_atual = ev["etapa"]
        self._total = ev["total"]
        if not self._estimador_iniciado:
            self.estimador.iniciar(fracao_inicial=self._total)
            self._estimador_iniciado = True
        self.estimador.atualizar(self._total)
        nomes = [e["id"] for e in self._etapas]
        nome = next((e["nome"] for e in self._etapas if e["id"] == ev["etapa"]), ev["etapa"])
        posicao = (
            f"Etapa {nomes.index(ev['etapa']) + 1} de {len(nomes)}: "
            if ev["etapa"] in nomes
            else ""
        )
        self.execucao.atualizar_progresso(
            self._total, f"{posicao}{nome} · {round(ev['fracao'] * 100)}%"
        )

    def _ao_pausa(self, ativa: bool) -> None:
        if ativa:
            self.estimador.pausar()
            self.execucao.definir_estado(EstadoUI.PAUSADO)
        elif self.execucao.estado in (EstadoUI.PAUSANDO, EstadoUI.PAUSADO):
            self.estimador.retomar()
            self.execucao.definir_estado(EstadoUI.EXECUTANDO)

    def _atualizar_tempo(self) -> None:
        if self.execucao.estado not in (EstadoUI.EXECUTANDO, EstadoUI.PAUSANDO, EstadoUI.PAUSADO):
            return
        if not self._estimador_iniciado:
            return
        texto = (
            f"Decorrido: {formatar_duracao(self.estimador.decorrido())}"
            f" · Tempo restante: {formatar_restante(self.estimador.restante(self._total))}"
        )
        if self.execucao.estado == EstadoUI.PAUSADO:
            texto += " (em pausa)"
        self.execucao.definir_tempo(texto)

    def _ao_fim(self, ev: dict) -> None:
        self._recebeu_fim = True
        resultado = ev["resultado"]
        pasta = str(self._pastas.raiz) if self._pastas else ""
        if resultado == "aguardando_revisao":
            self._revisao = ev.get("revisao") or {"id": "", "nome": "etapa"}
            self.execucao.definir_estado(EstadoUI.REVISAO, tem_projeto=True)
            self.execucao.definir_etapa(f"{self._revisao['nome']}: aguardando a sua revisão")
            self.execucao.mostrar_revisao(self._revisao["nome"])
            self._atualizar_aba_studio()
            return  # a tela continua bloqueada até aprovar ou fechar
        self.execucao.definir_tempo(
            f"Tempo desta execução: {formatar_duracao(self.estimador.decorrido())}"
        )
        if resultado == "concluido":
            self.execucao.atualizar_progresso(1.0, "")
            self.execucao.definir_estado(
                EstadoUI.CONCLUIDO, f"Os arquivos estão em:\n{pasta}", tem_projeto=True
            )
        elif resultado == "cancelado":
            self.execucao.definir_estado(EstadoUI.CANCELADO, tem_projeto=True)
        else:
            mensagem = ev.get("erro") or "erro desconhecido"
            self.execucao.definir_estado(EstadoUI.ERRO, mensagem, tem_projeto=True)
            dialogos.erro(
                self,
                "Erro no processamento",
                f"{mensagem}\n\nO projeto foi salvo e pode ser retomado. Os detalhes estão no "
                f"processamento.log da pasta do projeto.",
            )
        self._bloquear(False)
        # o Studio ficou à mão durante a renderização: com alterações não salvas ele não é refeito
        # (só relê o que foi renderizado), senão elas se perderiam
        self._atualizar_aba_studio(recarregar=not self.studio.mudou)
        if resultado == "concluido":
            self._fechar_se_pedido()

    def _ao_processo_encerrado(self, ev: dict) -> None:
        if self._recebeu_fim:
            return
        cauda = "\n".join(ev.get("stderr", [])[-6:]) or "sem mensagem de erro"
        self.execucao.definir_estado(
            EstadoUI.ERRO,
            "O processamento terminou de forma inesperada.",
            tem_projeto=self._pastas is not None,
        )
        dialogos.erro(
            self,
            "O processamento parou",
            f"O processo terminou sem avisar (código {ev.get('codigo')}).\n\n{cauda}\n\n"
            "O projeto pode ser retomado.",
        )
        self._bloquear(False)

    # --- fechar -------------------------------------------------------------------------------
    def _ao_fechar(self) -> None:
        na_fila = len(projetos_com_fila(self.config_app.pastas.projetos))
        if na_fila and not dialogos.confirmar(
            self,
            "Sair do aplicativo?",
            f"Há postagens na fila do Spurgeon em {na_fila} projeto{'s' if na_fila > 1 else ''} "
            "(o Instagram não agenda pela API). Elas só saem enquanto o aplicativo está aberto. "
            "Sair mesmo assim?",
            sim="Sair",
            nao="Continuar",
        ):
            return
        if self.executor.ativo:
            if not dialogos.confirmar(
                self,
                "Sair do aplicativo?",
                "Há um processamento em andamento. Ao sair ele será cancelado, mas o projeto "
                "fica salvo e pode ser retomado depois.",
                sim="Cancelar e sair",
                nao="Continuar",
            ):
                return
            self.executor.encerrar()
        self.fonte.limpar()
        self.destroy()

    # --- fechar ao terminar -------------------------------------------------------------------
    def _mudou_fechar_ao_terminar(self) -> None:
        """A caixa "Fechar o programa ao terminar" (só vale para esta vez, não é salva). Desmarcar
        durante a espera do fim cancela o fechamento."""
        if self._var_fechar.get():
            self.execucao.adicionar_log(
                "O programa vai se fechar sozinho quando o processamento terminar bem."
            )
        else:
            self._cancelar_fechamento()

    def _cancelar_fechamento(self) -> None:
        agendado = getattr(self, "_fechamento_agendado", None)
        self._fechamento_agendado = None
        if agendado is not None:
            with contextlib.suppress(tkinter.TclError):
                self.after_cancel(agendado)

    def _fechar_se_pedido(self) -> None:
        """O processamento terminou bem e a pessoa pediu para fechar: espera uns segundos (para
        dar tempo de desistir ou de ler o registro) e fecha."""
        if not self._var_fechar.get() or self._encerrando:
            return
        self.execucao.adicionar_log(
            f"O programa se fecha sozinho em {_ESPERA_PARA_FECHAR_S} s, como pedido. Para "
            "impedir, desmarque “Fechar o programa ao terminar”."
        )
        self._cancelar_fechamento()
        self._fechamento_agendado = self.after(
            _ESPERA_PARA_FECHAR_S * 1000, self._fechar_ao_terminar
        )

    def _fechar_ao_terminar(self) -> None:
        self._fechamento_agendado = None
        if self._encerrando or not self._var_fechar.get():
            return  # a pessoa desmarcou nesse meio tempo
        if self._fila_rodando:  # uma postagem está saindo agora: espera ela acabar
            self._fechamento_agendado = self.after(5000, self._fechar_ao_terminar)
            return
        motivo = self._motivo_para_nao_fechar()
        if motivo is not None:
            self.execucao.adicionar_log(
                f"O programa não se fechou sozinho: {motivo}", logging.WARNING
            )
            return
        self.fonte.limpar()
        self.destroy()

    def _motivo_para_nao_fechar(self) -> str | None:
        """Por que o fechamento a pedido não deve acontecer agora (None: pode fechar). Sem
        ninguém olhando, perguntar não adianta: na dúvida o programa fica aberto."""
        if self.execucao.estado in _ESTADOS_OCUPADOS or self.executor.ativo:
            return "começou outro processamento."
        if self.studio.mudou:
            return "há alterações não salvas no Studio Shorts."
        na_fila = len(projetos_com_fila(self.config_app.pastas.projetos))
        if na_fila:
            return (
                f"há postagens na fila do Spurgeon em {na_fila} projeto{'s' if na_fila > 1 else ''}"
                " (o Instagram só publica com o programa aberto)."
            )
        return None


def _visivel_de(widget):
    """O widget que de fato aparece na tela (num quadro rolável, o quadro externo)."""
    externo = getattr(widget, "_parent_frame", None)
    return widget if externo is None else externo


def _existentes(objeto, *nomes: str) -> list:
    """Os atributos de `objeto` com estes nomes que existem (a aba Studio ainda está mudando)."""
    achados = [getattr(objeto, nome, None) for nome in nomes]
    return [a for a in achados if a is not None]


def _a_vista(alvo) -> bool:
    """Algum dos widgets está na tela (não escondido por um modo, uma página ou uma aba)?"""
    alvos = alvo if isinstance(alvo, (list, tuple)) else [alvo]
    try:
        return any(_visivel_de(a).winfo_viewable() for a in alvos if a is not None)
    except tkinter.TclError:
        return False


def executar() -> int:
    """Abre o programa: reabre o último projeto e oferece o passo a passo (se não dispensado)."""
    JanelaPrincipal(
        oferecer_tutorial=True, reabrir_ultimo=True, verificar_atualizacao=True
    ).mainloop()
    return 0
