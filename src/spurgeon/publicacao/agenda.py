"""As postagens de cada projeto e o agendador local.

`postagens.json` (em `_work/`) guarda, por Short e por rede, o que aconteceu: publicado, agendado na
rede, na fila do Spurgeon, nos rascunhos, preparado ou com erro. O agendador local cuida só do que a
rede não agenda (Instagram): a cada minuto, enquanto o aplicativo está aberto, ele avança a fila.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.erros import Cancelado, ErroSpurgeon
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDeRede, ReconectarNecessario
from spurgeon.publicacao.intermediario import UploadPost
from spurgeon.publicacao.meta import Facebook, Instagram
from spurgeon.publicacao.modelo import (
    AGENDADO,
    ASSISTIDO,
    ENVIADO,
    ERRO,
    NA_FILA,
    RASCUNHO,
    Pacote,
    PedidoDeCancelar,
    Plataforma,
    Relogio,
    Resultado,
    agora_local,
)
from spurgeon.publicacao.rede import Transporte
from spurgeon.publicacao.tiktok import TikTok
from spurgeon.publicacao.youtube import YouTube

NOME_DO_ARQUIVO = "postagens.json"
#: a ordem em que as redes aparecem na tela
CLASSES_DAS_REDES: tuple[type[Plataforma], ...] = (YouTube, Instagram, Facebook, TikTok, UploadPost)
#: estados que já cumpriram (ou guardam) a postagem: publicar de novo duplicaria
ESTADOS_QUE_BLOQUEIAM = frozenset({ENVIADO, AGENDADO, NA_FILA, RASCUNHO})

AoProgresso = Callable[[str, float], None]


def criar_plataformas(
    cofre: Cofre | None = None, transporte: Transporte | None = None, relogio: Relogio = agora_local
) -> dict[str, Plataforma]:
    """Uma instância de cada rede, na ordem da tela (`chave` → plataforma)."""
    cofre = cofre or Cofre()
    return {c.chave: c(cofre, transporte, relogio=relogio) for c in CLASSES_DAS_REDES}


class RegistroDePostagens:
    """`{id do corte: {rede: Resultado}}` num arquivo do projeto."""

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self._trava = threading.Lock()

    def _ler(self) -> dict[str, dict[str, dict]]:
        try:
            dados = ler_json(self.caminho)
        except (OSError, ValueError):
            return {}
        return dados if isinstance(dados, dict) else {}

    def do_corte(self, id_do_corte: int) -> dict[str, Resultado]:
        with self._trava:
            bruto = self._ler().get(str(id_do_corte), {})
        return {rede: Resultado.de_dict(d) for rede, d in bruto.items() if isinstance(d, dict)}

    def gravar(self, id_do_corte: int, resultado: Resultado) -> None:
        with self._trava:
            dados = self._ler()
            dados.setdefault(str(id_do_corte), {})[resultado.plataforma] = resultado.para_dict()
            escrever_json_atomico(self.caminho, dados)

    def remover(self, id_do_corte: int, rede: str) -> None:
        with self._trava:
            dados = self._ler()
            if dados.get(str(id_do_corte), {}).pop(rede, None) is not None:
                if not dados[str(id_do_corte)]:
                    dados.pop(str(id_do_corte))
                escrever_json_atomico(self.caminho, dados)

    def na_fila(self) -> list[tuple[int, Resultado]]:
        """Tudo o que espera o agendador local, do horário mais cedo ao mais tarde."""
        with self._trava:
            dados = self._ler()
        fila = [
            (int(id_do_corte), Resultado.de_dict(d))
            for id_do_corte, redes in dados.items()
            for d in redes.values()
            if isinstance(d, dict) and d.get("estado") == NA_FILA
        ]
        return sorted(fila, key=lambda item: item[1].quando)


def publicar_em(
    plataformas: list[Plataforma],
    id_do_corte: int,
    pacote: Pacote,
    quando: datetime | None,
    registro: RegistroDePostagens,
    *,
    forcar: bool = False,
    ao_progresso: AoProgresso | None = None,
    cancelado: PedidoDeCancelar | None = None,
) -> list[Resultado]:
    """Publica (ou agenda) em cada rede, uma de cada vez; uma que falha não impede as outras.

    Quem já tem a postagem guardada (publicada, agendada, na fila ou nos rascunhos) fica como está,
    a menos que `forcar`. Cada resultado, inclusive o erro, é gravado no registro do projeto.
    """
    anteriores = registro.do_corte(id_do_corte)
    resultados: list[Resultado] = []
    for plataforma in plataformas:
        anterior = anteriores.get(plataforma.chave)
        if anterior is not None and anterior.estado in ESTADOS_QUE_BLOQUEIAM and not forcar:
            resultados.append(anterior)
            continue
        if cancelado is not None and cancelado():
            raise Cancelado("Envio cancelado.")

        def andamento(fracao: float, chave: str = plataforma.chave) -> None:
            if ao_progresso:
                ao_progresso(chave, fracao)

        try:
            resultado = plataforma.publicar(
                pacote, quando, ao_progresso=andamento, cancelado=cancelado
            )
        except Cancelado:
            raise
        except ErroSpurgeon as erro:
            resultado = Resultado(plataforma.chave, ERRO, detalhe=str(erro))
        registro.gravar(id_do_corte, resultado)
        resultados.append(resultado)
    return resultados


class Agendador:
    """Avança a fila local: `rodar_pendentes` é chamado a cada minuto pela janela."""

    def __init__(
        self,
        plataformas: dict[str, Plataforma],
        registro: RegistroDePostagens,
        pacote_do_corte: Callable[[int], Pacote | None],
    ):
        self.plataformas = plataformas
        self.registro = registro
        self.pacote_do_corte = pacote_do_corte

    def rodar_pendentes(
        self, *, cancelado: PedidoDeCancelar | None = None
    ) -> list[tuple[int, Resultado]]:
        """Dá o próximo passo de cada postagem da fila. Devolve as que mudaram de estado."""
        mudaram: list[tuple[int, Resultado]] = []
        for id_do_corte, resultado in self.registro.na_fila():
            plataforma = self.plataformas.get(resultado.plataforma)
            pacote = self.pacote_do_corte(id_do_corte)
            if plataforma is None:
                continue
            if pacote is None:
                novo = Resultado(
                    resultado.plataforma, ERRO, quando=resultado.quando,
                    detalhe="O arquivo do Short não existe mais (renderize-o de novo).",
                )  # fmt: skip
            else:
                try:
                    novo = plataforma.retomar(resultado, pacote, cancelado=cancelado)
                except Cancelado:
                    raise
                except ReconectarNecessario as erro:
                    novo = Resultado(resultado.plataforma, ERRO, quando=resultado.quando,
                                     detalhe=str(erro))  # fmt: skip
                except ErroDeRede as erro:
                    if erro.passageiro:
                        continue  # sem internet agora: tenta de novo no próximo minuto
                    novo = Resultado(resultado.plataforma, ERRO, quando=resultado.quando,
                                     detalhe=str(erro))  # fmt: skip
                except ErroSpurgeon as erro:
                    novo = Resultado(resultado.plataforma, ERRO, quando=resultado.quando,
                                     detalhe=str(erro))  # fmt: skip
            if novo.para_dict() != resultado.para_dict():
                self.registro.gravar(id_do_corte, novo)
                if novo.estado != resultado.estado:
                    mudaram.append((id_do_corte, novo))
        return mudaram


def resumo_das_redes(resultados: dict[str, Resultado]) -> str:
    """`YouTube agendado · Instagram na fila`: uma linha para a lista do Studio."""
    nomes = {c.chave: c.nome.split(" ")[0] for c in CLASSES_DAS_REDES}
    partes = [
        f"{nomes.get(rede, rede)} {r.rotulo}"
        for rede, r in resultados.items()
        if r.estado != ASSISTIDO or r.dados
    ]
    return " · ".join(partes)
