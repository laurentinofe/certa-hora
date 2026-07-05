@echo off
title Certa Hora - Servidor
cd /d "%~dp0"

set "PYTHON=C:\Users\Proeng\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%PYTHON%" (
    echo.
    echo Nao foi possivel localizar o Python usado pelo Codex.
    echo Instale o Python 3 ou ajuste o caminho no arquivo iniciar.bat.
    echo.
    pause
    exit /b 1
)

echo.
echo Iniciando o sistema Certa Hora...
echo Acesse no navegador: http://127.0.0.1:8000
echo Para encerrar o servidor, pressione Ctrl+C.
echo.

"%PYTHON%" server.py

echo.
echo O servidor foi encerrado.
pause
