@echo off
setlocal
cd /d "%~dp0"
title Studio Spurgeon - Instalacao

rem O trabalho todo esta no instalar.ps1 (PowerShell vem em todo Windows 10/11).
rem Opcoes: -SemModelos  -SemOllama  -SemAtalhos  -Silencioso
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL%" (
    echo [ERRO] O PowerShell nao foi encontrado neste Windows.
    pause
    exit /b 1
)

"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
set "CODIGO=%ERRORLEVEL%"

echo %* | find /i "-Silencioso" >nul
if errorlevel 1 (
    echo.
    pause
)
exit /b %CODIGO%
