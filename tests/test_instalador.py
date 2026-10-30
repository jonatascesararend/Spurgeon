"""Os scripts de instalação e de abertura: o que dá para conferir sem instalar nada.

O teste de verdade (instalar num computador sem nada) está descrito no Projeto.md; aqui ficam as
armadilhas que já morderam ou que quebram em silêncio: o .ps1 sem BOM (o PowerShell 5.1 lê os
acentos como lixo), hash vazio, sintaxe quebrada, o .pth que perde pasta com acento e o
iniciar.bat voltando a abrir o console.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest

from spurgeon.core.config import raiz_repositorio

RAIZ = raiz_repositorio()
PS1 = RAIZ / "instalar.ps1"
POWERSHELL = shutil.which("powershell")


def _texto(caminho) -> str:
    return caminho.read_bytes().decode("utf-8-sig")


def test_o_ps1_tem_bom_utf8_para_o_powershell_ler_os_acentos():
    assert PS1.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="precisa do PowerShell")
def test_o_ps1_nao_tem_erro_de_sintaxe():
    comando = (
        "$e = $null; $t = $null; "
        "[void][System.Management.Automation.Language.Parser]"
        f"::ParseFile('{PS1}', [ref]$t, [ref]$e); "
        "$e | ForEach-Object { $_.Message }; exit ($e.Count)"
    )
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", comando], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_os_downloads_fixados_tem_versao_e_sha256_de_verdade():
    texto = _texto(PS1)
    assert "@@" not in texto  # marcador que ninguém substituiu
    hashes = re.findall(r"\$(?:Uv|Ffmpeg)Sha256 = '([0-9a-f]+)'", texto)
    assert len(hashes) == 2
    assert all(len(h) == 64 for h in hashes), hashes
    assert re.search(r"\$UvVersao = '\d+\.\d+\.\d+'", texto)
    assert re.search(r"\$FfmpegVersao = '\d+\.\d+\.\d+'", texto)


def test_o_ps1_instala_cada_coisa_que_o_programa_precisa():
    texto = _texto(PS1)
    for funcao in (
        "Garantir-Visual-Cpp",
        "Garantir-Uv",
        "Sincronizar-Ambiente",
        "Garantir-Ffmpeg",
        "Garantir-Ollama",
        "Baixar-Modelos",
        "Criar-Atalhos",
    ):
        assert re.search(rf"^function {funcao}\b", texto, re.MULTILINE), funcao
    assert "sync --no-dev --extra ui --extra transcricao --extra shorts" in texto
    assert "-m spurgeon baixar-modelos" in texto
    assert "-m spurgeon doctor" in texto


def test_o_sitecustomize_do_ambiente_acha_o_src_mesmo_com_acento_na_pasta(tmp_path, monkeypatch):
    """O .pth do modo editável perde caminhos com acento no Python 3.11; este arquivo não."""
    trecho = re.search(r"\$SiteCustomize = @'\r?\n(.*?)\r?\n'@", _texto(PS1), re.DOTALL)
    assert trecho, "o instalar.ps1 não tem mais o $SiteCustomize"
    raiz = tmp_path / "João da Silva"
    (raiz / "src" / "spurgeon").mkdir(parents=True)
    site = raiz / ".venv" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    arquivo = site / "sitecustomize.py"
    arquivo.write_text(trecho.group(1), encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))
    codigo = compile(arquivo.read_text(encoding="utf-8"), str(arquivo), "exec")
    exec(codigo, {"__file__": str(arquivo)})
    assert str(raiz / "src") in sys.path


def test_o_atalho_abre_pelo_pythonw_sem_console():
    texto = _texto(PS1)
    assert "pythonw.exe" in texto
    assert "'-m spurgeon gui'" in texto


def test_os_bat_usam_crlf():
    for nome in ("instalar.bat", "iniciar.bat"):
        dados = (RAIZ / nome).read_bytes()
        assert b"\r\n" in dados and b"\n" not in dados.replace(b"\r\n", b""), nome


def test_o_instalar_bat_repassa_as_opcoes_ao_ps1():
    texto = _texto(RAIZ / "instalar.bat")
    assert "instalar.ps1" in texto
    assert "%*" in texto
    assert "-ExecutionPolicy Bypass" in texto


def test_o_iniciar_bat_abre_sem_console_e_nao_depende_do_uv():
    texto = _texto(RAIZ / "iniciar.bat")
    assert 'start "" ".venv\\Scripts\\pythonw.exe" -m spurgeon gui' in texto
    comandos = [
        linha for linha in texto.splitlines() if not linha.lstrip().lower().startswith("rem")
    ]
    assert not any("uv run" in linha for linha in comandos)
