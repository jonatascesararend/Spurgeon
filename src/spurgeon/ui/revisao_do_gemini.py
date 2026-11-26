"""O quadro "Revisão do Gemini" do Studio Shorts: o botão de pedir e o parecer do Short aberto."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from spurgeon.shorts.dados import Corte
from spurgeon.shorts.revisao_gemini import RevisaoDoGemini, da_revisao, quando_legivel
from spurgeon.ui.dicas import dica

_COR_DO_VEREDITO = {"bom": "#2e8b57", "ajustar": "#b8860b", "ruim": "#a84444"}
_COR_SUAVE = "gray60"
_COR_ALERTA = "#e0a030"
_COR_ERRO = "#e05555"
_LARGURA = 420


def selo_do_gemini(corte: Corte) -> str:
    """O pedaço da linha da lista: `✨ Gemini: bom 8/10` (vazio se não foi revisado)."""
    revisao = da_revisao(corte)
    if revisao is None:
        return ""
    return f"✨ Gemini: {revisao.veredito} {revisao.nota}/10"


def _marca(sim: bool, texto_sim: str, texto_nao: str) -> str:
    return f"✔ {texto_sim}" if sim else f"✖ {texto_nao}"


def detalhes(revisao: RevisaoDoGemini) -> str:
    linhas = []
    if revisao.comentario:
        linhas.append(revisao.comentario)
    linhas.append(
        " · ".join([
            _marca(revisao.corte_completo, "ideia completa", "depende do contexto"),
            _marca(revisao.titulo_combina, "título combina", "título não combina"),
            _marca(revisao.descricao_combina, "descrição combina", "descrição não combina"),
        ])
    )  # fmt: skip
    return "\n".join(linhas)


class QuadroDoGemini(ctk.CTkFrame):
    """`ao_revisar()` pede a revisão do Short aberto; `ao_usar("titulo" | "descricao")` leva a
    sugestão para o campo. Quem usa chama `mostrar(...)` a cada troca de Short ou de estado."""

    def __init__(
        self,
        master,
        *,
        ao_revisar: Callable[[], None],
        ao_usar: Callable[[str], None],
    ):
        super().__init__(master, corner_radius=8, border_width=1, border_color=("gray75", "gray30"))
        self.columnconfigure(0, weight=1)
        cabeca = ctk.CTkFrame(self, fg_color="transparent")
        cabeca.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        cabeca.columnconfigure(1, weight=1)
        ctk.CTkLabel(cabeca, text="Revisão do Gemini", anchor="w").grid(row=0, column=0, sticky="w")
        self.selo = ctk.CTkLabel(
            cabeca, text="", text_color="white", corner_radius=6, height=20,
            font=ctk.CTkFont(size=11, weight="bold"),
        )  # fmt: skip
        self.selo.grid(row=0, column=1, sticky="w", padx=8)
        self.botao = ctk.CTkButton(
            cabeca, text="✨ Revisar com o Gemini", width=170, height=24,
            font=ctk.CTkFont(size=12), command=ao_revisar,
        )  # fmt: skip
        self.botao.grid(row=0, column=2, sticky="e")
        dica(
            self.botao,
            "Manda a legenda, o título e a descrição deste Short para o Google Gemini, que diz se "
            "o corte ficou bom e se o título e a descrição combinam com o que é dito. Só "
            "aconselha: nada muda até você usar uma sugestão. Precisa da chave na aba Integrações.",
        )
        self.situacao = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", wraplength=_LARGURA,
            text_color=_COR_SUAVE, font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self.situacao.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 0))
        self.texto = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=_LARGURA)
        self.texto.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 0))
        self.sugestoes: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton]] = {}
        for linha, (campo, nome) in enumerate((("titulo", "título"), ("descricao", "descrição"))):
            quadro = ctk.CTkFrame(self, fg_color="transparent")
            quadro.grid(row=3 + linha, column=0, sticky="ew", padx=8, pady=(4, 0))
            quadro.columnconfigure(0, weight=1)
            rotulo = ctk.CTkLabel(quadro, text="", anchor="w", justify="left", wraplength=300)
            rotulo.grid(row=0, column=0, sticky="ew")
            botao = ctk.CTkButton(
                quadro, text=_TEXTO_DO_BOTAO[campo], width=130, height=22,
                font=ctk.CTkFont(size=12), fg_color=("gray70", "gray30"),
                command=lambda c=campo: ao_usar(c),
            )  # fmt: skip
            botao.grid(row=0, column=1, sticky="ne", padx=(6, 0))
            dica(botao, f"Troca o {nome} do Short pelo que o Gemini sugeriu (vale ao salvar).")
            self.sugestoes[campo] = (quadro, rotulo, botao)
        self.grid_rowconfigure(5, minsize=6)

    def mostrar(
        self,
        corte: Corte | None,
        *,
        revisando: bool = False,
        ocupado: bool = False,
        erro: str = "",
    ) -> None:
        """`revisando`: o Gemini está lendo este Short; `ocupado`: lendo outro (um por vez)."""
        revisao = da_revisao(corte) if corte is not None else None
        self.botao.configure(
            state="disabled" if corte is None or revisando or ocupado else "normal",
            text="Revisando…"
            if revisando
            else ("↻ Revisar de novo" if revisao else "✨ Revisar com o Gemini"),
        )
        if revisao is None:
            self.selo.configure(text="", fg_color="transparent")
        else:
            self.selo.configure(
                text=f"✨ {revisao.rotulo} · {revisao.nota}/10",
                fg_color=_COR_DO_VEREDITO.get(revisao.veredito, "gray40"),
            )
        if erro:
            self.situacao.configure(text=erro, text_color=_COR_ERRO)
        elif revisando:
            self.situacao.configure(text="O Gemini está lendo o Short…", text_color=_COR_SUAVE)
        elif revisao is None:
            self.situacao.configure(text="Ainda não revisado pelo Gemini.", text_color=_COR_SUAVE)
        elif corte is not None and revisao.desatualizada(corte):
            self.situacao.configure(
                text=f"Revisado em {quando_legivel(revisao.quando)} ({revisao.modelo}); o título "
                "ou a descrição mudaram depois disso.",
                text_color=_COR_ALERTA,
            )
        else:
            self.situacao.configure(
                text=f"Revisado em {quando_legivel(revisao.quando)} ({revisao.modelo}).",
                text_color=_COR_SUAVE,
            )
        self.texto.configure(text=detalhes(revisao) if revisao else "")
        if revisao is None or not self.texto.cget("text"):
            self.texto.grid_remove()
        else:
            self.texto.grid()
        for campo, (quadro, rotulo, botao) in self.sugestoes.items():
            sugestao = sugestao_de(revisao, campo) if revisao else ""
            if not sugestao:
                quadro.grid_remove()
                continue
            atual = (corte.titulo if campo == "titulo" else corte.descricao) if corte else ""
            em_uso = atual.strip() == sugestao.strip()
            rotulo.configure(text=f"{_NOME_DA_SUGESTAO[campo]}: “{sugestao}”")
            botao.configure(
                state="disabled" if em_uso else "normal",
                text="✔ Em uso" if em_uso else _TEXTO_DO_BOTAO[campo],
            )
            quadro.grid()


_NOME_DA_SUGESTAO = {"titulo": "Título sugerido", "descricao": "Descrição sugerida"}
_TEXTO_DO_BOTAO = {"titulo": "Usar este título", "descricao": "Usar esta descrição"}


def sugestao_de(revisao: RevisaoDoGemini, campo: str) -> str:
    return revisao.titulo_sugerido if campo == "titulo" else revisao.descricao_sugerida
