"""Fixtures compartilhadas pelos testes."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import platformdirs
import pytest

from spurgeon.core.config import ConfigApp, OpcoesProcessamento, Pastas, raiz_repositorio
from spurgeon.core.erros import ErroConfiguracao
from spurgeon.core.estado import EntradaVideo, Estado
from spurgeon.core.ffmpeg import localizar_binario
from spurgeon.core.log import criar_log_projeto, encerrar_log
from spurgeon.core.pipeline import ContextoProjeto
from spurgeon.core.projeto import PastasProjeto
from spurgeon.transcricao.formato import Bloco, Palavra, Segmento, Transcricao


@pytest.fixture(autouse=True)
def config_do_usuario_isolada(tmp_path, monkeypatch):
    """Nenhum teste escreve (nem lê) o `config.toml` de verdade do usuário.

    Os presets de zoom e de cor e as preferências da janela chamam `salvar_config()` sem caminho;
    sem isto um teste da janela gravava as pastas temporárias dele no arquivo do usuário.
    """
    pasta = tmp_path / "config_do_usuario"
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *_a, **_k: pasta)


@pytest.fixture(autouse=True)
def vocabulario_do_usuario_protegido():
    """Nenhum teste muda o vocabulário de verdade (`recursos/vocabulario_teologico.txt`).

    Os termos que a pessoa guarda ali são trabalho dela, fora do git. Se um teste mexer no arquivo
    (por exemplo, por esquecer de apontar as pastas do aplicativo para `tmp_path`), o arquivo
    volta ao que era e o teste falha.
    """
    caminho = Pastas().vocabulario
    antes = caminho.read_bytes() if caminho.is_file() else None
    yield
    depois = caminho.read_bytes() if caminho.is_file() else None
    if depois != antes:
        if antes is None:
            caminho.unlink(missing_ok=True)
        else:
            caminho.write_bytes(antes)
        pytest.fail(
            "o teste alterou o vocabulário de verdade (recursos/vocabulario_teologico.txt); "
            "o arquivo foi restaurado. Aponte `Pastas(recursos=tmp_path / ...)` para a pasta "
            "temporária do teste."
        )


def _nomes_em(pasta: Path) -> set[str]:
    try:
        return {p.name for p in pasta.iterdir()}
    except OSError:
        return set()


@pytest.fixture(autouse=True)
def pastas_de_verdade_protegidas():
    """Nenhum teste cria nem apaga nada nas pastas de verdade de projetos e de vídeos baixados.

    Um teste do controle remoto que usou o `NovosProjetos` padrão (que lê a configuração do
    aplicativo) criou um projeto em `Projetos/`. Aqui só se compara a lista de nomes antes e
    depois e o teste falha com os nomes; nada é apagado (a pessoa pode estar criando ou apagando
    um projeto pelo celular enquanto os testes rodam).
    """
    pastas = Pastas()
    antes = {pasta: _nomes_em(pasta) for pasta in (pastas.projetos, pastas.entrada)}
    yield
    for pasta, nomes in antes.items():
        depois = _nomes_em(pasta)
        if depois != nomes:
            novos, sumiram = sorted(depois - nomes), sorted(nomes - depois)
            pytest.fail(
                f"o teste mexeu na pasta de verdade {pasta}: apareceram {novos}, sumiram "
                f"{sumiram}. Aponte as pastas (ou o `carregar` do NovosProjetos) para `tmp_path`."
            )


def pytest_collection_modifyitems(items):
    try:
        localizar_binario("ffmpeg")
    except ErroConfiguracao:
        pular = pytest.mark.skip(reason="FFmpeg não instalado")
        for item in items:
            if "ffmpeg" in item.keywords:
                item.add_marker(pular)


@pytest.fixture(scope="session")
def video_teste(tmp_path_factory) -> Path:
    """Vídeo de 20 s (640x360, 25 fps, tom de 440 Hz) gerado pelo FFmpeg; não é versionado."""
    destino = tmp_path_factory.mktemp("fixtures") / "teste.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=20",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


# Tom de 440 Hz em 0–5 s, 17–25 s, 28–38 s e 53–60 s; pausas de 12 s, 3 s e 15 s no meio.
PAUSAS_DO_AUDIO = [(5.0, 17.0), (25.0, 28.0), (38.0, 53.0)]
_FILTRO_PAUSAS = (
    "volume='between(t,0,5)+between(t,17,25)+between(t,28,38)+between(t,53,60)':eval=frame"
)


@pytest.fixture(scope="session")
def audio_com_pausas(tmp_path_factory) -> Path:
    """WAV mono de 60 s: tom em rajadas sobre um ruído de fundo baixo, com pausas conhecidas."""
    destino = tmp_path_factory.mktemp("audio") / "com_pausas.wav"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=60",
            "-f", "lavfi", "-i",
            "anoisesrc=color=white:amplitude=0.01:sample_rate=48000:duration=60",
            "-filter_complex",
            f"[0:a]volume=0.3,{_FILTRO_PAUSAS}[t];[1:a]volume=0.1[n];"
            "[t][n]amix=inputs=2:duration=first:normalize=0",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


@pytest.fixture(scope="session")
def video_com_pausas(tmp_path_factory, audio_com_pausas) -> Path:
    """Vídeo de 60 s pequeno, com o áudio de `audio_com_pausas`."""
    destino = tmp_path_factory.mktemp("video") / "com_pausas.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=60",
            "-i", str(audio_com_pausas),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


TEXTO_DA_FALA = (
    "A graça de Deus não depende do mérito do homem. "
    "Quando o Senhor nos chama, ele mesmo nos capacita a responder. "
    "Por isso Paulo escreve aos Efésios que fomos salvos pela graça, mediante a fé. "
    "Pergunto a vocês: quem sustenta a nossa perseverança até o fim? "
    "Não é a nossa força, mas a fidelidade de Cristo."
)


@pytest.fixture(scope="session")
def fala_pt(tmp_path_factory) -> Path:
    """WAV de 48 kHz com fala em português, gerada pela voz do Windows (só roda no Windows)."""
    if sys.platform != "win32":
        pytest.skip("a voz sintética usa o Windows")
    pasta = tmp_path_factory.mktemp("fala")
    bruto, destino = pasta / "bruto.wav", pasta / "fala.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Maria Desktop'); "
        f"$s.SetOutputToWaveFile('{bruto}'); $s.Speak('{TEXTO_DA_FALA}'); $s.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True)
    if r.returncode != 0 or not bruto.exists():
        pytest.skip("a voz pt-BR do Windows não está disponível")
    subprocess.run(
        [str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(bruto), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(destino)],
        check=True,
    )  # fmt: skip
    return destino


@pytest.fixture(scope="session")
def video_com_fala(tmp_path_factory, fala_pt) -> Path:
    destino = tmp_path_factory.mktemp("videofala") / "com_fala.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=40",
            "-i", str(fala_pt), "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


@pytest.fixture
def config_tmp(tmp_path) -> ConfigApp:
    return ConfigApp(
        pastas=Pastas(
            entrada=tmp_path / "Downloads",
            projetos=tmp_path / "Projetos",
            recursos=tmp_path / "recursos",
            modelos=tmp_path / "modelos",
        )
    )


@pytest.fixture
def video_falso(tmp_path) -> Path:
    """Arquivo qualquer, suficiente para os testes que só precisam de hash e tamanho."""
    caminho = tmp_path / "sermao.mp4"
    caminho.write_bytes(b"conteudo-do-video" * 1000)
    return caminho


@pytest.fixture
def contexto(tmp_path, config_tmp, video_falso) -> Iterator[ContextoProjeto]:
    pastas = PastasProjeto(tmp_path / "projeto")
    pastas.criar()
    opcoes = OpcoesProcessamento()
    log = criar_log_projeto(pastas.log, nome=f"spurgeon.teste.{tmp_path.name}")
    yield ContextoProjeto(
        pastas=pastas,
        config=config_tmp,
        opcoes=opcoes,
        estado=Estado(entrada=EntradaVideo.de_arquivo(video_falso), opcoes=opcoes),
        log=log,
    )
    encerrar_log(log)


@pytest.fixture(scope="session")
def video_hdr(tmp_path_factory) -> Path:
    """Vídeo de 3 s em HEVC 10 bits marcado como HDR HLG (BT.2020), como o do iPhone."""
    destino = tmp_path_factory.mktemp("hdr") / "hdr.mp4"
    subprocess.run(
        [
            str(localizar_binario("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30000/1001:duration=3",
            "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params",
            "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error",
            str(destino),
        ],
        check=True,
    )  # fmt: skip
    return destino


@pytest.fixture
def ctx_shorts(contexto, video_hdr, audio_com_pausas, tmp_path):
    """Contexto de Shorts sobre o vídeo HDR de 3 s, com fonte e vocabulário do repositório."""
    contexto.opcoes.gerar_shorts = True
    contexto.opcoes.qualidade = "rapido"
    contexto.opcoes.legendas_dinamicas = True
    contexto.opcoes.normalizar_volume = True
    contexto.estado.entrada = EntradaVideo.de_arquivo(video_hdr)
    contexto.config.pastas.recursos = raiz_repositorio() / "recursos"
    contexto.pastas.work.mkdir(parents=True, exist_ok=True)
    contexto.pastas.audio_normalizado.write_bytes(audio_com_pausas.read_bytes())
    palavras = [
        Palavra("Graça", 0.1, 0.5), Palavra("e", 0.6, 0.7), Palavra("paz,", 0.8, 1.2),
        Palavra("irmãos.", 1.4, 1.9),
    ]  # fmt: skip
    Transcricao("t", 3.0, blocos=[Bloco(0, 3, [Segmento(palavras)], True)]).salvar(
        contexto.pastas.transcricao
    )
    return contexto


@pytest.fixture(scope="session")
def dicionario_br():
    """O dicionário pt_BR já carregado (leva alguns segundos; vale para a sessão toda)."""
    pytest.importorskip("spylls")
    pytest.importorskip("spellchecker")
    from spurgeon.transcricao.dicionario import DicionarioBr

    dicionario = DicionarioBr(Pastas().dicionario_pt_br)
    assert dicionario.disponivel, "recursos/dicionarios/pt_BR/ deve estar no repositório"
    dicionario.preparar()
    return dicionario
