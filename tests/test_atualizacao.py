"""Atualização pelo GitHub: consultar a versão e trocar os arquivos (sem rede de verdade)."""

import io
import zipfile
from pathlib import Path

import pytest

from spurgeon.core import atualizacao as at
from spurgeon.core.atualizacao import (
    ErroDeAtualizacao,
    InfoAtualizacao,
    aplicar_zip,
    baixar_e_atualizar,
    baixar_zip,
    consultar_atualizacao,
    e_mais_nova,
    ler_versao,
    motivo_de_nao_atualizar_sozinho,
    numeros_da_versao,
)

# --- versões -------------------------------------------------------------------------------


def test_le_a_versao_do_init_do_github():
    codigo = '"""Studio Spurgeon."""\n\n__version__ = "1.7.0"\n'
    assert ler_versao(codigo) == (1, 7, 0)
    assert ler_versao("__version__ = '2.0'") == (2, 0)
    assert ler_versao("nada aqui") is None
    assert ler_versao('__version__ = "1.7-beta"') is None  # só números e pontos


@pytest.mark.parametrize(
    ("nova", "atual", "esperado"),
    [
        ("1.7.0", "1.2.0", True),
        ("1.10.0", "1.9.9", True),  # 10 > 9: compara números, não texto
        ("1.7.0", "1.7.0", False),
        ("1.7", "1.7.0", False),  # a mesma versão
        ("1.7.1", "1.7", True),
        ("1.6.9", "1.7.0", False),  # o desenvolvedor, à frente do GitHub, não vê aviso
    ],
)
def test_comparar_versoes(nova, atual, esperado):
    assert e_mais_nova(numeros_da_versao(nova), numeros_da_versao(atual)) is esperado


# --- consultar -----------------------------------------------------------------------------


def _responde(versao: str):
    chamadas = []

    def abrir(url, timeout):
        chamadas.append((url, timeout))
        return f'"""x"""\n__version__ = "{versao}"\n'.encode()

    abrir.chamadas = chamadas
    return abrir


def test_versao_mais_nova_no_github_vira_aviso():
    abrir = _responde("1.8.0")
    info = consultar_atualizacao(versao_atual="1.7.0", abrir=abrir)
    assert info == InfoAtualizacao("1.8.0", "1.7.0")
    assert abrir.chamadas[0][0] == at.URL_DA_VERSAO and abrir.chamadas[0][1] == at.TEMPO_LIMITE_S


def test_em_dia_ou_a_frente_do_github_nao_avisa():
    assert consultar_atualizacao(versao_atual="1.8.0", abrir=_responde("1.8.0")) is None
    assert consultar_atualizacao(versao_atual="1.9.0", abrir=_responde("1.8.0")) is None


def test_sem_internet_levanta_erro_de_atualizacao_para_a_janela_ignorar():
    def sem_rede(url, timeout):
        raise TimeoutError("tempo esgotado")

    with pytest.raises(ErroDeAtualizacao, match="consultar o GitHub"):
        consultar_atualizacao(versao_atual="1.0.0", abrir=sem_rede)


def test_resposta_sem_versao_e_erro():
    with pytest.raises(ErroDeAtualizacao, match="número da versão"):
        consultar_atualizacao(versao_atual="1.0.0", abrir=lambda url, t: b"<html>404</html>")


# --- trocar os arquivos --------------------------------------------------------------------


def _pyproject(versao: str, dependencias: tuple[str, ...] = ("pillow",)) -> bytes:
    lista = ", ".join(f'"{d}"' for d in dependencias)
    return (
        f'[project]\nname = "spurgeon"\nversion = "{versao}"\ndependencies = [{lista}]\n'.encode()
    )


def _lock(versao: str, pillow: str = "10.0") -> bytes:
    return (
        f'[[package]]\nname = "pillow"\nversion = "{pillow}"\n\n'
        f'[[package]]\nname = "spurgeon"\nversion = "{versao}"\nsource = {{ editable = "." }}\n'
    ).encode()


