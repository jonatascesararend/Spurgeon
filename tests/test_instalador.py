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


def test_o_instalar_bat_usa_o_find_do_windows_e_nao_o_do_path():
    texto = _texto(RAIZ / "instalar.bat")
    assert 'set "FIND=%SystemRoot%\\System32\\find.exe"' in texto
    comandos = [
        linha
        for linha in texto.splitlines()
        if "find" in linha.lower() and not linha.lower().startswith("rem")
    ]
    assert all("%FIND%" in linha or linha.startswith("set ") for linha in comandos), comandos


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


# --- rodada 1.7: SmartScreen, plano B pelo winget, o Ollama que travava e a atualização ---------

APENAS_WINDOWS = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None, reason="precisa do Windows e do PowerShell"
)


def _funcao(texto: str, nome: str) -> str:
    """O corpo da função `nome` do .ps1 (até a próxima função ou o fim do arquivo)."""
    achada = re.search(
        rf"^function {re.escape(nome)}\b.*?(?=^function |^# --- |\Z)", texto, re.S | re.M
    )
    assert achada, f"o instalar.ps1 não tem a função {nome}"
    return achada.group(0)


def test_o_ps1_tem_as_opcoes_da_atualizacao_e_os_passos_contra_o_bloqueio():
    texto = _texto(PS1)
    assert "[int]$AguardarPid = 0" in texto and "[switch]$Abrir" in texto
    for funcao in (
        "Desbloquear-Pasta",
        "Desbloquear-O-Programa",
        "Esperar-O-Programa-Fechar",
        "Conferir-A-Protecao-Do-Windows",
        "Instalar-Pelo-Winget",
    ):
        assert re.search(rf"^function {funcao}\b", texto, re.MULTILINE), funcao
    # os passos rodam antes de qualquer instalação
    execucao = texto[re.search(r"^    Esperar-O-Programa-Fechar\s*$", texto, re.M).start() :]
    assert execucao.index("Desbloquear-O-Programa") < execucao.index("Conferir-Computador")
    assert "Conferir-A-Protecao-Do-Windows" in execucao


def test_a_instalacao_do_ollama_nao_usa_wait_e_tem_plano_b():
    corpo = re.sub(r"#.*", "", _funcao(_texto(PS1), "Garantir-Ollama"))  # sem os comentários
    # o -Wait do PowerShell 5.1 espera também o aplicativo que o instalador abre na bandeja
    assert "-Wait" not in corpo
    assert "-PassThru" in corpo and "WaitForExit(" in corpo
    assert "Instalar-Pelo-Winget 'Ollama.Ollama'" in corpo


def test_o_uv_tem_plano_b_pelo_winget_e_o_winget_so_aceita_o_que_a_microsoft_pede():
    texto = _texto(PS1)
    assert "Instalar-Pelo-Winget 'astral-sh.uv'" in _funcao(texto, "Garantir-Uv")
    corpo = _funcao(texto, "Instalar-Pelo-Winget")
    for opcao in ("--silent", "--accept-package-agreements", "--accept-source-agreements", "-e"):
        assert opcao in corpo


def test_a_marca_de_baixado_da_internet_tambem_sai_do_que_o_instalador_extrai():
    assert "Desbloquear-Pasta $Pasta" in _funcao(_texto(PS1), "Extrair-Zip")


@APENAS_WINDOWS
def test_desbloquear_pasta_tira_a_marca_de_baixado_da_internet(tmp_path):
    """O que o SmartScreen usa para bloquear é o fluxo Zone.Identifier: aqui ele sai de verdade."""
    (tmp_path / "sub").mkdir()
    arquivos = [tmp_path / "a.exe", tmp_path / "sub" / "b.py"]
    comando = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{PS1}'; "
        f"$arquivos = @('{arquivos[0]}', '{arquivos[1]}'); "
        "foreach ($f in $arquivos) { Set-Content -LiteralPath $f -Value 'x'; "
        "Set-Content -LiteralPath $f -Stream Zone.Identifier "
        '-Value "[ZoneTransfer]`r`nZoneId=3" }; '
        "$antes = @($arquivos | % { [bool](Get-Item -LiteralPath $_ -Stream Zone.Identifier "
        "-ErrorAction SilentlyContinue) }); "
        f"Desbloquear-Pasta '{tmp_path}'; "
        "$depois = @($arquivos | % { [bool](Get-Item -LiteralPath $_ -Stream Zone.Identifier "
        "-ErrorAction SilentlyContinue) }); "
        "Write-Output (\"antes=\" + ($antes -join ',') + \" depois=\" + ($depois -join ','))"
    )
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", comando],
        capture_output=True, text=True, timeout=90,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "antes=True,True depois=False,False" in r.stdout


