@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%~dp0src"
python "%~dp0scripts\verify_cr2.py" || exit /b 1
python -m unittest tests.test_cr2_authoritative_provenance || exit /b 1
python -m unittest discover -s "%~dp0tests" -p "test_*.py" || exit /b 1
endlocal
