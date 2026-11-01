@echo off
setlocal
cd /d "%~dp0.."
python manage.py start
endlocal
