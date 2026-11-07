@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "ERIS_PROJECT_ROOT=%~dp0"

if not exist "%~dp0config\secrets.json" (
    if exist "%~dp0config\secrets.example.json" (
        echo [THÔNG BÁO] Chưa tìm thấy config\secrets.json, tự động khởi tạo từ secrets.example.json...
        copy "%~dp0config\secrets.example.json" "%~dp0config\secrets.json" >nul
        echo [HƯỚNG DẪN] Vui lòng cập nhật API key vào file config\secrets.json hoặc .env nếu cần.
    )
)

echo [ERIS] Khởi động ErisTutor Python Runtime...
"%PYTHON%" "%~dp0src\vision.py" %*
if errorlevel 1 (
    echo [LỖI] Tiến trình ErisTutor kết thúc với mã lỗi %errorlevel%.
    pause
)
