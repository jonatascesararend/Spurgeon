"""Postar pelo YouTube Studio (Fase R4): as partes que não precisam do navegador. O passo a passo no
Studio de verdade foi conferido à mão no primeiro envio (Remoto.md, R0.3/R4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from spurgeon.canal.postar import PedidoDePostagem, data_do_studio, id_do_link, texto_da_descricao


def test_descricao_com_as_hashtags_numa_linha_propria():
    pedido = PedidoDePostagem(
        Path("s.mp4"), "Título", "  Uma descrição.  ", ["#shorts", "batismo", "#filipenses"]
    )
    assert texto_da_descricao(pedido) == "Uma descrição.\n\n#shorts #batismo #filipenses"
    assert texto_da_descricao(PedidoDePostagem(Path("s.mp4"), "T", "Só texto.")) == "Só texto."


def test_data_como_o_seletor_do_studio_mostra():
    assert data_do_studio(datetime(2026, 11, 20, 18, 30)) == "20 de nov. de 2026"
    assert data_do_studio(datetime(2027, 3, 5)) == "5 de mar. de 2027"


def test_id_do_short_pelo_link():
    assert id_do_link("https://youtube.com/shorts/qxZhGYpUVpc") == "qxZhGYpUVpc"
    assert id_do_link("https://youtu.be/qxZhGYpUVpc") == "qxZhGYpUVpc"
    assert id_do_link("https://studio.youtube.com/") == ""
