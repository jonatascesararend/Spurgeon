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

from spurgeon.canal.cronograma import SPURGEON as SPURGEON_ORIGEM
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
                relacionado=relacionado, origem=SPURGEON, projeto=pasta.name, corte=corte.id,
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


def _parecenca(a: str, b: str) -> float:
    """O quanto dois títulos se parecem (0 a 1), sem ligar para maiúsculas, espaços e pontuação."""
    from difflib import SequenceMatcher

    def limpo(texto: str) -> str:
        return " ".join("".join(c if c.isalnum() else " " for c in texto.casefold()).split())

    return SequenceMatcher(None, limpo(a), limpo(b)).ratio()


#: títulos parecidos a partir disto viram sugestão de "mesclar" (o nome mudou um pouco no YouTube)
PARECENCA_MINIMA = 0.5
MAXIMO_DE_SUGESTOES = 3


def parecidos_no_canal(
    item: ItemDoCronograma, do_canal: list[ItemDoCronograma], ja_ligados: set[str]
) -> list[str]:
    """Os Shorts do canal que podem ser este Short do Spurgeon com outro nome: título parecido, ou
    o mesmo dia; os mais parecidos primeiro. Os já ligados a outro Short do Spurgeon ficam fora."""
    notas = []
    for outro in do_canal:
        if outro.tipo != "short" or outro.id in ja_ligados:
            continue
        nota = _parecenca(item.titulo, outro.titulo)
        if outro.quando.date() == item.quando.date():
            nota += 0.25
        if nota >= PARECENCA_MINIMA:
            notas.append((nota, outro.id))
    return [i for _n, i in sorted(notas, reverse=True)[:MAXIMO_DE_SUGESTOES]]


def acertar_com_o_item(pastas: PastasProjeto, id_do_corte: int, item: ItemDoCronograma) -> bool:
    """Faz o Short do Spurgeon dizer o que o canal diz deste vídeo: o id do YouTube, o horário
    programado (o do YouTube manda: ele pode ter sido mudado à mão lá) ou que já saiu. Devolve
    True se mudou alguma coisa."""
    from spurgeon.canal.cronograma import PROGRAMADO
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO as POSTAGENS
    from spurgeon.publicacao.agenda import RegistroDePostagens
    from spurgeon.publicacao.modelo import AGENDADO, ENVIADO, Resultado
    from spurgeon.remoto.dados import carregar_cortes
    from spurgeon.shorts.agendamento import FORMATO_GUARDADO
    from spurgeon.shorts.etapas import gravar_campos_do_corte

    corte = next((c for c in carregar_cortes(pastas) if c.id == id_do_corte), None)
    if corte is None:
        return False
    registro = RegistroDePostagens(pastas.work / POSTAGENS)
    anterior = registro.do_corte(id_do_corte).get("youtube")
    hora = item.quando.strftime(FORMATO_GUARDADO)
    programado = item.situacao == PROGRAMADO
    estado = AGENDADO if programado else ENVIADO
    quando_iso = item.quando.astimezone().isoformat()
    mudou = False
    if (
        anterior is None or anterior.id_remoto != item.id or anterior.estado != estado
        or _mesmo_minuto(anterior.quando) != hora
    ):  # fmt: skip
        resultado = Resultado(
            "youtube", estado, item.id, f"https://youtube.com/shorts/{item.id}", quando_iso,
            anterior.detalhe if anterior is not None else "",
        )  # fmt: skip
        registro.gravar(id_do_corte, resultado)
        mudou = True
    if programado and (corte.agendado_para != hora or corte.postado):
        gravar_campos_do_corte(pastas.cortes_shorts, id_do_corte, agendado_para=hora,
                               postado=False, postado_em="")  # fmt: skip
        mudou = True
    elif not programado and (not corte.postado or corte.agendado_para):
        gravar_campos_do_corte(pastas.cortes_shorts, id_do_corte, agendado_para="",
                               postado=True, postado_em=hora)  # fmt: skip
        mudou = True
    return mudou


