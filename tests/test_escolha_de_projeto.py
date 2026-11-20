"""A lista de projetos da abertura: o que ela lista (núcleo) e a caixa em que se escolhe."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.estado import (
    Estado,
    EstadoEtapa,
    StatusEtapa,
    carregar_estado,
    salvar_estado,
)
from spurgeon.core.projeto import (
    ProjetoExistente,
    criar_ou_retomar_projeto,
    listar_projetos,
    situacao_do_projeto,
)
from spurgeon.ui.escolha_de_projeto import JanelaDeProjetos
from tests import test_janela as base

janela = base.janela  # a janela compartilhada dos testes da interface
gui = pytest.mark.gui


def _projeto(raiz: Path, video: Path, nome: str, dia: date) -> Path:
    return criar_ou_retomar_projeto(raiz, video, nome, dia, OpcoesProcessamento()).pastas.raiz


def _mexido_em(pasta: Path, segundos: float) -> None:
    arquivo = pasta / "estado.json"
    os.utime(arquivo, (segundos, segundos))


def _estado(*status: StatusEtapa, base_do_estado: Estado) -> Estado:
    base_do_estado.etapas = [EstadoEtapa(id=f"e{i}", status=s) for i, s in enumerate(status)]
    return base_do_estado


# --- o que a lista mostra -----------------------------------------------------------------------
def test_os_projetos_vem_do_mais_recente_para_o_mais_antigo(tmp_path, video_falso):
    raiz = tmp_path / "Projetos"
    velho = _projeto(raiz, video_falso, "Sermão velho", date(2026, 9, 6))
    novo = _projeto(raiz, video_falso, "Sermão novo", date(2026, 10, 4))
    meio = _projeto(raiz, video_falso, "Sermão do meio", date(2026, 9, 20))
    _mexido_em(velho, 1_000_000)
    _mexido_em(meio, 2_000_000)
    _mexido_em(novo, 3_000_000)  # o que mexeu por último vem primeiro, não o de data mais nova

    projetos = listar_projetos(raiz)
    assert [p.pasta for p in projetos] == [novo, meio, velho]
    assert [p.nome for p in projetos] == ["Sermão novo", "Sermão do meio", "Sermão velho"]
    assert projetos[0].data == date(2026, 10, 4)
    assert all(isinstance(p, ProjetoExistente) for p in projetos)


def test_so_entra_o_que_abre_pasta_sem_estado_ou_estragada_fica_de_fora(tmp_path, video_falso):
    raiz = tmp_path / "Projetos"
    bom = _projeto(raiz, video_falso, "Bom", date(2026, 10, 4))
    (raiz / "2026-10-05_Sem_estado").mkdir()
    estragado = raiz / "2026-10-06_Estragado"
    estragado.mkdir()
    (estragado / "estado.json").write_text("{isto não é json", encoding="utf-8")
    (raiz / "Qualquer_pasta").mkdir()  # nome fora do padrão AAAA-MM-DD_Nome
    (raiz / "2026-10-07_Arquivo.txt").write_text("não é pasta", encoding="utf-8")

    assert [p.pasta for p in listar_projetos(raiz)] == [bom]


def test_a_pasta_de_projetos_que_nao_existe_da_lista_vazia(tmp_path):
    assert listar_projetos(tmp_path / "nao_existe") == []


def test_a_lista_tem_um_limite(tmp_path, video_falso):
    raiz = tmp_path / "Projetos"
    for n in range(5):
        _projeto(raiz, video_falso, f"Sermão {n}", date(2026, 10, 1 + n))
    assert len(listar_projetos(raiz, limite=3)) == 3


def test_o_projeto_diz_o_que_ja_tem(tmp_path, video_falso):
    raiz = tmp_path / "Projetos"
    pasta = _projeto(raiz, video_falso, "Com resultado", date(2026, 10, 4))
    (pasta / "video_longo_otimizado.mp4").write_bytes(b"x")
    vazio = _projeto(raiz, video_falso, "Sem resultado", date(2026, 10, 5))
    por_nome = {p.nome: p for p in listar_projetos(raiz)}
    assert "vídeo longo" in por_nome["Com resultado"].detalhes
    assert not por_nome["Com resultado"].detalhes.startswith("Já existe")
    assert por_nome["Sem resultado"].detalhes == "sem resultados ainda"
    assert por_nome["Sem resultado"].pasta == vazio


@pytest.mark.parametrize(
    ("status", "esperado"),
    [
        ((), "Ainda não começou"),
        ((StatusEtapa.PENDENTE, StatusEtapa.PENDENTE), "Ainda não começou"),
        ((StatusEtapa.CONCLUIDA, StatusEtapa.CONCLUIDA), "Concluído"),
        (
            (StatusEtapa.CONCLUIDA, StatusEtapa.PENDENTE, StatusEtapa.PENDENTE),
            "1 de 3 etapas prontas",
        ),
        ((StatusEtapa.CONCLUIDA, StatusEtapa.ERRO), "Parou com erro"),
        ((StatusEtapa.CONCLUIDA, StatusEtapa.AGUARDANDO_REVISAO), "Esperando a sua revisão"),
        ((StatusEtapa.CONCLUIDA, StatusEtapa.EM_ANDAMENTO), "Em andamento (ou interrompido)"),
        ((StatusEtapa.CONCLUIDA, StatusEtapa.CANCELADA), "Cancelado"),
    ],
)
def test_a_situacao_do_projeto_em_poucas_palavras(tmp_path, video_falso, status, esperado):
    pasta = _projeto(tmp_path / "Projetos", video_falso, "Qualquer", date(2026, 10, 4))
    estado = _estado(*status, base_do_estado=carregar_estado(pasta))
    assert situacao_do_projeto(estado) == esperado
    salvar_estado(pasta, estado)  # e a lista mostra a mesma coisa
    assert listar_projetos(pasta.parent)[0].situacao == esperado


# --- a caixa ------------------------------------------------------------------------------------
def _existentes(n: int) -> list[ProjetoExistente]:
    return [
        ProjetoExistente(
            pasta=Path(f"C:/Projetos/2026-10-0{i + 1}_Sermao_{i}"),
            nome=f"Sermão {i}",
            data=date(2026, 10, i + 1),
            modificado=1_700_000_000.0 + i,
            situacao="Concluído",
            detalhes="vídeo longo, 8 Shorts sugeridos, 8 renderizados",
        )
        for i in range(n)
    ]


@pytest.fixture
def caixa(janela):
    abertas: list[JanelaDeProjetos] = []

    def abrir(n: int = 3, ultimo: str = "") -> JanelaDeProjetos:
        nova = JanelaDeProjetos(janela, _existentes(n), ultimo=ultimo)
        abertas.append(nova)
        return nova

    yield abrir
    for nova in abertas:
        if nova.winfo_exists():
            nova.destroy()


@gui
def test_a_caixa_tem_uma_linha_por_projeto_e_o_primeiro_vem_marcado(caixa):
    c = caixa(4)
    assert len(c.linhas) == 4 and c.indice == 0
    marcada = c.linhas[0].cget("fg_color")
    assert all(linha.cget("fg_color") != marcada for linha in c.linhas[1:])


@gui
def test_o_ultimo_projeto_aberto_vem_marcado(caixa):
    c = caixa(4, ultimo=str(_existentes(4)[2].pasta))
    assert c.indice == 2


@gui
def test_as_setas_andam_pela_lista_sem_sair_dela(caixa):
    c = caixa(3)
    c.mover(1)
    c.mover(1)
    c.mover(1)  # já é a última
    assert c.indice == 2
    c.mover(-5)
    assert c.indice == 0


@gui
def test_abrir_devolve_a_pasta_do_projeto_marcado(caixa):
    c = caixa(3)
    c.selecionar(1)
    c.abrir()
    assert c.escolhido == _existentes(3)[1].pasta
    assert not c.winfo_exists()  # a caixa se fechou


@gui
def test_o_duplo_clique_numa_linha_abre_aquele_projeto(caixa):
    c = caixa(3)
    c._abrir_o(2)
    assert c.escolhido == _existentes(3)[2].pasta


@gui
def test_comecar_sem_abrir_nenhum_devolve_nada(caixa):
    c = caixa(3)
    c.sem_abrir()
    assert c.escolhido is None


@gui
def test_cada_linha_e_cada_botao_tem_dica_de_ferramenta(caixa):
    c = caixa(2)
    sem = [
        w
        for w in (*c.linhas, c.botao_abrir, c.botao_vazio)
        if not getattr(getattr(w, "_dica_spurgeon", None), "texto", "")
    ]
    assert not sem, f"sem dica de ferramenta: {sem}"
    assert str(_existentes(2)[0].pasta) in c.linhas[0]._dica_spurgeon.texto  # diz onde a pasta está
