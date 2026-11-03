"""Aba "Studio Shorts": o editor de cortes do projeto aberto, com o botão de renderizar.

A aba mostra os Shorts sugeridos (trecho da legenda, nota calculada e título) para a pessoa escolher
o que será renderizado, ajustar o início e o fim e corrigir a legenda. Fica desativada até os cortes
existirem (normalização do áudio, extração da legenda e cálculo dos Shorts) e fica travada enquanto
um processamento está em andamento.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from spurgeon.core.config import Pastas
from spurgeon.core.projeto import PastasProjeto
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.livre import ContextoDaIA
from spurgeon.ui import dialogos
from spurgeon.ui.ajuda import dica_do_seletor
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.dicas import dica
from spurgeon.ui.postagem import ContextoDePublicacao
from spurgeon.ui.shorts_livre import PainelShortsLivre
from spurgeon.ui.studio_shorts import EditorDeCortes, OrigemDaPrevia

_COR_SUAVE = "gray60"
PAINEL_CORTES = "Cortes"
PAINEL_LIVRE = "Shorts livre"

_INTRODUCAO = (
    "Estes são os Shorts sugeridos para este sermão. Marque os que quer renderizar, ajuste o "
    "início e o fim de cada um e corrija a legenda antes de renderizar. O que já foi renderizado "
    "e não mudou não é refeito."
)


class AbaStudioShorts(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        ao_renderizar: Callable[[], None],
        pastas_do_app: Pastas,
        legenda_queimada: Callable[[], bool],
        janela_do_video: Callable[[], tuple[float, float | None]] | None = None,
        aparencia: Callable[[], str | None] | None = None,
        tarefas: Tarefas | None = None,
        origem_da_previa: Callable[[], OrigemDaPrevia | None] | None = None,
        contexto_da_ia: Callable[[], ContextoDaIA | None] | None = None,
        definir_ajustes: Callable[..., None] | None = None,
        publicacao: ContextoDePublicacao | None = None,
    ):
        """`definir_ajustes(legenda=…)` grava na tela principal a opção dos Shorts que se acerta
        olhando a prévia (a altura da legenda; o corte das pausas é de cada Short)."""
        super().__init__(master, fg_color="transparent")
        self._tarefas = tarefas
        self._contexto_da_ia = contexto_da_ia or (lambda: None)
        self._definir_ajustes = definir_ajustes
        self._publicacao = publicacao
        self._origem_da_previa = origem_da_previa
        self._ao_renderizar = ao_renderizar
        self._pastas_do_app = pastas_do_app
        self._legenda_queimada = legenda_queimada
        self._janela_do_video = janela_do_video
        self._aparencia = aparencia
        self._pastas: PastasProjeto | None = None
        self._editor: EditorDeCortes | None = None
        self._livre: PainelShortsLivre | None = None
        self._motivo_bloqueio: str | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._miolo = ctk.CTkFrame(self, fg_color="transparent")
        self._miolo.grid(row=0, column=0, sticky="nsew")
        self._miolo.columnconfigure(0, weight=1)
        self._miolo.rowconfigure(1, weight=1)
        self._seletor = ctk.CTkSegmentedButton(
            self._miolo, values=[PAINEL_CORTES, PAINEL_LIVRE], command=self._trocar_painel
        )
        self._seletor.set(PAINEL_CORTES)
        dica_do_seletor(
            self._seletor,
            {
                PAINEL_CORTES: "A lista dos Shorts do sermão: confira, ajuste, corrija a legenda e "
                "escolha o que renderizar.",
                PAINEL_LIVRE: "Escolha você mesmo um trecho do sermão (buscando por palavra ou "
                "assunto) para virar um Short.",
            },
        )
        self._pilha = ctk.CTkFrame(self._miolo, fg_color="transparent")
        self._pilha.grid(row=1, column=0, sticky="nsew")
        self._pilha.columnconfigure(0, weight=1)
        self._pilha.rowconfigure(0, weight=1)
        self._vazio = ctk.CTkLabel(
            self._pilha,
            text="Abra um projeto ou processe um vídeo: os Shorts aparecem aqui assim que forem "
            "calculados.",
            text_color=_COR_SUAVE,
            wraplength=600,
        )
        self._vazio.grid(row=0, column=0)

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        rodape.columnconfigure(0, weight=1)
        self._estado = ctk.CTkLabel(rodape, text="", anchor="w", text_color=_COR_SUAVE)
        self._estado.grid(row=0, column=0, sticky="ew")
        self._botao_salvar = ctk.CTkButton(
            rodape,
            text="Salvar alterações",
            width=140,
            fg_color=("gray70", "gray30"),
            command=lambda: self.salvar(exigir_ate_o_limite=False),
        )
        self._botao_salvar.grid(row=0, column=2, padx=(8, 0))
        dica(
            self._botao_salvar,
            "Grava no projeto o que você mudou (pontas, palavras cortadas, legenda, títulos, "
            "pausas…) sem renderizar nada.",
        )
        self._botao_descartar = ctk.CTkButton(
            rodape,
            text="Descartar alterações",
            width=150,
            fg_color=("gray70", "gray30"),
            command=self.descartar,
        )
        self._botao_descartar.grid(row=0, column=1, padx=(8, 0))
        dica(
            self._botao_descartar,
            "Joga fora tudo o que foi mudado desde a última vez que você salvou e volta os Shorts "
            "ao que está gravado no projeto.",
        )
        self._botao_renderizar = ctk.CTkButton(
            rodape,
            text="Renderizar",
            width=210,
            font=ctk.CTkFont(weight="bold"),
            command=self._ao_renderizar,
        )
        self._botao_renderizar.grid(row=0, column=3, padx=(8, 0))
        dica(
            self._botao_renderizar,
            "Renderiza os Shorts marcados que ainda não existem ou que mudaram desde a última "
            "renderização (os que já estão prontos não são refeitos).",
        )
        self._atualizar_botoes()

    # --- projeto ------------------------------------------------------------------------------
    @property
    def pastas(self) -> PastasProjeto | None:
        return self._pastas

    @property
    def editor(self) -> EditorDeCortes | None:
        return self._editor

    @property
    def liberada(self) -> bool:
        """Os cortes já foram calculados: dá para escolher o que renderizar."""
        return self._pastas is not None and self._pastas.cortes_shorts.exists()

    @property
    def mudou(self) -> bool:
        return self._editor is not None and self._editor.mudou

    def mostrar_projeto(self, pastas: PastasProjeto | None) -> None:
        """Carrega (ou recarrega) os cortes do projeto; `None` esvazia a aba."""
        self._pastas = pastas
        if self._editor is not None:
            self._editor.destroy()
            self._editor = None
        if self._livre is not None:
            self._livre.destroy()
            self._livre = None
        if pastas is None or not pastas.cortes_shorts.exists():
            self._seletor.grid_remove()
            self._vazio.grid(row=0, column=0)
            self._atualizar_botoes()
            return
        self._vazio.grid_remove()
        self._seletor.grid(row=0, column=0, sticky="w", padx=14, pady=(8, 0))
        self._editor = EditorDeCortes(
            self._pilha,
            pastas,
            pastas_do_app=self._pastas_do_app,
            introducao=_INTRODUCAO,
            legenda_queimada=self._legenda_queimada,
            janela_do_video=self._janela_do_video,
            aparencia=self._aparencia,
            ao_mudar=self._atualizar_botoes,
            tarefas=self._tarefas,
            origem_da_previa=self._origem_da_previa,
            definir_ajustes=self._definir_ajustes,
            contexto_da_ia=self._contexto_da_ia if self._tarefas is not None else None,
            publicacao=self._publicacao,
        )
        self._editor.grid(row=0, column=0, sticky="nsew")
        self._editor.bloquear(self._motivo_bloqueio)
        if self._tarefas is not None:
            self._livre = PainelShortsLivre(
                self._pilha,
                pastas,
                tarefas=self._tarefas,
                contexto_da_ia=self._contexto_da_ia,
                cortes_existentes=lambda: self._editor.cortes if self._editor else [],
                ao_criar=self._corte_livre_criado,
                janela_do_video=self._janela_do_video,
            )
            self._livre.grid(row=0, column=0, sticky="nsew")
            self._livre.bloquear(self._motivo_bloqueio)
        self._seletor.set(PAINEL_CORTES)
        self._trocar_painel(PAINEL_CORTES)
        self._atualizar_botoes()

    def _trocar_painel(self, nome: str) -> None:
        """Mostra a lista de cortes ou o Shorts livre (os dois ficam montados, um sobre o outro)."""
        if nome == PAINEL_LIVRE and self._livre is not None:
            self._livre.atualizar_marcas()  # os Shorts da lista podem ter mudado desde a última vez
            self._livre.tkraise()
        elif self._editor is not None:
            self._editor.tkraise()
        self._atualizar_botoes()

    def mostrar_painel(self, nome: str) -> None:
        self._seletor.set(nome)
        self._trocar_painel(nome)

    def _corte_livre_criado(self, corte: Corte) -> None:
        """Põe o Short livre na lista, grava e leva a pessoa até ele."""
        if self._editor is None:
            return
        self._editor.adicionar_corte(corte)
        self.salvar(exigir_ate_o_limite=False)
        self.mostrar_painel(PAINEL_CORTES)

    def atualizar_redes(self) -> None:
        """A fila de postagens andou: o resumo das redes do Short aberto se refaz."""
        if self._editor is not None:
            self._editor.atualizar_redes()

    def recarregar(self) -> None:
        """Relê os cortes e o que já foi renderizado (depois de um processamento)."""
        self.mostrar_projeto(self._pastas)

    def bloquear(self, motivo: str | None) -> None:
        self._motivo_bloqueio = motivo
        if self._editor is not None:
            self._editor.bloquear(motivo)
        if self._livre is not None:
            self._livre.bloquear(motivo)
        if self._editor is not None and bool(self._seletor.winfo_manager()) == bool(motivo):
            if motivo:  # só mexe quando o estado muda: a janela chama isto a cada segundo
                self._seletor.grid_remove()
            else:
                self._seletor.grid(row=0, column=0, sticky="w", padx=14, pady=(8, 0))
        self._atualizar_botoes()

    # --- ações --------------------------------------------------------------------------------
    def salvar(self, *, exigir_ate_o_limite: bool = True) -> bool:
        """Grava as alterações em `cortes_shorts.json`. False se algo impede (veja o aviso).

        O botão da aba salva sem exigir o limite de duração (é trabalho em andamento); quem vai
        renderizar ou
        processar exige.
        """
        if self._editor is None:
            return True
        ok = self._editor.salvar(exigir_ate_o_limite=exigir_ate_o_limite)
        self._atualizar_botoes()
        return ok

    def descartar(self) -> bool:
        """Volta os Shorts ao que está gravado (depois de perguntar). False se nada foi."""
        if self._editor is None or not self._editor.mudou:
            return False
        if not dialogos.confirmar(
            self.winfo_toplevel(),
            "Descartar alterações",
            "Voltar tudo ao que foi salvo por último? O que você mudou desde então (cortes, "
            "legenda, títulos, pausas…) será perdido.",
            sim="Descartar",
            nao="Continuar editando",
        ):
            return False
        self._editor.descartar_alteracoes()
        self._atualizar_botoes()
        return True

    def pendentes(self) -> int:
        return len(self._editor.pendentes()) if self._editor is not None else 0

    def aviso_sem_pendentes(self) -> None:
        dialogos.aviso(
            self.winfo_toplevel(),
            "Nada para renderizar",
            "Todos os Shorts marcados já estão renderizados e não mudaram desde então.\n\n"
            "Marque outros cortes, ou mude o início, o fim ou a legenda de algum.",
        )

    def _atualizar_botoes(self) -> None:
        editor = self._editor
        livre = editor is not None and self._motivo_bloqueio is None
        mudou = livre and editor.mudou
        self._botao_salvar.configure(state="normal" if mudou else "disabled")
        self._botao_descartar.configure(state="normal" if mudou else "disabled")
        if not livre:
            self._botao_renderizar.configure(state="disabled", text="Renderizar")
            self._estado.configure(text=self._motivo_bloqueio or "")
            return
        pendentes = editor.pendentes()
        n = len(pendentes)
        self._botao_renderizar.configure(
            state="normal",
            text=f"Renderizar {n} Short{'s' if n != 1 else ''}" if n else "Renderizar",
        )
        sufixo = "  ·  há alterações ainda não salvas" if editor.mudou else ""
        self._estado.configure(text=f"{n} para renderizar{sufixo}")