def _mesmo_minuto(iso: str) -> str:
    from spurgeon.shorts.agendamento import FORMATO_GUARDADO

    try:
        return datetime.fromisoformat(iso).astimezone().strftime(FORMATO_GUARDADO)
    except ValueError:
        return ""


def acertar_com_o_canal(
    pasta_projetos: Path | None,
    do_canal: list[ItemDoCronograma],
    *,
    pode_mover: Callable[[], bool] = lambda: True,
) -> list[str]:
    """Depois de ler o canal: cada Short do Spurgeon que está no canal (pelo id do YouTube no
    registro ou, sem id, pelo mesmo título exato) passa a ter o horário que o YouTube mostra
    (mudado à mão lá, por exemplo) e, se já saiu, vira postado. Devolve o que mudou ("título:
    horário"), para o registro e o aviso."""
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO as POSTAGENS
    from spurgeon.publicacao.agenda import RegistroDePostagens
    from spurgeon.remoto.dados import carregar_cortes
    from spurgeon.render.shorts import aplicar_marcas_de_postado

    mudancas: list[str] = []
    if pasta_projetos is None or not pasta_projetos.is_dir():
        return mudancas
    por_id = {i.id: i for i in do_canal if i.tipo == "short"}
    por_titulo: dict[str, ItemDoCronograma] = {}
    for item in por_id.values():
        por_titulo.setdefault(_sem_espacos(item.titulo), item)
    projetos = [
        PastasProjeto(p) for p in sorted(pasta_projetos.iterdir())
        if p.is_dir() and PastasProjeto(p).cortes_shorts.is_file()
    ]  # fmt: skip
    cortes = {pastas.raiz: carregar_cortes(pastas) for pastas in projetos}
    ligados: set[str] = set()  # os Shorts do canal que já têm o seu no Spurgeon
    for pastas in projetos:
        registro = RegistroDePostagens(pastas.work / POSTAGENS)
        for corte in cortes[pastas.raiz]:
            youtube = registro.do_corte(corte.id).get("youtube")
            if youtube is not None and youtube.id_remoto:
                ligados.add(youtube.id_remoto)
    for pastas in projetos:
        registro = RegistroDePostagens(pastas.work / POSTAGENS)
        mudou_aqui = False
        for corte in cortes[pastas.raiz]:
            youtube = registro.do_corte(corte.id).get("youtube")
            if youtube is not None and youtube.id_remoto:
                item = por_id.get(youtube.id_remoto)
            elif (corte.agendado_para or corte.postado) and corte.titulo:
                item = por_titulo.get(_sem_espacos(corte.titulo))
                if item is not None and item.id in ligados:
                    item = None
            else:
                item = None
            if item is None:
                continue
            ligados.add(item.id)
            if acertar_com_o_item(pastas, corte.id, item):
                mudou_aqui = True
                nome = corte.titulo or f"Corte {corte.id}"
                mudancas.append(f"{nome}: {item.quando:%d/%m às %H:%M}")
        if mudou_aqui and pode_mover():
            try:
                aplicar_marcas_de_postado(pastas, carregar_cortes(pastas))
            except OSError:
                log.warning("Não deu para mover os Shorts de %s.", pastas.raiz.name, exc_info=True)
    return mudancas


class MesclaInvalida(ValueError):
    pass


