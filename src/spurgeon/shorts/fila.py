"""A fila dos Shorts: renderizar e programar no YouTube, um passo por vez, sem atrapalhar o PC.

Cada projeto guarda a sua fila em `_work/fila_dos_shorts.json`: um item por Short, com o que falta
fazer. Um item pode só pedir a renderização (o "Renderizar" pedido enquanto outra renderização
roda) ou pedir a postagem no YouTube (programar num horário, ou publicar agora) e a troca do
horário de um Short já programado (reagendar). Quem anda a fila é o despachante
(`spurgeon.remoto.despachante`): primeiro renderiza tudo o que falta, e só com a renderização
parada abre o YouTube Studio, um Short de cada vez.

A própria etapa de renderização conta o que está fazendo em `_work/renderizando_agora.json` (o
Short da vez e os que faltam): é dali que saem as marcas "renderizando agora" e "na fila para
renderizar", valha a renderização pedida pela fila, pelo botão do PC ou pelo celular.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.projeto import PastasProjeto

NOME_DA_FILA = "fila_dos_shorts.json"
NOME_DO_RENDER = "renderizando_agora.json"
FORMATO = "%Y-%m-%dT%H:%M"

# --- o que o item pede ------------------------------------------------------------------------
SO_RENDERIZAR = ""  # só renderizar (o Short não vai ao YouTube por esta fila)
POSTAR = "postar"  # programar no horário `quando` (ou publicar agora, sem `quando`)
REAGENDAR = "reagendar"  # trocar o horário de um Short já programado no YouTube

# --- em que fase o item está agora ------------------------------------------------------------
FASE_ESPERANDO = ""
FASE_RENDER = "render"
FASE_YOUTUBE = "youtube"

#: o YouTube pede uma folga para programar (o mesmo do celular, `remoto/postagem.py`)
ANTECEDENCIA_PARA_PROGRAMAR = timedelta(minutes=15)
#: reagendar só até 5 minutos antes do horário marcado (depois disso o Short já está saindo)
LIMITE_PARA_REAGENDAR = timedelta(minutes=5)

# --- a marca de cada Short na lista -----------------------------------------------------------
RENDERIZANDO = "renderizando"
NA_FILA_DO_RENDER = "na_fila_do_render"
PROGRAMANDO = "programando"
NA_FILA_DA_PROGRAMACAO = "na_fila_da_programacao"
COM_ERRO = "erro"

ROTULO_DA_SITUACAO = {
    RENDERIZANDO: "⚙ renderizando agora",
    NA_FILA_DO_RENDER: "⏳ na fila para renderizar",
    PROGRAMANDO: "📤 programando no YouTube",
    NA_FILA_DA_PROGRAMACAO: "🕒 na fila de programação",
    COM_ERRO: "⚠ a fila parou neste Short",
}
#: a cor de fundo de cada marca (claro, escuro), como os selos do Studio
COR_DA_SITUACAO = {
    RENDERIZANDO: ("#1565C0", "#1565C0"),
    NA_FILA_DO_RENDER: ("#5C6BC0", "#3949AB"),
    PROGRAMANDO: ("#C62828", "#B71C1C"),
    NA_FILA_DA_PROGRAMACAO: ("#EF6C00", "#E65100"),
    COM_ERRO: ("#6D4C41", "#5D4037"),
}


class PedidoInvalido(ValueError):
    """O pedido não entra na fila (o texto diz por quê, para a pessoa)."""


@dataclass(frozen=True)
class ItemDaFila:
    corte: int
    acao: str = SO_RENDERIZAR
    #: renderizar antes (mesmo um Short já pronto, com `refazer`)
    renderizar: bool = False
    refazer: bool = False
    #: o horário pedido (`AAAA-MM-DDTHH:MM`, hora do computador); "" = publicar agora
    quando: str = ""
    #: o vídeo do canal a vincular como "vídeo relacionado" ("" = nenhum)
    relacionado: str = ""
    pedido_em: float = 0.0
    fase: str = FASE_ESPERANDO
    #: o que o despachante está fazendo agora (o andamento da automação)
    texto: str = ""
    erro: str = ""

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> ItemDaFila:
        conhecidos = {k: v for k, v in dados.items() if k in cls.__dataclass_fields__}
        return cls(**conhecidos)

    @property
    def vai_ao_youtube(self) -> bool:
        return self.acao in (POSTAR, REAGENDAR)

    def horario(self) -> datetime | None:
        try:
            return datetime.strptime(self.quando, FORMATO) if self.quando else None
        except ValueError:
            return None


class FilaDoProjeto:
    """`_work/fila_dos_shorts.json`: `{"itens": {"<id do corte>": {...}}}`."""

    _travas: ClassVar[dict[str, threading.Lock]] = {}
    _trava_das_travas = threading.Lock()

    def __init__(self, pastas: PastasProjeto):
        self.pastas = pastas
        self.caminho = pastas.work / NOME_DA_FILA
        with self._trava_das_travas:  # uma trava por arquivo, para todas as instâncias
            self._trava = self._travas.setdefault(str(self.caminho), threading.Lock())

    def _ler(self) -> dict[str, dict[str, Any]]:
        if not self.caminho.is_file():
            return {}
        try:
            dados = ler_json(self.caminho)
        except (OSError, ValueError):
            return {}
        itens = dados.get("itens") if isinstance(dados, dict) else None
        return itens if isinstance(itens, dict) else {}

    def _gravar(self, itens: dict[str, dict[str, Any]]) -> None:
        if not itens:
            self.caminho.unlink(missing_ok=True)
            return
        self.pastas.work.mkdir(parents=True, exist_ok=True)
        escrever_json_atomico(self.caminho, {"itens": itens})

    def itens(self) -> list[ItemDaFila]:
        """Do pedido mais antigo ao mais novo."""
        with self._trava:
            brutos = self._ler()
        lidos = []
        for d in brutos.values():
            if isinstance(d, dict):
                try:
                    lidos.append(ItemDaFila.de_dict(d))
                except (TypeError, ValueError):
                    continue
        return sorted(lidos, key=lambda i: (i.pedido_em, i.corte))

    def do_corte(self, id_do_corte: int) -> ItemDaFila | None:
        return next((i for i in self.itens() if i.corte == id_do_corte), None)

    def gravar(self, item: ItemDaFila) -> ItemDaFila:
        with self._trava:
            itens = self._ler()
            itens[str(item.corte)] = item.para_dict()
            self._gravar(itens)
        return item

    def mudar(self, id_do_corte: int, **campos) -> ItemDaFila | None:
        """Muda só estes campos do item (None se ele não está mais na fila)."""
        with self._trava:
            itens = self._ler()
            bruto = itens.get(str(id_do_corte))
            if not isinstance(bruto, dict):
                return None
            novo = replace(ItemDaFila.de_dict(bruto), **campos)
            itens[str(id_do_corte)] = novo.para_dict()
            self._gravar(itens)
        return novo

    def remover(self, id_do_corte: int) -> bool:
        with self._trava:
            itens = self._ler()
            if itens.pop(str(id_do_corte), None) is None:
                return False
            self._gravar(itens)
        return True


# --- pedir -------------------------------------------------------------------------------------
def pedir_render(
    pastas: PastasProjeto, ids: Iterable[int], *, refazer: bool = False, agora: float | None = None
) -> list[ItemDaFila]:
    """Põe estes Shorts na fila para renderizar. Um Short que já está na fila (para ir ao YouTube,
    por exemplo) só ganha o "renderizar antes"."""
    fila = FilaDoProjeto(pastas)
    momento = time.time() if agora is None else agora
    feitos = []
    for id_do_corte in ids:
        anterior = fila.do_corte(id_do_corte)
        if anterior is not None:
            item = replace(anterior, renderizar=True, refazer=anterior.refazer or refazer,
                           erro="")  # fmt: skip
        else:
            item = ItemDaFila(id_do_corte, renderizar=True, refazer=refazer, pedido_em=momento)
        feitos.append(fila.gravar(item))
    return feitos


def pedir_postagem(
    pastas: PastasProjeto,
    id_do_corte: int,
    quando: datetime | None,
    relacionado: str,
    *,
    agora: datetime | None = None,
) -> ItemDaFila:
    """Põe o Short na fila do YouTube: programar em `quando` (ou publicar agora, com None). Se ele
    ainda não está renderizado (ou mudou depois), a fila renderiza antes."""
    agora = agora or datetime.now()
    if quando is not None and quando < agora + ANTECEDENCIA_PARA_PROGRAMAR:
        raise PedidoInvalido(
            "Para programar, escolha um horário pelo menos 15 minutos à frente "
            "(a fila pode precisar renderizar antes)."
        )
    fila = FilaDoProjeto(pastas)
    anterior = fila.do_corte(id_do_corte)
    if anterior is not None and anterior.fase == FASE_YOUTUBE:
        raise PedidoInvalido("Este Short está sendo programado no YouTube agora. Espere terminar.")
    item = ItemDaFila(
        id_do_corte, POSTAR,
        renderizar=bool(anterior and anterior.renderizar),
        refazer=bool(anterior and anterior.refazer),
        quando=quando.strftime(FORMATO) if quando else "", relacionado=relacionado.strip()[:200],
        pedido_em=anterior.pedido_em if anterior else time.time(),
    )  # fmt: skip
    return fila.gravar(item)


def pode_reagendar(marcado: datetime, novo: datetime, agora: datetime) -> str:
    """Por que não dá para trocar o horário ("" = dá): só até 5 minutos antes do horário marcado,
    e o novo também precisa estar mais de 5 minutos à frente."""
    if marcado - agora <= LIMITE_PARA_REAGENDAR:
        return (
            "Faltam menos de 5 minutos para este Short sair no YouTube: o horário não pode mais "
            "ser trocado."
        )
    if novo - agora <= LIMITE_PARA_REAGENDAR:
        return "O novo horário precisa estar mais de 5 minutos à frente."
    if novo.replace(second=0, microsecond=0) == marcado.replace(second=0, microsecond=0):
        return "O Short já está programado para este horário."
    return ""


def pedir_reagendamento(
    pastas: PastasProjeto,
    id_do_corte: int,
    marcado: datetime,
    novo: datetime,
    *,
    agora: datetime | None = None,
) -> ItemDaFila:
    """Põe na fila a troca do horário de um Short já programado no YouTube (`marcado` → `novo`)."""
    motivo = pode_reagendar(marcado, novo, agora or datetime.now())
    if motivo:
        raise PedidoInvalido(motivo)
    fila = FilaDoProjeto(pastas)
    anterior = fila.do_corte(id_do_corte)
    if anterior is not None and anterior.fase == FASE_YOUTUBE:
        raise PedidoInvalido("Este Short está sendo programado no YouTube agora. Espere terminar.")
    item = ItemDaFila(
        id_do_corte, REAGENDAR, quando=novo.strftime(FORMATO),
        pedido_em=anterior.pedido_em if anterior else time.time(),
    )  # fmt: skip
    return fila.gravar(item)


def tirar_da_fila(pastas: PastasProjeto, id_do_corte: int) -> bool:
    """Tira o Short da fila (o que está sendo feito agora não para: a pessoa espera)."""
    fila = FilaDoProjeto(pastas)
    item = fila.do_corte(id_do_corte)
    if item is None:
        return False
    if item.fase != FASE_ESPERANDO:
        raise PedidoInvalido("Este Short está sendo feito agora: espere terminar.")
    return fila.remover(id_do_corte)


# --- a renderização que está rodando ----------------------------------------------------------
@dataclass(frozen=True)
class RenderEmAndamento:
    pid: int
    ids: tuple[int, ...]
    atual: int | None = None
    feitos: tuple[int, ...] = field(default_factory=tuple)

    def falta(self, id_do_corte: int) -> bool:
        return id_do_corte in self.ids and id_do_corte not in self.feitos


def _pid_vivo(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:  # sem como saber: vale como vivo (o arquivo some no fim da etapa)
        return True


def marcar_render(
    pastas: PastasProjeto,
    ids: Iterable[int],
    *,
    atual: int | None = None,
    feitos: Iterable[int] = (),
    pid: int | None = None,
) -> None:
    """A etapa de renderização conta o que está fazendo (chamado no começo e a cada Short)."""
    escrever_json_atomico(pastas.work / NOME_DO_RENDER, {
        "pid": os.getpid() if pid is None else pid, "ids": [int(i) for i in ids],
        "atual": atual, "feitos": [int(i) for i in feitos],
    })  # fmt: skip


def limpar_render(pastas: PastasProjeto) -> None:
    (pastas.work / NOME_DO_RENDER).unlink(missing_ok=True)


def render_em_andamento(
    pastas: PastasProjeto, *, vivo: Callable[[int], bool] = _pid_vivo
) -> RenderEmAndamento | None:
    """A renderização dos Shorts rodando neste projeto agora (None se não há, ou se o processo que
    a escreveu já morreu: um resto de uma renderização interrompida)."""
    caminho = pastas.work / NOME_DO_RENDER
    if not caminho.is_file():
        return None
    try:
        dados = ler_json(caminho)
        andamento = RenderEmAndamento(
            int(dados["pid"]), tuple(int(i) for i in dados.get("ids") or ()),
            int(dados["atual"]) if dados.get("atual") is not None else None,
            tuple(int(i) for i in dados.get("feitos") or ()),
        )  # fmt: skip
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return andamento if vivo(andamento.pid) else None


# --- a marca de cada Short ---------------------------------------------------------------------
def situacao_na_fila(
    id_do_corte: int,
    item: ItemDaFila | None,
    render: RenderEmAndamento | None,
    *,
    pronto: bool,
) -> str:
    """A marca do Short na lista ("" = nenhuma). `pronto`: o Short renderizado vale (um pronto
    que a renderização ainda vai alcançar não está "na fila": ela só o confere)."""
    if render is not None and render.atual == id_do_corte:
        return RENDERIZANDO
    if item is not None and item.fase == FASE_YOUTUBE:
        return PROGRAMANDO
    if item is not None and item.erro:
        return COM_ERRO
    if render is not None and render.falta(id_do_corte) and not pronto:
        return NA_FILA_DO_RENDER
    if item is not None and (item.renderizar or (item.vai_ao_youtube and not pronto
                                                 and item.acao != REAGENDAR)):  # fmt: skip
        return NA_FILA_DO_RENDER
    if item is not None and item.vai_ao_youtube:
        return NA_FILA_DA_PROGRAMACAO
    return ""


@dataclass(frozen=True)
class MarcaDaFila:
    situacao: str
    item: ItemDaFila | None

    @property
    def rotulo(self) -> str:
        return ROTULO_DA_SITUACAO.get(self.situacao, "")

    @property
    def detalhe(self) -> str:
        """O que dizer na tela do Short (o horário pedido, o andamento ou o erro)."""
        item = self.item
        if self.situacao == COM_ERRO and item is not None:
            return f"A fila parou: {item.erro}"
        if self.situacao == PROGRAMANDO and item is not None and item.texto:
            return item.texto
        if item is None or not item.vai_ao_youtube:
            return ""
        quando = item.horario()
        if item.acao == REAGENDAR and quando:
            return f"Vai trocar o horário no YouTube para {quando:%d/%m às %H:%M}."
        if quando:
            return f"Vai ser programado no YouTube para {quando:%d/%m às %H:%M}."
        return "Vai ser publicado no YouTube assim que chegar a vez."

    def para_json(self) -> dict[str, Any] | None:
        if not self.situacao:
            return None
        item = self.item
        return {
            "situacao": self.situacao, "rotulo": self.rotulo, "detalhe": self.detalhe,
            "acao": item.acao if item else "", "quando": item.quando if item else "",
            "erro": item.erro if item else "", "pode_tirar": bool(item and not item.fase),
        }  # fmt: skip


def marcas_do_projeto(
    pastas: PastasProjeto,
    prontos: Callable[[int], bool],
    ids: Iterable[int],
    *,
    vivo: Callable[[int], bool] = _pid_vivo,
) -> dict[int, MarcaDaFila]:
    """A marca de cada Short (só os que têm alguma). `prontos(id)`: o Short renderizado vale."""
    itens = {i.corte: i for i in FilaDoProjeto(pastas).itens()}
    render = render_em_andamento(pastas, vivo=vivo)
    marcas = {}
    for id_do_corte in ids:
        item = itens.get(id_do_corte)
        if item is None and (render is None or id_do_corte not in render.ids):
            continue
        situacao = situacao_na_fila(id_do_corte, item, render, pronto=prontos(id_do_corte))
        if situacao:
            marcas[id_do_corte] = MarcaDaFila(situacao, item)
    return marcas


def projetos_com_itens(pasta_projetos: Path) -> list[PastasProjeto]:
    """Os projetos com alguma coisa na fila (só olha se o arquivo existe)."""
    if not pasta_projetos.is_dir():
        return []
    return [
        PastasProjeto(p)
        for p in sorted(pasta_projetos.iterdir())
        if p.is_dir() and (p / "_work" / NOME_DA_FILA).is_file()
    ]


def youtube_ocupado(pasta_projetos: Path) -> bool:
    """A fila está com o YouTube Studio aberto agora (em qualquer projeto)."""
    return any(
        item.fase == FASE_YOUTUBE
        for pastas in projetos_com_itens(pasta_projetos)
        for item in FilaDoProjeto(pastas).itens()
    )
