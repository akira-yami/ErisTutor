@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

set "PYTHON="
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PYTHON if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
if not defined PYTHON if exist "%ROOT_DIR%\..\.venv\Scripts\python.exe" set "PYTHON=%ROOT_DIR%\..\.venv\Scripts\python.exe"
if not defined PYTHON if exist "%ROOT_DIR%\python\python.exe" set "PYTHON=%ROOT_DIR%\python\python.exe"
if not defined PYTHON set "PYTHON=python"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
if not defined PYTHONPATH (
    set "PYTHONPATH=%ROOT_DIR%\src"
) else (
    set "PYTHONPATH=%ROOT_DIR%\src;%PYTHONPATH%"
)
if not defined ERIS_PROJECT_ROOT set "ERIS_PROJECT_ROOT=%ROOT_DIR%"
set "ERIS_STANDALONE=1"
if not defined HF_HOME set "HF_HOME=%ROOT_DIR%\.cache\huggingface"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
if not defined XDG_CACHE_HOME set "XDG_CACHE_HOME=%ROOT_DIR%\.cache"
if not defined ERIS_AGENT_SERVICE_DISCOVERY set "ERIS_AGENT_SERVICE_DISCOVERY=%ROOT_DIR%\.eris_state\agent_service.json"
if not defined ERIS_HOME set "ERIS_HOME=%ROOT_DIR%\.eris_home"
if not defined ERIS_STATE_DIR set "ERIS_STATE_DIR=%ROOT_DIR%\.eris_state"

if not exist "%ROOT_DIR%\.env" (
    if exist "%ROOT_DIR%\.env.example" (
        echo [THÔNG BÁO] Chưa tìm thấy .env, tự động khởi tạo từ .env.example...
        copy "%ROOT_DIR%\.env.example" "%ROOT_DIR%\.env" >nul
    )
)

if not exist "%ROOT_DIR%\config\secrets.json" (
    if exist "%ROOT_DIR%\config\secrets.example.json" (
        echo [THÔNG BÁO] Chưa tìm thấy config\secrets.json, tự động khởi tạo từ secrets.example.json...
        copy "%ROOT_DIR%\config\secrets.example.json" "%ROOT_DIR%\config\secrets.json" >nul
        echo [HƯỚNG DẪN] Vui lòng cập nhật API key vào file config\secrets.json hoặc .env nếu cần.
    )
)

rem Dynamically find entry point
set "ENTRY_POINT="
if exist "%ROOT_DIR%\src\vision.py" set "ENTRY_POINT=%ROOT_DIR%\src\vision.py"
if not defined ENTRY_POINT if exist "%ROOT_DIR%\vision.py" set "ENTRY_POINT=%ROOT_DIR%\vision.py"
if not defined ENTRY_POINT if exist "%ROOT_DIR%\src\vision.pyc" set "ENTRY_POINT=%ROOT_DIR%\src\vision.pyc"
if not defined ENTRY_POINT set "ENTRY_POINT=%ROOT_DIR%\src\vision.py"

echo [ERIS] Khởi động ErisTutor Python Runtime (%ENTRY_POINT%)...
"%PYTHON%" "%ENTRY_POINT%" %*
if errorlevel 1 (
    echo [LỖI] Tiến trình ErisTutor kết thúc với mã lỗi %errorlevel%.
    pause
)
