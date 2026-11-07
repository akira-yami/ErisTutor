@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [ERIS] Dang tao goi zip export ma nguon cho AI Web doc...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\export_ai_zip.ps1"
if errorlevel 1 (
    echo [LOI] Co loi xay ra khi tao file zip!
    pause
    exit /b 1
)

echo [ERIS] Hoan tat! File zip nam trong thu muc 'exports\'.
