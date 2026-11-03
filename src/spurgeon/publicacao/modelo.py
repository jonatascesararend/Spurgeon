"""O que é publicar: o pacote (vídeo + textos), o resultado e a base de cada rede."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.rede import Transporte, TransporteUrllib

# --- estados de uma postagem ------------------------------------------------------------------
ENVIADO = "enviado"  # a rede publicou agora
AGENDADO = "agendado"  # a própria rede guarda o horário e publica sozinha
NA_FILA = (
    "na_fila"  # o Spurgeon guarda o horário e publica na hora (o aplicativo precisa estar aberto)
)
RASCUNHO = "rascunho"  # está nos rascunhos da rede, esperando a pessoa concluir
ASSISTIDO = "assistido"  # o Spurgeon só deixou tudo pronto para a pessoa postar à mão
ERRO = "erro"

ROTULO_DO_ESTADO = {
    ENVIADO: "publicado",
    AGENDADO: "agendado na rede",
    NA_FILA: "na fila do Spurgeon",
    RASCUNHO: "nos rascunhos",
    ASSISTIDO: "preparado",
    ERRO: "com erro",
}

Progresso = Callable[[float], None]
PedidoDeCancelar = Callable[[], bool]
Relogio = Callable[[], datetime]


def agora_local() -> datetime:
    return datetime.now().astimezone()


def em_utc(quando: datetime) -> datetime:
    """A mesma hora em UTC (um horário sem fuso vale como o do computador)."""
    if quando.tzinfo is None:
        quando = quando.astimezone()
    return quando.astimezone(UTC)


def iso_utc(quando: datetime) -> str:
    """`2026-11-10T14:00:00Z`: o formato que o YouTube pede em `publishAt`."""
    return em_utc(quando).strftime("%Y-%m-%dT%H:%M:%SZ")


def limpar_hashtag(texto: str) -> str:
    """`#Fé em Cristo` → `#FéEmCristo` (hashtag não tem espaço nem pontuação)."""
    palavras = [re.sub(r"[^\w]", "", p) for p in texto.replace("#", " ").split()]
    palavras = [p for p in palavras if p]
    corpo = "".join(p if i == 0 else p[0].upper() + p[1:] for i, p in enumerate(palavras))
    return f"#{corpo}" if corpo else ""


@dataclass(frozen=True)
class Pacote:
    """O que vai para as redes: o arquivo do Short e os textos que o acompanham."""

    video: Path
    titulo: str
    descricao: str = ""
    hashtags: tuple[str, ...] = ()
    duracao_s: float = 0.0

    @property
    def tamanho(self) -> int:
        return self.video.stat().st_size

    @property
    def etiquetas(self) -> list[str]:
        """As hashtags prontas (com `#`, sem repetir)."""
        vistas: dict[str, str] = {}
        for h in self.hashtags:
            limpa = limpar_hashtag(h)
            if limpa:
                vistas.setdefault(limpa.lower(), limpa)
        return list(vistas.values())

    def legenda(self, limite: int | None = None, *, com_titulo: bool = True) -> str:
        """Título, descrição e hashtags numa legenda só; se passar de `limite`, a descrição encurta
        (as hashtags e o título ficam)."""
        fixos = [self.titulo.strip()] if com_titulo and self.titulo.strip() else []
        marcas = " ".join(self.etiquetas)
        descricao = self.descricao.strip()

        def juntar(texto: str) -> str:
            return "\n\n".join(p for p in [*fixos, texto, marcas] if p)

        pronta = juntar(descricao)
        if limite is None or len(pronta) <= limite:
            return pronta
        sobra = limite - len(juntar("…"))
        if sobra <= 0:
            return pronta[: limite - 1].rstrip() + "…"
        corte = descricao[:sobra].rsplit(" ", 1)[0].rstrip(" ,;:.")
        return juntar(corte + "…" if corte else "")


@dataclass
class Resultado:
    plataforma: str
    estado: str
    id_remoto: str = ""
    url: str = ""
    quando: str = ""  # ISO com fuso: a hora marcada (agendamento) ou a do envio
    detalhe: str = ""  # o que a pessoa deve saber ("toque na notificação do TikTok")
    aviso: str = ""  # algo estranho que a rede devolveu (ex.: o YouTube deixou o vídeo privado)
    dados: dict[str, Any] = field(default_factory=dict)  # o andamento interno (fila local)

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> Resultado:
        conhecidos = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**conhecidos)

    @property
    def rotulo(self) -> str:
        return ROTULO_DO_ESTADO.get(self.estado, self.estado)


@dataclass(frozen=True)
class Campo:
    """Um dado que a pessoa cola ao conectar a conta (aparece na aba Integrações)."""

    nome: str
    rotulo: str
    segredo: bool = False
    ajuda: str = ""
    opcional: bool = False


class Plataforma:
    """A base de uma rede. As subclasses dizem como conectar, conferir e publicar."""

    chave = ""
    nome = ""
    #: a própria rede guarda o horário (senão o Spurgeon guarda: `NA_FILA`)
    agenda_na_rede = False
    #: a rede publica de verdade pela API (senão é rascunho ou assistido)
    publica_pela_api = True
    campos: tuple[Campo, ...] = ()
    #: o passo a passo, em texto, de como conseguir os dados dos campos
    como_conectar = ""

    def __init__(
        self,
        cofre: Cofre,
        transporte: Transporte | None = None,
        *,
        relogio: Relogio = agora_local,
    ):
        self.cofre = cofre
        self.transporte: Transporte = transporte or TransporteUrllib()
        self.relogio = relogio

    # o nome da conta no cofre (a Meta usa uma só para Instagram e Facebook)
    @property
    def conta(self) -> str:
        return self.chave

    def guardado(self, nome: str) -> str:
        return self.cofre.obter(self.conta, nome)

    @property
    def conectada(self) -> bool:
        raise NotImplementedError

    @property
    def disponivel(self) -> bool:
        """Dá para usar esta rede agora (o TikTok assistido não precisa de conta conectada)."""
        return self.conectada

    @property
    def resumo_da_conta(self) -> str:
        """O que mostrar ao lado de "Conectado" (o nome do canal, da Página…)."""
        return self.guardado("conta_nome")

    def conectar(
        self,
        *,
        abrir: Callable[[str], object] | None = None,
        cancelado: PedidoDeCancelar | None = None,
    ) -> str:
        raise NotImplementedError

    def desconectar(self) -> None:
        self.cofre.remover(self.conta)

    def problemas(self, pacote: Pacote, quando: datetime | None) -> list[str]:
        """O que impede de publicar este pacote nesta rede (vazio = pode)."""
        return []

    def publicar(
        self,
        pacote: Pacote,
        quando: datetime | None,
        *,
        ao_progresso: Progresso | None = None,
        cancelado: PedidoDeCancelar | None = None,
    ) -> Resultado:
        raise NotImplementedError

    def retomar(
        self,
        resultado: Resultado,
        pacote: Pacote,
        *,
        ao_progresso: Progresso | None = None,
        cancelado: PedidoDeCancelar | None = None,
    ) -> Resultado:
        """Faz o próximo passo de uma postagem `NA_FILA` (só quem agenda no Spurgeon usa)."""
        return resultado

    def consultar(self, resultado: Resultado) -> Resultado:
        """Pergunta à rede como está a postagem (quando ela deixa)."""
        return resultado
