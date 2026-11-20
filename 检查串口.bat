@echo off
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -c "from win_serial import list_ports; print('COM ports:', list_ports())"
echo.
pause
