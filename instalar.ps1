<#
.SYNOPSIS
    Instalador do Studio Spurgeon (Windows 10/11, 64 bits).

.DESCRIPTION
    Deixa o computador pronto para usar o aplicativo, mesmo sem nada instalado. Cada passo confere
    antes se o que ele instala já existe (rodar de novo é seguro) e o que faltar é instalado assim:

      1. Visual C++ (runtime exigido pelas bibliotecas de IA) - instalador oficial da Microsoft
      2. uv (gerenciador do Python) - baixado para a pasta do projeto
      3. Python 3.11 e todas as bibliotecas - pelo uv, num ambiente da pasta do projeto (.venv)
      4. FFmpeg - baixado para a pasta do projeto
      5. Ollama (a IA local dos Shorts) - instalador oficial
      6. Modelos de IA (Whisper, filtro de áudio, modelo do Ollama)
      7. Atalhos na Área de Trabalho e no Menu Iniciar

    Nada é instalado fora da pasta do projeto, exceto o Visual C++ e o Ollama (que têm instalador
    próprio). Precisa de internet. Não precisa ser administrador (só o Visual C++ pede permissão).

    Não é preciso instalar o uv nem o Ollama antes: se o download direto falhar, o instalador tenta
    o winget (da Microsoft) sozinho. Também tira dos arquivos do programa a marca "baixado da
    internet" que o SmartScreen do Windows usa para bloquear, e avisa se o Controle Inteligente de
    Aplicativos do Windows 11 estiver ligado (ele bloqueia programas sem assinatura digital).

.PARAMETER SemModelos
    Não baixa os modelos de IA agora; o aplicativo os baixa sozinho na primeira vez que precisar.
.PARAMETER SemOllama
    Não instala o Ollama (sem ele não há Shorts).
.PARAMETER SemAtalhos
    Não cria atalhos na Área de Trabalho nem no Menu Iniciar.
.PARAMETER Silencioso
    Sem perguntas: assume "sim" em tudo.
.PARAMETER AguardarPid
    Antes de começar, espera o programa com este número de processo fechar (a atualização pelo
    próprio programa usa isto: o Windows não deixa trocar arquivos de um programa aberto).
.PARAMETER Abrir
    No fim, se deu certo, abre o Studio Spurgeon (a atualização pelo programa usa isto).
