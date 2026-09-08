@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%src"
python scripts\verify_cr3.py
exit /b %ERRORLEVEL%
