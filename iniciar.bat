@echo off
setlocal
cd /d "%~dp0"

rem Abre o aplicativo pelo pythonw.exe (sem console) e fecha esta janela na hora.
rem Se algo der errado ao abrir, o erro aparece numa caixa e fica no interface.log
rem (%LOCALAPPDATA%\Spurgeon\Logs). Para ver a saida no terminal: uv run spurgeon gui
if not exist ".venv\Scripts\pythonw.exe" (
    echo O Studio Spurgeon ainda nao foi instalado nesta pasta.
    echo De dois cliques em instalar.bat e depois abra este arquivo de novo.
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m spurgeon gui
