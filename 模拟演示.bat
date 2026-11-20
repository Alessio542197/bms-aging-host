@echo off
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "app.py" --demo
pause
