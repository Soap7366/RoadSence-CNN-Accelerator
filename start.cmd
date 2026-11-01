@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build\bootstrap.ps1" -Mode start
if errorlevel 1 pause
endlocal
