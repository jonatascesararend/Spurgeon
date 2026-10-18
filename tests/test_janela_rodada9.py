"""A reorganização da coluna de opções: ordem dos cartões e "O que gerar" por modo."""

from __future__ import annotations

import pytest

from tests import test_janela as base

pytestmark = pytest.mark.gui

janela, tela = base.janela, base.tela  # as fixtures da janela compartilhada


def _titulos_numerados(widget) -> list[str]:
    """Os títulos de cartão (`3. Corte…`) dentro de um widget, de cima para baixo."""
    import customtkinter as ctk

    achados: list[tuple[int, str]] = []

    def varrer(pai) -> None:
        for filho in pai.winfo_children():
            if isinstance(filho, ctk.CTkLabel):
                texto = filho.cget("text")
                if len(texto) > 3 and texto[0].isdigit() and texto[1:3] == ". ":
                    achados.append((filho.winfo_y() + pai.winfo_y(), texto))
            varrer(filho)

    varrer(widget)
    return [texto for _, texto in sorted(achados)]


def _visivel(widget) -> bool:
    return bool(widget.winfo_manager())


def test_os_cartoes_ficam_em_tres_paginas_na_ordem_pedida(tela):
    paginas = [_titulos_numerados(p) for p in tela.opcoes.paginas]
    assert [[t.split(". ", 1)[1] for t in p] for p in paginas] == [
        ["Vídeo", "O que gerar"],
        ["Corte e enquadramento", "Tratamento de imagem", "Marca d'água"],
        ["Pontos de revisão", "Processamento"],
    ]
    numeros = [int(t.split(".")[0]) for p in paginas for t in p]
    assert numeros == sorted(numeros)


def test_so_uma_pagina_aparece_por_vez_e_o_seletor_troca(tela):
    from spurgeon.ui.painel_opcoes import NOMES_DAS_PAGINAS

    def visiveis():
        return [i for i, p in enumerate(tela.opcoes.paginas) if p.winfo_manager()]

    assert visiveis() == [0]
    for indice, nome in enumerate(NOMES_DAS_PAGINAS):
        tela._paginas.set(nome)
        tela._trocar_pagina(nome)
        assert visiveis() == [indice]
        assert tela.lateral._parent_canvas.yview()[0] == 0.0  # cada página abre no topo
    tela._trocar_pagina(NOMES_DAS_PAGINAS[0])


def test_corte_zoom_e_legenda_dos_shorts_ficam_no_cartao_3(tela):
    cartao = tela.opcoes.cartao_ajustes
    for nome, painel in (
        ("aparar", tela.entrada.aparar),
        ("zoom", tela.entrada.zoom),
        ("shorts", tela.entrada.shorts),
    ):
        assert painel.master is tela.entrada.secoes[nome].corpo
        assert tela.entrada.secoes[nome].master is cartao


def test_modo_tudo_mostra_todos_os_grupos(tela):
    tela.opcoes.definir_modo("completo")
    grupos = tela.opcoes._grupos
    assert all(_visivel(g) for g in grupos.values())
    assert _visivel(tela.opcoes._quadro_gerar_shorts)


def test_so_shorts_mostra_so_audio_e_shorts(tela):
    tela.opcoes.definir_modo("so_shorts")
    grupos = tela.opcoes._grupos
    assert _visivel(grupos["audio"]) and _visivel(grupos["shorts"])
    assert not _visivel(grupos["longo"]) and not _visivel(grupos["vinhetas"])
    assert not _visivel(tela.opcoes._quadro_gerar_shorts)  # os Shorts já são o produto
    assert _visivel(tela.opcoes._quadro_legendas)
    tela.opcoes.definir_modo("completo")


def test_so_vinhetas_mostra_so_a_introducao_e_a_vinheta(tela):
    tela.opcoes.definir_modo("so_vinhetas")
    grupos = tela.opcoes._grupos
    assert _visivel(grupos["vinhetas"])
    assert not any(_visivel(grupos[n]) for n in ("audio", "longo", "shorts"))
    tela.opcoes.definir_modo("completo")
    assert all(_visivel(g) for g in tela.opcoes._grupos.values())


def test_os_grupos_voltam_na_ordem_certa_depois_de_trocar_de_modo(tela):
    tela.opcoes.definir_modo("so_vinhetas")
    tela.opcoes.definir_modo("completo")
    tela.update_idletasks()
    ordem = sorted(tela.opcoes._grupos, key=lambda n: tela.opcoes._grupos[n].winfo_y())
    assert ordem == ["audio", "longo", "vinhetas", "shorts"]


def test_corte_zoom_e_legenda_dos_shorts_sao_secoes_que_recolhem(tela):
    secoes = tela.entrada.secoes
    assert set(secoes) == {"aparar", "zoom", "shorts"}
    assert not any(s.aberta for s in secoes.values())  # a coluna começa curta
    painel = tela.entrada.zoom
    assert painel.master is secoes["zoom"].corpo and not painel.winfo_ismapped()
    secoes["zoom"].alternar()
    assert secoes["zoom"].aberta and "▾" in secoes["zoom"]._cabecalho.cget("text")
    secoes["zoom"].alternar()
    assert not secoes["zoom"].aberta and "▸" in secoes["zoom"]._cabecalho.cget("text")


def test_o_cartao_do_video_usa_a_largura_toda_e_os_botoes_ficam_em_linha_propria(tela):
    tela.update_idletasks()
    entrada = tela.entrada
    campo = entrada._controles[0]  # o campo do arquivo
    assert int(campo.grid_info()["columnspan"]) == 2
    assert campo.winfo_width() > 300  # antes ficava com ~150 px, o resto era de botões
    assert entrada.botao_projeto.master is not entrada  # os botões moram numa linha à parte
    assert entrada.rotulo_estimativa.grid_info()["row"] != entrada.rotulo_info.grid_info()["row"]
