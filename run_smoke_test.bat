@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

echo [TEST] Chạy kiểm tra ruff lint...
"%PYTHON%" -m ruff check src tests
if errorlevel 1 (
    echo [LỖI] Linting thất bại!
    pause
    exit /b 1
)

echo [TEST] Chạy kiểm thử pytest toàn diện...
"%PYTHON%" -m pytest -q tests/
if errorlevel 1 (
    echo [LỖI] Có test case thất bại!
    pause
    exit /b 1
)

echo [OK] Toàn bộ kiểm thử và kiểm tra mã nguồn đều thành công!
pause

