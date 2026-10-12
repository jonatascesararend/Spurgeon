from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.estado import (
    EntradaVideo,
    Estado,
    EstadoEtapa,
    StatusEtapa,
    carregar_estado,
    hash_rapido,
    salvar_estado,
)


def test_hash_igual_para_conteudo_igual_e_diferente_para_outro(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.mp4", "b.mp4", "c.mp4"))
    a.write_bytes(b"x" * 5000)
    b.write_bytes(b"x" * 5000)
    c.write_bytes(b"x" * 4999 + b"y")
    assert hash_rapido(a) == hash_rapido(b)
    assert hash_rapido(a) != hash_rapido(c)


def test_hash_considera_o_final_de_arquivos_grandes(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    miolo = b"m" * (9 * 1024 * 1024)
    a.write_bytes(b"ini" + miolo + b"fim-A")
    b.write_bytes(b"ini" + miolo + b"fim-B")
    assert hash_rapido(a) != hash_rapido(b)


def test_estado_vai_e_volta_do_disco(tmp_path, video_falso):
    estado = Estado(
        entrada=EntradaVideo.de_arquivo(video_falso),
        opcoes=OpcoesProcessamento(limiar_pausa_s=12.5, gerar_shorts=True),
        etapas=[
            EstadoEtapa(id="extrair", status=StatusEtapa.CONCLUIDA, artefatos=["_work/a.wav"]),
            EstadoEtapa(id="transcrever", status=StatusEtapa.EM_ANDAMENTO, progresso=0.4),
        ],
    )
    salvar_estado(tmp_path, estado)
    lido = carregar_estado(tmp_path)
    assert lido is not None
    assert lido.opcoes.limiar_pausa_s == 12.5
    assert lido.etapa("transcrever").progresso == 0.4
    assert lido.etapa("extrair").artefatos == ["_work/a.wav"]
    assert lido.entrada.hash_rapido == estado.entrada.hash_rapido


def test_salvar_nao_deixa_temporarios(tmp_path, video_falso):
    pasta = tmp_path / "projeto"
    pasta.mkdir()
    salvar_estado(pasta, Estado(entrada=EntradaVideo.de_arquivo(video_falso)))
    assert [p.name for p in pasta.iterdir()] == ["estado.json"]


def test_carregar_estado_sem_arquivo_devolve_none(tmp_path):
    assert carregar_estado(tmp_path) is None