def _zip_do_github(destino: Path, versao: str, extras: dict[str, bytes] | None = None) -> Path:
    """Um ZIP como o do GitHub: tudo dentro de uma pasta `Spurgeon-main/`."""
    arquivos = {
        "src/spurgeon/__init__.py": f'__version__ = "{versao}"\n'.encode(),
        "src/spurgeon/novo.py": b"# um modulo novo\n",
        "src/spurgeon/core/coisa.py": b"CODIGO = 'novo'\n",
        "instalar.ps1": b"# instalador novo\n",
        "README.md": b"# readme novo\n",
        "pyproject.toml": _pyproject(versao),
        "uv.lock": _lock(versao),
        "recursos/vocabulario_teologico.txt": b"termo do github\n",
        "recursos/novo_recurso.txt": b"recurso novo\n",
        "Projetos/2026_x/estado.json": b"{}",  # nunca vem do GitHub, mas se viesse não entra
        "modelos/whisper/modelo.bin": b"nao",
        "src/spurgeon/__pycache__/x.pyc": b"lixo",
    }
    arquivos.update(extras or {})
    with zipfile.ZipFile(destino, "w") as zip_:
        for nome, conteudo in arquivos.items():
            zip_.writestr(f"Spurgeon-main/{nome}", conteudo)
    return destino


@pytest.fixture
def programa(tmp_path) -> Path:
    """A pasta do programa de um usuário, na versão 1.0.0, com coisas dele dentro."""
    raiz = tmp_path / "Spurgeon"
    for nome, conteudo in {
        "src/spurgeon/__init__.py": '__version__ = "1.0.0"\n',
        "src/spurgeon/core/coisa.py": "CODIGO = 'velho'\n",
        "instalar.ps1": "# instalador velho\n",
        "README.md": "# readme velho\n",
        "pyproject.toml": _pyproject("1.0.0").decode(),
        "uv.lock": _lock("1.0.0").decode(),
        "recursos/vocabulario_teologico.txt": "meus termos\n",
        "recursos/marca_dagua.png": "minha marca",
        "Projetos/2026_x/estado.json": '{"meu": "projeto"}',
        "modelos/whisper/modelo.bin": "modelo baixado",
    }.items():
        arquivo = raiz / nome
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(conteudo.encode("utf-8"))  # sem trocar \n por \r\n
    return raiz


def _ler(raiz: Path, nome: str) -> str:
    return (raiz / nome).read_text(encoding="utf-8")


def test_aplicar_troca_o_programa_e_deixa_o_que_e_da_pessoa(programa, tmp_path):
    zip_ = _zip_do_github(tmp_path / "main.zip", "1.1.0")
    resultado = aplicar_zip(zip_, programa, versao_atual="1.0.0")
    assert resultado.versao == "1.1.0"
    # o programa foi trocado
    assert _ler(programa, "src/spurgeon/core/coisa.py") == "CODIGO = 'novo'\n"
    assert _ler(programa, "src/spurgeon/__init__.py") == '__version__ = "1.1.0"\n'
    assert _ler(programa, "src/spurgeon/novo.py") == "# um modulo novo\n"
    assert _ler(programa, "instalar.ps1") == "# instalador novo\n"
    assert _ler(programa, "README.md") == "# readme novo\n"
    # o que é da pessoa ficou como estava
    assert _ler(programa, "recursos/vocabulario_teologico.txt") == "meus termos\n"
    assert _ler(programa, "recursos/marca_dagua.png") == "minha marca"
    assert _ler(programa, "Projetos/2026_x/estado.json") == '{"meu": "projeto"}'
    assert _ler(programa, "modelos/whisper/modelo.bin") == "modelo baixado"
    # um recurso que não existia entra; o lixo do __pycache__ não
    assert _ler(programa, "recursos/novo_recurso.txt") == "recurso novo\n"
    assert not (programa / "src/spurgeon/__pycache__").exists()
    # contagem: 6 trocados (coisa, init, instalar, readme, pyproject e uv.lock, que levam a
    # versão) e 2 novos (novo.py, novo_recurso)
    assert (resultado.trocados, resultado.novos) == (6, 2)
    # a pasta de trabalho só guarda a cópia do que foi trocado
    trabalho = programa / at.PASTA_DE_TRABALHO
    assert not (trabalho / "extraido").exists()
    assert (trabalho / "anterior" / "src/spurgeon/core/coisa.py").read_text("utf-8") == (
        "CODIGO = 'velho'\n"
    )


