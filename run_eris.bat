@echo off
setlocal enabledelayedexpansion
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
set "HF_HOME=%~dp0.cache\huggingface"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "XDG_CACHE_HOME=%~dp0.cache"
set "ERIS_AGENT_SERVICE_DISCOVERY=%~dp0.eris_state\agent_service.json"
set "ERIS_HOME=%~dp0.eris_home"
set "ERIS_STATE_DIR=%~dp0.eris_state"

if not exist "%~dp0config\secrets.json" (
    if exist "%~dp0config\secrets.example.json" (
        copy "%~dp0config\secrets.example.json" "%~dp0config\secrets.json" >nul
    )
)

if exist "%~dp0Eris.exe" (
    echo [ERIS] Khởi chạy giao diện Godot Body Desktop...
    start "Eris Body" "%~dp0Eris.exe"
) else (
    echo [THÔNG BÁO] Chưa tìm thấy Eris.exe (Godot GUI), khởi động Python Runtime trực tiếp...
    call "%~dp0run_runtime.bat"
)

