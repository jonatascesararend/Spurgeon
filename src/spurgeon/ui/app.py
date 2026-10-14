"""Janela principal do Studio Spurgeon."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

import customtkinter as ctk

from spurgeon.core.config import ConfigApp, OpcoesProcessamento, carregar_config, salvar_config
from spurgeon.core.erros import ErroSpurgeon, ProjetoConflito
from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.core.executor import ExecutorProjeto
from spurgeon.core.ffmpeg import InfoMidia, nvenc_funciona, sondar
from spurgeon.core.formatacao import resumir_linha_log
from spurgeon.core.hardware import PerfilHardware, Recomendacao, detectar_hardware, recomendar
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
from spurgeon.ui import dialogos
from spurgeon.ui.aba_studio import AbaStudioShorts
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
)
from spurgeon.ui.painel_entrada import PainelEntrada
from spurgeon.ui.painel_execucao import EstadoUI, PainelExecucao
from spurgeon.ui.painel_opcoes import PainelOpcoes
from spurgeon.ui.revisao import abrir_revisao

_INTERVALO_MS = 100
_CICLOS_POR_SEGUNDO = 1000 // _INTERVALO_MS
ABA_PROCESSAMENTO = "Processamento"
ABA_STUDIO = "Studio Shorts"
_ESTADOS_OCUPADOS = (
    EstadoUI.VERIFICANDO,
    EstadoUI.EXECUTANDO,
    EstadoUI.PAUSANDO,
    EstadoUI.PAUSADO,
)


class JanelaPrincipal(ctk.CTk):
    def __init__(self, config: ConfigApp | None = None):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.config_app = config or carregar_config()
        self.config_app.pastas.garantir()
        self.executor = ExecutorProjeto()
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
        self._chave_do_studio: tuple[str, bool] | None = None
        self._abrindo_projeto = False

        self.title("Studio Spurgeon")
        self._ajustar_tamanho()
        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.bind("<FocusIn>", self._ao_ganhar_foco)
        self.after(_INTERVALO_MS, self._ciclo)
        threading.Thread(target=self._detectar_hardware, daemon=True).start()

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
        self.rowconfigure(0, weight=1)

        lateral = ctk.CTkScrollableFrame(self, width=500, fg_color="transparent")
        lateral.grid(row=0, column=0, sticky="ns", padx=(12, 6), pady=(12, 0))
        self.lateral = lateral
        pastas = self.config_app.pastas
        self.entrada = PainelEntrada(
            lateral,
            pasta_entrada=pastas.entrada,
            pasta_projetos=pastas.projetos,
            ao_escolher_video=self._ao_escolher_video,
            ao_abrir_projeto=self._abrir_projeto,
        )
        self.entrada.pack(fill="x", pady=(0, 10))
        self.opcoes = PainelOpcoes(
            lateral,
            valores=ValoresFormulario.de_opcoes(self.config_app.padroes),
            pastas=pastas,
            ao_mudar=self._atualizar_estimativa,
        )
        self.opcoes.pack(fill="x")

        direita = ctk.CTkFrame(self, fg_color="transparent")
        direita.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(12, 0))
        direita.columnconfigure(0, weight=1)
        direita.rowconfigure(1, weight=1)
        self._abas = ctk.CTkSegmentedButton(
            direita, values=[ABA_PROCESSAMENTO, ABA_STUDIO], command=self._trocar_aba
        )
        self._abas.grid(row=0, column=0, sticky="w", pady=(0, 8))
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
        self.execucao.grid(row=1, column=0, sticky="nsew")
        self.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)
        self.studio = AbaStudioShorts(
            direita,
            ao_renderizar=self._renderizar_do_studio,
            pastas_do_app=pastas,
            legenda_queimada=lambda: self.opcoes.valores().legendas_dinamicas,
        )
        self.studio.grid(row=1, column=0, sticky="nsew")
        self._abas.set(ABA_PROCESSAMENTO)
        self._trocar_aba(ABA_PROCESSAMENTO)
        self._atualizar_aba_studio()

        self._rodape = ctk.CTkLabel(
            self,
            text="Detectando o hardware…",
            anchor="w",
            text_color="gray55",
            font=ctk.CTkFont(size=12),
        )
        self._rodape.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 8))

    # --- ciclo principal ----------------------------------------------------------------------
    def destroy(self) -> None:
        self._encerrando = True  # callbacks pendentes não devem mexer em widgets já destruídos
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

    def _ao_ganhar_foco(self, evento) -> None:
        if evento.widget is self:
            self.opcoes.atualizar_habilitacoes()  # introdução/vinheta podem ter sido copiadas

    # --- hardware e vídeo ---------------------------------------------------------------------
    def _detectar_hardware(self) -> None:
        perfil = detectar_hardware(self.config_app.pastas.projetos)
        recomendacao = recomendar(perfil)
        nvenc = nvenc_funciona()
        self._na_interface(lambda: self._aplicar_hardware(perfil, recomendacao, nvenc))

    def _aplicar_hardware(self, perfil: PerfilHardware, rec: Recomendacao, nvenc: bool) -> None:
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
        if self._info_video is None:
            self.entrada.mostrar_estimativa("")
            return
        try:
            opcoes = para_opcoes(self.opcoes.valores(), self.config_app.padroes)
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
            valores = replace(self.opcoes.valores(), **(ajustes or {}))
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
        self._lembrar_preferencias(config)
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
            da_tela = para_opcoes(self.opcoes.valores(), self.config_app.padroes)
        except ErroFormulario:
            da_tela = executadas
        return da_tela.model_copy(update={"modo": "completo"})

    def _lembrar_preferencias(self, config: ConfigApp) -> None:
        self.config_app.padroes = self._preferencias(config.padroes)
        self.config_app.pastas.projetos = config.pastas.projetos
        try:
            salvar_config(self.config_app)
        except OSError as erro:
            self.execucao.adicionar_log(
                f"Não foi possível salvar as preferências: {erro}", logging.WARNING
            )

    def _iniciar_execucao(self, aprovar: str | None = None) -> None:
        self._recebeu_fim = False
        self._estimador_iniciado = False
        self._revisao = None
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
        (self.studio if nome == ABA_STUDIO else self.execucao).tkraise()

    def _motivo_do_bloqueio(self) -> str | None:
        estado = self.execucao.estado
        if estado in _ESTADOS_OCUPADOS:
            return (
                "O processamento está em andamento. O Studio Shorts volta a ficar disponível "
                "quando ele terminar, for pausado e cancelado ou parar para a sua revisão."
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
        if recarregar or (chave != self._chave_do_studio and not self.studio.mudou):
            self.studio.mostrar_projeto(pastas if liberada else None)
            self._chave_do_studio = chave
        self.studio.bloquear(self._motivo_do_bloqueio())

    def _abrir_projeto(self, pasta: Path) -> None:
        """Reabre um projeto: preenche a tela com o que ele tem e libera o Studio Shorts."""
        if self.execucao.estado in _ESTADOS_OCUPADOS or self.executor.ativo:
            dialogos.aviso(self, "Abrir projeto", "Espere o processamento terminar para abrir.")
            return
        if self.studio.mudou and not self.studio.salvar():
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
            self.opcoes.aplicar_valores(ValoresFormulario.de_opcoes(opcoes))
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
        if self._pastas.cortes_shorts.exists():
            self._abas.set(ABA_STUDIO)
            self._trocar_aba(ABA_STUDIO)

    def _renderizar_do_studio(self) -> None:
        """Renderiza os Shorts marcados que ainda não existem (ou mudaram), sem o resto."""
        if self._pastas is None or not self.studio.liberada or self.studio.editor is None:
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
        self._atualizar_aba_studio(recarregar=True)

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
        self.destroy()


def executar() -> int:
    JanelaPrincipal().mainloop()
    return 0