def test_so_a_versao_do_programa_mudar_nao_pede_o_instalador(programa, tmp_path):
    # toda versão nova muda a linha `version` do pyproject.toml e do uv.lock: isso não é biblioteca
    zip_ = _zip_do_github(tmp_path / "a.zip", "1.1.0")
    resultado = aplicar_zip(zip_, programa, versao_atual="1.0.0")
    assert resultado.precisa_instalar is False
    assert 'version = "1.1.0"' in _ler(programa, "pyproject.toml")  # mas os arquivos foram trocados


def test_biblioteca_nova_ou_atualizada_pede_o_instalador(programa, tmp_path):
    nova = _zip_do_github(
        tmp_path / "a.zip", "1.1.0", {"pyproject.toml": _pyproject("1.1.0", ("pillow", "requests"))}
    )
    assert aplicar_zip(nova, programa, versao_atual="1.0.0").precisa_instalar is True
    atualizada = _zip_do_github(tmp_path / "b.zip", "1.2.0", {"uv.lock": _lock("1.2.0", "11.0")})
    assert aplicar_zip(atualizada, programa, versao_atual="1.1.0").precisa_instalar is True
    assert "11.0" in _ler(programa, "uv.lock")


def test_pasta_sem_uv_lock_ou_com_arquivo_ilegivel_pede_o_instalador(programa, tmp_path):
    (programa / "uv.lock").unlink()
    zip_ = _zip_do_github(tmp_path / "a.zip", "1.1.0")
    assert aplicar_zip(zip_, programa, versao_atual="1.0.0").precisa_instalar is True
    (programa / "pyproject.toml").write_text("isto = [nao e toml", encoding="utf-8")
    zip2 = _zip_do_github(
        tmp_path / "b.zip", "1.2.0", {"pyproject.toml": _pyproject("1.2.0", ("x",))}
    )
    assert aplicar_zip(zip2, programa, versao_atual="1.1.0").precisa_instalar is True


def test_so_o_fim_de_linha_diferente_nao_conta_como_mudanca(programa, tmp_path):
    for nome in ("pyproject.toml", "uv.lock"):  # a pasta da pessoa tem o fim de linha do Windows
        arquivo = programa / nome
        arquivo.write_bytes(arquivo.read_bytes().replace(b"\n", b"\r\n"))
    zip_ = _zip_do_github(tmp_path / "main.zip", "1.1.0")
    assert aplicar_zip(zip_, programa, versao_atual="1.0.0").precisa_instalar is False
    # nos arquivos de texto, uma diferença só no fim de linha também não os troca
    (programa / "README.md").write_bytes(b"# readme novo\r\n")
    zip2 = _zip_do_github(tmp_path / "b.zip", "1.2.0")
    resultado = aplicar_zip(zip2, programa, versao_atual="1.1.0")
    assert (programa / "README.md").read_bytes() == b"# readme novo\r\n"  # não foi regravado
    assert resultado.trocados == 3  # init, pyproject e uv.lock (as versões); o resto é igual


def test_versao_que_nao_e_mais_nova_nao_troca_nada(programa, tmp_path):
    zip_ = _zip_do_github(tmp_path / "main.zip", "1.0.0")
    with pytest.raises(ErroDeAtualizacao, match="não é mais nova"):
        aplicar_zip(zip_, programa, versao_atual="1.0.0")
    assert _ler(programa, "src/spurgeon/core/coisa.py") == "CODIGO = 'velho'\n"


