@echo off
setlocal
python "%~dp0manage.py" start
if errorlevel 1 pause
endlocal