#>
[CmdletBinding()]
param(
    [switch]$SemModelos,
    [switch]$SemOllama,
    [switch]$SemAtalhos,
    [switch]$Silencioso,
    [int]$AguardarPid = 0,
    [switch]$Abrir
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # o Invoke-WebRequest fica muito mais rápido sem a barra
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- o que este instalador baixa ----------------------------------------------------------------
$UvVersao = '0.13.0'
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersao/uv-x86_64-pc-windows-msvc.zip"
$UvSha256 = '088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c'
$FfmpegVersao = '9.0.2'
$FfmpegUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVersao/ffmpeg-$FfmpegVersao-full_build.zip"
$FfmpegSha256 = '759d0a9831c436a0eb331ad36f236c06cb04aaa0005da46571f0e9d3d9206f6b'
$VcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
$OllamaUrl = 'https://ollama.com/download/OllamaSetup.exe'
$EspacoRecomendadoGB = 20
$EspacoMinimoGB = 6

# --- onde as coisas ficam -----------------------------------------------------------------------
$Raiz = $PSScriptRoot
$PastaFerramentas = Join-Path $Raiz 'modelos\ferramentas'
$Py = Join-Path $Raiz '.venv\Scripts\python.exe'
$Pyw = Join-Path $Raiz '.venv\Scripts\pythonw.exe'
$Icone = Join-Path $Raiz 'recursos\icone_studio_spurgeon.ico'
$Log = Join-Path $Raiz 'instalacao.log'
$script:Pendencias = New-Object System.Collections.Generic.List[string]


# --- mensagens ----------------------------------------------------------------------------------
function Dizer([string]$Texto, [string]$Cor = 'Gray') {
    Write-Host $Texto -ForegroundColor $Cor
    try {
        Add-Content -LiteralPath $Log -Value ('{0:HH:mm:ss} {1}' -f (Get-Date), $Texto) -Encoding UTF8
    } catch { }
}
function Passo([string]$Titulo) { Dizer ''; Dizer "== $Titulo" 'Cyan' }
function Ok([string]$Texto) { Dizer "  [OK]    $Texto" 'Green' }
function Aviso([string]$Texto) { Dizer "  [AVISO] $Texto" 'Yellow' }
function Erro([string]$Texto) { Dizer "  [ERRO]  $Texto" 'Red' }
function Pendencia([string]$Texto) { $script:Pendencias.Add($Texto); Aviso $Texto }

function Perguntar([string]$Pergunta) {
    if ($Silencioso) { return $true }
    $resposta = Read-Host "$Pergunta [S/n]"
    return ($resposta -eq '' -or $resposta -match '^[sSyY]')
}


# --- download e extração ------------------------------------------------------------------------
function Hash-De([string]$Arquivo) { (Get-FileHash -LiteralPath $Arquivo -Algorithm SHA256).Hash }

function Baixar-Arquivo {
    # Baixa `Url` para `Destino` (retoma se cair) e confere o SHA-256, quando informado.
    param([string]$Url, [string]$Destino, [string]$Nome, [string]$Sha256 = '')
    if ($Sha256 -and (Test-Path -LiteralPath $Destino) -and ((Hash-De $Destino) -eq $Sha256)) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destino) | Out-Null
    $parcial = "$Destino.baixando"
    for ($tentativa = 1; $tentativa -le 3; $tentativa++) {
        Dizer "  Baixando $Nome ..."
        $feito = $false
        $curl = Join-Path $env:SystemRoot 'System32\curl.exe'   # mostra o progresso e retoma
        if (Test-Path -LiteralPath $curl) {
            $modo = if ([Console]::IsOutputRedirected) { '-sS' } else { '--progress-bar' }
            & $curl -L --fail --retry 5 --retry-delay 3 -C - $modo -o $parcial $Url
            $feito = ($LASTEXITCODE -eq 0)
        }
        if (-not $feito) {
            Remove-Item -LiteralPath $parcial -Force -ErrorAction SilentlyContinue
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $parcial
                $feito = $true
            } catch {
                if ($tentativa -ge 3) {
                    throw "Não foi possível baixar $Nome ($($_.Exception.Message)). Confira a internet e rode o instalador de novo."
                }
                Start-Sleep -Seconds (3 * $tentativa)
            }
        }
        if ($feito -and $Sha256 -and ((Hash-De $parcial) -ne $Sha256)) {
            Dizer '  O arquivo baixado não confere com o esperado; baixando de novo.' 'Yellow'
            Remove-Item -LiteralPath $parcial -Force -ErrorAction SilentlyContinue
            $feito = $false
            if ($tentativa -ge 3) { throw "O arquivo de $Nome baixou corrompido três vezes. Tente de novo mais tarde." }
        }
        if ($feito) { break }
    }
    Move-Item -LiteralPath $parcial -Destination $Destino -Force
}

function Extrair-Zip([string]$Zip, [string]$Pasta) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path -LiteralPath $Pasta) { Remove-Item -LiteralPath $Pasta -Recurse -Force }
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Pasta)
    Desbloquear-Pasta $Pasta
}

function Desbloquear-Pasta([string]$Pasta) {
    # Arquivos que vieram da internet por um navegador (ou por um ZIP baixado) levam a marca "baixado
    # da internet", e é ela que faz o SmartScreen do Windows bloquear. Tirar a marca não muda o
    # conteúdo; sem marca, o arquivo é tratado como qualquer outro do computador.
    if (-not (Test-Path -LiteralPath $Pasta)) { return }
    try {
        Get-ChildItem -LiteralPath $Pasta -Recurse -File -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
    } catch { }
}

function Desbloquear-O-Programa {
    # Só os arquivos pequenos do programa (código, scripts, recursos); os modelos de IA não têm marca.
    try {
        Get-ChildItem -LiteralPath $Raiz -File -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
    } catch { }
    foreach ($sub in 'src', 'recursos') { Desbloquear-Pasta (Join-Path $Raiz $sub) }
}

function Esperar-O-Programa-Fechar {
    if ($AguardarPid -le 0) { return }
    Dizer '  Esperando o Studio Spurgeon fechar para atualizar os arquivos dele ...'
    try { Wait-Process -Id $AguardarPid -Timeout 90 -ErrorAction Stop } catch { }
    if (Get-Process -Id $AguardarPid -ErrorAction SilentlyContinue) {
        Aviso 'O Studio Spurgeon ainda está aberto; se a instalação reclamar de arquivos em uso, feche-o e rode o instalar.bat de novo.'
    }
}

