"""O ajuste fino da legenda: palavras com tempos acertados à mão, no núcleo, no celular e no PC.

Nada aqui toca arquivos do autor nem roda o FFmpeg de verdade (o som do trecho é um `.wav` feito
aqui mesmo com o módulo `wave`).
"""

# ruff: noqa: F811  (os fixtures `pastas`, `fila` e `cliente` vêm de test_remoto_edicao)

from __future__ import annotations

import tkinter
import wave
from pathlib import Path

import pytest

from spurgeon.render import trecho_de_audio
from spurgeon.render.shorts import (
    SITUACAO_ALTERADO,
    SITUACAO_PRONTO,
    detalhes_do_registro,
    situacao_do_short,
)
from spurgeon.shorts import ajuste_fino
from spurgeon.shorts.ajuste_fino import AjusteInvalido, PalavraAjustada
from spurgeon.shorts.dados import Corte
from spurgeon.shorts.edicao import legenda_do_corte, palavras_com_a_legenda, palavras_do_corte
from spurgeon.transcricao.formato import Palavra
from tests.test_remoto_edicao import (  # noqa: F401
    BASE,
    _corte,
    cliente,
    fila,
    pastas,
)

# --- o núcleo ------------------------------------------------------------------------------


def _fala(n: int = 10) -> list[Palavra]:
    """`n` palavras de 0,5 s, uma a cada 0,6 s, a partir de 10 s."""
    return [Palavra(f"p{k}", 10 + 0.6 * k, 10.5 + 0.6 * k) for k in range(n)]


def _corte_da_fala(**mudar) -> Corte:
    corte = Corte(id=1, inicio=9.9, fim=16.0, texto="", categoria="exortacao", nota=80, detalhe={})
    for nome, valor in mudar.items():
        setattr(corte, nome, valor)
    return corte


def _como(palavras) -> list[tuple[str, float, float]]:
    return [(p.texto, round(p.inicio, 3), round(p.fim, 3)) for p in palavras]


def test_sem_ajuste_a_legenda_e_a_da_transcricao():
    fala, corte = _fala(), _corte_da_fala()
    assert not ajuste_fino.tem_ajuste(corte)
    assert palavras_com_a_legenda(fala, corte) == palavras_do_corte(fala, corte)


def test_ajustar_os_tempos_muda_a_legenda_mas_nao_o_texto():
    fala, corte = _fala(), _corte_da_fala()
    itens = [PalavraAjustada(p.texto, p.inicio + 0.2, p.fim + 0.2) for p in fala]
    ajuste_fino.aplicar(corte, palavras_do_corte(fala, corte), itens)
    assert corte.legenda == ""  # só os tempos mudaram: o texto continua o da transcrição
    legenda = palavras_com_a_legenda(fala, corte)
    assert [p.texto for p in legenda] == [p.texto for p in fala]
    assert legenda[0].inicio == pytest.approx(10.2) and legenda[-1].fim <= corte.fim
    assert fala[0].inicio == 10  # a transcrição não muda


def test_palavra_que_faltava_entra_no_texto_e_no_tempo():
    fala, corte = _fala(), _corte_da_fala()
    itens = [PalavraAjustada(p.texto, p.inicio, p.fim) for p in fala]
    itens.insert(3, PalavraAjustada("Deus", fala[2].fim, fala[3].inicio))
    ajuste_fino.aplicar(corte, palavras_do_corte(fala, corte), itens)
    textos = [p.texto for p in palavras_com_a_legenda(fala, corte)]
    assert textos[:5] == ["p0", "p1", "p2", "Deus", "p3"]
    assert legenda_do_corte(fala, corte).split()[3] == "Deus"  # as caixas de texto mostram igual
    deus = palavras_com_a_legenda(fala, corte)[3]
    assert (deus.inicio, deus.fim) == pytest.approx((fala[2].fim, fala[3].inicio))


def test_arrumar_separa_tira_vazias_ordena_e_nao_deixa_sobrepor():
    corte = _corte_da_fala()
    arrumadas = ajuste_fino.arrumar(
        [
            PalavraAjustada("depois", 12.0, 12.5),
            PalavraAjustada("  ", 11.0, 11.2),  # apagada = tirada
            PalavraAjustada("duas palavras", 10.0, 11.0),
            PalavraAjustada("antes", 5.0, 13.0),  # começa antes do Short e invade a seguinte
        ],
        corte,
    )
    assert [p.texto for p in arrumadas] == ["antes", "duas", "palavras", "depois"]
    assert arrumadas[0].inicio == corte.inicio and arrumadas[0].fim <= arrumadas[1].inicio
    assert (arrumadas[1].fim, arrumadas[2].inicio) == pytest.approx((10.5, 10.5))
    with pytest.raises(AjusteInvalido):
        ajuste_fino.arrumar([PalavraAjustada("", 10, 11)], corte)


