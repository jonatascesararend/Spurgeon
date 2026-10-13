"""Janela principal do Studio Spurgeon."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path

import customtkinter as ctk

from spurgeon.core.config import ConfigApp, OpcoesProcessamento, carregar_config, salvar_config
from spurgeon.core.erros import ErroSpurgeon, ProjetoConflito
from spurgeon.core.estado import StatusEtapa
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
)
from spurgeon.ui import dialogos
from spurgeon.ui.estimativa import (
    EstimadorTempo,
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
        largura, altura = int(min(1200, tela_l * 0.94)), int(min(800, tela_a * 0.86))
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
        )
        self.entrada.pack(fill="x", pady=(0, 10))
        self.opcoes = PainelOpcoes(
            lateral,
            valores=ValoresFormulario.de_opcoes(self.config_app.padroes),
            pastas=pastas,
            ao_mudar=self._atualizar_estimativa,
        )
        self.opcoes.pack(fill="x")

        self.execucao = PainelExecucao(
            self,
            ao_iniciar=self._iniciar,
            ao_pausar_retomar=self._pausar_retomar,
            ao_cancelar=self._cancelar,
            ao_abrir_pasta=self._abrir_pasta_projeto,
            ao_abrir_revisao=self._abrir_revisao,
            ao_aprovar=self._aprovar_revisao,
            ao_fechar_revisao=self._fechar_revisao,
        )
        self.execucao.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(12, 0))
        self.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)

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
        if dia is not None:
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
    def _coletar(self) -> tuple[OpcoesProcessamento, Path, str, date] | None:
        """Lê e valida a tela; mostra os problemas e devolve None se houver algum."""
        erros: list[str] = []
        opcoes = video = data = None
        try:
            opcoes = para_opcoes(self.opcoes.valores(), self.config_app.padroes)
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
        if erros:
            dialogos.aviso(self, "Confira os campos", "• " + "\n• ".join(erros))
            return None
        return opcoes, video, nome, data

    def _iniciar(self) -> None:
        coletado = self._coletar()
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
        abrir_pasta(criado.pastas.raiz)
        self._iniciar_execucao()

    def _lembrar_preferencias(self, config: ConfigApp) -> None:
        self.config_app.padroes = config.padroes
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

    def _voltar_ao_ocioso(self) -> None:
        self._bloquear(False)
        self.execucao.definir_estado(EstadoUI.OCIOSO)

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