function Conferir-A-Protecao-Do-Windows {
    # Controle Inteligente de Aplicativos (Windows 11): 0 = desligado, 1 = ligado, 2 = em avaliação.
    try {
        $estado = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' `
            -Name VerifiedAndReputablePolicyState -ErrorAction Stop).VerifiedAndReputablePolicyState
    } catch { return }
    if ($estado -in 1, 2) {
        Aviso 'O "Controle Inteligente de Aplicativos" do Windows está ligado: ele bloqueia programas sem assinatura digital, como o Python, o FFmpeg e o uv que este instalador baixa.'
        Aviso 'Se o Windows bloquear algo, desligue em: Segurança do Windows > Controle de aplicativos e do navegador > Configurações do Controle Inteligente de Aplicativos > Desativado. Em algumas versões do Windows ele só volta a ser ligado reinstalando o sistema.'
    }
}


# --- winget (plano B quando o download direto falha) --------------------------------------------
function Instalar-Pelo-Winget([string]$Id, [string]$Nome) {
    # O winget (da Microsoft) vem no Windows 11 e em Windows 10 atualizados; se faltar, não há plano B.
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { return $false }
    Dizer "  Tentando instalar $Nome pelo winget ..."
    try {
        & winget.exe install --id $Id -e --silent --accept-package-agreements --accept-source-agreements
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}


# --- conferências do computador -----------------------------------------------------------------
function Conferir-Computador {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'O Studio Spurgeon precisa do Windows de 64 bits.'
    }
    if ([Environment]::OSVersion.Version.Build -lt 17763) {
        throw 'O Studio Spurgeon precisa do Windows 10 (versão 1809 ou mais nova) ou do Windows 11.'
    }
    Ok "Windows $([Environment]::OSVersion.Version) de 64 bits"
    if ($Raiz -match 'OneDrive') {
        Aviso 'A pasta do programa está dentro do OneDrive: a sincronização pode atrapalhar os vídeos grandes. Prefira, por exemplo, C:\Spurgeon.'
    }
    if ($Raiz -like "$env:ProgramFiles*" -or $Raiz -like "${env:ProgramFiles(x86)}*") {
        throw 'Não instale dentro de "Arquivos de Programas" (o programa precisa gravar na própria pasta). Mova a pasta Spurgeon para, por exemplo, C:\Spurgeon.'
    }
    $unidade = Get-PSDrive -Name ($Raiz.Substring(0, 1)) -ErrorAction SilentlyContinue
    if ($unidade) {
        $livre = [math]::Round($unidade.Free / 1GB, 1)
        if ($livre -lt $EspacoMinimoGB) {
            throw "Só há $livre GB livres em $($unidade.Name):. São necessários pelo menos $EspacoMinimoGB GB (o recomendado é $EspacoRecomendadoGB GB, com os modelos de IA)."
        }
        if ($livre -lt $EspacoRecomendadoGB) {
            Aviso "Há $livre GB livres em $($unidade.Name):; o recomendado são $EspacoRecomendadoGB GB (programas e modelos de IA). Cada sermão ainda usa ~6 GB."
        } else {
            Ok "$livre GB livres em $($unidade.Name):"
        }
    }
}


# --- 1. Visual C++ ------------------------------------------------------------------------------
function Visual-Cpp-Instalado {
    foreach ($dll in 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll') {
        if (-not (Test-Path -LiteralPath (Join-Path $env:SystemRoot "System32\$dll"))) { return $false }
    }
    return $true
}

function Garantir-Visual-Cpp {
    if (Visual-Cpp-Instalado) { Ok 'Visual C++ já instalado'; return }
    Dizer '  O Visual C++ (runtime da Microsoft) não está instalado; as bibliotecas de IA precisam dele.'
    $exe = Join-Path $env:TEMP 'vc_redist.x64.exe'
    Baixar-Arquivo -Url $VcRedistUrl -Destino $exe -Nome 'Microsoft Visual C++ Redistributable (26 MB)'
    $assinatura = Get-AuthenticodeSignature -LiteralPath $exe
    if ($assinatura.Status -ne 'Valid') {
        Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue
        throw 'O instalador do Visual C++ baixado não tem a assinatura digital da Microsoft; foi descartado.'
    }
    Dizer '  Instalando (o Windows vai pedir permissão de administrador) ...'
    try {
        $processo = Start-Process -FilePath $exe -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait -PassThru
    } catch {
        Pendencia 'O Visual C++ não foi instalado (a permissão de administrador foi negada). Sem ele, o programa pode não abrir. Rode o instalar.bat de novo e aceite a permissão.'
        return
    } finally {
        Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue
    }
    if ($processo.ExitCode -in 0, 1638) { Ok 'Visual C++ instalado' }
    elseif ($processo.ExitCode -eq 3010) { Ok 'Visual C++ instalado (o Windows pede reiniciar; faça isso depois da instalação)' }
    else { Pendencia "O instalador do Visual C++ terminou com o código $($processo.ExitCode)." }
}