def test_puxar_o_fim_depois_do_ajuste_traz_as_palavras_novas_da_transcricao():
    fala = _fala(14)
    corte = _corte_da_fala()
    itens = [PalavraAjustada(p.texto.upper(), p.inicio + 0.1, p.fim + 0.1)
             for p in palavras_do_corte(fala, corte)]  # fmt: skip
    ajuste_fino.aplicar(corte, palavras_do_corte(fala, corte), itens)
    corte.fim = 17.75  # a pessoa estendeu o Short: entram p10…p12
    textos = [p.texto for p in ajuste_fino.base_do_corte(palavras_do_corte(fala, corte), corte)]
    assert textos[:10] == [f"P{k}" for k in range(10)] and textos[10:] == ["p10", "p11", "p12"]


def test_desfazer_volta_aos_tempos_da_transcricao():
    fala, corte = _fala(), _corte_da_fala()
    itens = [PalavraAjustada(p.texto, p.inicio + 0.3, p.fim + 0.3) for p in fala]
    ajuste_fino.aplicar(corte, palavras_do_corte(fala, corte), itens)
    ajuste_fino.desfazer(corte)
    assert _como(palavras_com_a_legenda(fala, corte)) == _como(palavras_do_corte(fala, corte))


def test_trechos_visiveis_tiram_os_saltos():
    corte = _corte_da_fala(saltos=[[11.0, 12.0], [13.0, 13.5]])
    assert ajuste_fino.trechos_visiveis(corte) == [(9.9, 11.0), (12.0, 13.0), (13.5, 16.0)]


def test_short_renderizado_fica_alterado_depois_do_ajuste_fino():
    fala, corte = _fala(), _corte_da_fala()
    registro = detalhes_do_registro(corte)
    assert situacao_do_short(corte, registro, True, com_legenda=True) == SITUACAO_PRONTO
    itens = [PalavraAjustada(p.texto, p.inicio + 0.2, p.fim + 0.2) for p in fala]
    ajuste_fino.aplicar(corte, palavras_do_corte(fala, corte), itens)
    assert situacao_do_short(corte, registro, True, com_legenda=True) == SITUACAO_ALTERADO
    assert situacao_do_short(corte, registro, True, com_legenda=False) == SITUACAO_PRONTO


def test_corte_antigo_sem_o_campo_continua_lendo():
    dados = _corte_da_fala().para_dict()
    del dados["ajuste_fino"]
    assert Corte.de_dict(dados).ajuste_fino == {}


# --- o som do trecho -------------------------------------------------------------------------


def _wav(caminho: Path, segundos: float, taxa: int = 1000) -> Path:
    with wave.open(str(caminho), "wb") as saida:
        saida.setnchannels(1)
        saida.setsampwidth(2)
        saida.setframerate(taxa)
        saida.writeframes(bytes(2 * int(segundos * taxa)))
    return caminho


def test_argumentos_do_trecho_de_audio():
    wav = trecho_de_audio.argumentos(Path("v.mp4"), 9.5, 20, Path("s.wav"), 0.5)
    assert wav[wav.index("-ss") + 1] == "9.5" and "atempo=0.5" in wav[wav.index("-af") + 1]
    assert "pcm_s16le" in wav
    m4a = trecho_de_audio.argumentos(Path("v.mp4"), 0, 20, Path("s.m4a"), 1.0, "m4a")
    assert "aac" in m4a and "atempo" not in m4a[m4a.index("-af") + 1]
    with pytest.raises(ValueError):
        trecho_de_audio.wav_do_trecho(Path("v"), 0, 999, Path("x.wav"), rodar=lambda *a, **k: 0)


def test_recortar_wav_pega_o_pedaco_certo(tmp_path):
    from spurgeon.ui.ajuste_fino import recortar_wav

    origem = _wav(tmp_path / "o.wav", 10)
    recortar_wav(origem, 2.0, 3.5, tmp_path / "p.wav")
    with wave.open(str(tmp_path / "p.wav"), "rb") as pedaco:
        assert pedaco.getnframes() == 1500


# --- pelo celular ----------------------------------------------------------------------------


