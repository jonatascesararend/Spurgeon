@echo off
setlocal
cd /d "%~dp0"
title Studio Spurgeon - Instalacao

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERRO] O "uv" nao foi encontrado.
    echo Instale com: winget install astral-sh.uv
    echo Depois feche este terminal, abra de novo e rode instalar.bat.
    pause
    exit /b 1
)

echo Criando o ambiente Python 3.11 e instalando as dependencias...
uv sync --no-dev --extra ui --extra transcricao
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias.
    pause
    exit /b 1
)

echo.
echo Verificando o ambiente...
uv run spurgeon doctor

echo.
pause
