@echo off
setlocal
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
python scripts\verify_xc3.py
exit /b %ERRORLEVEL%