def test_celular_le_ajusta_e_desfaz(cliente):
    lido = cliente.get(f"{BASE}/shorts/7/ajuste_fino").json()
    assert lido["palavras"] and not lido["ajustado"] and lido["som_de"] < lido["inicio"]
    palavras = [dict(p) for p in lido["palavras"]]
    for p in palavras:
        p["inicio"] += 0.1
        p["fim"] += 0.1
    palavras.insert(
        1, {"texto": "Senhor", "inicio": palavras[0]["fim"], "fim": palavras[1]["inicio"]}
    )
    assert (
        cliente.post(f"{BASE}/shorts/7/ajuste_fino", json={"palavras": palavras}).json()["erro"]
        == "sem_trava"
    )
    cliente.post(f"{BASE}/sessao")
    salvo = cliente.post(f"{BASE}/shorts/7/ajuste_fino", json={"palavras": palavras})
    assert salvo.status_code == 200 and salvo.json()["ajuste_fino"] is True
    corte = _corte(cliente.pastas, 7)
    assert ajuste_fino.tem_ajuste(corte) and corte.legenda.split()[1] == "Senhor"
    de_novo = cliente.get(f"{BASE}/shorts/7/ajuste_fino").json()
    assert de_novo["ajustado"] and de_novo["palavras"][1]["texto"] == "Senhor"
    # a legenda que o celular ouve (a audição) já vem com os tempos novos
    legenda = cliente.get(f"{BASE}/shorts/7/legenda").json()
    assert "Senhor" in str(legenda)
    assert cliente.post(f"{BASE}/shorts/7/ajuste_fino/desfazer").json()["ajuste_fino"] is False
    assert not ajuste_fino.tem_ajuste(_corte(cliente.pastas, 7))


def test_celular_ajuste_invalido(cliente):
    cliente.post(f"{BASE}/sessao")
    vazio = [{"texto": " ", "inicio": 1, "fim": 2}]
    assert cliente.post(f"{BASE}/shorts/7/ajuste_fino", json={"palavras": vazio}).status_code == 422
    torto = [{"texto": "a", "inicio": "x", "fim": 2}]
    assert cliente.post(f"{BASE}/shorts/7/ajuste_fino", json={"palavras": torto}).status_code == 422
    assert cliente.post(f"{BASE}/shorts/7/ajuste_fino", json={}).status_code == 400
    assert cliente.get(f"{BASE}/shorts/999/ajuste_fino").status_code == 404


def test_celular_som_do_trecho(cliente, monkeypatch, tmp_path):
    pedidos = []

    def gerar(fonte, de, ate, destino, *, formato, rodar=None):
        pedidos.append((de, ate, formato))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"som")
        return destino

    monkeypatch.setattr(trecho_de_audio, "fonte_do_som", lambda _p: tmp_path / "v.mp4")
    monkeypatch.setattr(trecho_de_audio, "wav_do_trecho", gerar)
    resposta = cliente.get(f"{BASE}/shorts/7/ajuste_fino/som")
    assert resposta.status_code == 200 and resposta.content == b"som"
    assert pedidos[0][2] == "m4a"
    cliente.get(f"{BASE}/shorts/7/ajuste_fino/som")
    assert len(pedidos) == 1  # o mesmo corte usa o arquivo guardado

    def sem_som(_p):
        raise trecho_de_audio.SemSom("O som do sermão não está mais no PC")

    monkeypatch.setattr(trecho_de_audio, "fonte_do_som", sem_som)
    assert cliente.get(f"{BASE}/shorts/7/ajuste_fino/som").status_code == 409


def test_fonte_do_som_sem_estado(tmp_path):
    from spurgeon.core.projeto import PastasProjeto

    with pytest.raises(trecho_de_audio.SemSom):
        trecho_de_audio.fonte_do_som(PastasProjeto(tmp_path / "vazio"))


# --- a janela do PC --------------------------------------------------------------------------


class RelogioFalso:
    def __init__(self):
        self.agora = 100.0

    def __call__(self):
        return self.agora


@pytest.fixture(scope="module")
def raiz():
    from tests.ambiente_grafico import criar_raiz

    try:
        janela = criar_raiz()
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    janela.withdraw()
    yield janela
    janela.destroy()


@pytest.fixture
def janela(raiz, tmp_path, monkeypatch):
    from spurgeon.ui import dialogos
    from spurgeon.ui.ajuste_fino import JanelaDoAjusteFino, Tocador

    monkeypatch.setattr(dialogos, "mostrar", lambda *_a, botoes=("OK",), **_k: botoes[-1])
    tocados, salvos = [], []
    relogio = RelogioFalso()

    def gerar(_fonte, de, ate, destino, *, velocidade):
        return _wav(destino, (ate - de) / velocidade)

    corte = _corte_da_fala()
    tocador = Tocador(tmp_path / "v.mp4", 8.9, 16.6, tocar_som=tocados.append,
                      relogio=relogio, gerar=gerar)  # fmt: skip
    tela = JanelaDoAjusteFino(
        raiz, corte=corte, palavras=palavras_do_corte(_fala(), corte), fonte=tmp_path / "v.mp4",
        ao_salvar=salvos.append, tocador=tocador,
    )  # fmt: skip
    tela.withdraw()
    tela.tocados, tela.salvos, tela.relogio = tocados, salvos, relogio
    yield tela
    if tela.winfo_exists():
        tela.destroy()


