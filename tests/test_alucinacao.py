from statistics import mean

import pytest

from spurgeon.transcricao.alucinacao import motivo_da_alucinacao, remover_alucinacoes
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao


def seg(texto: str, inicio: float, duracao: float, prob: float = 0.95) -> Segmento:
    palavras = texto.split()
    passo = duracao / len(palavras)
    return Segmento(
        [
            Palavra(t, inicio + i * passo, inicio + (i + 1) * passo, prob)
            for i, t in enumerate(palavras)
        ]
    )


def test_o_caso_real_do_sermao_e_detectado():
    # aos 39:03: três palavras em 0,14 s, com confiança baixa em duas delas
    s = Segmento(
        [
            Palavra("Obrigado", 2343.3, 2343.36, 0.41),
            Palavra("por", 2343.36, 2343.40, 0.09),
            Palavra("assistir", 2343.40, 2343.44, 0.98),
        ]
    )
    assert motivo_da_alucinacao(s) is not None


def test_frase_tipica_com_confianca_baixa_e_removida():
    s = seg("Obrigado por assistir", 100.0, 2.0, prob=0.3)
    assert "confiança baixa" in motivo_da_alucinacao(s)


def test_obrigado_dito_de_verdade_fica():
    # tempo normal (3 palavras em 1,5 s) e confiança alta: é fala, não alucinação
    assert motivo_da_alucinacao(seg("Obrigado por assistir", 100.0, 1.5, prob=0.97)) is None


def test_acentos_e_pontuacao_nao_atrapalham_a_deteccao():
    s = seg("Obrigado, por assistir!", 5.0, 0.2, prob=0.9)
    assert motivo_da_alucinacao(s) is not None
    s = seg("Inscreva-se no canal.", 5.0, 0.3, prob=0.9)
    assert motivo_da_alucinacao(s) is not None
    s = seg("Até o próximo vídeo", 5.0, 0.3, prob=0.9)
    assert motivo_da_alucinacao(s) is not None


def test_texto_espremido_num_instante_e_removido_mesmo_sem_frase_conhecida():
    s = seg("blá blá blá blá blá", 10.0, 0.2, prob=0.9)  # 25 palavras por segundo
    assert "impossível" in motivo_da_alucinacao(s)


def test_fala_normal_nunca_e_tocada():
    assert motivo_da_alucinacao(seg("Deus é soberano sobre toda a criação", 50.0, 3.5)) is None
    assert motivo_da_alucinacao(seg("Amém", 50.0, 0.5)) is None
    assert (
        motivo_da_alucinacao(seg("Fala rápida de pregador empolgado mesmo assim", 5.0, 1.0)) is None
    )


def test_remover_tira_so_o_suspeito_guarda_o_registro_e_preserva_o_resto():
    t = Transcricao(
        "tiny",
        200.0,
        blocos=[
            Bloco(0.0, 100.0, [seg("A graça de Deus", 3.0, 2.0),
                               seg("Obrigado por assistir", 99.0, 0.14, prob=0.4)], True),
            Bloco(100.0, 200.0, [seg("Não adianta meus irmãos", 101.0, 2.5)], True),
        ],
    )  # fmt: skip
    removidos = remover_alucinacoes(t)
    assert [r.texto for r in removidos] == ["Obrigado por assistir"]
    assert [s.texto for s in t.segmentos] == ["A graça de Deus", "Não adianta meus irmãos"]
    assert t.removidos[0]["texto"] == "Obrigado por assistir"
    assert t.removidos[0]["inicio"] == pytest.approx(99.0)
    assert "motivo" in t.removidos[0]


def test_remover_de_novo_nao_tem_mais_o_que_tirar():
    t = Transcricao(
        "tiny", 10.0, blocos=[Bloco(0.0, 10.0, [seg("Obrigado por assistir", 1.0, 0.1, 0.3)], True)]
    )
    assert len(remover_alucinacoes(t)) == 1
    assert remover_alucinacoes(t) == []
    assert len(t.removidos) == 1


def test_os_removidos_vao_e_voltam_do_disco(tmp_path):
    t = Transcricao(
        "tiny", 10.0, blocos=[Bloco(0.0, 10.0, [seg("Obrigado por assistir", 1.0, 0.1, 0.3)], True)]
    )
    remover_alucinacoes(t)
    t.salvar(tmp_path / "t.json")
    assert Transcricao.carregar(tmp_path / "t.json").removidos == t.removidos


def test_media_de_confianca_usada_e_a_do_segmento():
    s = seg("Obrigado por assistir", 1.0, 1.5, prob=0.59)
    assert mean(p.prob for p in s.palavras) < 0.6
    assert motivo_da_alucinacao(s) is not None
