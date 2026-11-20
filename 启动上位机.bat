@echo off
cd /d "%~dp0"
set "PYW=%~dp0runtime\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "app.py"
if errorlevel 1 (
  echo Failed to start: Python not found.
  echo Keep the runtime folder next to this file, or install Python 3.8+.
  pause
)