@pytest.mark.gui
def test_janela_mostra_cada_palavra_e_anda_os_tempos(janela):
    assert len(janela.lista.get_children()) == 10
    janela.selecionar(2)
    janela.mover("inicio", 0.1)
    assert janela.itens[2][1] == pytest.approx(11.3) and janela.itens[3][1] == pytest.approx(11.8)
    assert "mudou" in janela.lista.item("2", "tags") and janela.mudou
    janela._var_seguintes.set(True)
    janela.mover("inicio", -0.2)  # a palavra inteira e as seguintes
    assert janela.itens[2][1:] == pytest.approx([11.1, 11.5])
    assert janela.itens[3][1:] == pytest.approx([11.6, 12.1])
    assert janela.itens[1][1:] == pytest.approx([10.6, 11.1])  # a de antes não anda


@pytest.mark.gui
def test_janela_acrescenta_tira_e_salva(janela):
    janela.selecionar(0)
    janela.acrescentar_depois()  # cabe no silêncio de 0,1 s? não: divide a palavra ao meio
    assert janela._atual == 1 and janela.itens[1][0] == "…"
    assert janela.itens[0][2] == pytest.approx(janela.itens[1][1])
    janela.campo_texto.delete(0, "end")
    janela.campo_texto.insert(0, "Deus")
    janela._confirmar_texto()
    janela.selecionar(5)
    janela.tirar()
    assert len(janela.itens) == 10
    janela.salvar()
    (salvas,) = janela.salvos
    assert [p.texto for p in salvas][:3] == ["p0", "Deus", "p1"] and "p4" not in [
        p.texto for p in salvas
    ]


@pytest.mark.gui
def test_janela_toca_e_marca_o_agora(janela):
    janela.selecionar(4)
    janela.alternar()
    assert janela.tocados and janela.tocados[-1] is not None and janela.tocador.tocando
    janela.relogio.agora += 0.5  # tocou meio segundo a partir de 12,1 s (12,4 − 0,3)
    assert janela.tocador.posicao == pytest.approx(12.6)
    janela.marcar_agora("inicio")
    assert janela.itens[4][1] == pytest.approx(12.6 - 0.12)
    janela.parar()
    assert janela.tocados[-1] is None and not janela.tocador.tocando
    assert janela.palavra_em(10.2) == 0 and janela.palavra_em(10.55) == 0
    assert janela.palavra_em(9.0) is None


@pytest.mark.gui
def test_studio_abre_o_ajuste_e_leva_para_o_short(raiz, tmp_path, monkeypatch):
    import customtkinter as ctk

    from spurgeon.core.projeto import PastasProjeto
    from spurgeon.ui import studio_shorts
    from spurgeon.ui.studio_shorts import EditorDeCortes
    from tests.test_shorts import cortes_do_sermao, gravar_transcricao, sermao_falso

    monkeypatch.setattr(
        studio_shorts.dialogos, "mostrar", lambda *_a, botoes=("OK",), **_k: botoes[-1]
    )
    sem_som = trecho_de_audio.SemSom("sem som")
    monkeypatch.setattr(studio_shorts, "fonte_do_som", lambda _p: (_ for _ in ()).throw(sem_som))
    pastas = PastasProjeto(tmp_path / "proj")
    pastas.criar()
    palavras = sermao_falso(frases=60)
    gravar_transcricao(pastas.transcricao, palavras)
    from spurgeon.shorts.etapas import gravar_cortes

    gravar_cortes(pastas.cortes_shorts, cortes_do_sermao(palavras))
    topo = ctk.CTkToplevel(raiz)
    topo.withdraw()
    try:
        ed = EditorDeCortes(topo, pastas)
        i = ed._ordem[0]
        ed.selecionar(i)
        tela = ed.abrir_ajuste_fino()
        assert tela is not None and tela.tocador.fonte is None
        assert "sem som" in tela.aviso.cget("text")
        tela.selecionar(0)
        tela.mover("fim", -0.1)
        tela.salvar()
        corte = ed._cortes[i]
        assert ajuste_fino.tem_ajuste(corte) and ed.mudou
        assert "🎯 ajuste fino" in ed._rotulos_da_linha[i]["info"].cget("text")
        assert ed.pode_desfazer
        ed.desfazer()
        assert not ajuste_fino.tem_ajuste(ed._cortes[i])
    finally:
        topo.destroy()
