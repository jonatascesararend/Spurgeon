"""Editor dos cortes dos Shorts: a lista (nota, título, trecho da legenda) e o detalhe de cada um.

É a peça que serve às duas telas: a janela de aprovação dos cortes (que pausa o processamento) e a
aba "Studio Shorts" da janela principal (que vale a qualquer hora depois do cálculo dos cortes).

Cada corte sempre começa e termina numa palavra inteira. Passar de 180 s é permitido, mas avisa
(acima disso o corte não pode ser Short). A pessoa pode:
marcar ou desmarcar, corrigir título, descrição e hashtags (e pedir à IA que os refaça a partir do
texto do Short), deslocar o início e o fim (digitando o tempo, ou acrescentando e tirando palavras
ou frases, com as frases vizinhas à vista numa tabela) e corrigir a legenda que vai queimada no
vídeo: na própria tabela, ou na caixa da legenda, com a ajuda do verificador de português.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import re
import tempfile
import threading
import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from tkinter import BooleanVar, StringVar, TclError, filedialog

import customtkinter as ctk

from spurgeon.core.config import OpcoesProcessamento, Pastas
from spurgeon.core.erros import Cancelado
from spurgeon.core.ffmpeg import InfoMidia
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.agenda import resumo_das_redes
from spurgeon.publicacao.modelo import AGENDADO, ENVIADO, NA_FILA, Relogio, agora_local
from spurgeon.publicacao.pacote import pacote_do_corte, registro_do_projeto
from spurgeon.render import previa
from spurgeon.render.shorts import (
    PASTA_DOS_AGENDADOS,
    PASTA_DOS_POSTADOS,
    SITUACAO_ALTERADO,
    SITUACAO_NOVO,
    SITUACAO_PRONTO,
    ResultadoDasMarcas,
    aplicar_marcas_de_postado,
    ler_registro,
    pasta_do_estado,
    planejar_shorts,
    sincronizar_arquivos,
    situacao_do_short,
)
from spurgeon.shorts.agendamento import (
    aplicar_vencimento,
    guardar_horario,
    ler_horario,
    ler_postado_em,
    rotulo_do_postado,
)
from spurgeon.shorts.analise import palavras_da_janela, separar_frases
from spurgeon.shorts.dados import (
    ORIGEM_AUTOMATICA,
    ORIGEM_MANUAL,
    ROTULOS_ORIGEM,
    Corte,
    Frase,
    cor_da_categoria,
    rotulo_da_categoria,
)
from spurgeon.shorts.edicao import (
    AVISO_ACIMA_DO_LIMITE,
    DURACAO_MAXIMA_S,
    DURACAO_MINIMA_S,
    LimiteInvalido,
    MudancaDeLimite,
    abaixo_do_minimo,
    comparar_cortes,
    contexto_da_legenda,
    contexto_das_pontas,
    definir_limite,
    faixa_da_nota,
    formatar_tempo_preciso,
    incluir_palavras,
    indices_das_palavras,
    indices_incluidos,
    legenda_corrigida,
    legenda_do_corte,
    legenda_em_blocos,
    legenda_preservada,
    ler_hashtags,
    ler_tempo,
    linhas_da_tabela,
    mover_limite,
    mover_por_frase,
    palavras_do_corte,
    passa_do_limite,
    resumir,
    tirar_palavras,
)
from spurgeon.shorts.etapas import (
    carregar_cortes,
    gravar_campos_do_corte,
    gravar_cortes,
    nome_do_sermao,
)
from spurgeon.shorts.exportar import legenda_no_tempo_do_short, nome_do_arquivo, texto_da_legenda
from spurgeon.shorts.livre import ContextoDaIA, preparar_cliente
from spurgeon.shorts.llm import CAMPOS_REFAZIVEIS
from spurgeon.shorts.marcas import Detectado, categoria_de_cada_frase, trechos_detectados
from spurgeon.shorts.pausas import (
    LIMIAR_MAXIMO_S,
    LIMIAR_MINIMO_S,
    duracao_do_short,
    residual_da_pausa,
)
from spurgeon.shorts.pontuacao import PARTES, ROTULO_ALERTA
from spurgeon.shorts.selecao import nome_de_arquivo
from spurgeon.shorts.titulos import GENERICO, IGUAL, PARECIDO, problema_do_titulo
from spurgeon.transcricao.formato import Palavra, Transcricao, segundos_para_rotulo
from spurgeon.transcricao.verificador import (
    atualizar_vocabulario_compartilhado,
    verificador_compartilhado,
)
from spurgeon.ui import dialogos
from spurgeon.ui.ajuda import dica_do_seletor
from spurgeon.ui.assincrono import Tarefas
from spurgeon.ui.dicas import dica
from spurgeon.ui.dicionario import DicionarioPersonalizado
from spurgeon.ui.estabilidade import RotuloQueSome
from spurgeon.ui.ferramentas_de_marca import FerramentasDeMarca
from spurgeon.ui.legenda_de_categorias import LegendaDeCategorias, itens_da_legenda
from spurgeon.ui.miniplayer import MiniPlayer
from spurgeon.ui.postagem import (
    ContextoDePublicacao,
    JanelaDePostagem,
    LinhaDeAgendamento,
    preparar_assistido,
)
from spurgeon.ui.rolagem import QuadroRolavel
from spurgeon.ui.tabela_de_frases import (
    COR_FORA,
    FERRAMENTA_BORRACHA,
    FERRAMENTA_MARCA,
    FERRAMENTA_SETA,
    MargemDaLinha,
    TabelaDeFrases,
)
from spurgeon.ui.verificacao import PainelDePortugues

_ARTIGO_DO_CAMPO = {
    "titulo": "um novo título",
    "descricao": "uma nova descrição",
    "hashtags": "novas hashtags",
}
_COR_NOTA = {"alta": "#2e8b57", "media": "#b8860b", "baixa": "#a84444"}
_COR_ALERTA = "#e0a030"
_COR_ERRO = "#e05555"
_COR_SUAVE = "gray60"
_COR_OK = "#4caf72"
_COR_MANUAL = "#6ab0f3"
_COR_LINHA = ("gray86", "gray20")
_COR_LINHA_ATUAL = ("#cfe3f7", "#27415c")
_COR_FORA = COR_FORA
_TRECHO_NA_LISTA = 120  # caracteres da legenda mostrados em cada linha da lista
_QUEBRA_NA_LISTA = 215  # largura útil do texto de cada linha da lista, em pixels
_ALTURA_DA_JANELA = 360  # a tabela com o corte e o que há ao redor dele
_PALAVRAS_FORA = 220  # quanto da transcrição, além da ponta, a janela deixa rolar (≈ 80 s)
_ATRASO_DA_SINCRONIA_MS = 350  # o texto digitado numa caixa chega à outra depois deste silêncio
_PASSO_DA_LEGENDA = 0.02  # quanto cada clique em ▲ ▼ move a legenda (fração da altura do Short)
_LEGENDA_MINIMA, _LEGENDA_MAXIMA = 0.3, 0.9
_PAUSA_MINIMA_S, _PAUSA_MAXIMA_S = LIMIAR_MINIMO_S, LIMIAR_MAXIMO_S
_LIMITE_DO_HISTORICO = 200  # quantos passos de desfazer cada corte guarda
#: o que o desfazer e o refazer guardam de cada corte (a estrutura e a legenda, não os metadados)
_CAMPOS_DO_HISTORICO = ("inicio", "fim", "texto", "saltos", "legenda", "editado", "pausa_s")
_MANTER_A_IA_S = "3m"  # o Ollama solta o modelo da memória depois deste tempo parado
_ROTULO_DO_CAMPO = {"titulo": "o título", "descricao": "a descrição", "hashtags": "as hashtags"}

_PASTA = f"shorts_gerados/{PASTA_DOS_POSTADOS}"  # onde ficam os Shorts já postados
_PASTA_AGENDADOS = f"shorts_gerados/{PASTA_DOS_AGENDADOS}"  # e os que têm horário marcado
_CONFERIR_AGENDAMENTO_MS = 30_000  # de quanto em quanto tempo se vê se alguma hora passou
#: durante um processamento os arquivos ficam onde estão (o trabalhador guarda o registro deles na
#: memória e o regrava a cada Short pronto): a mudança de pasta espera o fim
_TEXTO_ADIADO = "O arquivo vai para {pasta} quando o processamento terminar."

ORDEM_NOTA = "Maior nota"
ORDEM_HORARIO = "Horário"
#: o que a lista mostra, quanto à publicação
FILTRO_TODOS = "Todos"
FILTRO_POSTADOS = "Postados"
FILTRO_AGENDADOS = "Agendados"
FILTRO_SEM_POSTAR = "Sem postar"  # nem postado nem agendado
FILTROS = (FILTRO_TODOS, FILTRO_POSTADOS, FILTRO_AGENDADOS, FILTRO_SEM_POSTAR)
_POSTADO, _AGENDADO = "postado", "agendado"
_COR_SELO_POSTADO = "#2e8b57"
_COR_SELO_AGENDADO = "#2b6cb0"
_ROTULO_SITUACAO = {
    SITUACAO_PRONTO: "✔ renderizado",
    SITUACAO_ALTERADO: "⟳ alterado: renderizar de novo",
    SITUACAO_NOVO: "",
}


_AJUDA_DOS_BOTOES_DA_PONTA = {
    "inicio": (
        "Acrescenta uma palavra antes do começo do Short.",
        "Acrescenta a frase anterior (ou o começo da frase em que o Short abre).",
        "Tira a primeira frase do Short (ou o pedaço dela que sobra).",
        "Tira a primeira palavra do Short.",
    ),
    "fim": (
        "Tira a última palavra do Short.",
        "Tira a última frase do Short (ou o pedaço dela que sobra).",
        "Acrescenta a frase seguinte (ou o que falta para fechar a frase em que o Short termina).",
        "Acrescenta uma palavra depois do fim do Short.",
    ),
}


def formatar_nota(nota: float) -> str:
    return f"{nota:.0f}"


def formatar_tempo(segundos: float) -> str:
    return segundos_para_rotulo(segundos)


def descrever_duracao(segundos: float) -> str:
    return f"{segundos:.0f} s"


def _texto_de_segundos(valor: float) -> str:
    """`1.0` → `1,0`; `0.25` → `0,25` (o número como a pessoa o escreve)."""
    texto = f"{valor:.2f}".rstrip("0")
    return (texto + "0" if texto.endswith(".") else texto).replace(".", ",")


def ler_segundos(texto: str) -> float:
    """`1,5` ou `1.5` → 1.5. Levanta ValueError se não for um número."""
    return float(texto.replace(",", ".").strip())


def formatar_total(segundos: float) -> str:
    minutos, seg = divmod(round(segundos), 60)
    return f"{minutos} min {seg:02d} s" if minutos else f"{seg} s"


def resumir_palavras(palavras, limite: int = 150) -> str:
    """As palavras juntas; se passam do limite, o começo e o fim com reticências no meio."""
    texto = " ".join(palavras)
    if len(texto) <= limite:
        return texto
    cabeca = limite * 2 // 3
    return f"{texto[:cabeca].rstrip()} … {texto[-(limite - cabeca - 3) :].lstrip()}"


def rotulo_da_origem(corte: Corte) -> str:
    """`gerado automaticamente` ou `gerado manualmente` (o que a análise ou a pessoa criou)."""
    return ROTULOS_ORIGEM.get(corte.origem, ROTULOS_ORIGEM[ORIGEM_AUTOMATICA])


def estado_de_publicacao(corte: Corte) -> str:
    """`postado`, `agendado` (um horário legível e ainda não postado) ou "" (nem um nem outro).
    Postado vale mais que agendado, como na pasta onde o arquivo fica."""
    if corte.postado:
        return _POSTADO
    return _AGENDADO if ler_horario(corte.agendado_para) is not None else ""


def passa_no_filtro(corte: Corte, filtro: str) -> bool:
    """O corte aparece na lista com este filtro de publicação?"""
    estado = estado_de_publicacao(corte)
    if filtro == FILTRO_POSTADOS:
        return estado == _POSTADO
    if filtro == FILTRO_AGENDADOS:
        return estado == _AGENDADO
    if filtro == FILTRO_SEM_POSTAR:
        return estado == ""
    return True


def _dia_e_hora(dia: date, hora, agora: datetime) -> str:
    """`05/11 às 18:30`; com o ano (`05/11/2026`) quando não é o de agora; sem hora, só o dia."""
    texto = f"{dia:%d/%m}" + (f"/{dia.year}" if dia.year != agora.year else "")
    return texto + (f" às {hora:%H:%M}" if hora is not None else "")


def selo_de_publicacao(corte: Corte, agora: datetime) -> tuple[str, str] | None:
    """O selo da linha da lista (texto e cor de fundo): quando o Short foi postado ou quando vai
    ao ar. None se não foi postado nem agendado."""
    estado = estado_de_publicacao(corte)
    if estado == _POSTADO:
        lido = ler_postado_em(corte.postado_em)
        quando = f" {_dia_e_hora(*lido, agora)}" if lido else ""
        return f"✔ Postado{quando}", _COR_SELO_POSTADO
    if estado == _AGENDADO:
        horario = ler_horario(corte.agendado_para).astimezone()
        return (
            f"📅 Agendado {_dia_e_hora(horario.date(), horario.time(), agora)}",
            _COR_SELO_AGENDADO,
        )
    return None


def texto_do_postado(
    corte: Corte, *, tem_arquivo: bool, alterado: bool, adiado: bool = False
) -> str:
    """O que a tela diz de um Short já postado ("" se ele não foi marcado). `adiado`: o arquivo
    ainda não mudou de pasta porque há um processamento rodando."""
    if not corte.postado:
        return ""
    quando = rotulo_do_postado(corte.postado_em)
    quando = f" em {quando}" if quando else ""
    if not tem_arquivo:
        return f"✔ Marcado como postado{quando}. Ainda não há arquivo: ele já sai em {_PASTA}."
    if adiado:
        texto = f"✔ Postado{quando}. {_TEXTO_ADIADO.format(pasta=_PASTA)}"
    else:
        texto = f"✔ Postado{quando}. O arquivo está em {_PASTA}."
    if alterado:
        texto += " ⚠ O corte mudou depois: um vídeo novo seria outro."
    return texto


def aviso_do_titulo(titulo: str, outros: list[str], sermao: str) -> str:
    """O aviso sob o campo Título quando ele repete, lembra demais o de outro Short marcado ou é
    genérico demais ("" se está bem). A abertura repetida não avisa: é só um estilo."""
    if not titulo.strip():
        return ""
    problema = problema_do_titulo(titulo, outros, sermao=sermao)
    if problema is None or problema.tipo not in (IGUAL, PARECIDO, GENERICO):
        return ""
    return f"⚠ Este título {problema.motivo}."


def trecho_da_legenda(palavras: list[Palavra], corte: Corte, limite: int = _TRECHO_NA_LISTA) -> str:
    """O começo da legenda do corte (a corrigida, se houver), para a lista."""
    texto = legenda_do_corte(palavras, corte) if palavras else ""
    texto = " ".join((texto or corte.texto).split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


@dataclass
class _Historico:
    """Os passos de desfazer (`passado`) e de refazer (`futuro`) de um corte."""

    passado: list[dict] = field(default_factory=list)
    futuro: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class OrigemDaPrevia:
    """O que a prévia de um Short precisa saber do que está na tela: o vídeo e as opções."""

    video: Path
    info: InfoMidia
    opcoes: OpcoesProcessamento


class EditorDeCortes(ctk.CTkFrame):
    def __init__(
        self,
        master,
        pastas: PastasProjeto,
        *,
        pastas_do_app: Pastas | None = None,
        introducao: str | None = None,
        legenda_queimada: Callable[[], bool] | None = None,
        janela_do_video: Callable[[], tuple[float, float | None]] | None = None,
        aparencia: Callable[[], str | None] | None = None,
        ao_mudar: Callable[[], None] | None = None,
        tarefas: Tarefas | None = None,
        origem_da_previa: Callable[[], OrigemDaPrevia | None] | None = None,
        definir_ajustes: Callable[..., None] | None = None,
        contexto_da_ia: Callable[[], ContextoDaIA | None] | None = None,
        publicacao: ContextoDePublicacao | None = None,
        relogio: Relogio = agora_local,
        processando: bool = False,
    ):
        """`definir_ajustes(legenda=…)` grava na tela principal a altura da legenda dos Shorts (a
        opção que a prévia usa); o valor atual vem de `origem_da_previa`. O corte das pausas é de
        cada Short e vive no próprio corte. `contexto_da_ia` diz como falar com o Ollama para
        refazer o título, a descrição e as hashtags. `processando`: há um processamento rodando
        (veja `definir_processamento`); vale já na carga, que também mexe em arquivos."""
        super().__init__(master, fg_color="transparent")
        self._pastas = pastas
        self._processando = processando
        self._arquivos_adiados = False  # algo ficou sem mudar de pasta por causa do processamento
        self._manter_visivel: int | None = None  # o corte que a lista mostra mesmo fora do filtro
        self._tarefas = tarefas
        self._origem_da_previa = origem_da_previa
        self._definir_ajustes = definir_ajustes
        self._contexto_da_ia = contexto_da_ia
        self._publicacao = publicacao  # as redes para postar; sem ele, o botão de postar some
        self._relogio = relogio
        self._agendamento_pendente: str | None = None  # o `after` que vê se uma hora passou
        #: o título que o nome do arquivo de cada corte tinha quando foi feito: se o título muda,
        #: o arquivo passa a se chamar como o título novo (e não como o que a IA sugeriu)
        self._titulo_do_nome: dict[int, str] = {}
        self._refazendo: tuple[int, str] | None = None  # (id do corte, campo) que a IA está criando
        self._inicio_da_ia = 0.0
        self._pastas_do_app = pastas_do_app
        self._introducao = introducao
        self._legenda_queimada = legenda_queimada or (lambda: False)
        self._janela_do_video = janela_do_video
        self._aparencia = aparencia or (lambda: None)
        self._mudanca: MudancaDeLimite | None = None  # o último ajuste, para mostrar na legenda
        self._ao_mudar = ao_mudar
        self._cortes: list[Corte] = []
        self._palavras: list[Palavra] = []
        self._frases: list[Frase] | None = None
        self._marcas: list[BooleanVar] = []
        self._registro: dict[str, dict] = {}
        self._ordem: list[int] = []
        self._atual: int | None = None
        self._linhas: dict[int, ctk.CTkFrame] = {}
        self._rotulos_da_linha: dict[int, dict[str, ctk.CTkLabel]] = {}
        self._cores_das_linhas: dict[int, object] = {}  # a cor que cada linha tem na tela
        self._mudou = False
        self._carregando = False  # True enquanto a tela é preenchida (não conta como edição)
        self._tela_desatualizada = False  # True entre trocar o corte e a tela mostrar o corte novo
        # a margem da tabela (categorias detectadas e Shorts já feitos), calculada uma vez por carga
        self._deteccoes: dict[int, Detectado] | None = None  # posição da frase → o que a IA viu
        self._mapa_de_frases: tuple[list[int], list[float]] | None = None
        self._tempos_das_palavras: tuple[list[float], list[float]] | None = None
        self._indices_dos_cortes: dict[int, tuple[tuple, frozenset[int]]] = {}
        self._itens_da_legenda_da_tabela: tuple = ()
        self._grupos_da_tabela: dict[str, list[int]] = {}
        self._salto_da_tabela: dict[str, int] = {}
        self._portugues: PainelDePortugues | None = None
        self._portugues_visivel = False
        self._bloqueado: str | None = None
        self._botoes_das_pontas: dict[str, list[ctk.CTkButton]] = {}
        self._historico: dict[int, _Historico] = {}  # por id do corte: desfazer e refazer
        self._pausa_lembrada = 1.0  # o tamanho de pausa que a caixa mostra quando está desligada
        self._dicionario: DicionarioPersonalizado | None = None
        self._botoes_do_historico: dict[str, list[ctk.CTkButton]] = {"desfazer": [], "refazer": []}
        self._atalhos_do_historico: list[tuple[str, str]] = []  # (evento, id) ligados na janela
        # a legenda tem dois lugares de edição, a caixa da legenda e a tabela, que se acompanham
        self._tabela_pendente = False  # a tabela foi editada e a caixa ainda não recebeu o texto
        self._sincronizacao: str | None = None  # o `after` que leva o texto da tabela à caixa
        self._acompanhar_a_tabela: str | None = None  # o `after` que leva o da caixa à tabela
        self._pasta_das_previas = Path(tempfile.gettempdir()) / "spurgeon_previas" / "shorts"
        self._previas: dict[str, tuple[str, previa.PreviaDoShort]] = {}  # por id do corte
        self._gerando: str | None = None  # id do corte cuja prévia está sendo gerada
        self._cancelar_previa = threading.Event()
        self._progresso_previa = 0.0
        self._montar()
        self.carregar()
        self._ligar_atalhos_do_historico()

    # --- carga --------------------------------------------------------------------------------
    @property
    def mudou(self) -> bool:
        return self._mudou

    def carregar(self) -> None:
        """Lê `cortes_shorts.json`, a transcrição e o registro dos Shorts já renderizados."""
        self._cortes = (
            carregar_cortes(self._pastas.cortes_shorts)
            if self._pastas.cortes_shorts.exists()
            else []
        )
        self._palavras = (
            Transcricao.carregar(self._pastas.transcricao).palavras
            if self._pastas.transcricao.exists()
            else []
        )
        if self._janela_do_video is not None:  # só a fala do trecho do vídeo que o projeto usa
            inicio, fim = self._janela_do_video()
            self._palavras = palavras_da_janela(self._palavras, inicio, fim)
        self._frases = None
        self._deteccoes = self._mapa_de_frases = self._tempos_das_palavras = None
        self._indices_dos_cortes = {}
        self._marcas = [BooleanVar(value=c.incluir) for c in self._cortes]
        self._titulo_do_nome = {c.id: c.titulo for c in self._cortes}
        self._atual = None
        self._mudou = False
        self._manter_visivel = None
        marcados = [c for c in self._cortes if c.agendado_para or c.postado]
        if marcados:  # marcados antes de existir a pasta (ou com o arquivo fora dela)
            with contextlib.suppress(OSError, ValueError, KeyError):
                self._mover_arquivos(marcados)
        self.recarregar_registro(reordenar=False)
        self._atualizar_texto_introducao()
        self._reordenar()
        if self._cortes:  # o filtro pode não mostrar nenhum: abre o primeiro na ordem de sempre
            self.selecionar((self._ordem or self._ordenar(range(len(self._cortes))))[0])
        else:
            self._limpar_detalhe()
        self._conferir_agendamentos()  # e daqui em diante, de tempos em tempos

    def descartar_alteracoes(self) -> None:
        """Joga fora o que não foi salvo: relê os cortes do arquivo (a tela continua no mesmo corte,
        se ele ainda existe) e esquece a história do desfazer."""
        id_aberto = self._cortes[self._atual].id if self._atual is not None else None
        self._tabela_pendente = False
        self._historico.clear()
        self.carregar()
        if id_aberto is not None:
            for i, corte in enumerate(self._cortes):
                if corte.id == id_aberto:
                    self.selecionar(i)
                    posicao = self._posicao_na_lista(i)
                    if posicao is not None:
                        self.after_idle(self._rolar_ate_o_atual, posicao)
                    break
        self._atualizar_botoes_do_historico()
        if self._ao_mudar is not None:
            self._ao_mudar()

    def recarregar_registro(self, *, reordenar: bool = True) -> None:
        """Relê o que já foi renderizado (depois de uma renderização)."""
        self._registro = ler_registro(self._pastas.shorts_renderizados)
        self._existem = {
            id_: (self._pastas.shorts / e["arquivo"]).exists() for id_, e in self._registro.items()
        }
        if reordenar and self._cortes:
            self._reordenar_mantendo()

    def _frases_do_sermao(self) -> list[Frase]:
        if self._frases is None:
            self._frases = separar_frases(self._palavras) if self._palavras else []
        return self._frases

    def situacao(self, corte: Corte) -> str:
        id_ = str(corte.id)
        return situacao_do_short(
            corte,
            self._registro.get(id_),
            self._existem.get(id_, False),
            com_legenda=self._legenda_queimada(),
            visual=self._aparencia(),
        )

    @property
    def cortes(self) -> list[Corte]:
        """Os cortes da lista, já com o que está digitado no detalhe."""
        self._guardar_detalhe()
        return list(self._cortes)

    def adicionar_corte(self, corte: Corte) -> None:
        """Põe na lista um corte novo (o do Shorts livre), marcado, e o abre no detalhe."""
        self._guardar_detalhe()
        self._cortes.append(corte)
        self._marcas.append(BooleanVar(value=corte.incluir))
        self._houve_mudanca()
        self._manter_visivel = len(self._cortes) - 1  # um corte novo não é postado nem agendado
        self._reordenar()
        self.selecionar(len(self._cortes) - 1)
        self.after_idle(self._rolar_ate_o_atual, self._ordem.index(self._atual))

    def pendentes(self) -> list[Corte]:
        """Os cortes marcados que ainda não têm Short renderizado (ou mudaram depois)."""
        self._guardar_detalhe()
        return [c for c in self._cortes if c.incluir and self.situacao(c) != SITUACAO_PRONTO]

    # --- montagem -----------------------------------------------------------------------------
    def _montar(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.grid(row=0, column=0, sticky="nsew")
        self._corpo = corpo
        corpo.columnconfigure(0, weight=2, minsize=330)  # a lista
        corpo.columnconfigure(1, weight=5, minsize=400)  # o detalhe: ocupa o que a janela der
        corpo.rowconfigure(2, weight=1)
        self._rotulo_intro = ctk.CTkLabel(
            corpo, anchor="w", justify="left", wraplength=1080, text=""
        )
        self._rotulo_intro.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        corpo.bind(
            "<Configure>",
            lambda e: self._rotulo_intro.configure(wraplength=max(300, e.width - 40)),
        )  # o texto acompanha a largura da área

        barra = ctk.CTkFrame(corpo, fg_color="transparent")
        barra.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))
        marcar = ctk.CTkButton(
            barra, text="Marcar todos", width=110, command=lambda: self.marcar_todos(True)
        )
        marcar.pack(side="left")
        dica(
            marcar,
            "Marca todos os Shorts que a lista mostra para serem renderizados (com um filtro "
            "ligado, só os que ele mostra).",
        )
        desmarcar = ctk.CTkButton(
            barra, text="Desmarcar todos", width=120, command=lambda: self.marcar_todos(False)
        )
        desmarcar.pack(side="left", padx=6)
        dica(
            desmarcar,
            "Desmarca todos os Shorts que a lista mostra: nenhum deles será renderizado até você "
            "marcar de novo (com um filtro ligado, só os que ele mostra).",
        )
        ctk.CTkLabel(barra, text="Ordenar por:", text_color=_COR_SUAVE).pack(
            side="left", padx=(18, 6)
        )
        self._seletor_ordem = ctk.CTkSegmentedButton(
            barra, values=[ORDEM_NOTA, ORDEM_HORARIO], command=lambda _v: self._reordenar()
        )
        self._seletor_ordem.set(ORDEM_NOTA)
        self._seletor_ordem.pack(side="left")
        dica_do_seletor(
            self._seletor_ordem,
            {
                ORDEM_NOTA: "Os Shorts de maior nota ficam no topo da lista.",
                ORDEM_HORARIO: "Os Shorts ficam na ordem em que aparecem no sermão.",
            },
        )
        ctk.CTkLabel(barra, text="Mostrar:", text_color=_COR_SUAVE).pack(side="left", padx=(18, 6))
        self._seletor_filtro = ctk.CTkSegmentedButton(
            barra, values=list(FILTROS), command=lambda _v: self._trocar_filtro()
        )
        self._seletor_filtro.set(FILTRO_TODOS)
        self._seletor_filtro.pack(side="left")
        dica_do_seletor(
            self._seletor_filtro,
            {
                FILTRO_TODOS: "Todos os Shorts do sermão.",
                FILTRO_POSTADOS: "Só os Shorts que você já postou (com o dia e a hora em que "
                "foram marcados).",
                FILTRO_AGENDADOS: "Só os Shorts agendados: têm um horário marcado e ainda não "
                "foram ao ar.",
                FILTRO_SEM_POSTAR: "Só os Shorts que ainda não foram postados nem agendados: o "
                "que falta publicar.",
            },
        )
        self._resumo = ctk.CTkLabel(corpo, text="", anchor="w", text_color=_COR_SUAVE)
        self._resumo.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 4))

        self._lista = QuadroRolavel(corpo, label_text="")
        self._lista.grid(row=2, column=0, sticky="nsew", padx=(14, 7), pady=(0, 8))
        self._lista.columnconfigure(0, weight=1)

        self._detalhe = QuadroRolavel(corpo, label_text="")
        self._detalhe.grid(row=2, column=1, sticky="nsew", padx=(7, 14), pady=(0, 8))
        self._detalhe.columnconfigure(0, weight=1)
        self._montar_detalhe()
        self._detalhe.bind("<Configure>", self._ajustar_quebras_do_detalhe, add="+")

        self._aviso_bloqueio = ctk.CTkLabel(
            self, text="", wraplength=700, justify="center", text_color=_COR_ALERTA
        )

    def _atualizar_texto_introducao(self) -> None:
        principais = sum(1 for c in self._cortes if not c.reserva)
        reservas = len(self._cortes) - principais
        marcados = f"{principais} cortes sugeridos já vêm marcados"
        if reservas:
            marcados += f", mais {reservas} reservas desmarcadas"
        self._rotulo_intro.configure(
            text=self._introducao
            or (
                f"{marcados}. A nota (0 a 100) soma o que a IA achou com o que se mede no texto: "
                "veja o detalhe ao lado. Desmarque o que não quiser e, se precisar, ajuste o "
                "início e o fim de um corte e corrija a legenda."
            )
        )

    def _montar_detalhe(self) -> None:
        d = self._detalhe
        self._rotulo_nota = ctk.CTkLabel(d, anchor="w", font=ctk.CTkFont(size=16, weight="bold"))
        self._rotulo_nota.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        linha_categoria = ctk.CTkFrame(d, fg_color="transparent")
        linha_categoria.grid(row=1, column=0, sticky="ew", padx=8)
        linha_categoria.columnconfigure(1, weight=1)
        self._chip_categoria = ctk.CTkLabel(
            linha_categoria, text="", text_color="white", corner_radius=6, height=22,
            font=ctk.CTkFont(size=12, weight="bold"),
        )  # fmt: skip
        self._chip_categoria.grid(row=0, column=0, sticky="w", padx=(0, 8))
        dica(
            self._chip_categoria,
            "A categoria que a IA deu a este trecho (cada uma tem a sua cor, na lista e aqui).",
        )
        self._rotulo_categoria = ctk.CTkLabel(linha_categoria, anchor="w", text_color=_COR_SUAVE)
        self._rotulo_categoria.grid(row=0, column=1, sticky="ew")
        self._botao_nota = ctk.CTkButton(
            linha_categoria,
            text="▸ Como a nota foi calculada",
            width=170,
            height=22,
            fg_color="transparent",
            text_color=_COR_SUAVE,
            hover_color=("gray80", "gray25"),
            command=self._alternar_nota,
        )
        self._botao_nota.grid(row=0, column=2, sticky="e")
        dica(
            self._botao_nota,
            "Mostra quanto cada parte (retenção, bordas, duração…) somou na nota de 0 a 100.",
        )

        self._partes: dict[str, tuple[ctk.CTkProgressBar, ctk.CTkLabel]] = {}
        quadro = ctk.CTkFrame(d, fg_color="transparent")
        quadro.grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 2))
        quadro.columnconfigure(1, weight=1)
        for linha, (chave, (rotulo, _maximo)) in enumerate(PARTES.items()):
            ctk.CTkLabel(quadro, text=rotulo, anchor="w", width=190).grid(
                row=linha, column=0, sticky="w", pady=1
            )
            barra = ctk.CTkProgressBar(quadro, height=9)
            barra.grid(row=linha, column=1, sticky="ew", padx=8)
            valor = ctk.CTkLabel(quadro, text="", width=70, anchor="e")
            valor.grid(row=linha, column=2, sticky="e")
            self._partes[chave] = (barra, valor)
        quadro.grid_remove()  # a decomposição da nota só aparece quando pedida
        self._quadro_nota = quadro
        self._rotulo_alerta = RotuloQueSome(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ALERTA, text=""
        )
        self._rotulo_alerta.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 0))
        self._rotulo_motivo = RotuloQueSome(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_SUAVE, text=""
        )
        self._rotulo_motivo.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 8))

        self._var_titulo = StringVar()
        self._var_hashtags = StringVar()
        self._botoes_da_ia: dict[str, ctk.CTkButton] = {}
        self._situacao_da_ia: dict[str, ctk.CTkLabel] = {}
        self._var_postado = BooleanVar(value=False)
        self._cabeca_do_campo(d, 5, "Título", "titulo")
        quadro_titulo = ctk.CTkFrame(d, fg_color="transparent")
        quadro_titulo.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 6))
        quadro_titulo.columnconfigure(0, weight=1)
        self._campo_titulo = ctk.CTkEntry(quadro_titulo, textvariable=self._var_titulo)
        dica(
            self._campo_titulo,
            "O título do Short (também o nome do arquivo). Edite à vontade: a lista da esquerda "
            "acompanha na hora e o arquivo já renderizado é renomeado ao salvar.",
        )
        self._campo_titulo.grid(row=0, column=0, sticky="ew")
        self._aviso_do_titulo = RotuloQueSome(
            quadro_titulo, anchor="w", justify="left", wraplength=420, text_color=_COR_ALERTA,
            text="",
        )  # fmt: skip
        self._aviso_do_titulo.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        linha_postado = ctk.CTkFrame(quadro_titulo, fg_color="transparent")
        linha_postado.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        linha_postado.columnconfigure(1, weight=1)
        self._check_postado = ctk.CTkCheckBox(
            linha_postado, text="Já postado", variable=self._var_postado,
            command=self._ao_marcar_postado,
        )  # fmt: skip
        self._check_postado.grid(row=0, column=0, sticky="w")
        dica(
            self._check_postado,
            "Marque quando já publicou este Short (em qualquer rede), para se organizar. "
            "Grava na hora "
            f"(sem precisar salvar) e move o arquivo para {_PASTA}, dentro da pasta do "
            "projeto. Desmarcar o traz de volta.",
        )
        self._rotulo_postado = ctk.CTkLabel(
            linha_postado, text="", anchor="w", justify="left", wraplength=300,
            text_color=_COR_SUAVE, font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._rotulo_postado.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self._linha_agendamento = LinhaDeAgendamento(
            quadro_titulo,
            ao_mudar=self._ao_agendar,
            ao_postar=self._abrir_postagem,
            relogio=self._relogio,
        )
        self._linha_agendamento.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        if self._publicacao is None:  # sem as redes (janela de aprovação) não há o que postar
            self._linha_agendamento.botao_postar.grid_remove()
        self._cabeca_do_campo(d, 7, "Descrição", "descricao")
        self._campo_descricao = ctk.CTkTextbox(d, height=64, wrap="word")
        self._campo_descricao.grid(row=8, column=0, sticky="ew", padx=8, pady=(0, 6))
        self._cabeca_do_campo(d, 9, "Hashtags", "hashtags")
        self._campo_hashtags = ctk.CTkEntry(d, textvariable=self._var_hashtags)
        dica(self._campo_hashtags, "As hashtags do Short, separadas por espaço (com ou sem #).")
        self._campo_hashtags.grid(row=10, column=0, sticky="ew", padx=8, pady=(0, 8))
        for variavel in (self._var_titulo, self._var_hashtags):
            variavel.trace_add("write", lambda *_a: self._marcar_mudanca())
        self._var_titulo.trace_add("write", lambda *_a: self._atualizar_aviso_do_titulo())
        self._var_titulo.trace_add("write", lambda *_a: self._espelhar_titulo_na_lista())
        self._campo_descricao.bind("<KeyRelease>", lambda _e: self._marcar_mudanca())

        self._rotulo_tempo = ctk.CTkLabel(d, anchor="w", font=ctk.CTkFont(weight="bold"))
        self._rotulo_tempo.grid(row=11, column=0, sticky="ew", padx=8)
        self._rotulo_limite = RotuloQueSome(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ERRO, text=""
        )
        self._rotulo_limite.grid(row=12, column=0, sticky="ew", padx=8)
        self._var_inicio = StringVar()
        self._var_fim = StringVar()
        self._campo_inicio = self._montar_campo_do_tempo(
            d, 13, "Começa em", "inicio", self._var_inicio
        )
        self._montar_botoes_da_ponta(d, 14, "inicio")
        self._montar_janela_do_corte(d, 15)
        self._montar_botoes_da_ponta(d, 17, "fim")
        self._campo_fim = self._montar_campo_do_tempo(d, 18, "Termina em", "fim", self._var_fim)
        self._rotulo_ajuste = RotuloQueSome(
            d, anchor="w", text="", text_color=_COR_ALERTA, wraplength=420, justify="left"
        )
        self._rotulo_ajuste.grid(row=21, column=0, sticky="ew", padx=8, pady=(6, 0))
        self._rotulo_avisos = RotuloQueSome(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_ALERTA, text=""
        )
        self._rotulo_avisos.grid(row=22, column=0, sticky="ew", padx=8, pady=(0, 4))

        cabeca_legenda = ctk.CTkFrame(d, fg_color="transparent")
        cabeca_legenda.grid(row=23, column=0, sticky="ew", padx=8, pady=(8, 0))
        ctk.CTkLabel(cabeca_legenda, text="Legenda do Short", anchor="w").pack(side="left")
        self._rotulo_editada = ctk.CTkLabel(cabeca_legenda, text="", text_color=_COR_ALERTA)
        self._rotulo_editada.pack(side="left", padx=8)
        self._botao_exportar_legenda = ctk.CTkButton(
            cabeca_legenda, text="Exportar a legenda…", width=150, height=26,
            fg_color="transparent", border_width=1, command=self._exportar_legenda,
        )  # fmt: skip
        self._botao_exportar_legenda.pack(side="right")
        dica(
            self._botao_exportar_legenda,
            "Salva a legenda deste Short num arquivo de texto, uma frase por linha, com o tempo de "
            "cada frase no Short pronto (o que está na caixa agora, mesmo sem salvar).",
        )
        self._dica_legenda = ctk.CTkLabel(
            d,
            text="Cada bloco de até 2 linhas é o que aparece na tela de uma vez, como no vídeo. "
            "As quebras se refazem sozinhas ao sair do campo.",
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._dica_legenda.grid(row=24, column=0, sticky="ew", padx=8)
        moldura = ctk.CTkFrame(d, fg_color="transparent")
        moldura.grid(row=25, column=0, sticky="ew", padx=8, pady=(2, 4))
        moldura.columnconfigure(0, weight=1)
        self._rotulo_mudanca = RotuloQueSome(
            moldura, anchor="w", justify="left", wraplength=420, text=""
        )
        self._rotulo_mudanca.grid(row=0, column=0, sticky="ew")
        self._contexto_antes = RotuloQueSome(
            moldura, anchor="w", justify="left", wraplength=420, text="", text_color=_COR_FORA,
            font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._contexto_antes.grid(row=1, column=0, sticky="ew")
        self._texto = ctk.CTkTextbox(
            moldura, height=190, wrap="word", undo=True,
            font=ctk.CTkFont(size=15, weight="bold"),
        )  # fmt: skip
        self._texto.grid(row=2, column=0, sticky="ew", pady=(2, 2))
        self._texto._textbox.tag_config("novo", background="#1f5f3a", foreground="#ffffff")
        self._contexto_depois = RotuloQueSome(
            moldura, anchor="w", justify="left", wraplength=420, text="", text_color=_COR_FORA,
            font=ctk.CTkFont(size=12),
        )  # fmt: skip
        self._contexto_depois.grid(row=3, column=0, sticky="ew")
        self._texto.bind("<KeyRelease>", lambda _e: self._ao_editar_legenda())
        self._texto.bind("<FocusOut>", lambda _e: self._reorganizar_legenda())
        self._rotulo_queimada = RotuloQueSome(
            d, anchor="w", justify="left", wraplength=420, text_color=_COR_SUAVE, text=""
        )
        self._rotulo_queimada.grid(row=26, column=0, sticky="ew", padx=8)
        botoes = ctk.CTkFrame(d, fg_color="transparent")
        botoes.grid(row=27, column=0, sticky="ew", padx=8, pady=(4, 8))
        botao_portugues = ctk.CTkButton(
            botoes, text="Verificar português", width=150, command=self._alternar_portugues
        )
        botao_portugues.pack(side="left")
        dica(
            botao_portugues,
            "Procura erros de ortografia, repetições e pontuação na legenda do Short e deixa "
            "corrigir no clique. Os erros ficam sublinhados na caixa acima.",
        )
        botao_desfazer_correcoes = ctk.CTkButton(
            botoes,
            text="Desfazer correções",
            width=140,
            fg_color=("gray70", "gray30"),
            command=self.desfazer_legenda,
        )
        botao_desfazer_correcoes.pack(side="left", padx=10)
        dica(
            botao_desfazer_correcoes,
            "Volta a legenda deste Short ao texto da transcrição, sem as correções que você fez.",
        )
        botao_dicionario = ctk.CTkButton(
            botoes,
            text="Dicionário…",
            width=110,
            fg_color=("gray70", "gray30"),
            command=self.abrir_dicionario,
        )
        botao_dicionario.pack(side="left")
        dica(
            botao_dicionario,
            "Abre o dicionário personalizado: os termos que você guardou como certos. Dá para "
            "acrescentar termos e tirar os que foram guardados por engano.",
        )
        self._montar_previa(d, 29)

    def _montar_previa(self, pai, linha: int) -> None:
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=0, sticky="ew", padx=8, pady=(6, 12))
        quadro.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            quadro, text="Prévia do Short", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="ew")
        self._dica_previa = ctk.CTkLabel(
            quadro,
            text="Uma miniatura de qualidade bem baixa, só para ver como o Short vai ficar: "
            "recorte, zoom, cor e legenda (a legenda aparece sempre na prévia; no vídeo final "
            "ela só é queimada com “Legendas dinâmicas” ligado). O som é o do vídeo original "
            "(sem limpeza nem volume ajustado) e a marca d'água não aparece.",
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._dica_previa.grid(row=1, column=0, sticky="ew")
        self._montar_ajustes_do_short(quadro, 2)
        linha_botoes = ctk.CTkFrame(quadro, fg_color="transparent")
        linha_botoes.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self._botao_previa = ctk.CTkButton(
            linha_botoes, text="Gerar prévia", width=130, command=self.gerar_previa
        )
        self._botao_previa.pack(side="left")
        dica(
            self._botao_previa,
            "Monta uma miniatura do Short como ele vai ficar (recorte, zoom, cor, legenda, saltos "
            "e pausas cortadas), para ver e ouvir sem renderizar.",
        )
        self._botao_cancelar_previa = ctk.CTkButton(
            linha_botoes,
            text="Cancelar",
            width=90,
            fg_color=("gray70", "gray30"),
            command=self._clicou_em_cancelar,
        )
        dica(self._botao_cancelar_previa, "Interrompe a montagem da prévia.")
        self._barra_previa = ctk.CTkProgressBar(quadro, height=8)
        self._barra_previa.set(0)
        self._situacao_previa = ctk.CTkLabel(
            quadro, anchor="w", justify="left", wraplength=420, text="", text_color=_COR_SUAVE
        )
        self._situacao_previa.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        self._player = MiniPlayer(quadro)
        self._player.grid(row=6, column=0, pady=(6, 0))
        self._player.grid_remove()

    def _montar_ajustes_do_short(self, pai, linha: int) -> None:
        """O que se acerta olhando a prévia: a altura da legenda (de todos os Shorts, as mesmas
        opções da tela de opções) e o corte das pausas (só deste Short).

        Mexer na altura da legenda ou nas pausas deixa a prévia guardada sem valor até ser gerada
        de novo.
        """
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=0, sticky="ew", pady=(6, 0))
        legenda = ctk.CTkFrame(quadro, fg_color="transparent")
        legenda.pack(fill="x")
        ctk.CTkLabel(legenda, text="Legenda", anchor="w", width=60).pack(side="left")
        self._botoes_da_legenda = []
        for texto, sentido, ajuda in (
            ("▲ Subir", -1, "Sobe a legenda dos Shorts (2% da altura por clique)."),
            ("▼ Descer", 1, "Desce a legenda dos Shorts (2% da altura por clique)."),
        ):
            botao = ctk.CTkButton(
                legenda,
                text=texto,
                width=90,
                height=28,
                fg_color=("gray70", "gray30"),
                command=lambda s=sentido: self.mover_legenda(s),
            )
            botao.pack(side="left", padx=(0, 10))
            dica(botao, ajuda + " Vale para todos os Shorts; gere a prévia de novo para ver.")
            self._botoes_da_legenda.append(botao)
        self._rotulo_legenda_altura = ctk.CTkLabel(legenda, text="", text_color=_COR_SUAVE)
        self._rotulo_legenda_altura.pack(side="left", padx=(4, 0))
        self._quadro_da_legenda = legenda

        pausas = ctk.CTkFrame(quadro, fg_color="transparent")
        pausas.pack(fill="x", pady=(8, 0))
        self._var_cortar_pausas = BooleanVar(value=False)
        self._var_pausa = StringVar(value="1,0")
        self._caixa_pausas = ctk.CTkCheckBox(
            pausas,
            text="Cortar as pausas deste Short maiores que",
            variable=self._var_cortar_pausas,
            command=self._mudou_o_corte_de_pausas,
        )
        self._caixa_pausas.pack(side="left")
        dica(
            self._caixa_pausas,
            "Encurta as pausas da fala neste Short (só neste): a pausa que chega ao tamanho ao "
            "lado fica com um respiro de até 0,4 s. Deixa o Short mais ligeiro; confira na prévia.",
        )
        self._campo_pausa = ctk.CTkEntry(
            pausas, textvariable=self._var_pausa, width=56, justify="center"
        )
        self._campo_pausa.pack(side="left", padx=(8, 0))
        dica(
            self._campo_pausa,
            "Tamanho da pausa, em segundos (de 0,1 a 10), a partir do qual ela é cortada.",
        )
        self._campo_pausa.bind("<Return>", lambda _e: self._mudou_o_corte_de_pausas())
        self._campo_pausa.bind("<FocusOut>", lambda _e: self._mudou_o_corte_de_pausas())
        ctk.CTkLabel(pausas, text="s", text_color=_COR_SUAVE).pack(side="left", padx=(4, 12))
        self._botao_pausa_em_todos = ctk.CTkButton(
            pausas,
            text="Aplicar a todos",
            width=116,
            height=26,
            fg_color=("gray70", "gray30"),
            command=self.aplicar_a_pausa_em_todos,
        )
        self._botao_pausa_em_todos.pack(side="left")
        dica(
            self._botao_pausa_em_todos,
            "Copia o corte de pausas deste Short (ligado ou desligado, com este tamanho) para "
            "todos os outros Shorts da lista.",
        )
        self._rotulo_pausas = ctk.CTkLabel(
            quadro,
            text="",
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._rotulo_pausas.pack(fill="x", pady=(2, 0))
        if self._definir_ajustes is None:
            legenda.pack_forget()  # a janela de aprovação não tem prévia: sem altura da legenda

    def _ajustar_quebras_do_detalhe(self, evento) -> None:
        largura = max(240, evento.width - 70)
        for rotulo in (
            self._rotulo_alerta,
            self._rotulo_motivo,
            self._rotulo_ajuste,
            self._rotulo_avisos,
            self._rotulo_queimada,
            self._rotulo_limite,
            self._dica_legenda,
            self._dica_previa,
            self._situacao_previa,
            self._rotulo_pausas,
            self._dica_janela,
            self._rotulo_mudanca,
            self._contexto_antes,
            self._contexto_depois,
        ):
            with contextlib.suppress(TclError):  # o editor pode ter sido refeito (projeto reaberto)
                rotulo.configure(wraplength=largura)

    def _alternar_nota(self) -> None:
        if self._quadro_nota.winfo_manager():
            self._quadro_nota.grid_remove()
            self._botao_nota.configure(text="▸ Como a nota foi calculada")
        else:
            self._quadro_nota.grid()
            self._botao_nota.configure(text="▾ Como a nota foi calculada")

    def _montar_campo_do_tempo(self, pai, linha: int, titulo: str, ponta: str, variavel: StringVar):
        quadro = ctk.CTkFrame(pai, fg_color="transparent")
        quadro.grid(row=linha, column=0, sticky="ew", padx=8, pady=(4, 0))
        ctk.CTkLabel(quadro, text=titulo, anchor="w", width=80).pack(side="left")
        campo = ctk.CTkEntry(quadro, textvariable=variavel, width=88, justify="center")
        campo.pack(side="left")
        campo.bind("<Return>", lambda _e, p=ponta: self.aplicar_tempo(p))
        campo.bind("<FocusOut>", lambda _e, p=ponta: self.aplicar_tempo(p))
        dica(
            campo,
            f"O instante em que o Short {'começa' if ponta == 'inicio' else 'termina'}, em "
            "minutos:segundos,décimos (por exemplo 2:14,3). Ao sair do campo, o corte vai até a "
            "palavra mais perto.",
        )
        ctk.CTkLabel(quadro, text="min:s,décimos", text_color=_COR_SUAVE).pack(
            side="left", padx=(6, 0)
        )
        return campo

    def _cabeca_do_campo(self, pai, linha: int, titulo: str, campo: str) -> None:
        """O nome do campo, o aviso do que a IA está fazendo e o botão que a manda refazê-lo."""
        cabeca = ctk.CTkFrame(pai, fg_color="transparent")
        cabeca.grid(row=linha, column=0, sticky="ew", padx=8)
        cabeca.columnconfigure(1, weight=1)
        ctk.CTkLabel(cabeca, text=titulo, anchor="w").grid(row=0, column=0, sticky="w")
        situacao = ctk.CTkLabel(
            cabeca, text="", anchor="e", text_color=_COR_SUAVE, font=ctk.CTkFont(size=12)
        )
        situacao.grid(row=0, column=1, sticky="e", padx=(0, 6))
        botao = ctk.CTkButton(
            cabeca,
            text="↻ Refazer com IA",
            width=124,
            height=22,
            font=ctk.CTkFont(size=12),
            fg_color=("gray70", "gray30"),
            command=lambda: self.refazer_com_ia(campo),
        )
        botao.grid(row=0, column=2, sticky="e")
        dica(
            botao,
            f"Pede à IA {_ARTIGO_DO_CAMPO[campo]} com base no texto que está no Short agora "
            "(vale também depois de você cortar ou trocar palavras). Demora alguns segundos.",
        )
        if self._tarefas is None or self._contexto_da_ia is None:
            botao.grid_remove()  # sem a IA (a janela de aprovação) o botão não faz sentido
        self._botoes_da_ia[campo] = botao
        self._situacao_da_ia[campo] = situacao

    def _montar_botoes_da_ponta(self, pai, linha: int, ponta: str) -> None:
        """Os quatro botões de uma ponta, com a seta mostrando para onde a ponta anda.

        No começo, pôr palavras leva o começo para a esquerda e tirá-las o leva para a direita; no
        fim é o contrário. A ordem não é a mesma nas duas pontas, de propósito: o começo vai do
        que acrescenta ao que tira e o fim, do que tira ao que acrescenta.
        """
        botoes = ctk.CTkFrame(pai, fg_color="transparent")
        botoes.grid(row=linha, column=0, sticky="ew", padx=8, pady=(2, 2))
        sinal = 1 if ponta == "inicio" else -1  # o que "tirar" faz com o número da palavra
        if ponta == "inicio":
            rotulos = ("◀ +palavra", "◀ +frase", "−frase ▶", "−palavra ▶")
            comandos = (
                lambda: self.mover(ponta, -sinal),
                lambda: self.mover_frase(ponta, -sinal),
                lambda: self.mover_frase(ponta, sinal),
                lambda: self.mover(ponta, sinal),
            )
        else:
            rotulos = ("◀ −palavra", "◀ −frase", "+frase ▶", "+palavra ▶")
            comandos = (
                lambda: self.mover(ponta, sinal),
                lambda: self.mover_frase(ponta, sinal),
                lambda: self.mover_frase(ponta, -sinal),
                lambda: self.mover(ponta, -sinal),
            )
        self._botoes_das_pontas[ponta] = []
        ajudas = _AJUDA_DOS_BOTOES_DA_PONTA[ponta]
        for texto, comando, ajuda in zip(rotulos, comandos, ajudas, strict=True):
            botao = ctk.CTkButton(
                botoes,
                text=texto,
                width=88,
                height=28,
                fg_color=("gray70", "gray30"),
                command=comando,
            )
            botao.pack(side="left", padx=(0, 10))
            dica(botao, ajuda)
            self._botoes_das_pontas[ponta].append(botao)
        # desfazer e refazer ficam no lado direito (as duas pontas mexem na mesma história)
        for nome, texto, comando, ajuda in (
            ("refazer", "Refazer ↷", self.refazer, "Refaz o que foi desfeito (Ctrl+Y)."),
            ("desfazer", "↶ Desfazer", self.desfazer, "Desfaz a última mudança neste Short: "
             "pontas, palavras cortadas, pausas e legenda (Ctrl+Z)."),
        ):  # fmt: skip
            botao = ctk.CTkButton(
                botoes, text=texto, width=92, height=28,
                fg_color=("gray70", "gray30"), command=comando, state="disabled",
            )  # fmt: skip
            botao.pack(side="right", padx=(10, 0))
            dica(botao, ajuda)
            self._botoes_do_historico[nome].append(botao)

    def _montar_janela_do_corte(self, pai, linha: int) -> None:
        """A tabela de frases: o que vem antes (cinza), o corte inteiro e o que vem depois (cinza).

        Cada linha é uma frase, com a hora ao lado. As do corte se editam ali mesmo: o que se
        escreve vira a legenda do Short, a mesma da caixa “Legenda do Short” mais abaixo.
        """
        cabeca = ctk.CTkFrame(pai, fg_color="transparent")
        cabeca.grid(row=linha, column=0, sticky="ew", padx=8, pady=(6, 0))
        cabeca.columnconfigure(1, weight=1)
        self._ferramentas = FerramentasDeMarca(cabeca, ao_trocar=self._trocou_a_ferramenta)
        self._ferramentas.grid(row=0, column=0, sticky="w")
        for coluna, (texto, ponta, ajuda) in enumerate(
            (
                ("↥ começo", "inicio", "Rola a tabela até onde o Short começa."),
                ("↧ fim", "fim", "Rola a tabela até onde o Short termina."),
            ),
            2,
        ):
            botao = ctk.CTkButton(
                cabeca,
                text=texto,
                width=72,
                height=24,
                fg_color="transparent",
                text_color=_COR_SUAVE,
                hover_color=("gray80", "gray25"),
                command=lambda p=ponta: self._ir_a_ponta(p),
            )
            botao.grid(row=0, column=coluna, padx=(8, 0), sticky="e")
            dica(botao, ajuda)
        self._dica_janela = ctk.CTkLabel(
            cabeca,
            text="Uma frase por linha. Cinza: fora do Short. Branco: o texto do Short, que se "
            "corrige aqui mesmo (vale para a legenda). Riscado: pedaço cortado do meio. Troque a "
            "ferramenta acima para pintar (marca-texto: acende o que vai entrar) ou apagar "
            "(borracha: acende o que vai sair) palavra por palavra.",
            anchor="w",
            justify="left",
            wraplength=500,
            text_color=_COR_SUAVE,
            font=ctk.CTkFont(size=12),
        )
        self._dica_janela.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._legenda_da_tabela = LegendaDeCategorias(cabeca, ao_clicar=self._ir_para_na_tabela)
        self._legenda_da_tabela.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._legenda_da_tabela.grid_remove()  # só aparece quando há o que mostrar
        self._janela = TabelaDeFrases(
            pai,
            height=_ALTURA_DA_JANELA,
            ao_editar=self._ao_editar_tabela,
            ao_duplo_clique=self._levar_ponta_a,
            ao_menu=lambda indice, ponta: self._levar_ponta_a(indice, ponta),
            ao_pintar=self._pintou,
        )
        self._janela.grid(row=linha + 1, column=0, sticky="ew", padx=8, pady=(0, 2))

    # --- lista --------------------------------------------------------------------------------
    def _ordenar(self, indices) -> list[int]:
        """Os cortes `indices` na ordem escolhida (maior nota ou horário no sermão)."""
        if self._seletor_ordem.get() == ORDEM_HORARIO:
            return sorted(indices, key=lambda i: self._cortes[i].inicio)
        return sorted(indices, key=lambda i: (-self._cortes[i].nota, self._cortes[i].inicio))

    def _ordem_desejada(self) -> list[int]:
        """Os cortes que a lista mostra, na ordem escolhida: o filtro de publicação tira os outros
        (o corte que acabou de mudar de situação fica até a lista ser refeita)."""
        filtro = self._seletor_filtro.get()
        visiveis = [
            i for i, corte in enumerate(self._cortes)
            if i == self._manter_visivel or passa_no_filtro(corte, filtro)
        ]  # fmt: skip
        return self._ordenar(visiveis)

    def _posicao_na_lista(self, i: int | None) -> int | None:
        """Em que posição da lista está o corte `i`; None se ela não o mostra (o filtro o tirou)."""
        return self._ordem.index(i) if i in self._ordem else None

    def _trocar_filtro(self) -> None:
        """Mudou o filtro de publicação: a lista se refaz e, se o Short aberto saiu dela, abre o
        primeiro que ela mostra (sem nenhum, a tela do detalhe fica como estava)."""
        self._manter_visivel = None
        self._reordenar()
        if self._ordem and self._atual not in self._ordem:
            self.selecionar(self._ordem[0])
            self.after_idle(self._rolar_ate_o_atual, 0)

    def _reordenar(self) -> None:
        self._guardar_detalhe()
        self._ordem = self._ordem_desejada()
        for filho in self._lista.winfo_children():
            filho.destroy()
        self._linhas.clear()
        self._rotulos_da_linha.clear()
        self._cores_das_linhas.clear()
        for posicao, i in enumerate(self._ordem):
            self._linhas[i] = self._criar_linha(posicao, i)
        if not self._ordem and self._cortes:
            ctk.CTkLabel(
                self._lista, text="Nenhum Short neste filtro.", text_color=_COR_SUAVE
            ).grid(row=0, column=0, pady=24)
        self._atualizar_resumo()
        if self._atual is not None:
            self._pintar_atual()
            posicao = self._posicao_na_lista(self._atual)
            if posicao is not None:
                self.after_idle(self._rolar_ate_o_atual, posicao)

    def _reposicionar(self) -> None:
        """Põe a lista na ordem de agora sem recriar as linhas (só muda o lugar de cada uma)."""
        nova = self._ordenar(self._ordem)
        if nova == self._ordem:
            return
        self._ordem = nova
        for posicao, i in enumerate(nova):
            self._linhas[i].grid(row=posicao, column=0, sticky="ew", pady=2)

    def _atualizar_linha(self, i: int) -> None:
        """Refaz os textos da linha do corte `i` (depois de mexer nas pontas), no lugar."""
        rotulos = self._rotulos_da_linha.get(i)
        if rotulos is None:
            return
        corte = self._cortes[i]
        textos = self._textos_da_linha(corte)
        with contextlib.suppress(TclError):
            self._configurar(
                rotulos["chip"],
                text=rotulo_da_categoria(corte.categoria),
                fg_color=cor_da_categoria(corte.categoria),
            )
            self._configurar(
                rotulos["nota"],
                text=formatar_nota(corte.nota),
                fg_color=_COR_NOTA[faixa_da_nota(corte.nota)],
            )
            self._pintar_selo(rotulos["selo"], corte)
            for nome in ("info", "titulo", "legenda"):
                self._configurar(rotulos[nome], text=textos[nome])

    def _pintar_selo(self, rotulo: ctk.CTkLabel, corte: Corte) -> None:
        """O selo "✔ Postado 03/11 às 18:30" ou "📅 Agendado 07/11 às 19:00" da linha; some
        (e não ocupa espaço) quando o Short não foi postado nem agendado."""
        selo = selo_de_publicacao(corte, self._relogio())
        if selo is None:
            if rotulo.winfo_manager():
                rotulo.grid_remove()
            return
        texto, cor = selo
        self._configurar(rotulo, text=texto, fg_color=cor)
        if not rotulo.winfo_manager():
            rotulo.grid()

    def _textos_da_linha(self, corte: Corte) -> dict[str, str]:
        sufixo = " · reserva" if corte.reserva else ""
        sufixo += " · editado" if corte.editado else ""
        sufixo += " · ✎ legenda" if corte.legenda else ""
        sufixo += " · ⚠ contexto" if corte.alerta_contexto else ""
        sufixo += f" · ⚠ mais de {DURACAO_MAXIMA_S:g} s" if passa_do_limite(corte) else ""
        situacao = _ROTULO_SITUACAO[self.situacao(corte)]
        trecho = trecho_da_legenda(self._palavras, corte)
        return {
            "info": (
                f"{formatar_tempo(corte.inicio)}–{formatar_tempo(corte.fim)}"
                f" · {descrever_duracao(corte.duracao)}"
                f"{sufixo}\n{rotulo_da_origem(corte)}"
            ),
            "titulo": corte.titulo or corte.texto[:60],
            "legenda": f"“{trecho}”" + (f"\n{situacao}" if situacao else ""),
        }

    def _criar_linha(self, posicao: int, i: int) -> ctk.CTkFrame:
        corte = self._cortes[i]
        textos = self._textos_da_linha(corte)
        linha = ctk.CTkFrame(self._lista, fg_color=_COR_LINHA, corner_radius=6)
        self._cores_das_linhas[i] = _COR_LINHA
        linha.grid(row=posicao, column=0, sticky="ew", pady=2)
        linha.columnconfigure(2, weight=1)
        caixa_de_marcar = ctk.CTkCheckBox(
            linha, text="", width=24, variable=self._marcas[i], command=lambda: self._ao_marcar(i)
        )
        caixa_de_marcar.grid(row=0, column=0, rowspan=5, padx=(8, 0), pady=6)
        dica(caixa_de_marcar, "Marcado = este Short será renderizado; desmarcado fica de fora.")
        nota = ctk.CTkLabel(
            linha,
            text=formatar_nota(corte.nota),
            width=38,
            height=26,
            corner_radius=6,
            text_color="white",
            fg_color=_COR_NOTA[faixa_da_nota(corte.nota)],
            font=ctk.CTkFont(weight="bold"),
        )
        nota.grid(row=0, column=1, rowspan=5, padx=8, pady=6)
        dica(nota, "A nota de 0 a 100: o que a IA achou somado ao que se mede no texto.")
        chip = ctk.CTkLabel(
            linha, text=rotulo_da_categoria(corte.categoria), text_color="white",
            fg_color=cor_da_categoria(corte.categoria), corner_radius=6, height=20,
            font=ctk.CTkFont(size=11, weight="bold"),
        )  # fmt: skip
        chip.grid(row=0, column=2, sticky="w", pady=(6, 0))
        selo = ctk.CTkLabel(
            linha, text="", text_color="white", corner_radius=6, height=20,
            font=ctk.CTkFont(size=11, weight="bold"),
        )  # fmt: skip
        selo.grid(row=1, column=2, sticky="w", pady=(3, 0))
        self._pintar_selo(selo, corte)  # só aparece se o Short foi postado ou agendado
        dica(
            selo,
            "Quando este Short foi postado (o dia e a hora em que você o marcou) ou para quando "
            "está agendado.",
        )
        info = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text_color=_COR_SUAVE,
            text=textos["info"],
        )
        info.grid(row=2, column=2, sticky="ew")
        titulo = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text=textos["titulo"],
            font=ctk.CTkFont(weight="bold"),
        )
        titulo.grid(row=3, column=2, sticky="ew")
        legenda = ctk.CTkLabel(
            linha,
            anchor="w",
            justify="left",
            wraplength=_QUEBRA_NA_LISTA,
            text_color=_COR_SUAVE,
            text=textos["legenda"],
        )
        legenda.grid(row=4, column=2, sticky="ew", pady=(0, 6))
        for widget in (linha, nota, chip, selo, info, titulo, legenda):
            widget.bind("<Button-1>", lambda _e, i=i: self.selecionar(i))
        self._rotulos_da_linha[i] = {
            "nota": nota, "chip": chip, "selo": selo, "info": info, "titulo": titulo,
            "legenda": legenda,
        }  # fmt: skip
        return linha

    def _ao_marcar(self, i: int) -> None:
        self._cortes[i].incluir = self._marcas[i].get()
        self._houve_mudanca()
        self._atualizar_resumo()
        self._atualizar_aviso_do_titulo()

    def _andar(self, passo: int) -> None:
        """Seleciona o corte seguinte (ou anterior) da lista, na ordem em que ela aparece."""
        if self._atual is None or not self._ordem:
            return
        posicao = self._posicao_na_lista(self._atual)
        if posicao is None:  # o filtro tirou o aberto da lista: segue da ponta de onde se vai
            posicao = -1 if passo > 0 else len(self._ordem)
        posicao += passo
        if 0 <= posicao < len(self._ordem):
            self.selecionar(self._ordem[posicao])
            self._rolar_ate_o_atual(posicao)

    def _rolar_ate_o_atual(self, posicao: int) -> None:
        with contextlib.suppress(TclError, AttributeError):
            canvas = self._lista._parent_canvas
            canvas.yview_moveto(max(0.0, (posicao - 2) / max(1, len(self._ordem))))

    def alternar_atual(self) -> None:
        """Marca ou desmarca o corte que está aberto."""
        if self._atual is not None:
            self._marcas[self._atual].set(not self._marcas[self._atual].get())
            self._ao_marcar(self._atual)

    def marcar_todos(self, valor: bool) -> None:
        """Marca ou desmarca os cortes que a lista mostra (todos, se não há filtro)."""
        for i in self._ordem:
            self._marcas[i].set(valor)
            self._cortes[i].incluir = valor
        self._houve_mudanca()
        self._atualizar_resumo()

    def _atualizar_resumo(self) -> None:
        r = resumir(self._cortes)
        texto = f"{r.marcados} de {r.total} marcados · {formatar_total(r.duracao_s)} de vídeo"
        pendentes = sum(
            1 for c in self._cortes if c.incluir and self.situacao(c) != SITUACAO_PRONTO
        )
        if self._registro:
            texto += f" · {pendentes} por renderizar"
        longos = sum(1 for c in self._cortes if c.incluir and passa_do_limite(c))
        if longos:
            texto += f" · ⚠ {longos} com mais de {DURACAO_MAXIMA_S:g} s"
        estados = [estado_de_publicacao(c) for c in self._cortes]
        postados, agendados = estados.count(_POSTADO), estados.count(_AGENDADO)
        if postados:
            texto += f" · ✔ {postados} já postado{'s' if postados > 1 else ''}"
        if agendados:
            texto += f" · 📅 {agendados} agendado{'s' if agendados > 1 else ''}"
        if self._seletor_filtro.get() != FILTRO_TODOS:
            texto += f" · mostrando {len(self._ordem)} de {len(self._cortes)}"
        self._resumo.configure(text=texto)

    def _pintar_atual(self) -> None:
        """Destaca a linha do corte aberto. Só redesenha as que mudam de cor (cada `configure` do
        CustomTkinter redesenha o widget inteiro, e a lista pode ter dezenas de linhas)."""
        for i, linha in self._linhas.items():
            cor = _COR_LINHA_ATUAL if i == self._atual else _COR_LINHA
            if self._cores_das_linhas.get(i) == cor:
                continue
            with contextlib.suppress(TclError):
                linha.configure(fg_color=cor)
            self._cores_das_linhas[i] = cor

    @staticmethod
    def _configurar(widget, **opcoes) -> None:
        """`configure` só quando algo muda: refazer o desenho de um rótulo igual custa tempo."""
        with contextlib.suppress(TclError, ValueError, KeyError):
            if all(widget.cget(nome) == valor for nome, valor in opcoes.items()):
                return
        widget.configure(**opcoes)

    # --- detalhe ------------------------------------------------------------------------------
    def selecionar(self, i: int) -> None:
        self._guardar_detalhe()
        self._atual = i
        self._mudanca = None
        self._pintar_atual()
        self._mostrar_detalhe()

    def _limpar_detalhe(self) -> None:
        self._carregando = True
        try:
            self._rotulo_nota.configure(text="Nenhum corte calculado ainda.")
            for rotulo in (self._rotulo_categoria, self._rotulo_alerta, self._rotulo_motivo):
                rotulo.configure(text="")
        finally:
            self._carregando = False

    def _mostrar_detalhe(self, *, so_pontas: bool = False) -> None:
        """Mostra o corte aberto. `so_pontas`: só o que depende de onde ele começa e termina (as
        pontas mexeram; a nota, o título e o resto continuam como estavam na tela)."""
        self._carregando = True
        try:
            self._preencher_detalhe(self._cortes[self._atual], so_pontas=so_pontas)
        finally:
            self._carregando = False

    def _preencher_detalhe(self, corte: Corte, *, so_pontas: bool = False) -> None:
        faixa = faixa_da_nota(corte.nota)
        self._configurar(
            self._rotulo_nota,
            text=f"Nota {formatar_nota(corte.nota)} de 100"
            + ("  (do corte original)" if corte.editado else ""),
            text_color=_COR_NOTA[faixa],
        )
        if so_pontas:
            self._mostrar_legenda(corte)
            self._mostrar_limites(corte)
            self._configurar(self._rotulo_ajuste, text="")
            self._mostrar_previa(corte)
            return
        self._configurar(
            self._chip_categoria,
            text=rotulo_da_categoria(corte.categoria),
            fg_color=cor_da_categoria(corte.categoria),
        )
        self._rotulo_categoria.configure(
            text=f"{rotulo_da_origem(corte)}"
            f"{' · reserva: só entra se você marcar' if corte.reserva else ''}",
            text_color=_COR_MANUAL if corte.origem == ORIGEM_MANUAL else _COR_SUAVE,
        )
        for chave, (barra, valor) in self._partes.items():
            maximo = PARTES[chave][1]
            pontos = corte.detalhe.get(chave, 0.0)
            barra.set(max(0.0, min(1.0, pontos / maximo)))
            valor.configure(text=f"{pontos:g} de {maximo:g}")
        penalidade = corte.detalhe.get("alerta_de_contexto")
        self._rotulo_alerta.configure(
            text=(
                f"⚠ {ROTULO_ALERTA} ({penalidade:g} pontos): {corte.alerta_contexto}"
                if corte.alerta_contexto
                else ""
            )
        )
        self._rotulo_motivo.configure(text=f"IA: {corte.motivo}" if corte.motivo else "")
        self._var_titulo.set(corte.titulo)
        self._mostrar_postado(corte)
        self._mostrar_agendamento(corte)
        self._campo_descricao.delete("1.0", "end")
        self._campo_descricao.insert("1.0", corte.descricao)
        self._var_hashtags.set(" ".join(corte.hashtags))
        self._mostrar_legenda(corte)
        self._mostrar_limites(corte)
        self._rotulo_ajuste.configure(text="")
        for situacao in self._situacao_da_ia.values():
            situacao.configure(text="")
        self._atualizar_botoes_da_ia()
        self._mostrar_previa(corte)

    def _mostrar_postado(self, corte: Corte, falhas: list[str] | None = None) -> None:
        """A caixa "Já postado" e o texto ao lado dela (e o que não deu para mover)."""
        self._var_postado.set(corte.postado)
        texto = texto_do_postado(
            corte,
            tem_arquivo=self._existem.get(str(corte.id), False),
            alterado=self.situacao(corte) == SITUACAO_ALTERADO,
            adiado=self._falta_mover(corte),
        )
        cor = _COR_OK if corte.postado else _COR_SUAVE
        if falhas:
            texto += (
                f"\n⚠ Não consegui mover: {'; '.join(falhas)}. Feche o arquivo e tente de novo."
            )
            cor = _COR_ALERTA
        self._configurar(self._rotulo_postado, text=texto, text_color=cor)

    def _atualizar_aviso_do_titulo(self) -> None:
        """Avisa sob o título quando ele repete, lembra demais o de outro Short marcado ou é
        genérico. Só vale para um Short marcado (um desmarcado não vai ser publicado)."""
        texto = ""
        if self._atual is not None and self._cortes and self._cortes[self._atual].incluir:
            outros = [
                c.titulo for i, c in enumerate(self._cortes)
                if i != self._atual and c.incluir and c.titulo.strip()
            ]  # fmt: skip
            texto = aviso_do_titulo(
                self._var_titulo.get(), outros, nome_do_sermao(self._pastas.raiz)
            )
        with contextlib.suppress(TclError):
            self._configurar(self._aviso_do_titulo, text=texto)

    def _ao_marcar_postado(self) -> None:
        """A pessoa marcou ou desmarcou "Já postado": grava só isso, na hora, e leva o
        arquivo já renderizado para `postados/` (ou o traz de volta). Se o corte ainda não está no
        arquivo (foi criado agora), a marca entra no próximo "Salvar"."""
        if self._atual is None or self._carregando or self._tela_desatualizada:
            return
        corte = self._cortes[self._atual]
        corte.postado = bool(self._var_postado.get())
        corte.postado_em = guardar_horario(self._relogio()) if corte.postado else ""
        if corte.postado:
            corte.agendado_para = ""  # já foi ao ar: não está mais agendado
        falhas: list[str] = []
        try:
            gravado = gravar_campos_do_corte(
                self._pastas.cortes_shorts, corte.id, postado=corte.postado,
                postado_em=corte.postado_em, agendado_para=corte.agendado_para,
            )  # fmt: skip
        except (OSError, ValueError, TypeError, KeyError):
            gravado = False
        if gravado:
            falhas = self._mover_arquivos([corte]).falhas
            self.recarregar_registro(reordenar=False)
        else:
            self._houve_mudanca()
        self._manter_na_lista(corte)
        self._atualizar_linha(self._atual)
        self._atualizar_resumo()
        self._mostrar_postado(corte, falhas)
        self._mostrar_agendamento(corte)

    # --- agendar e postar nas redes -------------------------------------------------------
    def _espelhar_titulo_na_lista(self) -> None:
        """O título que se digita já aparece na lista da esquerda (o corte só o recebe em
        `_guardar_detalhe`). Fora de uma edição de verdade (tela sendo preenchida) não faz nada."""
        if self._atual is None or self._carregando or self._tela_desatualizada:
            return
        rotulos = self._rotulos_da_linha.get(self._atual)
        if rotulos is None or not self._cortes:
            return
        texto = self._var_titulo.get().strip() or self._cortes[self._atual].texto[:60]
        self._configurar(rotulos["titulo"], text=texto)

    def _resultados_das_redes(self, corte: Corte) -> dict:
        """O que cada rede já fez com este Short (vazio se não há o que mostrar)."""
        if self._publicacao is None:
            return {}
        try:
            return registro_do_projeto(self._pastas).do_corte(corte.id)
        except (OSError, ValueError):
            return {}

    def _resumo_do_agendamento(self, corte: Corte, falhas: list[str] | None = None) -> str:
        """Sob a linha do agendamento: onde está o arquivo, o que cada rede fez e o que não deu
        para mover."""
        linhas = []
        if corte.agendado_para and self._existem.get(str(corte.id), False):
            linhas.append(
                _TEXTO_ADIADO.format(pasta=_PASTA_AGENDADOS)
                if self._falta_mover(corte)
                else f"O arquivo está em {_PASTA_AGENDADOS}."
            )
        redes = resumo_das_redes(self._resultados_das_redes(corte))
        if redes:
            linhas.append(redes)
        if falhas:
            linhas.append(
                f"⚠ Não consegui mover: {'; '.join(falhas)}. Feche o arquivo e tente de novo."
            )
        return "\n".join(linhas)

    def _mostrar_agendamento(self, corte: Corte) -> None:
        self._linha_agendamento.carregar(corte.agendado_para, self._resumo_do_agendamento(corte))

    def atualizar_redes(self) -> None:
        """O registro das redes mudou (a fila do Spurgeon publicou, por exemplo): refaz o resumo."""
        if self._atual is not None and self._cortes:
            self._mostrar_agendamento(self._cortes[self._atual])

    def _gravar_agendamento(self, corte: Corte, texto: str) -> None:
        """Grava só o horário agendado (na hora, como o “já postado”), leva o arquivo já
        renderizado para `agendados/` (ou o traz de volta, se o horário saiu) e refaz a lista."""
        corte.agendado_para = texto
        try:
            gravado = gravar_campos_do_corte(
                self._pastas.cortes_shorts, corte.id, agendado_para=texto
            )
        except (OSError, ValueError, TypeError, KeyError):
            gravado = False
        falhas: list[str] = []
        if gravado:
            falhas = self._mover_arquivos([corte]).falhas
            self.recarregar_registro(reordenar=False)
        else:
            self._houve_mudanca()  # o corte ainda não está no arquivo: entra no próximo "Salvar"
        self._manter_na_lista(corte)
        with contextlib.suppress(ValueError):
            self._atualizar_linha(self._cortes.index(corte))
        self._atualizar_resumo()
        if self._atual is not None and self._cortes[self._atual] is corte:
            self._linha_agendamento.resumo.configure(
                text=self._resumo_do_agendamento(corte, falhas)
            )  # só o texto: o que a pessoa está digitando nos campos não é reescrito

    def _ao_agendar(self, texto: str) -> None:
        """A pessoa marcou, mudou ou tirou o horário agendado do corte aberto."""
        if self._atual is None or self._carregando or self._tela_desatualizada:
            return
        self._gravar_agendamento(self._cortes[self._atual], texto)

    def _abrir_postagem(self) -> None:
        """Abre a janela que envia este Short às redes (agora ou no horário marcado)."""
        if self._atual is None or self._publicacao is None:
            return
        self._guardar_detalhe()
        if self._mudou and not self.salvar(exigir_ate_o_limite=False):
            return  # o que vai para as redes são os textos salvos
        corte = self._cortes[self._atual]
        if self.situacao(corte) == SITUACAO_ALTERADO:
            dialogos.aviso(
                self.winfo_toplevel(),
                "Postar nas redes",
                "O corte mudou depois de renderizado: o vídeo pronto não tem as suas edições. "
                "Renderize de novo antes de postar.",
            )
            return
        JanelaDePostagem(
            self.winfo_toplevel(),
            titulo_do_short=corte.titulo,
            pacote=pacote_do_corte(self._pastas, corte),
            plataformas=self._publicacao.plataformas(),
            registro=registro_do_projeto(self._pastas),
            id_do_corte=corte.id,
            quando=self._linha_agendamento.horario(),
            tarefas=self._publicacao.tarefas,
            relogio=self._relogio,
            ao_terminar=lambda r, q, id_=corte.id: self._terminou_a_postagem(id_, r, q),
            ao_assistir=lambda r: preparar_assistido(self.winfo_toplevel(), r),
        )

    def _terminou_a_postagem(self, id_do_corte: int, resultados, quando: datetime | None) -> None:
        """Depois de postar: agendado nas redes guarda o horário no Short; publicado agora o marca
        como já postado. Rascunhos e passos assistidos não mudam nada (a pessoa decide)."""
        corte = next((c for c in self._cortes if c.id == id_do_corte), None)
        if corte is None or self._publicacao is None:
            return
        for r in resultados:
            plataforma = self._publicacao.plataformas().get(r.plataforma)
            nome = plataforma.nome if plataforma is not None else r.plataforma
            self._publicacao.registrar(f"{nome}: “{corte.titulo}” {r.rotulo}")
        if quando is not None and any(r.estado in (AGENDADO, NA_FILA) for r in resultados):
            self._gravar_agendamento(corte, guardar_horario(quando))
        elif quando is None and any(r.estado == ENVIADO for r in resultados) and not corte.postado:
            self._marcar_como_postado(corte)
        if self._atual is not None and self._cortes[self._atual] is corte:
            self._mostrar_agendamento(corte)
            self._mostrar_postado(corte)

    def _marcar_como_postado(self, corte: Corte) -> None:
        """Marca o corte como já postado agora (como a caixa), sem mexer na tela aberta."""
        corte.postado, corte.agendado_para = True, ""
        corte.postado_em = guardar_horario(self._relogio())
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            gravar_campos_do_corte(
                self._pastas.cortes_shorts, corte.id, postado=True,
                postado_em=corte.postado_em, agendado_para="",
            )  # fmt: skip
        self._mover_arquivos([corte])
        self.recarregar_registro(reordenar=False)
        self._manter_na_lista(corte)
        with contextlib.suppress(ValueError):
            self._atualizar_linha(self._cortes.index(corte))
        self._atualizar_resumo()

    def _conferir_agendamentos(self) -> None:
        """Passou a hora de um Short agendado: ele vira “já postado” sozinho (e o arquivo vai
        para `postados/`). Roda ao abrir o projeto e depois a cada 30 s."""
        if self._agendamento_pendente is not None:
            with contextlib.suppress(TclError):
                self.after_cancel(self._agendamento_pendente)
            self._agendamento_pendente = None
        agora = self._relogio()
        venceram = [c for c in self._cortes if c.agendado_para and aplicar_vencimento(c, agora)]
        for corte in venceram:
            with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
                gravar_campos_do_corte(
                    self._pastas.cortes_shorts, corte.id, postado=True,
                    postado_em=corte.postado_em, agendado_para="",
                )  # fmt: skip
        if venceram:
            falhas = self._mover_arquivos(venceram).falhas
            self.recarregar_registro(reordenar=False)
            for i, corte in enumerate(self._cortes):
                if corte in venceram:
                    self._atualizar_linha(i)
            self._atualizar_resumo()
            if self._atual is not None:
                self._mostrar_postado(self._cortes[self._atual], falhas)
                self._mostrar_agendamento(self._cortes[self._atual])
        else:
            self._linha_agendamento.repintar()
        with contextlib.suppress(TclError):
            self._agendamento_pendente = self.after(
                _CONFERIR_AGENDAMENTO_MS, self._conferir_agendamentos
            )

    def _mostrar_legenda(self, corte: Corte) -> None:
        self._texto.delete("1.0", "end")
        self._texto.insert("1.0", self._legenda_para_editar(corte))
        self._texto._textbox.edit_reset()
        self._configurar(self._rotulo_editada, text="✎ legenda editada" if corte.legenda else "")
        self._mostrar_mudanca_na_legenda(corte)
        queimada = self._legenda_queimada()
        self._configurar(
            self._rotulo_queimada,
            text=(
                "A legenda é queimada no vídeo: o que você corrigir aqui aparece no Short."
                if queimada
                else "As “Legendas dinâmicas” estão desligadas nas opções: a correção fica "
                "guardada, mas só aparece no vídeo se você ligá-las."
            ),
        )
        if self._portugues is not None:
            self._portugues.limpar()

    def _mostrar_mudanca_na_legenda(self, corte: Corte) -> None:
        """Mostra na legenda o que o último ajuste acrescentou ou tirou, e o que fica de fora.

        As palavras novas ganham fundo verde, a caixa rola até o ponto que mudou (o começo ou o
        fim, que numa legenda de 3 minutos ficaria fora de vista) e, acima e abaixo, o texto que
        vem antes e depois do corte corre junto, para ver o que ainda há para trás e para a frente.
        """
        antes, depois = contexto_da_legenda(self._palavras, corte)
        if self._palavras:
            self._configurar(
                self._contexto_antes,
                text=f"◀ antes do começo (fica de fora): …{antes}"
                if antes
                else "◀ início do sermão",
            )
            self._configurar(
                self._contexto_depois,
                text=f"depois do fim (fica de fora) ▶: {depois}…" if depois else "fim do sermão ▶",
            )
        else:
            self._configurar(self._contexto_antes, text="")
            self._configurar(self._contexto_depois, text="")
        caixa = self._texto._textbox
        caixa.tag_remove("novo", "1.0", "end")
        mudanca = self._mudanca
        if mudanca is None or not mudanca.houve:
            self._configurar(self._rotulo_mudanca, text="")
            return
        linhas = []
        for rotulo, palavras in (
            ("➕ acrescentou no começo", mudanca.inicio_acrescentou),
            ("➖ tirou do começo", mudanca.inicio_tirou),
            ("➕ acrescentou no fim", mudanca.fim_acrescentou),
            ("➖ tirou do fim", mudanca.fim_tirou),
        ):
            if palavras:
                linhas.append(f"{rotulo}: “{resumir_palavras(palavras)}”")
        so_acrescentou = not (mudanca.inicio_tirou or mudanca.fim_tirou)
        self._rotulo_mudanca.configure(
            text="\n".join(linhas),
            text_color=_COR_OK if so_acrescentou else _COR_ALERTA,
        )
        texto = caixa.get("1.0", "end-1c")
        achadas = list(re.finditer(r"\S+", texto))
        novas = ((len(mudanca.inicio_acrescentou), False), (len(mudanca.fim_acrescentou), True))
        for quantas, do_fim in novas:
            alvo = achadas[-quantas:] if do_fim else achadas[:quantas]
            for achada in alvo if quantas else ():
                caixa.tag_add("novo", f"1.0+{achada.start()}c", f"1.0+{achada.end()}c")
        ponto = "end-1c" if mudanca.ponta == "fim" else "1.0"
        self.after_idle(lambda: self._rolar_legenda_ate(ponto))

    def _rolar_legenda_ate(self, ponto: str) -> None:
        with contextlib.suppress(TclError):
            self._texto._textbox.see(ponto)

    def _legenda_para_editar(self, corte: Corte) -> str:
        """A legenda dividida em blocos de até 2 linhas, como vai aparecer no vídeo."""
        if self._palavras and palavras_do_corte(self._palavras, corte):
            return legenda_em_blocos(self._palavras, corte)
        return corte.texto

    def _mostrar_limites(self, corte: Corte) -> None:
        acima = passa_do_limite(corte)
        self._configurar(
            self._rotulo_tempo,
            text=(
                f"{formatar_tempo(corte.inicio)} a {formatar_tempo(corte.fim)} · "
                f"{corte.duracao:.1f} s"
            ),
            text_color=_COR_ERRO if acima else ("gray10", "gray90"),
        )
        self._configurar(
            self._rotulo_limite,
            text=(
                f"⚠ {AVISO_ACIMA_DO_LIMITE} Encurte o corte (ou desmarque-o) antes de "
                f"renderizar; o limite é {DURACAO_MAXIMA_S:g} s."
                if acima
                else ""
            ),
        )
        self._var_inicio.set(formatar_tempo_preciso(corte.inicio))
        self._var_fim.set(formatar_tempo_preciso(corte.fim))
        self._preencher_janela(corte, ver=self._mudanca.ponta if self._mudanca else None)
        avisos: tuple[str, ...] = ()
        with contextlib.suppress(LimiteInvalido):
            avisos = contexto_das_pontas(self._palavras, corte, self._frases_do_sermao()).avisos
        if abaixo_do_minimo(corte):
            avisos = (
                f"O Short ficou com {corte.duracao:.0f} s: menos de {DURACAO_MINIMA_S:g} s é curto "
                "demais para publicar.",
                *avisos,
            )
        self._configurar(self._rotulo_avisos, text="\n".join(f"⚠ {a}" for a in avisos))

    def _preencher_janela(
        self, corte: Corte, *, texto: str | None = None, ver: str | None = None
    ) -> None:
        """A tabela de frases ao redor do corte: o que fica fora em cinza, o corte em destaque.

        `texto` é a legenda como está na caixa agora (vazio = a guardada no corte). Quem muda as
        pontas passa `ver` (a ponta que mudou): a vista não se mexe, só garante que ela apareça.
        Sem `ver` e sem mudança de pontas, a vista vai para o começo do corte.
        """
        try:
            linhas = linhas_da_tabela(
                self._palavras,
                corte,
                self._frases_do_sermao(),
                fora=_PALAVRAS_FORA,
                texto_dentro=texto,
            )
        except LimiteInvalido:
            self._janela.mostrar_aviso("(transcrição indisponível)")
            return
        margens = self._margens_da_tabela(linhas, corte)
        self._janela.mostrar(
            linhas,
            centralizar=None if ver or texto is not None else "inicio",
            margens=None if margens is None else margens[0],
        )
        self._atualizar_a_legenda_da_tabela(margens)
        if ver:
            self._janela.ir_a(ver, so_se_preciso=True)

    # --- a margem da tabela: categorias detectadas e Shorts que já existem ---------------------
    def _mapa_das_frases(self) -> tuple[list[int], list[float]]:
        """(a posição da frase de cada palavra, o instante em que cada frase começa)."""
        if self._mapa_de_frases is None:
            da_palavra = [0] * len(self._palavras)
            for numero, frase in enumerate(self._frases_do_sermao()):
                for i in range(frase.primeira, frase.ultima + 1):
                    da_palavra[i] = numero
            self._mapa_de_frases = (da_palavra, [f.inicio for f in self._frases_do_sermao()])
        return self._mapa_de_frases

    def _deteccoes_por_frase(self) -> dict[int, Detectado]:
        """Para cada frase, o trecho avaliado pela IA de maior nota que a cobre."""
        if self._deteccoes is None:
            detectados = trechos_detectados(self._pastas, self._palavras)
            self._deteccoes = categoria_de_cada_frase(detectados, self._frases_do_sermao())
        return self._deteccoes

    def _indices_do_corte(self, corte: Corte) -> frozenset[int]:
        """As palavras que um corte fala, guardadas até o corte mudar (são muitos cortes)."""
        assinatura = (corte.inicio, corte.fim, tuple(tuple(s) for s in corte.saltos))
        guardado = self._indices_dos_cortes.get(corte.id)
        if guardado is not None and guardado[0] == assinatura:
            return guardado[1]
        if self._tempos_das_palavras is None:
            self._tempos_das_palavras = (
                [p.inicio for p in self._palavras],
                [p.fim for p in self._palavras],
            )
        inicios, fins = self._tempos_das_palavras
        de = max(0, bisect_left(fins, corte.inicio) - 20)  # folga: os tempos podem se sobrepor
        ate = min(len(self._palavras), bisect_right(inicios, corte.fim) + 20)
        indices = frozenset(de + i for i in indices_incluidos(self._palavras[de:ate], corte))
        self._indices_dos_cortes[corte.id] = (assinatura, indices)
        return indices

    def _margens_da_tabela(
        self, linhas, corte: Corte
    ) -> tuple[list[MargemDaLinha | None], int] | None:
        """A margem de cada linha da tabela e quantos outros Shorts aparecem (None sem texto)."""
        frases = self._frases_do_sermao()
        if not frases:
            return None
        da_palavra, inicios = self._mapa_das_frases()
        detectadas = self._deteccoes_por_frase()
        outros: dict[int, tuple[int, str]] = {}  # palavra → (id, categoria) do primeiro Short dela
        for outro in self._cortes:
            if outro.id != corte.id:
                for i in self._indices_do_corte(outro):
                    outros.setdefault(i, (outro.id, outro.categoria))
        margens: list[MargemDaLinha | None] = []
        presentes: set[int] = set()
        for linha in linhas:
            if linha.e_marcador:
                margens.append(None)
                continue
            conhecida = next((i for i in linha.indices if i >= 0), None)
            numero = (
                da_palavra[conhecida]
                if conhecida is not None
                else max(0, bisect_right(inicios, linha.tempo + 0.05) - 1)
            )
            detectado = detectadas.get(numero)
            frase = frases[numero]
            da_frase = [outros[i] for i in range(frase.primeira, frase.ultima + 1) if i in outros]
            presentes.update(id_ for id_, _categoria in da_frase)
            margens.append(
                MargemDaLinha(
                    detectado.categoria if detectado else None,
                    da_frase[0][1] if da_frase else None,
                    {i: outros[i][1] for i in linha.indices if i in outros},
                )
            )
        return margens, len(presentes)

    def _atualizar_a_legenda_da_tabela(
        self, margens: tuple[list[MargemDaLinha | None], int] | None
    ) -> None:
        """A legenda de cores acima da tabela: o que a IA viu e o que já virou Short, nas frases
        que a tabela mostra. Os chips só são refeitos quando mudam (refazê-los piscaria)."""
        por_categoria: dict[str, list[int]] = {}
        linhas_dos_feitos: list[int] = []
        quantos = 0
        if margens is not None:
            lista, quantos = margens
            for numero, margem in enumerate(lista, start=1):
                if margem is None:
                    continue
                if margem.categoria:
                    por_categoria.setdefault(margem.categoria, []).append(numero)
                if margem.feito:
                    linhas_dos_feitos.append(numero)
        itens, self._grupos_da_tabela = itens_da_legenda(por_categoria, linhas_dos_feitos, quantos)
        self._salto_da_tabela = {}
        if tuple(itens) != self._itens_da_legenda_da_tabela:
            self._itens_da_legenda_da_tabela = tuple(itens)
            self._legenda_da_tabela.definir(itens)
            if itens:
                self._legenda_da_tabela.grid()
            else:
                self._legenda_da_tabela.grid_remove()

    def _ir_para_na_tabela(self, chave: str) -> int | None:
        """Leva a tabela ao próximo trecho da categoria (ou dos outros Shorts); devolve a linha."""
        linhas = self._grupos_da_tabela.get(chave)
        if not linhas:
            return None
        posicao = (self._salto_da_tabela.get(chave, -1) + 1) % len(linhas)
        self._salto_da_tabela[chave] = posicao
        self._janela.ir_para_linha(linhas[posicao])
        return linhas[posicao]

    def _ir_a_ponta(self, ponta: str) -> None:
        self._janela.ir_a(ponta)

    def _levar_ponta_a(self, indice: int, ponta: str | None = None) -> None:
        """Leva o começo ou o fim do corte à palavra `indice` (sem `ponta`, à que estiver mais
        perto: foi o duplo clique numa palavra)."""
        if self._atual is None:
            return
        corte = self._cortes[self._atual]
        if ponta is None:
            try:
                primeira, ultima = indices_das_palavras(self._palavras, corte)
            except LimiteInvalido:
                return
            ponta = "inicio" if indice - primeira <= ultima - indice else "fim"
        palavra = self._palavras[indice]
        segundos = palavra.inicio if ponta == "inicio" else palavra.fim
        if self._ajustar(lambda c: definir_limite(self._palavras, c, ponta, segundos)):
            self._janela.selecionar_palavra(indice)  # o duplo clique também seleciona a palavra

    # --- ajustes do início e do fim -----------------------------------------------------------
    def mover(self, ponta: str, passo: int) -> bool:
        """Desloca o início ou o fim do corte atual `passo` palavras. False se não foi possível."""
        return self._ajustar(lambda c: mover_limite(self._palavras, c, ponta, passo))

    def mover_frase(self, ponta: str, sentido: int) -> bool:
        """Leva o início ou o fim à fronteira de frase anterior (−1) ou seguinte (+1)."""
        return self._ajustar(
            lambda c: mover_por_frase(self._palavras, c, ponta, sentido, self._frases_do_sermao())
        )

    def aplicar_tempo(self, ponta: str) -> bool:
        """Leva o início ou o fim ao limite de palavra mais perto do tempo digitado."""
        if self._atual is None or self._carregando:
            return False
        self._guardar_detalhe()  # o que se digitou na tabela não pode se perder no redesenho
        texto = (self._var_inicio if ponta == "inicio" else self._var_fim).get()
        corte = self._cortes[self._atual]
        atual = corte.inicio if ponta == "inicio" else corte.fim
        try:
            segundos = ler_tempo(texto)
        except ValueError as erro:
            self._rotulo_ajuste.configure(text=str(erro))
            self._mostrar_limites(corte)
            return False
        if abs(segundos - atual) < 0.05:
            self._mostrar_limites(corte)  # nada mudou: só arruma a formatação do campo
            return True
        return self._ajustar(lambda c: definir_limite(self._palavras, c, ponta, segundos))

    def _ajustar(self, operacao: Callable[[Corte], Corte]) -> bool:
        """Aplica a mudança de início, fim ou palavras ao corte aberto, mexendo só no que mudou: o
        detalhe e a linha da lista se atualizam no lugar (a lista não é refeita), e a legenda
        corrigida continua valendo para as palavras que ficam."""
        if self._atual is None:
            return False
        self._guardar_detalhe()
        antes = self._cortes[self._atual]
        try:
            novo = operacao(antes)
        except LimiteInvalido as erro:
            self._rotulo_ajuste.configure(text=str(erro))
            self._mostrar_limites(antes)
            return False
        novo = replace(novo, legenda=legenda_preservada(self._palavras, antes, novo))
        self._registrar_no_historico(antes)
        self._depois_de_mudar(antes, novo, avisar_da_legenda=True)
        return True

    def _depois_de_mudar(self, antes: Corte, novo: Corte, *, avisar_da_legenda: bool) -> None:
        """Põe `novo` no lugar de `antes` e atualiza a tela (detalhe, linha da lista e resumo).

        A ordem importa: quem é avisado da mudança (a aba, que pede `pendentes()`) acaba chamando
        `_guardar_detalhe`, que copia a caixa da legenda para o corte. Se isso acontecesse antes de
        a caixa mostrar o corte novo, a legenda de antes cobriria a do corte novo e as palavras
        que entraram sumiriam do Short e da tabela. Por isso o detalhe é refeito primeiro e, até
        lá, `_guardar_detalhe` não copia nada.
        """
        self._cortes[self._atual] = novo
        self._mudanca = comparar_cortes(self._palavras, antes, novo)
        self._tela_desatualizada = True
        try:
            self._mostrar_detalhe(so_pontas=True)
        finally:
            self._tela_desatualizada = False
        self._houve_mudanca()
        if avisar_da_legenda and antes.legenda and not novo.legenda:
            self._rotulo_ajuste.configure(
                text="As correções da legenda eram de palavras que saíram do corte e foram "
                "descartadas. Corrija a legenda de novo, se precisar."
            )
        self._atualizar_linha(self._atual)
        self._reposicionar()
        self._pintar_atual()
        self._atualizar_resumo()

    # --- marca-texto e borracha -----------------------------------------------------------------
    def _trocou_a_ferramenta(self, nome: str) -> None:
        self._janela.definir_ferramenta(nome)
        if nome != FERRAMENTA_SETA:
            self.after_idle(self._trazer_a_tabela_para_a_vista)

    def _trazer_a_tabela_para_a_vista(self) -> None:
        """Rola o detalhe (a coluna da direita) até a tabela caber na tela: pintar e apagar exigem
        ver o texto inteiro, e a tabela costuma ficar com a metade de baixo escondida."""
        try:
            tela = self._detalhe._parent_canvas
            topo = self._janela.winfo_rooty() - tela.winfo_rooty()
            base = topo + self._janela.winfo_height()
            altura = tela.winfo_height()
            if topo >= 0 and base <= altura:
                return
            total = tela.bbox("all")[3]
            if total <= altura:
                return
            avanco = topo - 8 if topo < 0 else min(base - altura + 8, topo - 8)
            tela.yview_moveto(max(0.0, (tela.yview()[0] * total + avanco) / total))
        except (TclError, TypeError, ZeroDivisionError):
            return

    def _pintou(self, indices: list[int], ferramenta: str) -> None:
        """A tabela entrega as palavras que o marca-texto pintou ou a borracha apagou."""
        if self._atual is None or not indices:
            return
        if ferramenta == FERRAMENTA_MARCA:
            self._ajustar(lambda c: incluir_palavras(self._palavras, c, indices))
        elif ferramenta == FERRAMENTA_BORRACHA:
            self._ajustar(lambda c: tirar_palavras(self._palavras, c, indices))

    # --- desfazer e refazer ---------------------------------------------------------------------
    @staticmethod
    def _foto(corte: Corte) -> dict:
        return {nome: copy.deepcopy(getattr(corte, nome)) for nome in _CAMPOS_DO_HISTORICO}

    def _registrar_no_historico(self, corte: Corte) -> None:
        """Guarda o corte como está, antes de uma mudança, para poder desfazê-la."""
        historico = self._historico.setdefault(corte.id, _Historico())
        foto = self._foto(corte)
        if historico.passado and historico.passado[-1] == foto:
            return
        historico.passado.append(foto)
        del historico.passado[:-_LIMITE_DO_HISTORICO]
        historico.futuro.clear()
        self._atualizar_botoes_do_historico()

    @property
    def pode_desfazer(self) -> bool:
        historico = self._historico_do_aberto()
        return bool(historico and historico.passado)

    @property
    def pode_refazer(self) -> bool:
        historico = self._historico_do_aberto()
        return bool(historico and historico.futuro)

    def _historico_do_aberto(self) -> _Historico | None:
        if self._atual is None or not self._cortes:
            return None
        return self._historico.get(self._cortes[self._atual].id)

    def desfazer(self) -> bool:
        """Volta o corte aberto ao que era antes da última mudança (pontas, palavras cortadas,
        pausas ou legenda). False se não há o que desfazer."""
        return self._andar_no_historico(-1)

    def refazer(self) -> bool:
        """Refaz o que foi desfeito. False se não há o que refazer."""
        return self._andar_no_historico(1)

    def _andar_no_historico(self, sentido: int) -> bool:
        if self._atual is None or self._bloqueado:
            return False
        self._guardar_detalhe()  # o que está digitado entra na história antes de voltar nela
        antes = self._cortes[self._atual]
        historico = self._historico.get(antes.id)
        if historico is None:
            return False
        origem, destino = (
            (historico.passado, historico.futuro)
            if sentido < 0
            else (historico.futuro, historico.passado)
        )
        if not origem:
            return False
        destino.append(self._foto(antes))
        novo = replace(antes, **copy.deepcopy(origem.pop()))
        self._depois_de_mudar(antes, novo, avisar_da_legenda=False)
        self._atualizar_botoes_do_historico()
        return True

    def _atualizar_botoes_do_historico(self) -> None:
        estados = {
            "desfazer": "normal" if self.pode_desfazer and not self._bloqueado else "disabled",
            "refazer": "normal" if self.pode_refazer and not self._bloqueado else "disabled",
        }
        for nome, botoes in self._botoes_do_historico.items():
            for botao in botoes:
                self._configurar(botao, state=estados[nome])

    def _ligar_atalhos_do_historico(self) -> None:
        """Ctrl+Z e Ctrl+Y (os eventos `<<Undo>>` e `<<Redo>>`) agem no corte, não no texto: as
        caixas de texto têm o desfazer delas desligado, para só haver uma história."""
        janela = self.winfo_toplevel()
        for evento, comando in (
            ("<<Undo>>", self._atalho_desfazer),
            ("<<Redo>>", self._atalho_refazer),
        ):
            self._atalhos_do_historico.append((evento, janela.bind(evento, comando, add="+")))
        for caixa in (self._texto._textbox, self._janela._textbox):
            caixa.bind("<<Undo>>", lambda _e: self._atalho_desfazer(forcar=True))
            caixa.bind("<<Redo>>", lambda _e: self._atalho_refazer(forcar=True))

    def _atalho_vale(self) -> bool:
        """O atalho vale se a pessoa está neste editor (ou em nenhum campo): num campo de uma linha
        só (título, hashtags, tempos) o Ctrl+Z não mexe na história do corte."""
        try:
            foco = self.focus_get()
        except (KeyError, TclError):
            return True
        if foco is None or foco is self.winfo_toplevel():
            return True
        return str(foco).startswith(str(self)) and not str(foco.winfo_class()).endswith("Entry")

    def _atalho_desfazer(self, _evento=None, *, forcar: bool = False) -> str | None:
        if forcar or self._atalho_vale():
            self.desfazer()
            return "break"
        return None

    def _atalho_refazer(self, _evento=None, *, forcar: bool = False) -> str | None:
        if forcar or self._atalho_vale():
            self.refazer()
            return "break"
        return None

    def _reordenar_mantendo(self) -> None:
        atual = self._atual
        self._reordenar()
        self._atual = atual
        self._pintar_atual()

    # --- legenda ------------------------------------------------------------------------------
    # Ela se edita em dois lugares, que se acompanham: a caixa "Legenda do Short" (blocos de duas
    # linhas, com o verificador de português) e a tabela de frases. Quem digita numa delas vê a
    # outra se atualizar depois de uma pausa; a caixa é sempre a que `_guardar_detalhe` lê.
    def _ao_editar_legenda(self) -> None:
        """Mexeram na caixa da legenda: marca a mudança e leva o texto à tabela."""
        if self._carregando or self._atual is None:
            return
        self._marcar_mudanca()
        self._atualizar_rotulo_da_legenda(self._cortes[self._atual])
        if self._acompanhar_a_tabela is not None:
            self.after_cancel(self._acompanhar_a_tabela)
        self._acompanhar_a_tabela = self.after(
            _ATRASO_DA_SINCRONIA_MS, self._levar_a_legenda_para_a_tabela
        )

    def _atualizar_rotulo_da_legenda(self, corte: Corte) -> None:
        editada = bool(self._legenda_digitada(corte))
        self._rotulo_editada.configure(text="✎ legenda editada" if editada else "")

    def _levar_a_legenda_para_a_tabela(self) -> None:
        self._acompanhar_a_tabela = None
        if self._atual is None or self._tabela_pendente:
            return  # a tabela tem edição ainda não passada para a caixa: ela é que vale
        corte = self._cortes[self._atual]
        self._confirmar_legenda(corte)
        with contextlib.suppress(TclError):
            self._preencher_janela(corte, texto=self._texto.get("1.0", "end-1c"))

    def _confirmar_legenda(self, corte: Corte) -> None:
        """Passa a legenda da caixa para o corte, guardando a anterior na história do desfazer."""
        nova = self._legenda_digitada(corte)
        if nova != corte.legenda:
            self._registrar_no_historico(corte)
            corte.legenda = nova

    def _ao_editar_tabela(self) -> None:
        """Mexeram no texto do corte na tabela: marca a mudança e leva o texto à caixa."""
        if self._carregando or self._atual is None:
            return
        self._tabela_pendente = True
        self._marcar_mudanca()
        if self._sincronizacao is not None:
            self.after_cancel(self._sincronizacao)
        self._sincronizacao = self.after(
            _ATRASO_DA_SINCRONIA_MS, self._levar_a_tabela_para_a_legenda
        )

    def _levar_a_tabela_para_a_legenda(self) -> None:
        """Põe na caixa da legenda o que está na tabela (nada se perde: é o que será gravado)."""
        if self._sincronizacao is not None:
            with contextlib.suppress(TclError):
                self.after_cancel(self._sincronizacao)
            self._sincronizacao = None
        if not self._tabela_pendente:
            return
        self._tabela_pendente = False
        if self._atual is None:
            return
        corte = self._cortes[self._atual]
        texto = self._janela.texto_dentro()
        novo = (
            legenda_em_blocos(self._palavras, corte, texto)
            if self._palavras and palavras_do_corte(self._palavras, corte)
            else texto
        )
        if novo != self._texto.get("1.0", "end-1c"):
            self._texto.delete("1.0", "end")
            self._texto.insert("1.0", novo)
            self._texto._textbox.edit_reset()
            if self._portugues is not None:
                self._portugues.limpar()  # as marcas eram do texto de antes
        self._confirmar_legenda(corte)
        self._atualizar_rotulo_da_legenda(corte)

    def _legenda_digitada(self, corte: Corte) -> str:
        """O que vai para `Corte.legenda`: vazio se o texto é o mesmo da transcrição."""
        texto = self._texto.get("1.0", "end-1c")
        if not self._palavras:
            return "" if " ".join(texto.split()) == " ".join(corte.texto.split()) else texto.strip()
        return legenda_corrigida(self._palavras, corte, texto)

    def texto_da_legenda_exportada(self) -> tuple[str, str] | None:
        """O nome sugerido e o texto do arquivo da legenda do Short aberto (None se não há)."""
        if self._atual is None or not self._palavras:
            return None
        self._guardar_detalhe()  # o que está na caixa agora, mesmo sem salvar
        corte = self._cortes[self._atual]
        numero = next(
            (p.numero for p in planejar_shorts(self._cortes) if p.corte.id == corte.id), None
        )
        legenda = legenda_no_tempo_do_short(self._palavras, corte)
        texto = texto_da_legenda(
            corte, legenda, sermao=nome_do_sermao(self._pastas.raiz), numero=numero
        )
        return nome_do_arquivo(corte, numero), texto

    def _exportar_legenda(self) -> None:
        """Pergunta onde salvar e grava a legenda do Short aberto num arquivo de texto."""
        pronto = self.texto_da_legenda_exportada()
        if pronto is None:
            return
        nome, texto = pronto
        pasta = self._pastas.shorts if self._pastas.shorts.is_dir() else self._pastas.raiz
        caminho = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="Exportar a legenda", initialdir=str(pasta),
            initialfile=nome, defaultextension=".txt", filetypes=[("Texto", "*.txt")],
        )  # fmt: skip
        if not caminho:
            return
        try:
            Path(caminho).write_bytes(texto.encode("utf-8"))
        except OSError as erro:
            dialogos.erro(
                self.winfo_toplevel(), "Exportar a legenda", f"Não deu para salvar: {erro}"
            )
            return
        self._rotulo_ajuste.configure(text=f"Legenda exportada: {Path(caminho).name}")

    def _reorganizar_legenda(self) -> None:
        """Refaz as quebras de linha como o vídeo vai mostrar, sem mexer nas palavras.

        Só os espaços entre as palavras são trocados (por Enter ou por espaço), então as marcas do
        verificador de português continuam nas palavras certas.
        """
        if self._atual is None or self._carregando or not self._palavras:
            return
        corte = self._cortes[self._atual]
        if not palavras_do_corte(self._palavras, corte):
            return
        atual = self._texto.get("1.0", "end-1c")
        desejado = legenda_em_blocos(self._palavras, corte, atual)
        if desejado == atual or atual.split() != desejado.split():
            return
        palavras_atuais = list(re.finditer(r"\S+", atual))
        separadores = [m.group() for m in re.finditer(r"\s+", desejado)]
        caixa = self._texto._textbox
        for k in range(len(palavras_atuais) - 2, -1, -1):  # de trás para frente: as posições valem
            de, ate = palavras_atuais[k].end(), palavras_atuais[k + 1].start()
            if atual[de:ate] != separadores[k]:
                caixa.delete(f"1.0+{de}c", f"1.0+{ate}c")
                caixa.insert(f"1.0+{de}c", separadores[k])

    def desfazer_legenda(self) -> None:
        """Volta a legenda do corte atual para o texto da transcrição."""
        if self._atual is None:
            return
        corte = self._cortes[self._atual]
        if corte.legenda:
            self._registrar_no_historico(corte)
        corte.legenda = ""
        self._mudanca = None
        self._tabela_pendente = False
        self._carregando = True
        try:
            self._mostrar_legenda(corte)
            self._preencher_janela(corte, texto="")
        finally:
            self._carregando = False
        self._houve_mudanca()

    def _alternar_portugues(self) -> None:
        if self._portugues is None:
            pastas_app = self._pastas_do_app or Pastas()
            self._portugues = PainelDePortugues(
                self._detalhe,
                self._texto._textbox,
                lambda: verificador_compartilhado(pastas_app),
                vocabulario=pastas_app.vocabulario,
                ao_corrigir=self._ao_corrigir_portugues,
                largura=400,
                compacto=True,
            )
        if self._portugues_visivel:
            self._portugues.grid_remove()
            self._portugues_visivel = False
            return
        self._portugues.grid(row=28, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._portugues_visivel = True
        self._portugues.verificar()

    def abrir_dicionario(self) -> DicionarioPersonalizado:
        """Abre (ou traz para a frente) o dicionário personalizado (os termos guardados)."""
        if self._dicionario is not None and self._dicionario.winfo_exists():
            self._dicionario.lift()
            return self._dicionario
        pastas_app = self._pastas_do_app or Pastas()
        self._dicionario = DicionarioPersonalizado(
            self.winfo_toplevel(),
            pastas_app.vocabulario,
            ao_mudar=lambda: atualizar_vocabulario_compartilhado(pastas_app),
        )
        return self._dicionario

    def _ao_corrigir_portugues(self) -> None:
        self._ao_editar_legenda()
        self.after_idle(self._reorganizar_legenda)  # corrigir pode mudar onde a linha quebra

    # --- prévia -------------------------------------------------------------------------------
    def _assinatura_da_previa(self, corte: Corte, origem: OrigemDaPrevia) -> str:
        """O que decide se a prévia guardada ainda mostra este corte com esta aparência."""
        o = origem.opcoes
        dados = [
            str(origem.video),
            round(corte.inicio, 3),
            round(corte.fim, 3),
            corte.legenda,
            o.legenda_posicao_vertical,
            o.recorte_deslocamento_x_pct,
            o.enquadramento_nos_curtos.model_dump(),
            o.enquadramento_do_short.model_dump(),
            o.tratamento_nos_curtos.model_dump(),
            [list(salto) for salto in corte.saltos],
            corte.pausa_s,
        ]
        return hashlib.sha1(json.dumps(dados, sort_keys=True, default=str).encode()).hexdigest()[
            :12
        ]

    def _origem(self) -> OrigemDaPrevia | None:
        return self._origem_da_previa() if self._origem_da_previa is not None else None

    # --- ajustes que se acertam olhando a prévia ----------------------------------------------
    def _atualizar_ajustes_do_short(self, corte: Corte | None = None) -> None:
        """Mostra a altura da legenda (de todos os Shorts) e o corte das pausas (só deste Short)."""
        if corte is None and self._atual is not None:
            corte = self._cortes[self._atual]
        origem = self._origem()
        pode_mexer_na_legenda = origem is not None and self._definir_ajustes is not None
        for botao in self._botoes_da_legenda:
            self._configurar(botao, state="normal" if pode_mexer_na_legenda else "disabled")
        self._configurar(
            self._rotulo_legenda_altura,
            text=""
            if origem is None
            else f"a {origem.opcoes.legenda_posicao_vertical * 100:.0f}% da altura, de cima",
        )
        ligada = bool(corte is not None and corte.pausa_s)
        if self._var_cortar_pausas.get() != ligada:
            self._var_cortar_pausas.set(ligada)
        try:
            digitando = self.focus_get() is self._campo_pausa._entry
        except (KeyError, TclError):
            digitando = False
        if not digitando:  # não atrapalha quem está digitando
            mostrado = (
                corte.pausa_s if corte is not None and corte.pausa_s else self._pausa_lembrada
            )
            self._var_pausa.set(_texto_de_segundos(mostrado))
        estado = "normal" if corte is not None else "disabled"
        for widget in (self._caixa_pausas, self._campo_pausa, self._botao_pausa_em_todos):
            self._configurar(widget, state=estado)
        self._configurar(self._rotulo_pausas, text=self._texto_das_pausas(corte))

    def _texto_das_pausas(self, corte: Corte | None) -> str:
        if corte is None or not corte.pausa_s:
            return "Desligado: este Short mantém todas as pausas da fala."
        limiar = _texto_de_segundos(corte.pausa_s)
        sobra = _texto_de_segundos(residual_da_pausa(corte.pausa_s))
        regra = f"Pausas de {limiar} s ou mais ficam com {sobra} s. "
        if not self._palavras:
            return regra.rstrip()
        sem = duracao_do_short(self._palavras, corte)
        if sem >= corte.duracao - 0.05:
            return regra + "Este Short não tem pausa desse tamanho: nada é cortado."
        return (
            regra + f"O Short passa de {corte.duracao:.0f} s para {sem:.0f} s "
            f"({corte.duracao - sem:.0f} s a menos)."
        )

    def mover_legenda(self, sentido: int) -> bool:
        """Sobe (−1) ou desce (+1) a legenda dos Shorts. Vale para todos; a prévia guardada deixa de
        valer até ser gerada de novo."""
        origem = self._origem()
        if origem is None or self._definir_ajustes is None:
            return False
        atual = origem.opcoes.legenda_posicao_vertical
        nova = round(
            min(max(atual + sentido * _PASSO_DA_LEGENDA, _LEGENDA_MINIMA), _LEGENDA_MAXIMA), 2
        )
        if nova == round(atual, 2):
            return False
        self._definir_ajustes(legenda=nova)
        self._atualizar_previa_atual()
        return True

    def _mudou_o_corte_de_pausas(self) -> None:
        """Liga ou desliga o corte das pausas deste Short, ou muda o tamanho a partir do qual elas
        se cortam (de 0,1 a 10 s)."""
        if self._atual is None or self._carregando:
            return
        corte = self._cortes[self._atual]
        ligado = bool(self._var_cortar_pausas.get())
        try:
            segundos = ler_segundos(self._var_pausa.get())
        except ValueError:
            segundos = self._pausa_lembrada  # texto que não é número: volta ao que valia
        segundos = max(
            round(min(max(segundos, _PAUSA_MINIMA_S), _PAUSA_MAXIMA_S), 1), _PAUSA_MINIMA_S
        )
        self._pausa_lembrada = segundos
        self._var_pausa.set(_texto_de_segundos(segundos))
        nova = segundos if ligado else 0.0
        if abs(nova - corte.pausa_s) < 1e-9:
            self._atualizar_ajustes_do_short(corte)
            return
        self._registrar_no_historico(corte)
        corte.pausa_s = nova
        self._houve_mudanca()
        self._atualizar_linha(self._atual)
        self._atualizar_resumo()
        self._atualizar_previa_atual()

    def aplicar_a_pausa_em_todos(self) -> int:
        """Copia o corte de pausas deste Short para todos os outros da lista. Devolve quantos
        mudaram (0 se a pessoa desistiu ou já eram todos iguais)."""
        if self._atual is None:
            return 0
        self._guardar_detalhe()
        corte = self._cortes[self._atual]
        outros = [c for c in self._cortes if c is not corte and c.pausa_s != corte.pausa_s]
        if not outros:
            return 0
        o_que = (
            f"cortar as pausas de {_texto_de_segundos(corte.pausa_s)} s ou mais"
            if corte.pausa_s
            else "deixar as pausas como estão (sem cortar)"
        )
        if not dialogos.confirmar(
            self.winfo_toplevel(),
            "Aplicar a todos os Shorts",
            f"Fazer isto em {len(outros)} outro(s) Short(s): {o_que}?",
            sim="Aplicar",
            nao="Cancelar",
        ):
            return 0
        for outro in outros:
            outro.pausa_s = corte.pausa_s
        self._houve_mudanca()
        for i in range(len(self._cortes)):
            self._atualizar_linha(i)
        self._atualizar_resumo()
        return len(outros)

    def _mostrar_previa(self, corte: Corte) -> None:
        """Põe no detalhe a prévia deste corte (se já foi gerada e ainda vale) ou o botão."""
        self._atualizar_ajustes_do_short(corte)
        self._atualizar_botoes_do_historico()
        id_ = str(corte.id)
        if self._gerando is not None and self._gerando != id_:
            self._cancelar_a_previa()  # outro corte foi aberto: a prévia que corria não interessa
        gerando = self._gerando == id_
        guardada = self._previas.get(id_)
        origem = self._origem() if guardada and not gerando else None
        vale = bool(
            guardada and origem and self._assinatura_da_previa(corte, origem) == guardada[0]
        )
        if vale:
            if self._player.previa is not guardada[1]:
                self._player.carregar(guardada[1])
            self._player.grid()
        else:
            self._player.limpar()
            self._player.grid_remove()
        poder = self._tarefas is not None and self._origem_da_previa is not None and not gerando
        self._configurar(
            self._botao_previa,
            state="normal" if poder else "disabled",
            text="Gerando…" if gerando else ("Gerar de novo" if guardada else "Gerar prévia"),
        )
        if gerando:
            return
        if self._tarefas is None or self._origem_da_previa is None:
            texto = "A prévia fica disponível na aba Studio Shorts."
        elif guardada and not vale:
            texto = (
                "⟳ A prévia é de uma versão anterior: o trecho, a legenda ou a aparência mudou. "
                "Gere de novo."
            )
        else:
            texto = ""
        self._configurar(
            self._situacao_previa, text=texto, text_color=_COR_ALERTA if guardada else _COR_SUAVE
        )

    def gerar_previa(self) -> None:
        """Renderiza o corte aberto em miniatura (em segundo plano) e mostra no mini player."""
        if self._atual is None or self._gerando is not None or self._tarefas is None:
            return
        janela = self.winfo_toplevel()
        origem = self._origem()
        if origem is None:
            dialogos.aviso(
                janela,
                "Prévia do Short",
                "Escolha o vídeo do sermão (e confira se as opções estão completas) para ver a "
                "prévia: ela é feita a partir do vídeo original.",
            )
            return
        self._guardar_detalhe()
        corte = Corte.de_dict(self._cortes[self._atual].para_dict())  # a thread usa uma cópia
        id_ = str(corte.id)
        assinatura = self._assinatura_da_previa(corte, origem)
        palavras = list(self._palavras)
        o = origem.opcoes
        fontes = (self._pastas_do_app or Pastas()).fontes
        destino = self._pasta_das_previas / f"previa_{id_}.mp4"
        cancelar = threading.Event()
        self._cancelar_previa = cancelar
        self._gerando = id_
        self._progresso_previa = 0.0
        self._player.limpar()
        self._player.grid_remove()
        self._previas.pop(id_, None)
        self._botao_previa.configure(state="disabled", text="Gerando…")
        self._botao_cancelar_previa.pack(side="left", padx=(6, 0))
        self._barra_previa.set(0)
        self._barra_previa.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self._situacao_previa.configure(text="Gerando a prévia…", text_color=_COR_SUAVE)
        self.after(300, self._acompanhar_previa)

        def definir_progresso(fracao: float) -> None:
            self._progresso_previa = fracao

        def pronta(resultado: previa.PreviaDoShort) -> None:
            if cancelar.is_set() or not self.winfo_exists():  # cancelada: outro corte já mandou
                return
            self._previas[id_] = (assinatura, resultado)
            self._terminou_a_previa()
            self._atualizar_previa_atual()

        def falhou(erro: Exception) -> None:
            if cancelar.is_set() or isinstance(erro, Cancelado) or not self.winfo_exists():
                return  # a tela já foi arrumada por quem cancelou
            self._terminou_a_previa()
            self._atualizar_previa_atual()
            self._situacao_previa.configure(
                text="Não foi possível gerar a prévia.", text_color=_COR_ERRO
            )
            dialogos.erro(
                self.winfo_toplevel(), "Prévia do Short", f"Não foi possível gerar a prévia: {erro}"
            )

        self._tarefas.rodar(
            lambda: previa.previa_do_corte(
                origem.video, origem.info, corte, palavras, destino,
                deslocamento_pct=o.recorte_deslocamento_x_pct,
                posicao_legenda=o.legenda_posicao_vertical,
                com_legenda=True,
                enquadramento=o.enquadramento_nos_curtos,
                zoom_do_short=o.enquadramento_do_short,
                tratamento=o.tratamento_nos_curtos,
                pasta_fontes=fontes,
                ao_progresso=definir_progresso,
                cancelar=cancelar,
            ),
            pronta,
            falhou,
            chave=f"previa_do_short_{id_}",
        )  # fmt: skip

    def _acompanhar_previa(self) -> None:
        if self._gerando is None:
            return
        with contextlib.suppress(TclError):
            self._barra_previa.set(self._progresso_previa)
            self._situacao_previa.configure(
                text=f"Gerando a prévia… {self._progresso_previa * 100:.0f}%"
            )
            self.after(300, self._acompanhar_previa)

    def _atualizar_previa_atual(self) -> None:
        if self._atual is not None:
            self._mostrar_previa(self._cortes[self._atual])

    def _clicou_em_cancelar(self) -> None:
        self._cancelar_a_previa()
        self._atualizar_previa_atual()
        self._situacao_previa.configure(text="Prévia cancelada.", text_color=_COR_SUAVE)

    def _cancelar_a_previa(self) -> None:
        self._cancelar_previa.set()
        self._terminou_a_previa()

    def _terminou_a_previa(self) -> None:
        self._gerando = None
        with contextlib.suppress(TclError):
            self._botao_cancelar_previa.pack_forget()
            self._barra_previa.grid_remove()
            self._botao_previa.configure(state="normal")

    def destroy(self) -> None:
        self._cancelar_previa.set()
        for nome in ("_sincronizacao", "_acompanhar_a_tabela", "_agendamento_pendente"):
            pendente = getattr(self, nome)
            if pendente is not None:
                with contextlib.suppress(TclError):
                    self.after_cancel(pendente)
                setattr(self, nome, None)
        self._refazendo = None  # a IA que ainda pensa não tem mais onde entregar o resultado
        janela = self.winfo_toplevel()
        for evento, identificador in self._atalhos_do_historico:
            with contextlib.suppress(TclError):
                janela.unbind(evento, identificador)
        self._atalhos_do_historico.clear()
        super().destroy()

    # --- refazer título, descrição e hashtags com a IA ------------------------------------------
    def refazer_com_ia(self, campo: str) -> bool:
        """Pede à IA o título, a descrição ou as hashtags do corte aberto de novo, a partir do texto
        que o Short tem agora (a legenda, já com o que foi corrigido), não do trecho original.

        Roda em segundo plano (na CPU leva cerca de 1 minuto); o resultado entra no campo e só é
        gravado quando a pessoa salva. Devolve False se não pôde começar.
        """
        if (
            campo not in CAMPOS_REFAZIVEIS
            or self._atual is None
            or self._refazendo is not None
            or self._tarefas is None
            or self._contexto_da_ia is None
        ):
            return False
        janela = self.winfo_toplevel()
        ia = self._contexto_da_ia()
        if ia is None:
            dialogos.aviso(
                janela,
                "Refazer com IA",
                "Confira as opções da janela (o modelo do Ollama está nas opções avançadas): "
                "há um campo incompleto.",
            )
            return False
        self._guardar_detalhe()
        corte = self._cortes[self._atual]
        texto = " ".join(legenda_do_corte(self._palavras, corte).split()) or corte.texto
        if len(texto.split()) < 3:
            dialogos.aviso(janela, "Refazer com IA", "O Short ainda não tem texto para a IA ler.")
            return False
        titulos = [c.titulo for c in self._cortes if c.id != corte.id and c.titulo]
        if campo == "titulo" and corte.titulo:
            titulos.append(corte.titulo)  # quem pede de novo quer um título diferente do de agora
        sermao = nome_do_sermao(self._pastas.raiz)
        id_, categoria, duracao = corte.id, corte.categoria, corte.duracao

        def trabalho():
            cliente = preparar_cliente(
                ia.modelo, threads=ia.threads, usar_gpu=ia.usar_gpu,
                manter_na_memoria=_MANTER_A_IA_S,
            )  # fmt: skip
            return cliente.refazer(campo, sermao, texto, categoria, duracao, titulos)

        self._refazendo = (id_, campo)
        self._inicio_da_ia = time.monotonic()
        self._atualizar_botoes_da_ia()
        self.after(500, self._acompanhar_ia)
        self._tarefas.rodar(
            trabalho,
            lambda valor: self._ia_pronta(id_, campo, valor),
            lambda erro: self._ia_falhou(id_, campo, erro),
            chave=f"refazer_{id_}_{campo}",
        )
        return True

    def _atualizar_botoes_da_ia(self) -> None:
        """Os botões "Refazer com IA": um só trabalho por vez (a IA pesa na CPU)."""
        corte_aberto = self._cortes[self._atual].id if self._atual is not None else None
        pode = self._atual is not None and self._refazendo is None
        for campo, botao in self._botoes_da_ia.items():
            fazendo_este = self._refazendo == (corte_aberto, campo)
            with contextlib.suppress(TclError):
                self._configurar(
                    botao,
                    state="normal" if pode else "disabled",
                    text="Criando…" if fazendo_este else "↻ Refazer com IA",
                )
                if not fazendo_este:
                    self._configurar(self._situacao_da_ia[campo], text="")

    def _acompanhar_ia(self) -> None:
        if self._refazendo is None:
            return
        with contextlib.suppress(TclError):
            id_, campo = self._refazendo
            if self._atual is not None and self._cortes[self._atual].id == id_:
                self._situacao_da_ia[campo].configure(
                    text=f"A IA está criando… {time.monotonic() - self._inicio_da_ia:.0f} s",
                    text_color=_COR_SUAVE,
                )
            self.after(1000, self._acompanhar_ia)

    def _ia_pronta(self, id_: int, campo: str, valor: str | list[str]) -> None:
        if not self.winfo_exists():
            return
        self._refazendo = None
        indice = next((i for i, c in enumerate(self._cortes) if c.id == id_), None)
        if indice is None:  # os cortes foram recarregados enquanto a IA pensava
            self._atualizar_botoes_da_ia()
            return
        corte = self._cortes[indice]
        aberto = self._atual == indice
        if campo == "titulo":
            corte.slug = ""  # o nome do arquivo acompanha o título novo (refeito ao salvar)
        if aberto:
            self._mostrar_o_que_a_ia_criou(campo, valor)
        else:
            if campo == "titulo":
                corte.titulo = str(valor)
            elif campo == "descricao":
                corte.descricao = str(valor)
            else:
                corte.hashtags = list(valor)
            self._atualizar_linha(indice)
        self._houve_mudanca()
        self._atualizar_botoes_da_ia()
        if aberto:
            self._situacao_da_ia[campo].configure(
                text=f"✔ {_ROTULO_DO_CAMPO[campo]} refeito", text_color=_COR_OK
            )

    def _mostrar_o_que_a_ia_criou(self, campo: str, valor: str | list[str]) -> None:
        if campo == "titulo":
            self._var_titulo.set(str(valor))
        elif campo == "descricao":
            self._campo_descricao.delete("1.0", "end")
            self._campo_descricao.insert("1.0", str(valor))
        else:
            self._var_hashtags.set(" ".join(valor))
        self._guardar_detalhe()
        self._atualizar_linha(self._atual)

    def _ia_falhou(self, id_: int, campo: str, erro: Exception) -> None:
        if not self.winfo_exists():
            return
        self._refazendo = None
        self._atualizar_botoes_da_ia()
        if isinstance(erro, Cancelado):
            return
        dialogos.erro(
            self.winfo_toplevel(),
            "Refazer com IA",
            f"Não foi possível refazer {_ROTULO_DO_CAMPO[campo]}: {erro}",
        )

    # --- estado e gravação --------------------------------------------------------------------
    def _marcar_mudanca(self) -> None:
        if self._atual is not None and not self._carregando:
            self._houve_mudanca()

    def _houve_mudanca(self) -> None:
        if not self._carregando:
            self._mudou = True
        if self._ao_mudar is not None:
            self._ao_mudar()

    def _guardar_detalhe(self) -> None:
        """Copia título, descrição, hashtags e legenda da tela para o corte que estava aberto."""
        if self._atual is None or self._carregando or self._tela_desatualizada or not self._cortes:
            return
        self._levar_a_tabela_para_a_legenda()  # o que foi editado na tabela chega à caixa antes
        corte = self._cortes[self._atual]
        corte.titulo = self._var_titulo.get().strip()
        corte.descricao = self._campo_descricao.get("1.0", "end-1c").strip()
        corte.hashtags = ler_hashtags(self._var_hashtags.get())
        self._confirmar_legenda(corte)

    def cortes_acima_do_limite(self) -> list[Corte]:
        """Os cortes marcados que passam do limite e por isso não podem ser Shorts."""
        self._guardar_detalhe()
        return [c for c in self._cortes if c.incluir and passa_do_limite(c)]

    def salvar(self, *, exigir_ate_o_limite: bool = True) -> bool:
        """Grava `cortes_shorts.json`. Devolve False se a pessoa desistiu ou há o que corrigir.

        Com `exigir_ate_o_limite`, um corte marcado acima do limite de duração impede de salvar
        (quem salva para
        renderizar não pode deixá-lo passar); sem isso, vale como trabalho em andamento.
        """
        self._guardar_detalhe()
        janela = self.winfo_toplevel()
        longos = self.cortes_acima_do_limite() if exigir_ate_o_limite else []
        if longos:
            lista = "\n".join(f"• {c.titulo or c.texto[:40]} ({c.duracao:.0f} s)" for c in longos)
            dialogos.aviso(
                janela,
                f"Corte com mais de {DURACAO_MAXIMA_S:g} segundos",
                f"{AVISO_ACIMA_DO_LIMITE}\n\n{lista}\n\nEncurte o corte ou desmarque-o.",
            )
            return False
        r = resumir(self._cortes)
        if r.marcados == 0 and not dialogos.confirmar(
            janela,
            "Nenhum corte marcado",
            "Sem cortes marcados, nenhum Short será gerado. Salvar assim mesmo?",
            sim="Salvar",
            nao="Voltar",
        ):
            return False
        if r.sem_titulo:
            dialogos.aviso(
                janela,
                "Corte sem título",
                f"{r.sem_titulo} corte(s) marcado(s) estão sem título. Escreva um título ou "
                "desmarque o corte.",
            )
            return False
        self._gravar_no_disco()
        return True

    def salvar_em_silencio(self) -> None:
        """Grava o que está em edição sem perguntar nada: o celular tomou o projeto (controle
        remoto, Remoto.md R8). Corte sem título, acima do limite ou nenhum marcado ficam como
        estão, como trabalho em andamento (o celular continua de onde o PC parou)."""
        self._guardar_detalhe()
        self._gravar_no_disco()

    def _gravar_no_disco(self) -> None:
        usados: set[str] = set()
        for corte in self._cortes:
            if corte.titulo != self._titulo_do_nome.get(corte.id, corte.titulo):
                corte.slug = ""  # o título mudou: o arquivo passa a ter o nome dele, não o da IA
            corte.slug = nome_de_arquivo(corte.slug or corte.titulo, usados)
            usados.add(corte.slug)
            self._titulo_do_nome[corte.id] = corte.titulo
        gravar_cortes(self._pastas.cortes_shorts, self._cortes)
        self._mudou = False
        arquivos = self._sincronizar_arquivos()  # nome, pasta e .txt
        if arquivos.movidos:
            self.recarregar_registro(reordenar=False)
        for i in range(len(self._cortes)):
            self._atualizar_linha(i)
        if self._atual is not None and (arquivos.movidos or arquivos.falhas):
            self._mostrar_postado(self._cortes[self._atual], arquivos.falhas)
        self._atualizar_resumo()

    # --- processamento rodando ----------------------------------------------------------------
    # Durante um processamento (a renderização) o editor continua à mão. O que ele NÃO faz agora é
    # mudar arquivos já renderizados de nome ou de pasta: o trabalhador guarda o registro deles na
    # memória e o regrava a cada Short pronto (desfaria a mudança), e a conferência final lê os
    # arquivos. A marca vale na hora no `cortes_shorts.json`; o arquivo muda de lugar no fim.
    def definir_processamento(self, ativo: bool) -> None:
        """Diz ao editor se há um processamento rodando. Ao terminar, leva os arquivos para as
        pastas e os nomes que o que foi salvo pede e relê o que foi renderizado."""
        if ativo == self._processando:
            return
        self._processando = ativo
        if ativo or not self._cortes:
            return
        falhas: list[str] = []
        if self._arquivos_adiados:
            self._arquivos_adiados = False
            with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
                falhas = sincronizar_arquivos(
                    self._pastas, carregar_cortes(self._pastas.cortes_shorts)
                ).falhas  # o que está salvo, não o que está sendo digitado
        self.recarregar_registro()
        if self._atual is not None:
            self._mostrar_postado(self._cortes[self._atual], falhas)
            self._mostrar_agendamento(self._cortes[self._atual])

    def _mover_arquivos(self, cortes: Sequence[Corte]) -> ResultadoDasMarcas:
        """Leva os Shorts já renderizados para `postados/` ou `agendados/` (ou os traz de volta);
        durante um processamento só anota que falta fazer."""
        if self._processando:
            self._arquivos_adiados = True
            return ResultadoDasMarcas([], [])
        return aplicar_marcas_de_postado(self._pastas, cortes)

    def _sincronizar_arquivos(self) -> ResultadoDasMarcas:
        """Nome, pasta e `.txt` dos Shorts renderizados conforme os cortes de agora (depois de
        salvar); durante um processamento só anota que falta fazer."""
        if self._processando:
            self._arquivos_adiados = True
            return ResultadoDasMarcas([], [])
        return sincronizar_arquivos(self._pastas, self._cortes)

    def _falta_mover(self, corte: Corte) -> bool:
        """O arquivo renderizado do Short ainda não está na pasta que a situação dele pede (a
        mudança está esperando o fim do processamento)."""
        entrada = self._registro.get(str(corte.id))
        if not self._processando or entrada is None or not self._existem.get(str(corte.id)):
            return False
        atual = Path(entrada["arquivo"]).parent.as_posix()
        esperado = pasta_do_estado(corte)
        if esperado:
            return atual != esperado
        return atual in (PASTA_DOS_POSTADOS, PASTA_DOS_AGENDADOS)

    def _manter_na_lista(self, corte: Corte) -> None:
        """A situação deste corte mudou por uma ação da pessoa: com um filtro ligado ele continua
        na lista até ela ser refeita (senão a linha sumiria debaixo do mouse)."""
        with contextlib.suppress(ValueError):
            self._manter_visivel = self._cortes.index(corte)

    # --- bloqueio -----------------------------------------------------------------------------
    def bloquear(self, motivo: str | None) -> None:
        """Esconde o editor e mostra o motivo (processamento em andamento); `None` libera."""
        if motivo == self._bloqueado:
            return
        self._bloqueado = motivo
        if motivo:
            self._corpo.grid_remove()
            self._aviso_bloqueio.configure(text=motivo)
            self._aviso_bloqueio.grid(row=0, column=0, pady=60)
        else:
            self._aviso_bloqueio.grid_remove()
            self._corpo.grid()