# --- 2. uv --------------------------------------------------------------------------------------
function Uv-Funciona([string]$Caminho) {
    try { & $Caminho --version *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

function Achar-Uv {
    $comando = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($comando -and (Uv-Funciona $comando.Source)) { return $comando.Source }
    $candidatos = @(
        (Join-Path $PastaFerramentas 'uv\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe'),
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
    )
    $pacotes = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $pacotes) {
        $candidatos += @(Get-ChildItem -LiteralPath $pacotes -Directory -Filter 'astral-sh.uv*' |
            ForEach-Object { Join-Path $_.FullName 'uv.exe' })
    }
    foreach ($c in $candidatos) {
        if ((Test-Path -LiteralPath $c) -and (Uv-Funciona $c)) { return $c }
    }
    return $null
}

function Garantir-Uv {
    $uv = Achar-Uv
    if ($uv) { $script:Uv = $uv; Ok "uv encontrado: $uv"; return }
    $uv = Join-Path $PastaFerramentas 'uv\uv.exe'
    try {
        $zip = Join-Path $PastaFerramentas 'uv.zip'
        Baixar-Arquivo -Url $UvUrl -Destino $zip -Sha256 $UvSha256 -Nome "uv $UvVersao (16 MB)"
        Extrair-Zip $zip (Join-Path $PastaFerramentas 'uv')
        Remove-Item -LiteralPath $zip -Force
        if (-not (Uv-Funciona $uv)) {
            throw "o uv baixado não abriu (o Windows ou o antivírus pode ter bloqueado: $uv)"
        }
    } catch {
        Aviso "O download direto do uv não deu certo ($($_.Exception.Message))."
        if (-not (Instalar-Pelo-Winget 'astral-sh.uv' 'o uv')) {
            throw "Não foi possível instalar o uv: $($_.Exception.Message)"
        }
        $uv = Achar-Uv
        if (-not $uv) {
            throw 'O uv foi instalado pelo winget, mas não foi encontrado. Feche esta janela e rode o instalar.bat de novo.'
        }
        $script:Uv = $uv
        Ok "uv instalado pelo winget: $uv"
        return
    }
    $script:Uv = $uv
    Ok "uv $UvVersao instalado em $uv"
}


# --- 3. Python e bibliotecas --------------------------------------------------------------------
# O Python 3.11 lê os arquivos .pth na página de código do Windows (cp1252), não em UTF-8. O .pth da
# instalação editável guarda o caminho da pasta src em UTF-8; com acento na pasta (C:\Users\João\...)
# o caminho vira lixo, o Python o ignora em silêncio e "import spurgeon" falha. Este arquivo acha a
# pasta src pela posição dele no ambiente, sem depender do .pth.
$SiteCustomize = @'
# Criado pelo instalar.ps1 (Studio Spurgeon). Pode apagar: o instalar.bat o recria.
#
# O Python 3.11 le os arquivos .pth na pagina de codigo do Windows, nao em UTF-8; por isso o .pth da
# instalacao editavel perde o caminho quando a pasta do programa tem acento (C:\Users\João\...).
# Aqui a pasta src e achada pela posicao deste arquivo (.venv/Lib/site-packages -> pasta do programa).
import sys
from pathlib import Path

_src = Path(__file__).resolve().parents[3] / "src"
if (_src / "spurgeon").is_dir() and str(_src) not in sys.path:
    sys.path.append(str(_src))
'@

function Garantir-Caminho-Do-Codigo {
    $arquivo = Join-Path $Raiz '.venv\Lib\site-packages\sitecustomize.py'
    if (Test-Path -LiteralPath $arquivo) {
        $atual = [IO.File]::ReadAllText($arquivo)
        if ($atual -notmatch 'Criado pelo instalar\.ps1') {
            Aviso 'Já existe um sitecustomize.py de outro programa no ambiente; não foi alterado.'
            return
        }
    }
    [IO.File]::WriteAllText($arquivo, $SiteCustomize, (New-Object Text.UTF8Encoding($false)))
}

function Sincronizar-Ambiente {
    $cfg = Join-Path $Raiz '.venv\pyvenv.cfg'
    if (Test-Path -LiteralPath $cfg) {
        $linha = Select-String -LiteralPath $cfg -Pattern '^\s*home\s*=\s*(.+?)\s*$' | Select-Object -First 1
        if ($linha) {
            $pastaDoPython = $linha.Matches[0].Groups[1].Value
            if (-not (Test-Path -LiteralPath $pastaDoPython)) {
                Dizer '  O ambiente (.venv) veio de outro computador e não serve aqui; recriando.'
                Remove-Item -LiteralPath (Join-Path $Raiz '.venv') -Recurse -Force
            }
        }
    }
    Dizer '  Instalando o Python 3.11 e as bibliotecas (alguns minutos na primeira vez) ...'
    for ($tentativa = 1; $tentativa -le 2; $tentativa++) {
        & $script:Uv sync --no-dev --extra ui --extra transcricao --extra shorts
        if ($LASTEXITCODE -eq 0) { break }
        if ($tentativa -ge 2) {
            throw 'Falha ao instalar as bibliotecas Python (veja a mensagem do uv acima; confira a internet).'
        }
        Dizer '  Falhou; tentando de novo ...' 'Yellow'
    }
    Garantir-Caminho-Do-Codigo
    & $Py -X utf8 -c 'import tkinter, customtkinter, faster_whisper, spacy, ollama, PIL, spurgeon'
    if ($LASTEXITCODE -ne 0) {
        throw 'As bibliotecas foram instaladas, mas o Python não consegue abri-las (veja a mensagem acima). Se for "DLL load failed", falta o Visual C++: rode o instalador de novo e aceite a permissão de administrador.'
    }
    Ok 'Python 3.11 e bibliotecas prontos'
}


# --- 4. FFmpeg ----------------------------------------------------------------------------------
function Ffmpeg-Serve([string]$Exe) {
    # O aplicativo usa estes filtros e codificadores (a mesma lista da pré-verificação).
    try {
        $filtros = (& $Exe -hide_banner -filters 2>$null) | Out-String
        $codificadores = (& $Exe -hide_banner -encoders 2>$null) | Out-String
    } catch { return $false }
    # Cada linha é "<flags> <nome> <entrada->saída> <descrição>" nos filtros e "<flags> <nome> <descrição>"
    # nos codificadores; a largura das flags já mudou entre versões do FFmpeg (3 colunas, depois 2).
    foreach ($nome in 'ass', 'loudnorm', 'zscale', 'tonemap') {
        if ($filtros -notmatch "(?m)^\s*\S{2,3}\s+$nome\s+\S+->\S+") { return $false }
    }
    foreach ($nome in 'libx264', 'aac') {
        if ($codificadores -notmatch "(?m)^\s*[VAS]\S{5}\s+$nome\s") { return $false }
    }
    return $true
}

function Achar-Ffmpeg {
    $candidatos = @()
    $local = Join-Path $PastaFerramentas 'ffmpeg'
    if (Test-Path -LiteralPath $local) {
        $candidatos += @(Get-ChildItem -LiteralPath $local -Recurse -Filter ffmpeg.exe | ForEach-Object FullName)
    }
    $comando = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($comando) { $candidatos += $comando.Source }
    $pacotes = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $pacotes) {
        $candidatos += @(Get-ChildItem -LiteralPath $pacotes -Directory -Filter 'Gyan.FFmpeg*' |
            ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Recurse -Filter ffmpeg.exe } |
            ForEach-Object FullName)
    }
    foreach ($c in $candidatos) {
        $ffprobe = Join-Path (Split-Path -Parent $c) 'ffprobe.exe'
        if ((Test-Path -LiteralPath $ffprobe) -and (Ffmpeg-Serve $c)) { return $c }
    }
    return $null
}

