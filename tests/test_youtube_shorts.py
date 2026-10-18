"""Os elementos do YouTube Shorts por cima da prévia dos Shorts."""

from __future__ import annotations

import pytest
from PIL import Image

from spurgeon.ui.youtube_shorts import REGIOES, conflitos_da_legenda, sobrepor_elementos


def test_a_sobreposicao_mantem_o_tamanho_e_nao_mexe_no_original():
    base = Image.new("RGB", (234, 416), (90, 110, 130))
    resultado = sobrepor_elementos(base)
    assert resultado.size == base.size
    assert resultado.mode == "RGB"
    assert base.getpixel((100, 200)) == (90, 110, 130)


def test_os_elementos_aparecem_nas_regioes_do_youtube_e_o_meio_fica_limpo():
    base = Image.new("RGB", (234, 416), (90, 110, 130))
    resultado = sobrepor_elementos(base)
    assert resultado.getpixel((117, 150)) == (90, 110, 130)  # o centro do quadro, com o pregador
    # embaixo à esquerda o escurecimento e o texto mudam a cor
    diferentes = sum(
        resultado.getpixel((x, y)) != base.getpixel((x, y))
        for x in range(0, 120, 4)
        for y in range(340, 410, 4)
    )
    assert diferentes > 100
    # à direita estão os botões
    assert any(
        resultado.getpixel((x, y)) != base.getpixel((x, y))
        for x in range(205, 233, 2)
        for y in range(200, 300, 2)
    )


@pytest.mark.parametrize("posicao", [0.20, 0.30, 0.40])
def test_legenda_no_alto_nao_encosta_em_nada(posicao):
    assert conflitos_da_legenda(posicao) == []


def test_legenda_no_padrao_pode_encostar_nos_botoes_da_direita():
    achados = conflitos_da_legenda(0.62)
    assert len(achados) == 1
    assert "botões da direita" in achados[0]


def test_legenda_embaixo_encobre_o_canal_e_o_titulo():
    achados = conflitos_da_legenda(0.88)
    assert any("canal" in a for a in achados)


def test_as_regioes_cabem_na_tela():
    for r in REGIOES:
        assert 0.0 <= r.x0 < r.x1 <= 1.0
        assert 0.0 <= r.y0 < r.y1 <= 1.0
