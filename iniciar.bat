@echo off
setlocal
cd /d "%~dp0"
title Studio Spurgeon

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERRO] O "uv" nao foi encontrado. Rode o instalar.bat primeiro.
    pause
    exit /b 1
)

uv run --no-dev --extra ui --extra transcricao --extra shorts spurgeon gui
if errorlevel 1 pause