function Garantir-Ffmpeg {
    $ffmpeg = Achar-Ffmpeg
    if ($ffmpeg) { Ok "FFmpeg encontrado: $ffmpeg"; return }
    Dizer '  Nenhum FFmpeg completo foi encontrado; baixando uma cópia para a pasta do programa.'
    $zip = Join-Path $PastaFerramentas "ffmpeg-$FfmpegVersao.zip"
    Baixar-Arquivo -Url $FfmpegUrl -Destino $zip -Sha256 $FfmpegSha256 -Nome "FFmpeg $FfmpegVersao (258 MB)"
    Dizer '  Extraindo ...'
    $temporaria = Join-Path $PastaFerramentas 'ffmpeg_extraindo'
    Extrair-Zip $zip $temporaria
    $interna = Get-ChildItem -LiteralPath $temporaria -Directory | Select-Object -First 1
    $destino = Join-Path $PastaFerramentas 'ffmpeg'
    if (Test-Path -LiteralPath $destino) { Remove-Item -LiteralPath $destino -Recurse -Force }
    Move-Item -LiteralPath $interna.FullName -Destination $destino
    Remove-Item -LiteralPath $temporaria -Recurse -Force
    Remove-Item -LiteralPath $zip -Force
    $ffmpeg = Join-Path $destino 'bin\ffmpeg.exe'
    if (-not (Ffmpeg-Serve $ffmpeg)) { throw "O FFmpeg baixado não tem os recursos que o programa usa ($ffmpeg)." }
    Ok "FFmpeg $FfmpegVersao instalado em $destino"
}