def test_zip_que_nao_e_do_programa_ou_corrompido_e_recusado(programa, tmp_path):
    outro = tmp_path / "outro.zip"
    with zipfile.ZipFile(outro, "w") as zip_:
        zip_.writestr("qualquer/coisa.txt", "x")
    with pytest.raises(ErroDeAtualizacao, match="não é o Studio Spurgeon"):
        aplicar_zip(outro, programa, versao_atual="1.0.0")
    lixo = tmp_path / "lixo.zip"
    lixo.write_bytes(b"isto nao e um zip")
    with pytest.raises(ErroDeAtualizacao, match="corrompido"):
        aplicar_zip(lixo, programa, versao_atual="1.0.0")


def test_zip_com_caminho_que_foge_da_pasta_e_recusado(programa, tmp_path):
    malicioso = tmp_path / "mal.zip"
    with zipfile.ZipFile(malicioso, "w") as zip_:
        zip_.writestr("Spurgeon-main/src/spurgeon/__init__.py", '__version__ = "9.0.0"\n')
        zip_.writestr("../../fora.txt", "escapou")
    with pytest.raises(ErroDeAtualizacao, match="caminhos inválidos"):
        aplicar_zip(malicioso, programa, versao_atual="1.0.0")
    assert not (tmp_path / "fora.txt").exists() and not (programa.parent / "fora.txt").exists()


def test_falha_no_meio_desfaz_tudo_e_a_versao_continua_a_antiga(programa, tmp_path, monkeypatch):
    zip_ = _zip_do_github(tmp_path / "main.zip", "1.1.0")
    copiar = at.shutil.copy2
    feitas = []

    def copiar_com_falha(origem, destino, *args, **kwargs):
        # as cópias de segurança (para "anterior") passam; a 5ª troca de verdade falha, como um
        # arquivo preso por outro programa (até ali já houve 2 trocas e 1 arquivo novo)
        if "extraido" in str(origem):
            feitas.append(destino)
            if len(feitas) == 5:
                raise PermissionError(13, "arquivo em uso")
        return copiar(origem, destino, *args, **kwargs)

    monkeypatch.setattr(at.shutil, "copy2", copiar_com_falha)
    with pytest.raises(ErroDeAtualizacao, match="nada foi alterado"):
        aplicar_zip(zip_, programa, versao_atual="1.0.0")
    monkeypatch.setattr(at.shutil, "copy2", copiar)
    assert _ler(programa, "src/spurgeon/core/coisa.py") == "CODIGO = 'velho'\n"
    assert _ler(programa, "src/spurgeon/__init__.py") == '__version__ = "1.0.0"\n'
    assert _ler(programa, "instalar.ps1") == "# instalador velho\n"
    assert not (programa / "recursos/novo_recurso.txt").exists()  # o que era novo também sai
    assert not (programa / "src/spurgeon/novo.py").exists()
    assert not (programa / at.PASTA_DE_TRABALHO / "extraido").exists()


def test_o_arquivo_com_a_versao_e_o_ultimo_a_ser_trocado(programa, tmp_path):
    zip_ = _zip_do_github(tmp_path / "main.zip", "1.1.0")
    with zipfile.ZipFile(zip_) as z:
        z.extractall(tmp_path / "x")
    plano = at._plano(tmp_path / "x" / "Spurgeon-main", programa)
    assert plano[-1] == Path("src/spurgeon/__init__.py")
    assert all("__pycache__" not in p.parts for p in plano)
    assert Path("recursos/vocabulario_teologico.txt") not in plano  # já existe: é da pessoa
    assert Path("recursos/novo_recurso.txt") in plano


# --- baixar e atualizar --------------------------------------------------------------------


