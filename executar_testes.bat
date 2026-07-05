@echo off
title Certa Hora - Testes Automatizados
cd /d "%~dp0"

set "PYTHON=C:\Users\Proeng\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo.
echo Executando testes automatizados do sistema...
echo.
"%PYTHON%" -m unittest discover -s tests -p "test_*.py" -v

echo.
pause
