@echo off
setlocal
set "URL=%~1"
if "%URL%"=="" set /p URL=Manifest URL: 
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_update_url.ps1" "%URL%"
pause
