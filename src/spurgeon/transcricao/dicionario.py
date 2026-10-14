"""Dicionário do português do Brasil para o verificador (offline).

Quem diz se a palavra existe é o dicionário Hunspell `pt_BR` do projeto VERO/LibreOffice
(`recursos/dicionarios/pt_BR/`), lido pelo `spylls`: ele conhece as formas brasileiras ("incômodo",
"aspecto", "decepção") e as flexões dos verbos. O `pyspellchecker` só entra para **sugerir** (é
rápido), e cada sugestão passa pelo dicionário brasileiro antes de aparecer.

O Hunspell leva alguns segundos para carregar; por isso há um só por arquivo por execução e o
verificador pode pedir `preparar()` em uma thread antes de ser usado.
"""

from __future__ import annotations

import re
import threading
from functools import lru_cache
from pathlib import Path

_ACENTUAVEIS = {
    "a": "áâãà",
    "e": "éê",
    "i": "í",
    "o": "óôõ",
    "u": "ú",
    "c": "ç",
}
_PARTES_COM_LIGACAO = re.compile(r"[-’']")
_TRAVA = threading.Lock()


@lru_cache(maxsize=2)
def _abrir_hunspell(prefixo: str):
    from spylls.hunspell import Dictionary

    return Dictionary.from_files(prefixo)


class DicionarioBr:
    def __init__(self, prefixo: Path | None = None):
        self._prefixo = Path(prefixo) if prefixo else None
        self._hunspell = None
        self._ortografo = None
        self._tentou = False
        self._memoria: dict[str, bool] = {}

    # --- carga ---------------------------------------------------------------------------------
    @property
    def disponivel(self) -> bool:
        """Os arquivos do Hunspell existem (senão só se usa o `pyspellchecker`, menos exato)."""
        return bool(
            self._prefixo
            and self._prefixo.with_suffix(".dic").is_file()
            and self._prefixo.with_suffix(".aff").is_file()
        )

    def preparar(self) -> None:
        """Carrega tudo agora (leva alguns segundos); pode rodar em uma thread."""
        with _TRAVA:
            if self.disponivel and self._hunspell is None and not self._tentou:
                self._tentou = True
                try:
                    self._hunspell = _abrir_hunspell(str(self._prefixo))
                except Exception:  # arquivo corrompido: cai no dicionário simples
                    self._hunspell = None
            self.ortografo  # noqa: B018 - carrega o pyspellchecker

    @property
    def ortografo(self):
        if self._ortografo is None:
            from spellchecker import SpellChecker

            self._ortografo = SpellChecker(language="pt")
        return self._ortografo

    # --- consulta ------------------------------------------------------------------------------
    def conhece(self, palavra: str) -> bool:
        """A palavra existe (como está escrita, em minúsculas ou com inicial maiúscula)?"""
        if palavra not in self._memoria:
            self._memoria[palavra] = self._consultar(palavra)
        return self._memoria[palavra]

    def _consultar(self, palavra: str) -> bool:
        if not self._tentou:
            self.preparar()
        if self._hunspell is None:
            return bool(self.ortografo.known([palavra.casefold()]))
        variantes = (palavra, palavra.casefold(), palavra[:1].upper() + palavra[1:].casefold())
        if any(self._hunspell.lookup(v) for v in dict.fromkeys(variantes)):
            return True
        partes = [p for p in _PARTES_COM_LIGACAO.split(palavra) if p]
        return len(partes) > 1 and all(self.conhece(p) for p in partes)

    def comum(self, palavra: str) -> bool:
        """A palavra está na lista de palavras comuns do idioma (em minúsculas, nomes incluídos)?"""
        return bool(self.ortografo.known([palavra.casefold()]))

    def frequencia(self, palavra: str) -> float:
        return self.ortografo.word_usage_frequency(palavra.casefold())

    def candidatas(self, palavra: str) -> list[str]:
        """Palavras brasileiras parecidas, as mais usadas primeiro (rápido)."""
        minuscula = palavra.casefold()
        achadas = set(self._com_acento(minuscula))
        achadas |= {c for c in (self.ortografo.candidates(minuscula) or ()) if self.conhece(c)}
        achadas.discard(minuscula)
        return sorted(achadas, key=lambda c: (-self.frequencia(c), c))

    def _com_acento(self, palavra: str) -> list[str]:
        """Trocas de uma letra por sua versão acentuada que formam uma palavra do dicionário."""
        resultado = []
        for i, letra in enumerate(palavra):
            for acentuada in _ACENTUAVEIS.get(letra, ""):
                tentativa = palavra[:i] + acentuada + palavra[i + 1 :]
                if self.conhece(tentativa):
                    resultado.append(tentativa)
        # a letra já acentuada errada ("amôr" → "amor") também conta
        sem_acento = _sem_acento(palavra)
        if sem_acento != palavra and self.conhece(sem_acento):
            resultado.append(sem_acento)
        return resultado

    def sugestoes_do_hunspell(self, palavra: str, limite: int = 6) -> list[str]:
        """As sugestões do próprio Hunspell: boas, mas levam de 0,5 a 6 s por palavra."""
        if self._hunspell is None:
            return []
        achadas: list[str] = []
        for s in self._hunspell.suggest(palavra):
            if s not in achadas and s.casefold() != palavra.casefold():
                achadas.append(s)
            if len(achadas) >= limite:
                break
        return achadas


def _sem_acento(palavra: str) -> str:
    import unicodedata

    base = "".join(
        c for c in unicodedata.normalize("NFD", palavra) if unicodedata.category(c) != "Mn"
    )
    return base.replace("ç", "c")
