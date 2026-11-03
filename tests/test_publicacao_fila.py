"""A fila local varrendo todos os projetos."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from spurgeon.core.arquivos import escrever_json_atomico
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, RegistroDePostagens
from spurgeon.publicacao.fila import projetos_com_fila, rodar_fila
from spurgeon.publicacao.modelo import ENVIADO, NA_FILA, Plataforma, Resultado
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.etapas import gravar_cortes

BRASILIA = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 2, 12, 0, tzinfo=BRASILIA)


class Passo(Plataforma):
    chave = "instagram"
    nome = "Instagram"

    def __init__(self):
        super().__init__(None)  # type: ignore[arg-type]
        self.pacotes = []

    @property
    def conectada(self) -> bool:
        return True

    def retomar(self, resultado, pacote, *, ao_progresso=None, cancelado=None):
        self.pacotes.append(pacote)
        return Resultado("instagram", ENVIADO, "M1")


def _projeto(raiz, nome: str, *, na_fila: bool, com_arquivo: bool = True) -> PastasProjeto:
    pastas = PastasProjeto(raiz / nome)
    pastas.criar()
    pastas.shorts.mkdir(parents=True, exist_ok=True)
    corte = Corte(
        id=3, inicio=0.0, fim=40.0, texto="t", categoria="outra", nota=80.0, detalhe={},
        titulo=f"Título de {nome}", descricao="d", hashtags=["fé"], slug="s",
    )  # fmt: skip
    gravar_cortes(pastas.cortes_shorts, [corte])
    if com_arquivo:
        (pastas.shorts / "short_01_s.mp4").write_bytes(b"v")
        escrever_json_atomico(
            pastas.shorts_renderizados, {"3": {"arquivo": "short_01_s.mp4", "assinatura": "a"}}
        )
    if na_fila:
        RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO).gravar(
            3, Resultado("instagram", NA_FILA, quando=AGORA.isoformat())
        )
    return pastas


def test_so_os_projetos_com_fila_sao_abertos(tmp_path):
    com = _projeto(tmp_path, "com_fila", na_fila=True)
    _projeto(tmp_path, "sem_fila", na_fila=False)
    (tmp_path / "arquivo_solto.txt").write_text("x", "utf-8")
    assert [p.raiz for p in projetos_com_fila(tmp_path)] == [com.raiz]
    assert projetos_com_fila(tmp_path / "não existe") == []


def test_rodar_fila_avanca_cada_projeto_e_avisa_o_que_mudou(tmp_path):
    _projeto(tmp_path, "um", na_fila=True)
    _projeto(tmp_path, "dois", na_fila=True)
    passo = Passo()
    avisos: list[tuple[str, int, str]] = []
    mudaram = rodar_fila(
        {"instagram": passo}, tmp_path,
        ao_mudar=lambda pastas, id_, r: avisos.append((pastas.raiz.name, id_, r.estado)),
    )  # fmt: skip
    assert mudaram == 2
    assert sorted(avisos) == [("dois", 3, ENVIADO), ("um", 3, ENVIADO)]
    assert {p.titulo for p in passo.pacotes} == {"Título de um", "Título de dois"}
    assert projetos_com_fila(tmp_path) == []  # a fila esvaziou
    assert rodar_fila({"instagram": passo}, tmp_path) == 0


def test_projeto_com_cortes_ilegiveis_nao_derruba_os_outros(tmp_path):
    ruim = _projeto(tmp_path, "a_ruim", na_fila=True)
    ruim.cortes_shorts.write_text("{quebrado", "utf-8")
    _projeto(tmp_path, "b_boa", na_fila=True)
    assert rodar_fila({"instagram": Passo()}, tmp_path) == 1


def test_short_sem_arquivo_vira_erro_registrado(tmp_path):
    sem = _projeto(tmp_path, "sem_arquivo", na_fila=True, com_arquivo=False)
    assert rodar_fila({"instagram": Passo()}, tmp_path) == 1
    guardado = RegistroDePostagens(sem.work / NOME_DO_ARQUIVO).do_corte(3)["instagram"]
    assert guardado.estado == "erro" and "renderize" in guardado.detalhe


def test_corte_que_nao_existe_mais_vira_erro(tmp_path):
    pastas = _projeto(tmp_path, "p", na_fila=True)
    gravar_cortes(pastas.cortes_shorts, [replace(Corte.de_dict({
        "id": 99, "inicio": 0.0, "fim": 5.0, "texto": "t", "categoria": "outra", "nota": 1.0,
        "detalhe": {},
    }))])  # fmt: skip
    assert rodar_fila({"instagram": Passo()}, tmp_path) == 1


def test_sem_plataformas_a_fila_fica_intacta(tmp_path):
    _projeto(tmp_path, "p", na_fila=True)
    assert rodar_fila({}, tmp_path) == 0
    assert len(projetos_com_fila(tmp_path)) == 1


@pytest.mark.parametrize("nome", ["postagens.json"])
def test_nome_do_arquivo_da_fila(nome):
    assert nome == NOME_DO_ARQUIVO
