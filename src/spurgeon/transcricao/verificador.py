"""Verificador de português para a revisão da transcrição e das legendas dos Shorts (offline).

Procura, em ordem de utilidade:

* **ortografia**: palavra que não está no dicionário do português nem no vocabulário teológico
  (nomes, jargões), com sugestões ordenadas por frequência de uso;
* **repetição**: a mesma palavra duas vezes seguidas ("de de"), um erro típico da transcrição;
* **maiúscula**: frase que começa em minúscula depois de ponto final, e nomes de Deus, de Jesus ou
  de pessoas do vocabulário escritos em minúscula;
* **pontuação**: pontuação solta, com espaço antes ("palavra ,"), repetida (",,") ou parênteses
  sem par.

Não é um corretor gramatical completo (concordância e crase ficam de fora): é o que se acha sem
Java nem internet, rápido o bastante para rodar a cada clique.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from rapidfuzz import fuzz, process

from spurgeon.transcricao.dicionario import DicionarioBr
from spurgeon.transcricao.edicao import separar_pontuacao

TipoDoProblema = Literal["ortografia", "repeticao", "maiuscula", "pontuacao"]

MAXIMO_DE_SUGESTOES = 5
SEMELHANCA_COM_O_VOCABULARIO = 80
#: uma palavra fora do dicionário que aparece tantas vezes no texto é quase certamente um nome
OCORRENCIAS_DE_UM_NOME = 3
_FIM_DE_FRASE = (".", "?", "!", "…")
#: depois destas, o ponto não encerra a frase
_ABREVIACOES = frozenset(
    [
        "sr", "sra", "srs", "dr", "dra", "pr", "pra", "prof", "profa", "v", "vv", "vs", "cap",
        "caps", "cf", "ex", "etc", "pág", "pag", "art", "ap", "av",
    ]
)  # fmt: skip
#: nomes que levam inicial maiúscula mesmo estando no dicionário como palavra comum
_SAGRADOS = frozenset({"deus", "jesus", "cristo", "javé", "jeová", "yahweh"})
_SO_PONTUACAO = re.compile(r"^[,.;:!?…]+$")
#: "chamá -las": o Whisper separa o pronome do verbo
_PRONOME_SOLTO = re.compile(r"^-[^\W\d_]+[,.;:!?…]*$")
_COM_NUMERO = re.compile(r"\d")


@dataclass(frozen=True)
class Problema:
    linha: int  # posição da linha (segmento) na lista verificada
    inicio: int  # posição, dentro do texto da linha, do começo do trecho
    fim: int
    tipo: TipoDoProblema
    mensagem: str
    trecho: str
    sugestoes: tuple[str, ...] = ()

    @property
    def chave(self) -> str:
        """Identifica o problema para ignorá-lo de novo na próxima verificação."""
        return f"{self.tipo}:{self.trecho.casefold()}"


@dataclass(frozen=True)
class _Token:
    texto: str
    linha: int
    inicio: int
    fim: int

    @property
    def nucleo(self) -> str:
        return separar_pontuacao(self.texto)[1]


def _tokens(linhas: Sequence[str]) -> list[_Token]:
    achados: list[_Token] = []
    for numero, linha in enumerate(linhas):
        for casamento in re.finditer(r"\S+", linha):
            achados.append(_Token(casamento.group(), numero, casamento.start(), casamento.end()))
    return achados


def _com_a_inicial_de(modelo: str, palavra: str) -> str:
    if modelo[:1].isupper() and not modelo.isupper():
        return palavra[:1].upper() + palavra[1:]
    return palavra


class Verificador:
    def __init__(
        self,
        vocabulario: Iterable[str] = (),
        ignorar: Iterable[str] = (),
        dicionario: DicionarioBr | None = None,
    ):
        self._termos = [t for t in vocabulario if t.strip()]
        self._conhecidas = {parte.casefold() for t in self._termos for parte in t.split()}
        self._ignorar = {i.casefold() for i in ignorar}
        # nomes próprios do vocabulário (inicial maiúscula e fora do dicionário comum)
        self._nomes = {t.casefold(): t for t in self._termos if " " not in t and t[:1].isupper()}
        self.dicionario = dicionario or DicionarioBr()

    def preparar(self) -> None:
        """Carrega o dicionário (alguns segundos): chame em uma thread antes do primeiro uso."""
        self.dicionario.preparar()

    # --- dicionário ----------------------------------------------------------------------------
    def _conhecida(self, palavra: str) -> bool:
        minuscula = palavra.casefold()
        if minuscula in self._conhecidas or minuscula in self._ignorar:
            return True
        return self.dicionario.conhece(palavra)

    def sugerir(self, palavra: str, *, a_fundo: bool = False) -> tuple[str, ...]:
        """Palavras parecidas: o vocabulário primeiro, depois o dicionário por frequência.

        `a_fundo` acrescenta as sugestões do Hunspell, bem melhores mas lentas (segundos).
        """
        sugestoes: list[str] = []

        def acrescentar(texto: str) -> None:
            texto = _com_a_inicial_de(palavra, texto)
            if texto not in sugestoes and texto.casefold() != palavra.casefold():
                sugestoes.append(texto)

        if self._termos:
            achado = process.extractOne(
                palavra,
                self._termos,
                scorer=fuzz.WRatio,
                score_cutoff=SEMELHANCA_COM_O_VOCABULARIO,
                processor=str.casefold,
            )
            if achado:
                acrescentar(achado[0])
        for candidata in self.dicionario.candidatas(palavra):
            acrescentar(candidata)
        if a_fundo:
            for sugestao in self.dicionario.sugestoes_do_hunspell(palavra):
                acrescentar(sugestao)
        return tuple(sugestoes[:MAXIMO_DE_SUGESTOES])

    # --- verificação ---------------------------------------------------------------------------
    def verificar(self, linhas: Sequence[str]) -> list[Problema]:
        tokens = _tokens(linhas)
        problemas = [
            *self._ortografia(tokens),
            *self._repeticoes(tokens),
            *self._maiusculas(tokens),
            *self._pontuacao(tokens, linhas),
        ]
        problemas = [p for p in problemas if p.chave not in self._ignorar_chaves()]
        return sorted(problemas, key=lambda p: (p.linha, p.inicio, p.tipo))

    def _ignorar_chaves(self) -> set[str]:
        return {i for i in self._ignorar if ":" in i}

    def ignorar(self, problema: Problema) -> None:
        """Não avisar mais deste problema (palavra, ou o mesmo tipo de aviso para o trecho)."""
        if problema.tipo == "ortografia":
            self._ignorar.add(problema.trecho.casefold())
        else:
            self._ignorar.add(problema.chave)

    def aceitar_palavra(self, palavra: str) -> None:
        self._conhecidas.add(palavra.casefold())

    def _ortografia(self, tokens: list[_Token]) -> list[Problema]:
        ocorrencias = Counter(t.nucleo.casefold() for t in tokens)
        achados = []
        for t in tokens:
            abre, nucleo, _ = separar_pontuacao(t.texto)
            sem_ligacao = nucleo.replace("-", "").replace("’", "").replace("'", "")
            if len(nucleo) < 2 or _COM_NUMERO.search(nucleo) or not sem_ligacao.isalpha():
                continue
            if self._conhecida(nucleo):
                continue
            if ocorrencias[nucleo.casefold()] >= OCORRENCIAS_DE_UM_NOME:
                continue  # repetida no texto: é um nome, não um erro de digitação
            inicio = t.inicio + len(abre)
            achados.append(
                Problema(
                    t.linha,
                    inicio,
                    inicio + len(nucleo),
                    "ortografia",
                    f"“{nucleo}” não está no dicionário",
                    nucleo,
                    self.sugerir(nucleo),
                )
            )
        return achados

    def _repeticoes(self, tokens: list[_Token]) -> list[Problema]:
        achados = []
        for a, b in pairwise(tokens):
            if a.linha != b.linha or a.texto.endswith((",", ";", ":", *_FIM_DE_FRASE)):
                continue
            na, nb = a.nucleo, b.nucleo
            if len(na) >= 2 and na.casefold() == nb.casefold() and na.isalpha():
                achados.append(
                    Problema(
                        a.linha,
                        a.inicio,
                        b.fim,
                        "repeticao",
                        f"“{na}” repetida",
                        f"{na} {nb}",
                        (na,),
                    )
                )
        return achados

    def _maiusculas(self, tokens: list[_Token]) -> list[Problema]:
        achados = []
        for anterior, t in zip([None, *tokens[:-1]], tokens, strict=True):
            nucleo = t.nucleo
            if not nucleo or not nucleo[0].isalpha() or not nucleo[0].islower():
                continue
            inicio = t.inicio + len(separar_pontuacao(t.texto)[0])
            fim = inicio + len(nucleo)
            minuscula = nucleo.casefold()
            if anterior is not None and self._encerra_frase(anterior):
                achados.append(
                    Problema(
                        t.linha,
                        inicio,
                        fim,
                        "maiuscula",
                        "Frase começando em minúscula",
                        nucleo,
                        (nucleo[0].upper() + nucleo[1:],),
                    )
                )
            elif minuscula in _SAGRADOS or (
                minuscula in self._nomes and not self.dicionario.comum(minuscula)
            ):
                nome = self._nomes.get(minuscula) or nucleo[0].upper() + nucleo[1:]
                achados.append(
                    Problema(
                        t.linha,
                        inicio,
                        fim,
                        "maiuscula",
                        f"“{nome}” leva inicial maiúscula",
                        nucleo,
                        (nome,),
                    )
                )
        return achados

    @staticmethod
    def _encerra_frase(token: _Token) -> bool:
        if not token.texto.endswith(_FIM_DE_FRASE):
            return False
        nucleo = token.nucleo.casefold()
        return not (nucleo in _ABREVIACOES or (len(nucleo) <= 1 and token.texto.endswith(".")))

    def _pontuacao(self, tokens: list[_Token], linhas: Sequence[str]) -> list[Problema]:
        achados = []
        for anterior, t in zip([None, *tokens[:-1]], tokens, strict=True):
            colado = anterior is not None and anterior.linha == t.linha
            if colado and _SO_PONTUACAO.match(t.texto):
                achados.append(
                    Problema(
                        t.linha,
                        anterior.fim,
                        t.fim,
                        "pontuacao",
                        "Pontuação solta: ela deve ficar colada na palavra anterior",
                        f" {t.texto}",
                        (t.texto,),
                    )
                )
            elif colado and _PRONOME_SOLTO.match(t.texto):
                achados.append(
                    Problema(
                        t.linha,
                        anterior.fim,
                        t.fim,
                        "pontuacao",
                        "Hífen solto: deve ficar colado à palavra anterior",
                        f" {t.texto}",
                        (t.texto,),
                    )
                )
            elif re.search(r"[,;:]{2,}", t.texto) or re.search(r"[,;:][.]", t.texto):
                achados.append(
                    Problema(
                        t.linha,
                        t.inicio,
                        t.fim,
                        "pontuacao",
                        "Pontuação repetida",
                        t.texto,
                        (re.sub(r"([,;:])[,;:.]+", r"\1", t.texto),),
                    )
                )
        for numero, linha in enumerate(linhas):
            if linha.count("(") != linha.count(")"):
                posicao = (
                    linha.find("(") if linha.count("(") > linha.count(")") else linha.find(")")
                )
                achados.append(
                    Problema(
                        numero,
                        max(posicao, 0),
                        max(posicao, 0) + 1,
                        "pontuacao",
                        "Parêntese sem par",
                        "(" if "(" in linha[max(posicao, 0) :][:1] else ")",
                    )
                )
        return achados


_COMPARTILHADOS: dict[str, Verificador] = {}


def verificador_compartilhado(pastas) -> Verificador:
    """O verificador do aplicativo: um só por execução, para o dicionário carregar uma vez.

    Guarda também o que a pessoa mandou ignorar. `pastas` é o `core.config.Pastas` do aplicativo.
    """
    from spurgeon.transcricao.vocabulario import carregar_vocabulario

    chave = str(pastas.dicionario_pt_br)
    if chave not in _COMPARTILHADOS:
        _COMPARTILHADOS[chave] = Verificador(
            carregar_vocabulario(pastas.vocabulario),
            dicionario=DicionarioBr(pastas.dicionario_pt_br),
        )
    return _COMPARTILHADOS[chave]
