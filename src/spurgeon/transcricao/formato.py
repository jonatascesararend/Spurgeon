"""Formato da transcrição (`transcricao.json`): blocos, segmentos e palavras com tempos.

Todos os tempos estão na linha do tempo ORIGINAL do vídeo, em segundos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spurgeon.core.arquivos import escrever_json_atomico, ler_json

VERSAO = 1


@dataclass
class Palavra:
    texto: str  # com a pontuação colada, como o Whisper devolve ("Deus,")
    inicio: float
    fim: float
    prob: float = 1.0  # confiança do Whisper (1.0 para o que uma pessoa corrigiu)
    original: str | None = None  # texto antes de uma correção, para o usuário conferir

    def para_lista(self) -> list[Any]:
        item: list[Any] = [
            self.texto,
            round(self.inicio, 3),
            round(self.fim, 3),
            round(self.prob, 3),
        ]
        if self.original is not None:
            item.append(self.original)
        return item

    @classmethod
    def de_lista(cls, item: list[Any]) -> Palavra:
        return cls(item[0], item[1], item[2], item[3], item[4] if len(item) > 4 else None)


@dataclass
class Segmento:
    palavras: list[Palavra]

    @property
    def inicio(self) -> float:
        return self.palavras[0].inicio

    @property
    def fim(self) -> float:
        return self.palavras[-1].fim

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palavras)


@dataclass
class Bloco:
    inicio: float
    fim: float
    segmentos: list[Segmento] = field(default_factory=list)
    concluido: bool = False


@dataclass
class Transcricao:
    modelo: str
    duracao_s: float
    idioma: str = "pt"
    blocos: list[Bloco] = field(default_factory=list)
    revisada: bool = False  # True depois que uma pessoa salvou edições
    #: identifica as opções e a decodificação que geraram os blocos (para saber se dá para
    #: retomar uma transcrição interrompida ou se é preciso recomeçar)
    assinatura: str = ""
    #: segmentos tirados por parecerem inventados pelo Whisper (guardados para conferência)
    removidos: list[dict[str, Any]] = field(default_factory=list)
    versao: int = VERSAO

    @property
    def segmentos(self) -> list[Segmento]:
        return [s for b in self.blocos for s in b.segmentos]

    @property
    def palavras(self) -> list[Palavra]:
        return [p for s in self.segmentos for p in s.palavras]

    def para_dict(self) -> dict[str, Any]:
        return {
            "versao": self.versao,
            "modelo": self.modelo,
            "idioma": self.idioma,
            "duracao_s": self.duracao_s,
            "revisada": self.revisada,
            "assinatura": self.assinatura,
            "removidos": self.removidos,
            "blocos": [
                {
                    "inicio": b.inicio,
                    "fim": b.fim,
                    "concluido": b.concluido,
                    "segmentos": [[p.para_lista() for p in s.palavras] for s in b.segmentos],
                }
                for b in self.blocos
            ],
        }

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> Transcricao:
        return cls(
            modelo=dados["modelo"],
            duracao_s=dados["duracao_s"],
            idioma=dados.get("idioma", "pt"),
            revisada=dados.get("revisada", False),
            assinatura=dados.get("assinatura", ""),
            removidos=dados.get("removidos", []),
            versao=dados.get("versao", VERSAO),
            blocos=[
                Bloco(
                    inicio=b["inicio"],
                    fim=b["fim"],
                    concluido=b["concluido"],
                    segmentos=[
                        Segmento([Palavra.de_lista(p) for p in seg]) for seg in b["segmentos"]
                    ],
                )
                for b in dados["blocos"]
            ],
        )

    def salvar(self, caminho: Path) -> None:
        escrever_json_atomico(caminho, self.para_dict(), indent=None)

    @classmethod
    def carregar(cls, caminho: Path) -> Transcricao:
        return cls.de_dict(ler_json(caminho))


def segundos_para_rotulo(segundos: float) -> str:
    """3725.4 → `1:02:05`; 125 → `2:05`."""
    total = int(segundos)
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{seg:02d}" if horas else f"{minutos}:{seg:02d}"
