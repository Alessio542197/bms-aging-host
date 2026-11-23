@echo off
setlocal
set "FILE=%~1"
if "%FILE%"=="" set /p FILE=Drag the EXE file here, then press Enter: 
set "FILE=%FILE:"=%"
if not exist "%FILE%" (
  echo File not found: %FILE%
  pause
  exit /b 1
)
echo.
echo File: %FILE%
echo.
for /f "skip=1 delims=" %%A in ('certutil -hashfile "%FILE%" SHA256 ^| findstr /v /c:"CertUtil"') do (
  set "HASH=%%A"
  goto :done
)
:done
set "HASH=%HASH: =%"
echo SHA256:
echo %HASH%
echo.
echo (the hash has been copied to the clipboard)
echo %HASH%| clip
pause