@APENAS_WINDOWS
def test_esperar_o_programa_fechar_espera_o_processo_e_sem_pid_nao_espera_nada():
    comando = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{PS1}'; "
        '$p = Start-Process -FilePath "$env:SystemRoot\\System32\\ping.exe" '
        "-ArgumentList '-n','3','127.0.0.1' -PassThru -WindowStyle Hidden; "
        "$script:AguardarPid = $p.Id; $t = Get-Date; Esperar-O-Programa-Fechar; "
        "$esperou = ((Get-Date) - $t).TotalSeconds; "
        "$script:AguardarPid = 0; $t = Get-Date; Esperar-O-Programa-Fechar; "
        "$sem = ((Get-Date) - $t).TotalSeconds; "
        "Write-Output ('esperou=' + [int]($esperou -ge 1.5) + ' sem=' + [int]($sem -lt 0.5))"
    )
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", comando],
        capture_output=True, text=True, timeout=90,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "esperou=1 sem=1" in r.stdout


@APENAS_WINDOWS
def test_start_process_wait_espera_o_filho_e_o_waitforexit_nao(tmp_path):
    """A causa provável da instalação do Ollama "travada": o instalador sai, mas deixa o aplicativo
    do Ollama rodando, e o `-Wait` do PowerShell 5.1 espera por ele. `WaitForExit` espera só o
    instalador, que é o que a instalação usa."""
    falso = tmp_path / "instalador_falso.cmd"
    falso.write_bytes(b'@echo off\r\nstart "" /b ping -n 8 127.0.0.1 >nul\r\nexit /b 0\r\n')
    comando = (
        "$ErrorActionPreference = 'Stop'; "
        f"$cmd = '{falso}'; "
        "$t = Get-Date; "
        "$p = Start-Process -FilePath $env:ComSpec -ArgumentList '/c', ('\"' + $cmd + '\"') "
        "-PassThru -WindowStyle Hidden; $null = $p.Handle; $saiu = $p.WaitForExit(30000); "
        "$rapido = ((Get-Date) - $t).TotalSeconds; "
        "Write-Output ('saiu=' + $saiu + ' codigo=' + $p.ExitCode + "
        "' rapido=' + [int]($rapido -lt 4))"
    )
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", comando], capture_output=True, text=True, timeout=90
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "saiu=True codigo=0 rapido=1" in r.stdout


@APENAS_WINDOWS
@pytest.mark.parametrize(
    ("argumentos", "codigo", "pausa"),
    [
        ([], 0, True),  # duplo clique: espera uma tecla para a pessoa ler
        ([], 1, True),
        (["-Silencioso"], 0, False),  # automação: nunca trava esperando tecla
        (["-Silencioso"], 1, False),
        (["-Silencioso", "-Abrir"], 0, False),  # atualização pelo programa: some sozinha…
        (["-Silencioso", "-Abrir"], 1, True),  # …mas, se deu erro, fica para ler o que houve
    ],
)
def test_o_instalar_bat_so_espera_tecla_quando_deve(tmp_path, argumentos, codigo, pausa):
    """Roda o instalar.bat de verdade, com um instalar.ps1 de mentira que só devolve um código."""
    shutil.copy(RAIZ / "instalar.bat", tmp_path / "instalar.bat")
    (tmp_path / "instalar.ps1").write_text(f"exit {codigo}\n", encoding="utf-8")
    r = subprocess.run(
        ["cmd.exe", "/c", str(tmp_path / "instalar.bat"), *argumentos],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=60, cwd=tmp_path,
    )  # fmt: skip
    saida = r.stdout.decode("cp850", "replace").lower()
    assert r.returncode == codigo, saida
    assert ("tecla" in saida or "any key" in saida) is pausa, saida