class _Resposta(io.BytesIO):
    def __init__(self, conteudo: bytes):
        super().__init__(conteudo)
        self.headers = {"Content-Length": str(len(conteudo))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_baixar_zip_grava_no_nome_final_e_informa_o_andamento(tmp_path):
    conteudo = b"x" * 600_000
    andamento = []
    destino = tmp_path / "pasta" / "main.zip"
    baixar_zip(destino, ao_progresso=andamento.append, abrir=lambda p, t: _Resposta(conteudo))
    assert destino.read_bytes() == conteudo
    assert not destino.with_name("main.zip.baixando").exists()
    assert andamento == sorted(andamento) and andamento[-1] == 1.0 and len(andamento) >= 3


def test_o_github_serve_o_zip_sem_tamanho_e_o_andamento_diz_que_nao_sabe(tmp_path):
    """Visto de verdade: o ZIP da `main` vem sem `Content-Length`, então não há porcentagem."""
    conteudo = b"x" * 600_000
    resposta = _Resposta(conteudo)
    resposta.headers = {}
    andamento: list[float] = []
    baixar_zip(tmp_path / "main.zip", ao_progresso=andamento.append, abrir=lambda p, t: resposta)
    assert (tmp_path / "main.zip").read_bytes() == conteudo
    assert andamento[:-1] == [at.DESCONHECIDO] * (len(andamento) - 1) and len(andamento) >= 3
    assert andamento[-1] == 1.0  # ao terminar, sabe-se que acabou


def test_download_que_falha_nao_deixa_arquivo_pela_metade(tmp_path):
    def cai(pedido, timeout):
        raise ConnectionResetError("caiu")

    destino = tmp_path / "main.zip"
    with pytest.raises(ErroDeAtualizacao, match="Confira a internet"):
        baixar_zip(destino, abrir=cai)
    assert not destino.exists() and not list(tmp_path.glob("*.baixando"))


def test_baixar_e_atualizar_de_ponta_a_ponta(programa, tmp_path):
    zip_ = _zip_do_github(tmp_path / "main.zip", "1.1.0")
    pedidos = []

    def servidor(pedido, timeout):
        pedidos.append(pedido.full_url)
        return _Resposta(zip_.read_bytes())

    info = InfoAtualizacao("1.1.0", "1.0.0")
    resultado = baixar_e_atualizar(programa, info, abrir=servidor)
    assert pedidos == [at.URL_DO_ZIP] and resultado.versao == "1.1.0"
    assert _ler(programa, "src/spurgeon/core/coisa.py") == "CODIGO = 'novo'\n"
    assert not (programa / at.PASTA_DE_TRABALHO / "spurgeon-main.zip").exists()  # o ZIP é apagado


def test_clone_do_git_nao_se_atualiza_sozinho(programa, tmp_path):
    (programa / ".git").mkdir()
    assert "git pull" in motivo_de_nao_atualizar_sozinho(programa)
    with pytest.raises(ErroDeAtualizacao, match="git pull"):
        baixar_e_atualizar(programa, InfoAtualizacao("1.1.0", "1.0.0"), abrir=lambda p, t: None)
    assert not (programa / at.PASTA_DE_TRABALHO).exists()  # nem chegou a baixar


def test_pasta_comum_pode_se_atualizar(programa):
    assert motivo_de_nao_atualizar_sozinho(programa) is None


# --- reabrir -------------------------------------------------------------------------------


def test_reabrir_sem_instalar_abre_o_programa_pelo_mesmo_python(programa, monkeypatch):
    chamadas = []
    monkeypatch.setattr(at.subprocess, "Popen", lambda cmd, **kw: chamadas.append((cmd, kw)))
    at.reabrir_programa(programa, instalar=False)
    comando, opcoes = chamadas[0]
    assert comando[1:] == ["-m", "spurgeon", "gui"] and opcoes["cwd"] == programa


def test_reabrir_com_instalacao_chama_o_instalar_que_espera_o_programa_fechar(
    programa, monkeypatch
):
    chamadas = []
    monkeypatch.setattr(at.subprocess, "Popen", lambda cmd, **kw: chamadas.append((cmd, kw)))
    at.reabrir_programa(programa, instalar=True, pid=4242)
    comando, _ = chamadas[0]
    assert comando[:2] == ["cmd.exe", "/c"] and comando[2].endswith("instalar.bat")
    assert comando[comando.index("-AguardarPid") + 1] == "4242"
    for opcao in ("-Silencioso", "-SemModelos", "-SemOllama", "-SemAtalhos", "-Abrir"):
        assert opcao in comando
