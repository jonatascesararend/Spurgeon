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
from spurgeon.ui import dialogos
from spurgeon.ui.studio_shorts import EditorDeCortes

_COR_SUAVE = "gray60"

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
    ):
        super().__init__(master, fg_color="transparent")
        self._ao_renderizar = ao_renderizar
        self._pastas_do_app = pastas_do_app
        self._legenda_queimada = legenda_queimada
        self._pastas: PastasProjeto | None = None
        self._editor: EditorDeCortes | None = None
        self._motivo_bloqueio: str | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._miolo = ctk.CTkFrame(self, fg_color="transparent")
        self._miolo.grid(row=0, column=0, sticky="nsew")
        self._miolo.columnconfigure(0, weight=1)
        self._miolo.rowconfigure(0, weight=1)
        self._vazio = ctk.CTkLabel(
            self._miolo,
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
            command=lambda: self.salvar(exigir_ate_60s=False),
        )
        self._botao_salvar.grid(row=0, column=1, padx=(8, 0))
        self._botao_renderizar = ctk.CTkButton(
            rodape,
            text="Renderizar",
            width=210,
            font=ctk.CTkFont(weight="bold"),
            command=self._ao_renderizar,
        )
        self._botao_renderizar.grid(row=0, column=2, padx=(8, 0))
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
        if pastas is None or not pastas.cortes_shorts.exists():
            self._vazio.grid(row=0, column=0)
            self._atualizar_botoes()
            return
        self._vazio.grid_remove()
        self._editor = EditorDeCortes(
            self._miolo,
            pastas,
            pastas_do_app=self._pastas_do_app,
            introducao=_INTRODUCAO,
            legenda_queimada=self._legenda_queimada,
            ao_mudar=self._atualizar_botoes,
        )
        self._editor.grid(row=0, column=0, sticky="nsew")
        self._editor.bloquear(self._motivo_bloqueio)
        self._atualizar_botoes()

    def recarregar(self) -> None:
        """Relê os cortes e o que já foi renderizado (depois de um processamento)."""
        self.mostrar_projeto(self._pastas)

    def bloquear(self, motivo: str | None) -> None:
        self._motivo_bloqueio = motivo
        if self._editor is not None:
            self._editor.bloquear(motivo)
        self._atualizar_botoes()

    # --- ações --------------------------------------------------------------------------------
    def salvar(self, *, exigir_ate_60s: bool = True) -> bool:
        """Grava as alterações em `cortes_shorts.json`. False se algo impede (veja o aviso).

        O botão da aba salva sem exigir os 60 s (é trabalho em andamento); quem vai renderizar ou
        processar exige.
        """
        if self._editor is None:
            return True
        ok = self._editor.salvar(exigir_ate_60s=exigir_ate_60s)
        self._atualizar_botoes()
        return ok

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
        self._botao_salvar.configure(
            state="normal" if livre and editor.mudou else "disabled",
        )
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
