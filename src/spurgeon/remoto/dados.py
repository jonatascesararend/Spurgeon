"""O que o celular vê dos projetos: só leitura, com as mesmas funções do Studio (Fase R1).

Nada aqui escreve no projeto. O id de um projeto é o nome da pasta dele; tudo o que vem do celular é
conferido contra a pasta de projetos (nada de caminho inventado: `achar_projeto`).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from spurgeon.core.arquivos import ler_json
from spurgeon.core.estado import StatusEtapa, carregar_estado
from spurgeon.core.projeto import (
    PastasProjeto,
    ProjetoExistente,
    ler_nome_e_data,
    listar_projetos,
    resumo_dos_resultados,
    situacao_do_projeto,
)
from spurgeon.render.shorts import (
    SITUACAO_NOVO,
    arquivo_do_short_pronto,
    assinatura_visual,
    ler_registro,
    planejar_shorts,
    situacao_do_short,
)
from spurgeon.shorts.agendamento import ler_horario, rotulo_do_horario, rotulo_do_postado
from spurgeon.shorts.dados import Corte, cor_da_categoria, rotulo_da_categoria

#: linhas do fim do `processamento.log` que o celular mostra
LINHAS_DO_LOG = 40
_BYTES_DO_FIM_DO_LOG = 16_000


class ProjetoNaoEncontrado(LookupError):
    pass


@cache
def nomes_das_etapas() -> dict[str, str]:
    """`id` → nome da etapa para gente ("Transcrevendo o áudio (Whisper)")."""
    from spurgeon.core.etapas import etapas_registradas

    return {e.id: e.nome for e in etapas_registradas()}


def resumo_do_projeto(projeto: ProjetoExistente) -> dict[str, Any]:
    return {
        "id": projeto.pasta.name,
        "nome": projeto.nome,
        "data": projeto.data.isoformat(),
        "situacao": projeto.situacao,
        "detalhes": projeto.detalhes,
        "modificado": projeto.modificado,
    }


def projetos(pasta_projetos: Path) -> list[dict[str, Any]]:
    return [resumo_do_projeto(p) for p in listar_projetos(pasta_projetos)]


def achar_projeto(pasta_projetos: Path, ident: str) -> PastasProjeto:
    """A pasta do projeto `ident` (o nome da pasta), só se ela está DENTRO da pasta de projetos e é
    um projeto (`AAAA-MM-DD_Nome` com `estado.json`)."""
    if not ident or ident != Path(ident).name or ident in (".", ".."):
        raise ProjetoNaoEncontrado(ident)
    base = Path(pasta_projetos).resolve()
    pasta = (base / ident).resolve()
    if (
        pasta.parent != base
        or ler_nome_e_data(pasta) is None
        or not (pasta / "estado.json").is_file()
    ):
        raise ProjetoNaoEncontrado(ident)
    return PastasProjeto(pasta)


def fim_do_log(caminho: Path, linhas: int = LINHAS_DO_LOG) -> list[str]:
    try:
        with caminho.open("rb") as arquivo:
            arquivo.seek(0, 2)
            tamanho = arquivo.tell()
            arquivo.seek(max(0, tamanho - _BYTES_DO_FIM_DO_LOG))
            texto = arquivo.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    todas = texto.splitlines()
    if len(todas) > 1 and tamanho > _BYTES_DO_FIM_DO_LOG:
        todas = todas[1:]  # a primeira pode ter vindo cortada no meio
    return todas[-linhas:]


def andamento(pastas: PastasProjeto) -> dict[str, Any]:
    """As etapas do projeto e o fim do log: o que o celular mostra enquanto o PC processa."""
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        raise ProjetoNaoEncontrado(pastas.raiz.name)
    nomes = nomes_das_etapas()
    etapas = [
        {
            "id": e.id,
            "nome": nomes.get(e.id, e.id),
            "status": e.status.value,
            "progresso": round(e.progresso, 3),
            "erro": e.erro or "",
        }
        for e in estado.etapas
    ]
    return {
        "situacao": situacao_do_projeto(estado),
        "em_andamento": any(e.status == StatusEtapa.EM_ANDAMENTO for e in estado.etapas),
        "esperando_revisao": any(e.status == StatusEtapa.AGUARDANDO_REVISAO for e in estado.etapas),
        "atualizado_em": estado.atualizado_em.isoformat(),
        "etapas": etapas,
        "log": fim_do_log(pastas.log),
    }


def detalhe_do_projeto(pastas: PastasProjeto) -> dict[str, Any]:
    identificado = ler_nome_e_data(pastas.raiz)
    nome, dia = identificado if identificado else (pastas.raiz.name, None)
    return {
        "id": pastas.raiz.name,
        "nome": nome,
        "data": dia.isoformat() if dia else "",
        "resultados": resumo_dos_resultados(pastas),
        "tem_video_longo": pastas.video_longo.is_file(),
        "andamento": andamento(pastas),
    }


def carregar_cortes(pastas: PastasProjeto) -> list[Corte]:
    """Os cortes salvos; lista vazia se o projeto não chegou lá (ou o arquivo está estragado)."""
    try:
        dados = ler_json(pastas.cortes_shorts)
        return [Corte.de_dict(d) for d in dados] if isinstance(dados, list) else []
    except (OSError, ValueError, TypeError):
        return []


@dataclass(frozen=True)
class ShortNoDisco:
    corte: Corte
    numero: int | None  # a posição entre os marcados (None = desmarcado)
    arquivo: Path | None  # o `.mp4` renderizado, onde estiver
    #: `novo` (nunca renderizado), `pronto` ou `alterado` (mudou depois do render), como no Studio
    situacao: str = SITUACAO_NOVO


def shorts_no_disco(pastas: PastasProjeto) -> list[ShortNoDisco]:
    """Todos os cortes, na ordem do sermão, com o número e o arquivo renderizado de cada um."""
    cortes = carregar_cortes(pastas)
    planejados = {p.corte.id: p for p in planejar_shorts(cortes)}
    registro = ler_registro(pastas.shorts_renderizados)
    estado = carregar_estado(pastas.raiz)
    opcoes = estado.opcoes if estado is not None else None
    achados: list[ShortNoDisco] = []
    for corte in sorted(cortes, key=lambda c: c.inicio):
        plano = planejados.get(corte.id)
        if plano is not None:
            arquivo = arquivo_do_short_pronto(pastas, plano, registro)
        else:  # desmarcado: só o que o registro diz (costuma estar em `desmarcados/`)
            entrada = registro.get(str(corte.id))
            candidato = pastas.shorts / entrada["arquivo"] if entrada else None
            arquivo = candidato if candidato is not None and candidato.is_file() else None
        situacao = situacao_do_short(
            corte, registro.get(str(corte.id)), arquivo is not None,
            com_legenda=bool(opcoes and opcoes.legendas_dinamicas),
            visual=assinatura_visual(opcoes) if opcoes is not None else None,
        )  # fmt: skip
        achados.append(ShortNoDisco(corte, plano.numero if plano else None, arquivo, situacao))
    return achados


def _rotulo_do_agendado(texto: str) -> str:
    quando = ler_horario(texto)
    return rotulo_do_horario(quando) if quando else ""


def resumo_do_short(short: ShortNoDisco) -> dict[str, Any]:
    c = short.corte
    return {
        "id": c.id,
        "numero": short.numero,
        "titulo": c.titulo or c.slug or f"Corte {c.id}",
        "descricao": c.descricao,
        "hashtags": list(c.hashtags),
        "categoria": {
            "chave": c.categoria,
            "rotulo": rotulo_da_categoria(c.categoria),
            "cor": cor_da_categoria(c.categoria),
        },
        "nota": round(c.nota, 1),
        "inicio": round(c.inicio, 2),
        "fim": round(c.fim, 2),
        "duracao": round(c.duracao, 1),
        "incluir": c.incluir,
        "reserva": c.reserva,
        "origem": c.origem,
        "postado": c.postado,
        "postado_rotulo": rotulo_do_postado(c.postado_em) if c.postado else "",
        "agendado_para": c.agendado_para,
        "agendado_rotulo": _rotulo_do_agendado(c.agendado_para),
        "renderizado": short.arquivo is not None,
        "situacao": short.situacao,
        "texto": c.texto,
        "legenda": c.legenda,
    }


def shorts(pastas: PastasProjeto) -> list[dict[str, Any]]:
    """Os Shorts para o celular, cada um com o que já foi feito no YouTube (`postagens.json`)."""
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, RegistroDePostagens

    registro = RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO)
    lista = []
    for short in shorts_no_disco(pastas):
        resumo = resumo_do_short(short)
        youtube = registro.do_corte(short.corte.id).get("youtube")
        resumo["youtube"] = (
            {"estado": youtube.estado, "url": youtube.url, "quando": youtube.quando}
            if youtube
            else None
        )
        lista.append(resumo)
    return lista


def arquivo_do_short(pastas: PastasProjeto, id_do_corte: int) -> Path | None:
    for short in shorts_no_disco(pastas):
        if short.corte.id == id_do_corte:
            return short.arquivo
    return None


def a_renderizar(pastas: PastasProjeto) -> list[ShortNoDisco]:
    """Os Shorts marcados que ainda não foram renderizados ou mudaram depois do render."""
    return [s for s in shorts_no_disco(pastas) if s.numero is not None and s.situacao != "pronto"]


def video_original(pastas: PastasProjeto) -> Path | None:
    """O vídeo original do projeto, se ainda está no PC (renderizar precisa dele)."""
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        return None
    caminho = Path(estado.entrada.caminho)
    return caminho if caminho.is_file() else None
