@echo off
cd /d "%~dp0"
set "PYW=%~dp0runtime\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "app.py" --minimized
if errorlevel 1 (
  echo Failed to start: Python not found.
  pause
)
