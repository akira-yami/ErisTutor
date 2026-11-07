@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0python\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [Live2D] Python: %PYTHON%
"%PYTHON%" -m pip install -r "%~dp0requirements-live2d.txt"
if errorlevel 1 (
    echo [Live2D] Cai dependency that bai.
    pause
    exit /b 1
)

echo [Live2D] OK.
pause
