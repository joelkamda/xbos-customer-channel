@echo off
setlocal EnableExtensions
python scripts\verify_xc4.py
exit /b %ERRORLEVEL%
