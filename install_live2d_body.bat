@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON="
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON if exist "%~dp0..\.venv\Scripts\python.exe" set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not defined PYTHON if exist "%~dp0python\python.exe" set "PYTHON=%~dp0python\python.exe"
if not defined PYTHON set "PYTHON=python"

echo [Live2D] Python: %PYTHON%
"%PYTHON%" -m pip install -r "%~dp0requirements-live2d.txt"
if errorlevel 1 (
    echo [Live2D] Cai dependency that bai.
    pause
    exit /b 1
)

echo [Live2D] OK.
pause
