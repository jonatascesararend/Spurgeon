@echo off
setlocal
cd /d "%~dp0"
title Studio Spurgeon - Instalacao

rem O trabalho todo esta no instalar.ps1 (PowerShell vem em todo Windows 10/11).
rem Opcoes: -SemModelos  -SemOllama  -SemAtalhos  -Silencioso  -AguardarPid N  -Abrir
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL%" (
    echo [ERRO] O PowerShell nao foi encontrado neste Windows.
    pause
    exit /b 1
)

"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
set "CODIGO=%ERRORLEVEL%"

rem Sem -Silencioso a janela espera uma tecla no fim. Com -Silencioso ela fecha sozinha; so que na
rem atualizacao feita pelo programa (-Abrir) ela fica aberta se der erro, para ler o que houve.
rem O find do Windows pelo caminho inteiro: o Git para Windows traz outro "find" que quebra isto.
set "FIND=%SystemRoot%\System32\find.exe"
echo %* | "%FIND%" /i "-Silencioso" >nul
if errorlevel 1 goto pausar
if "%CODIGO%"=="0" goto fim
echo %* | "%FIND%" /i "-Abrir" >nul
if errorlevel 1 goto fim
:pausar
echo.
pause
:fim
exit /b %CODIGO%