def mesclar(
    pasta_projetos: Path,
    do_canal: list[ItemDoCronograma],
    projeto: str,
    id_do_corte: int,
    id_no_canal: str,
    *,
    pode_mover: Callable[[], bool] = lambda: True,
) -> ItemDoCronograma:
    """Junta o Short do Spurgeon (projeto e corte) com o Short do canal `id_no_canal` (o mesmo
    vídeo, com o nome um pouco diferente): o Spurgeon passa a saber o id dele no YouTube e fica
    com o horário do YouTube. Devolve o item do canal."""
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO as POSTAGENS
    from spurgeon.publicacao.agenda import RegistroDePostagens
    from spurgeon.remoto.dados import ProjetoNaoEncontrado, achar_projeto, carregar_cortes
    from spurgeon.render.shorts import aplicar_marcas_de_postado

    item = next((i for i in do_canal if i.id == id_no_canal and i.tipo == "short"), None)
    if item is None:
        raise MesclaInvalida(
            "Este Short não está na última leitura do canal: atualize o cronograma."
        )
    try:
        pastas = achar_projeto(pasta_projetos, projeto)
    except ProjetoNaoEncontrado as erro:
        raise MesclaInvalida("O projeto deste Short não está mais no PC.") from erro
    cortes = carregar_cortes(pastas)
    if not any(c.id == id_do_corte for c in cortes):
        raise MesclaInvalida("Este corte não existe mais no projeto.")
    registro = RegistroDePostagens(pastas.work / POSTAGENS)
    for corte in cortes:
        youtube = registro.do_corte(corte.id).get("youtube")
        if corte.id != id_do_corte and youtube is not None and youtube.id_remoto == id_no_canal:
            raise MesclaInvalida(
                f"Este Short do canal já está mesclado com “{corte.titulo or corte.id}”."
            )
    acertar_com_o_item(pastas, id_do_corte, item)
    if pode_mover():
        try:
            aplicar_marcas_de_postado(pastas, carregar_cortes(pastas))
        except OSError:
            log.warning("Não deu para mover o Short mesclado de %s.", projeto, exc_info=True)
    return item


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
        #: dá para mover os `.mp4` agora (nenhum processamento rodando)? O `servir` liga ao PC
        self.pode_mover: Callable[[], bool] = lambda: True
        #: o que a última leitura acertou nos projetos ("título: horário")
        self.acertados: list[str] = []
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
        try:  # o horário do YouTube manda (pode ter sido mudado à mão no Studio)
            self.acertados = acertar_com_o_canal(
                self.pasta_projetos, self.itens, pode_mover=self.pode_mover
            )
        except Exception:
            log.warning("Não deu para acertar os projetos com o canal.", exc_info=True)
            self.acertados = []
        if self.acertados:
            log.info("Horários acertados com o YouTube: %s", "; ".join(self.acertados))
        if self.arquivo is not None:
            try:
                escrever_json_atomico(self.arquivo, {
                    "versao": VERSAO_DO_ARQUIVO,
                    "lido_em": agora.isoformat(timespec="seconds"),
                    "itens": [i.para_json() for i in self.itens],
                })  # fmt: skip
            except OSError:
                log.warning("O cronograma não pôde ser guardado.", exc_info=True)

    def mesclar(self, projeto: str, id_do_corte: int, id_no_canal: str) -> ItemDoCronograma:
        if self.pasta_projetos is None:
            raise MesclaInvalida("Sem a pasta dos projetos.")
        return mesclar(self.pasta_projetos, self.itens, projeto, id_do_corte, id_no_canal,
                       pode_mover=self.pode_mover)  # fmt: skip

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
        ligados = {i.id for i in locais if not i.id.startswith("spurgeon-")}
        jsons = []
        for i in itens:
            dado = i.para_json()
            if i.origem == SPURGEON_ORIGEM and i.corte is not None:
                dado["parecidos"] = parecidos_no_canal(i, do_canal, ligados)
            jsons.append(dado)
        return {
            "itens": jsons,
            "desde": desde.isoformat(),
            "hoje": hoje.isoformat(),
            "lido_em": self.lido_em.strftime("%Y-%m-%dT%H:%M") if self.lido_em else "",
            "velho": self.velho(),
            "rodando": self.rodando,
            "erro": self.erro,
            "acertados": list(self.acertados),
        }
