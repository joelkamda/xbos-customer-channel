@echo off
setlocal
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
python scripts\verify_cr1.py
if errorlevel 1 exit /b %errorlevel%
exit /b 0
