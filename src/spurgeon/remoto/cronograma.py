"""O cronograma do canal no celular: a última leitura do YouTube Studio, refeita em segundo plano.

Ler o canal abre o Studio no navegador da automação (uns 30 s), então a leitura roda numa thread e
o celular acompanha. A última leitura fica num arquivo ao lado do `remoto.json` (`cronograma.json`):
abrir a aba mostra na hora o que já se sabe, com a hora em que foi lido.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from spurgeon.canal.cronograma import ItemDoCronograma, comeco_do_mes_passado
from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.projeto import PastasProjeto

log = logging.getLogger(__name__)

NOME_DO_ARQUIVO = "cronograma.json"
#: subiu o formato do arquivo (2 = com o vídeo relacionado dos Shorts)? A leitura velha é refeita
VERSAO_DO_ARQUIVO = 2
#: depois disso a leitura é velha e abrir a aba já pede outra
VALIDADE_S = 30 * 60


def _sem_espacos(texto: str) -> str:
    return " ".join(texto.split()).casefold()


def itens_dos_projetos(pasta_projetos: Path | None, desde: date) -> list[ItemDoCronograma]:
    """Os Shorts que os projetos dizem estar agendados ou já postados (de `desde` em diante): o
    que o Spurgeon agendou ou postou (pelo celular ou pelo Studio do PC) aparece no calendário na
    hora, sem esperar a próxima leitura do canal. O id é o do YouTube quando a postagem foi pelo
    Spurgeon (`postagens.json`); o sermão relacionado, o que a postagem gravou."""
    from spurgeon.canal.cronograma import PROGRAMADO, PUBLICADO, SHORT, SPURGEON
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO as POSTAGENS
    from spurgeon.publicacao.agenda import RegistroDePostagens
    from spurgeon.remoto.dados import carregar_cortes
    from spurgeon.shorts.agendamento import ler_horario, ler_postado_em

    itens: list[ItemDoCronograma] = []
    if pasta_projetos is None or not pasta_projetos.is_dir():
        return itens
    for pasta in sorted(p for p in pasta_projetos.iterdir() if p.is_dir()):
        pastas = PastasProjeto(pasta)
        if not pastas.cortes_shorts.is_file():
            continue
        registro = RegistroDePostagens(pastas.work / POSTAGENS)
        for corte in carregar_cortes(pastas):
            if corte.agendado_para and not corte.postado:
                marcado = ler_horario(corte.agendado_para)
                situacao, quando = PROGRAMADO, marcado.replace(tzinfo=None) if marcado else None
            elif corte.postado and (lido := ler_postado_em(corte.postado_em)):
                dia, hora = lido
                situacao, quando = PUBLICADO, datetime.combine(dia, hora or datetime.min.time())
            else:
                continue
            if quando is None or quando.date() < desde:
                continue
            youtube = registro.do_corte(corte.id).get("youtube")
            detalhe = youtube.detalhe if youtube is not None else ""
            relacionado = (
                detalhe.split(":", 1)[1].strip() if detalhe.startswith("Vídeo relacionado:") else ""
            )
            id_no_youtube = youtube.id_remoto if youtube is not None else ""
            itens.append(ItemDoCronograma(
                id=id_no_youtube or f"spurgeon-{pasta.name}-{corte.id}",
                titulo=" ".join(corte.titulo.split()) or f"Corte {corte.id}",
                tipo=SHORT, situacao=situacao, quando=quando, duracao_s=round(corte.duracao),
                relacionado=relacionado, origem=SPURGEON,
            ))  # fmt: skip
    return itens


def juntar_com_os_projetos(
    do_canal: list[ItemDoCronograma], dos_projetos: list[ItemDoCronograma]
) -> list[ItemDoCronograma]:
    """O canal manda; um Short dos projetos só entra se o canal (ainda) não o tem: pelo id do
    YouTube, pelo título ou, se programado, por outro Short do canal programado no mesmo minuto (o
    título pode ter sido mudado lá no YouTube)."""
    shorts = [i for i in do_canal if i.tipo == "short"]
    ids = {i.id for i in do_canal}
    titulos = {_sem_espacos(i.titulo) for i in shorts}
    programados = {i.quando for i in shorts if i.situacao == "programado"}
    novos = [
        i for i in dos_projetos
        if i.id not in ids and _sem_espacos(i.titulo) not in titulos
        and not (i.situacao == "programado" and i.quando in programados)
    ]  # fmt: skip
    return sorted([*do_canal, *novos], key=lambda i: (i.quando, i.titulo))


def ler_do_canal(desde: date) -> list[ItemDoCronograma]:
    from spurgeon.canal.cronograma import ler_cronograma
    from spurgeon.canal.navegador import abrir_studio

    with abrir_studio(fora_da_tela=True) as studio:
        return ler_cronograma(studio, desde)


class Cronograma:
    def __init__(
        self,
        arquivo: Path | None = None,
        *,
        ler: Callable[[date], list[ItemDoCronograma]] = ler_do_canal,
        relogio: Callable[[], datetime] = datetime.now,
        pasta_projetos: Path | None = None,
    ):
        self.arquivo, self._ler, self._relogio = arquivo, ler, relogio
        #: de onde vêm os Shorts agendados pelo Spurgeon (None = só o canal)
        self.pasta_projetos = pasta_projetos
        self._trava = threading.Lock()
        self._thread: threading.Thread | None = None
        self.itens: list[ItemDoCronograma] = []
        self.lido_em: datetime | None = None
        self.erro = ""
        self._carregar()

    def _carregar(self) -> None:
        if self.arquivo is None or not self.arquivo.is_file():
            return
        try:
            dados = ler_json(self.arquivo)
            if dados.get("versao") != VERSAO_DO_ARQUIVO:
                return  # de antes de uma mudança no formato: lê o canal de novo
            self.itens = [ItemDoCronograma.de_json(d) for d in dados.get("itens", [])]
            lido = dados.get("lido_em")
            self.lido_em = datetime.fromisoformat(lido) if lido else None
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("O cronograma guardado não pôde ser lido (será lido de novo).", exc_info=1)
            self.itens, self.lido_em = [], None

    @property
    def rodando(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def velho(self) -> bool:
        return self.lido_em is None or (self._relogio() - self.lido_em).total_seconds() > VALIDADE_S

    def marcar_velho(self) -> None:
        """O canal mudou (uma postagem terminou): a próxima abertura da aba lê de novo."""
        self.lido_em = None

    def atualizar(self) -> bool:
        """Começa a ler o canal (False se já está lendo)."""
        with self._trava:
            if self.rodando:
                return False
            self.erro = ""
            self._thread = threading.Thread(target=self._rodar, name="cronograma", daemon=True)
            self._thread.start()
            return True

    def esperar(self, segundos: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(segundos)

    def _rodar(self) -> None:
        agora = self._relogio()
        try:
            itens = self._ler(comeco_do_mes_passado(agora.date()))
        except Exception as erro:  # o Studio fora do ar, a sessão que caiu, o canal errado…
            log.exception("Não deu para ler o cronograma do canal.")
            self.erro = f"Não consegui ler o canal no YouTube Studio: {erro}"
            return
        self.itens, self.lido_em = list(itens), agora
        if self.arquivo is not None:
            try:
                escrever_json_atomico(self.arquivo, {
                    "versao": VERSAO_DO_ARQUIVO,
                    "lido_em": agora.isoformat(timespec="seconds"),
                    "itens": [i.para_json() for i in self.itens],
                })  # fmt: skip
            except OSError:
                log.warning("O cronograma não pôde ser guardado.", exc_info=True)

    def estado(self) -> dict[str, Any]:
        hoje = self._relogio().date()
        desde = comeco_do_mes_passado(hoje)
        do_canal = [i for i in self.itens if i.quando.date() >= desde]
        try:
            locais = itens_dos_projetos(self.pasta_projetos, desde)
        except Exception:  # um projeto estragado não tira o calendário do ar
            log.warning("Não deu para ler os agendados dos projetos.", exc_info=True)
            locais = []
        itens = juntar_com_os_projetos(do_canal, locais)
        return {
            "itens": [i.para_json() for i in itens],
            "desde": desde.isoformat(),
            "hoje": hoje.isoformat(),
            "lido_em": self.lido_em.strftime("%Y-%m-%dT%H:%M") if self.lido_em else "",
            "velho": self.velho(),
            "rodando": self.rodando,
            "erro": self.erro,
        }
