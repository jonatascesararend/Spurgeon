"""A aba Integrações: ligar as contas das redes da igreja (YouTube, Meta, TikTok…).

Cada conta tem um cartão com os campos que a pessoa cola (ID, chave secreta, token), os botões
Salvar, Conectar e Desconectar, e o passo a passo de como conseguir esses dados. Os segredos vão
direto para o `Cofre` (criptografados pelo Windows) e nunca voltam para a tela: um campo secreto já
guardado mostra só "guardado" e a pessoa digita por cima para trocar.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

import customtkinter as ctk

from spurgeon.core.erros import Cancelado, ErroSpurgeon
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.modelo import Campo, Plataforma
from spurgeon.ui import dialogos
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.dicas import dica
from spurgeon.ui.estabilidade import RotuloQueSome
from spurgeon.ui.rolagem import QuadroRolavel
from spurgeon.ui.secao import SecaoRecolhivel

_COR_OK = "#3fb950"
_COR_ERRO = "#e05555"
_COR_SUAVE = ("gray35", "gray65")
_LARGURA_DO_TEXTO = 760

TITULOS = {
    "youtube": "YouTube Shorts",
    "meta": "Instagram e Facebook (Meta)",
    "tiktok": "TikTok",
    "upload_post": "Upload-Post: todas as redes de uma vez (opcional, pago)",
}
RESUMOS = {
    "youtube": (
        "Publica e agenda pela API do YouTube. O agendamento é do próprio YouTube: ele publica "
        "sozinho na hora marcada, mesmo com o computador desligado."
    ),
    "meta": (
        "Instagram: envia o Reel com o arquivo do computador. A API do Instagram não agenda, então "
        "o Spurgeon guarda o horário e publica na hora (o aplicativo precisa estar aberto). "
        "Facebook: agenda na própria rede, para Shorts de 3 a 90 segundos."
    ),
    "tiktok": (
        "O TikTok só deixa um programa publicar depois de uma auditoria que um app para a própria "
        "conta não passa. Por isso o Spurgeon deixa a legenda copiada e abre o TikTok Studio, onde "
        "você agenda. Opcional: enviar o vídeo para os rascunhos pela API."
    ),
    "upload_post": (
        "Um serviço de terceiros que publica e agenda nas quatro redes (inclusive o TikTok em "
        "público) com uma chave só. É pago e o vídeo passa pelos servidores dele."
    ),
}
_DICA_DO_SEGREDO = (
    "Fica criptografado pelo Windows, só neste computador e neste usuário. Depois de salvo ele não "
    "volta para a tela: para trocar, digite o novo por cima."
)


class CartaoDaConta(ctk.CTkFrame):
    """Uma conta: título, situação, campos e os botões de salvar, conectar e desconectar."""

    def __init__(
        self,
        master,
        *,
        conta: str,
        plataformas: list[Plataforma],
        tarefas: Tarefas,
        ao_mudar: Callable[[], None] | None = None,
    ):
        super().__init__(master, corner_radius=8)
        self.conta = conta
        self.plataformas = plataformas
        self._plataforma = plataformas[0]
        self._tarefas = tarefas
        self._ao_mudar = ao_mudar
        self._cancelamento: threading.Event | None = None
        self.entradas: dict[str, ctk.CTkEntry] = {}
        self.columnconfigure(0, weight=1)

        linha = ctk.CTkFrame(self, fg_color="transparent")
        linha.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        linha.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            linha,
            text=TITULOS.get(conta, conta),
            anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.situacao = ctk.CTkLabel(linha, text="", anchor="e", font=ctk.CTkFont(weight="bold"))
        self.situacao.grid(row=0, column=1, sticky="e")
        dica(self.situacao, "Se esta conta já está ligada ao Spurgeon.")

        ctk.CTkLabel(
            self, text=RESUMOS.get(conta, ""), anchor="w", justify="left",
            wraplength=_LARGURA_DO_TEXTO, text_color=_COR_SUAVE,
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 6))  # fmt: skip

        self.passo_a_passo = SecaoRecolhivel(
            self,
            "Como conseguir as chaves",
            ajuda="O passo a passo para criar o app da sua igreja nesta rede e pegar os dados "
            "que se colam aqui.",
        )
        self.passo_a_passo.grid(row=2, column=0, sticky="ew", padx=6)
        ctk.CTkLabel(
            self.passo_a_passo.corpo,
            text=self._plataforma.como_conectar,
            anchor="w",
            justify="left",
            wraplength=_LARGURA_DO_TEXTO - 20,
        ).pack(fill="x", padx=14, pady=(6, 8))

        campos = ctk.CTkFrame(self, fg_color="transparent")
        campos.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 0))
        campos.columnconfigure(1, weight=1)
        for i, campo in enumerate(self._plataforma.campos):
            self._criar_campo(campos, i, campo)

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.grid(row=4, column=0, sticky="ew", padx=14, pady=(8, 4))
        self.botao_salvar = ctk.CTkButton(botoes, text="Salvar", width=90, command=self.salvar)
        self.botao_salvar.pack(side="left")
        dica(
            self.botao_salvar,
            "Guarda os dados digitados (os secretos vão criptografados) sem conectar ainda.",
        )
        self.botao_conectar = ctk.CTkButton(
            botoes, text="Conectar", width=110, command=self.conectar
        )
        self.botao_conectar.pack(side="left", padx=(8, 0))
        dica(
            self.botao_conectar,
            "Salva os dados e entra na conta. Quando preciso, abre o navegador para você aceitar.",
        )
        self.botao_cancelar = ctk.CTkButton(
            botoes, text="Cancelar", width=90, fg_color=("gray70", "gray30"),
            command=self._cancelar,
        )  # fmt: skip
        dica(self.botao_cancelar, "Desiste de conectar (fecha a espera pelo navegador).")
        self.botao_desconectar = ctk.CTkButton(
            botoes, text="Desconectar", width=110, fg_color=("gray70", "gray30"),
            command=self.desconectar,
        )  # fmt: skip
        self.botao_desconectar.pack(side="right")
        dica(
            self.botao_desconectar,
            "Apaga do computador tudo o que foi guardado desta conta (chaves e acessos).",
        )

        self.mensagem = RotuloQueSome(
            self, anchor="w", justify="left", wraplength=_LARGURA_DO_TEXTO, text=""
        )
        self.mensagem.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.atualizar()

    # --- os campos ------------------------------------------------------------------------------
    def _criar_campo(self, quadro, linha: int, campo: Campo) -> None:
        rotulo = campo.rotulo + (" (opcional)" if campo.opcional else "")
        ctk.CTkLabel(quadro, text=rotulo, anchor="w").grid(
            row=linha, column=0, sticky="w", padx=(0, 10), pady=3
        )
        entrada = ctk.CTkEntry(quadro, show="•" if campo.segredo else "", width=420)
        entrada.grid(row=linha, column=1, sticky="ew", pady=3)
        dica(entrada, campo.ajuda or (_DICA_DO_SEGREDO if campo.segredo else campo.rotulo))
        self.entradas[campo.nome] = entrada

    def _guardado(self, nome: str) -> str:
        return self._plataforma.cofre.obter(self.conta, nome)

    def _preencher(self) -> None:
        for campo in self._plataforma.campos:
            entrada = self.entradas[campo.nome]
            if campo.segredo:
                entrada.delete(0, "end")
                entrada.configure(
                    placeholder_text="guardado (digite para trocar)"
                    if self._plataforma.cofre.tem(self.conta, campo.nome)
                    else ""
                )
            elif not entrada.get():
                entrada.insert(0, self._guardado(campo.nome))

    # --- situação ---------------------------------------------------------------------------------
    @property
    def ocupado(self) -> bool:
        return self._cancelamento is not None

    def atualizar(self) -> None:
        if not self.ocupado:
            if any(p.conectada for p in self.plataformas):
                resumo = self._plataforma.resumo_da_conta
                self.situacao.configure(
                    text="● Conectado" + (f" · {resumo}" if resumo else ""), text_color=_COR_OK
                )
            elif self._plataforma.disponivel:
                self.situacao.configure(
                    text=f"● {self._plataforma.resumo_da_conta}", text_color=_COR_OK
                )
            else:
                self.situacao.configure(text="○ Não conectado", text_color=_COR_SUAVE)
        tem_algo = bool(self._plataforma.cofre.campos_da_conta(self.conta))
        estado = "disabled" if self.ocupado else "normal"
        self.botao_salvar.configure(state=estado)
        self.botao_conectar.configure(state=estado)
        self.botao_desconectar.configure(
            state="normal" if tem_algo and not self.ocupado else "disabled"
        )
        self._preencher()

    def _dizer(self, texto: str, *, erro: bool = False) -> None:
        self.mensagem.configure(text=texto, text_color=_COR_ERRO if erro else _COR_OK)

    # --- ações ------------------------------------------------------------------------------------
    def salvar(self) -> None:
        """Guarda o que está digitado. Segredo vazio não apaga o que já estava guardado."""
        cofre = self._plataforma.cofre
        for campo in self._plataforma.campos:
            valor = self.entradas[campo.nome].get().strip()
            if campo.segredo:
                if valor:
                    cofre.definir(self.conta, campo.nome, valor, segredo=True)
            else:
                cofre.definir(self.conta, campo.nome, valor)
                entrada = self.entradas[campo.nome]  # mostra o que foi guardado (sem espaços)
                entrada.delete(0, "end")
                entrada.insert(0, valor)
        self._dizer("Dados guardados neste computador.")
        self.atualizar()
        if self._ao_mudar:
            self._ao_mudar()

    def _faltam(self) -> list[str]:
        cofre = self._plataforma.cofre
        return [
            c.rotulo for c in self._plataforma.campos
            if not c.opcional and not cofre.tem(self.conta, c.nome)
        ]  # fmt: skip

    def conectar(self) -> None:
        if self.ocupado:
            return
        self.salvar()
        faltam = self._faltam()
        if faltam:
            self._dizer("Falta preencher: " + ", ".join(faltam) + ".", erro=True)
            return
        if not self._plataforma.cofre.tem(self.conta, self._plataforma.campos[0].nome):
            # só o TikTok chega aqui sem chaves: o fluxo assistido não precisa de conta
            self._dizer("Sem chaves o TikTok já funciona no fluxo assistido. Nada a conectar.")
            return
        self._cancelamento = threading.Event()
        cancelamento = self._cancelamento
        self.situacao.configure(text="… conectando: confirme no navegador", text_color=_COR_SUAVE)
        self.botao_cancelar.pack(side="left", padx=(8, 0))
        self.mensagem.configure(text="")
        self.atualizar()
        self._tarefas.rodar(
            lambda: self._plataforma.conectar(cancelado=cancelamento.is_set),
            self._conectou,
            self._falhou,
        )

    def _terminou(self) -> None:
        self._cancelamento = None
        self.botao_cancelar.pack_forget()

    def _conectou(self, resumo: str) -> None:
        self._terminou()
        self._dizer(f"Conectado: {resumo}.")
        self.atualizar()
        if self._ao_mudar:
            self._ao_mudar()

    def _falhou(self, erro: Exception) -> None:
        self._terminou()
        if isinstance(erro, Cancelado):
            self._dizer("Conexão cancelada.", erro=True)
        elif isinstance(erro, ErroSpurgeon):
            self._dizer(str(erro), erro=True)
        else:
            self._dizer(f"Erro inesperado ao conectar: {erro}", erro=True)
        self.atualizar()

    def _cancelar(self) -> None:
        if self._cancelamento is not None:
            self._cancelamento.set()

    def desconectar(self) -> None:
        if self.ocupado:
            return
        if not dialogos.confirmar(
            self.winfo_toplevel(),
            "Desconectar",
            f"Apagar deste computador tudo o que foi guardado de “{TITULOS.get(self.conta)}” "
            "(chaves e acessos)? Os Shorts já postados não mudam.",
            sim="Desconectar",
            nao="Voltar",
        ):
            return
        self._plataforma.desconectar()
        for entrada in self.entradas.values():
            entrada.delete(0, "end")
        self._dizer("Conta desconectada.")
        self.atualizar()
        if self._ao_mudar:
            self._ao_mudar()


class AbaIntegracoes(ctk.CTkFrame):
    """A aba inteira: uma explicação, um cartão por conta e o registro do que o Spurgeon fez."""

    def __init__(
        self,
        master,
        *,
        plataformas: dict[str, Plataforma],
        cofre: Cofre,
        tarefas: Tarefas,
        ao_mudar: Callable[[], None] | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self.cofre = cofre
        self.plataformas = plataformas
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ctk.CTkLabel(
            self,
            text=(
                "Aqui você liga as contas da igreja para o Studio Shorts postar e agendar cada "
                "Short em todas as redes de uma vez. As chaves ficam criptografadas neste "
                "computador e o Spurgeon só se conecta à rede quando você pede (conectar, "
                "agendar ou publicar)."
            ),
            anchor="w", justify="left", wraplength=_LARGURA_DO_TEXTO + 40, text_color=_COR_SUAVE,
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 6))  # fmt: skip
        corpo = QuadroRolavel(self, fg_color="transparent")
        corpo.grid(row=1, column=0, sticky="nsew")
        corpo.columnconfigure(0, weight=1)
        self.corpo = corpo

        contas: dict[str, list[Plataforma]] = {}
        for plataforma in plataformas.values():
            contas.setdefault(plataforma.conta, []).append(plataforma)
        self.cartoes: dict[str, CartaoDaConta] = {}
        for linha, (conta, lista) in enumerate(contas.items()):
            cartao = CartaoDaConta(
                corpo, conta=conta, plataformas=lista, tarefas=tarefas, ao_mudar=ao_mudar
            )
            cartao.grid(row=linha, column=0, sticky="ew", padx=10, pady=6)
            self.cartoes[conta] = cartao

        rotulo = ctk.CTkLabel(
            corpo, text="O que o Spurgeon fez por aqui", anchor="w", font=ctk.CTkFont(weight="bold")
        )
        rotulo.grid(row=len(contas), column=0, sticky="w", padx=14, pady=(10, 2))
        self.atividade = ctk.CTkTextbox(corpo, height=120, state="disabled", wrap="word")
        self.atividade.grid(row=len(contas) + 1, column=0, sticky="ew", padx=10, pady=(0, 14))
        dica(
            self.atividade,
            "As últimas postagens, agendamentos e avisos desta sessão (a fila do Instagram também "
            "aparece aqui quando publica).",
        )

    def atualizar(self) -> None:
        for cartao in self.cartoes.values():
            if not cartao.ocupado:
                cartao.atualizar()

    def registrar(self, texto: str, *, quando: datetime | None = None) -> None:
        """Acrescenta uma linha ao registro de atividade."""
        hora = (quando or datetime.now()).strftime("%d/%m %H:%M")
        self.atividade.configure(state="normal")
        self.atividade.insert("end", f"{hora}  {texto}\n")
        self.atividade.see("end")
        self.atividade.configure(state="disabled")