# --- 5. Ollama ----------------------------------------------------------------------------------
function Achar-Ollama {
    $comando = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    foreach ($c in @((Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
                     (Join-Path $env:ProgramFiles 'Ollama\ollama.exe'))) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

function Garantir-Ollama {
    $ollama = Achar-Ollama
    if ($ollama) { Ok "Ollama encontrado: $ollama"; return }
    Dizer '  O Ollama (a IA local que escolhe os Shorts) não está instalado.'
    $exe = Join-Path $env:TEMP 'OllamaSetup.exe'
    try {
        Baixar-Arquivo -Url $OllamaUrl -Destino $exe -Nome 'Ollama (cerca de 1,7 GB)'
        $assinatura = Get-AuthenticodeSignature -LiteralPath $exe
        if ($assinatura.Status -in 'HashMismatch', 'NotTrusted') {
            throw 'o instalador baixado não passou na conferência da assinatura digital'
        }
        Dizer '  Instalando o Ollama (pode levar alguns minutos) ...'
        # Sem -Wait: no fim o instalador abre o aplicativo do Ollama, que fica rodando na bandeja, e o
        # -Wait do PowerShell 5.1 espera por esse aplicativo também (a instalação parecia travada).
        $processo = Start-Process -FilePath $exe -ArgumentList '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES' -PassThru
        $null = $processo.Handle   # sem isto o ExitCode pode vir vazio
        if (-not $processo.WaitForExit(20 * 60 * 1000)) {
            Stop-Process -Id $processo.Id -Force -ErrorAction SilentlyContinue
            throw 'o instalador do Ollama demorou mais de 20 minutos'
        }
        if ($processo.ExitCode -ne 0) { throw "o instalador terminou com o código $($processo.ExitCode)" }
    } catch {
        Aviso "A instalação direta do Ollama não deu certo ($($_.Exception.Message))."
        if (-not (Instalar-Pelo-Winget 'Ollama.Ollama' 'o Ollama') -and -not (Achar-Ollama)) {
            Pendencia "O Ollama não foi instalado. Os Shorts precisam dele: instale em https://ollama.com/download (ou, no PowerShell: winget install Ollama.Ollama) e rode o instalar.bat de novo."
            return
        }
    } finally {
        Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue
    }
    $ollama = Achar-Ollama
    if ($ollama) { Ok "Ollama instalado: $ollama" }
    else { Pendencia 'O Ollama foi instalado, mas não foi encontrado. Abra o Ollama pelo Menu Iniciar uma vez.' }
}


# --- 6. Modelos de IA ---------------------------------------------------------------------------
function Baixar-Modelos {
    Dizer '  Whisper (~1,6 GB), filtro de áudio (~26 MB) e o modelo do Ollama (~5 GB). Pode demorar.'
    & $Py -X utf8 -m spurgeon baixar-modelos
    if ($LASTEXITCODE -ne 0) {
        Pendencia 'Alguns modelos não foram baixados agora. Sem problema: o programa baixa sozinho na primeira vez que precisar (com internet).'
    } else {
        Ok 'Modelos de IA prontos'
    }
}


# --- 7. Atalhos ---------------------------------------------------------------------------------
function Criar-Atalho([string]$Pasta, [string]$Nome) {
    New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $atalho = $shell.CreateShortcut((Join-Path $Pasta "$Nome.lnk"))
    $atalho.TargetPath = $Pyw              # pythonw: abre a janela sem o console preto
    $atalho.Arguments = '-m spurgeon gui'
    $atalho.WorkingDirectory = $Raiz
    $atalho.Description = 'Studio Spurgeon'
    if (Test-Path -LiteralPath $Icone) { $atalho.IconLocation = $Icone }
    $atalho.Save()
}

function Criar-Atalhos {
    foreach ($pasta in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        try {
            Criar-Atalho -Pasta $pasta -Nome 'Studio Spurgeon'
            Ok "Atalho criado em $pasta"
        } catch {
            Pendencia "Não foi possível criar o atalho em ${pasta}: $($_.Exception.Message)"
        }
    }
}


# --- execução -----------------------------------------------------------------------------------
if ($MyInvocation.InvocationName -eq '.') { return }   # carregado só para testar as funções

try {
    Set-Location -LiteralPath $Raiz
    Dizer ''
    Dizer '=== Studio Spurgeon - instalação ===' 'Cyan'
    Dizer "Pasta: $Raiz"

    Esperar-O-Programa-Fechar
    Desbloquear-O-Programa

    Passo 'Conferindo o computador'
    Conferir-Computador
    Conferir-A-Protecao-Do-Windows
    if (-not (Perguntar 'Instalar agora? (faltando algo, serão baixados até ~2 GB, mais ~7 GB de modelos de IA)')) {
        Dizer 'Instalação cancelada.'
        exit 2
    }

    Passo '1/7  Visual C++'
    Garantir-Visual-Cpp
    Passo '2/7  uv (gerenciador do Python)'
    Garantir-Uv
    Passo '3/7  Python 3.11 e bibliotecas'
    Sincronizar-Ambiente
    Passo '4/7  FFmpeg'
    Garantir-Ffmpeg

    Passo '5/7  Ollama (IA dos Shorts)'
    if ($SemOllama) { Aviso 'Pulado (-SemOllama): sem o Ollama não há Shorts.' } else { Garantir-Ollama }

    Passo '6/7  Modelos de IA'
    if ($SemModelos) { Aviso 'Pulado (-SemModelos): o programa baixa cada modelo na primeira vez que precisar.' }
    elseif (Perguntar 'Baixar os modelos de IA agora (~7 GB)? Assim o primeiro vídeo já roda sem internet') { Baixar-Modelos }
    else { Aviso 'Pulado: o programa baixa cada modelo na primeira vez que precisar (com internet).' }

    Passo '7/7  Atalhos'
    if ($SemAtalhos) { Aviso 'Pulado (-SemAtalhos).' } else { Criar-Atalhos }

    Passo 'Conferência final'
    & $Py -X utf8 -m spurgeon doctor
    $conferencia = $LASTEXITCODE

    Dizer ''
    if ($conferencia -ne 0) {
        Erro 'A conferência final encontrou problemas (linhas [ERRO] acima). Corrija e rode o instalar.bat de novo.'
        exit 1
    }
    if ($script:Pendencias.Count -gt 0) {
        Dizer '=== Pronto, com avisos ===' 'Yellow'
        foreach ($p in $script:Pendencias) { Dizer "  - $p" 'Yellow' }
    } else {
        Dizer '=== Tudo pronto ===' 'Green'
    }
    if ($script:Pendencias.Count -gt 0) {
        Dizer 'O que ficou pendente acima não impede de abrir o programa. Para tentar de novo, rode o instalar.bat: ele pula o que já está pronto.' 'Gray'
    }
    if ($Abrir) {
        Dizer 'Abrindo o Studio Spurgeon ...'
        Start-Process -FilePath $Pyw -ArgumentList '-m', 'spurgeon', 'gui' -WorkingDirectory $Raiz
    }
    elseif ($SemAtalhos) { Dizer 'Para abrir o programa: dê dois cliques em iniciar.bat.' }
    else { Dizer 'Para abrir o programa: use o atalho "Studio Spurgeon" da Área de Trabalho (ou o iniciar.bat).' }
    exit 0
} catch {
    Dizer ''
    Erro $_.Exception.Message
    Dizer "O registro desta instalação está em: $Log" 'Gray'
    exit 1
}
