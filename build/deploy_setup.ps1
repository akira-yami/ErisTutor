# build\deploy_setup.ps1
# ============================================================================
# Dung mot local virtual environment co lap va tu dong bien dich trong folder build.
# Giup Eris chay doc lap ma khong lo xung dot version Python tren may.
# ============================================================================
param(
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$Root = (Resolve-Path "$ScriptDir\..").Path

if (-not $Target) {
    if ($env:ERIS_DEPLOY_DIR) {
        $Target = $env:ERIS_DEPLOY_DIR
    } else {
        $docs = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::MyDocuments)
        if (-not $docs -or -not (Test-Path $docs)) {
            $docs = Join-Path $env:USERPROFILE "Documents"
        }
        $Target = Join-Path $docs "eris"
    }
}

Write-Host "=== ERIS SELF-CONTAINED DEPLOY SETUP (USING UV) ===" -ForegroundColor Cyan
Write-Host "Repo Root: $Root"
Write-Host "Target   : $Target"
Write-Host ""

# 1. Tao thu muc Target
New-Item -ItemType Directory -Force -Path $Target | Out-Null

# 2. Copy folder src/ (chu y giu nguyen GGUF models neu co)
$TgtSrc = Join-Path $Target "src"
$xd = @("__pycache__", ".hypothesis", ".pytest_cache")
$xf = @("*.pyc", "*.pyo")
$rcArgs = @($(Join-Path $Root "src"), $TgtSrc, "/E", "/MT:16", "/R:1", "/W:1", "/NFL", "/NDL", "/NP")
$rcArgs += "/XD"; $rcArgs += $xd
$rcArgs += "/XF"; $rcArgs += $xf

Write-Host "[1/6] Dang copy thu muc src sang build..." -ForegroundColor Yellow
& robocopy @rcArgs | Out-Null

# 3. Tao local virtual environment (.venv) trong folder build
$TgtVenv = Join-Path $Target ".venv"
$TgtPython = Join-Path $TgtVenv "Scripts\python.exe"

if (-not (Test-Path $TgtVenv)) {
    Write-Host "[2/6] Dang khoi tao .venv cuc bo bang uv..." -ForegroundColor Yellow
    & uv venv $TgtVenv --python 3.14
} else {
    Write-Host "[2/6] .venv cuc bo da ton tai, bo qua khoi tao." -ForegroundColor Yellow
}

# 4. Cai dat dependencies tu requirements.txt vao local .venv
Copy-Item -Force (Join-Path $Root "requirements.txt") (Join-Path $Target "requirements.txt")
Write-Host "[3/6] Dang cai dat dependencies (requirements.txt) bang uv..." -ForegroundColor Yellow
& uv pip install -r (Join-Path $Target "requirements.txt") --python $TgtPython

# 5. Bien dich .py sang .pyc bang dung Python cua .venv vua tao
Write-Host "[4/6] Dang bien dich .py -> .pyc bang Python .venv moi..." -ForegroundColor Yellow
& $TgtPython -m compileall -b -q $TgtSrc

# Xoa .py goc (giu lai vision.py)
Write-Host "[5/6] Dang xoa file nguon .py va don dep cache..." -ForegroundColor Yellow
Get-ChildItem $TgtSrc -Recurse -Filter *.py -File | Where-Object { $_.Name -ne "vision.py" } | Remove-Item -Force
Get-ChildItem $TgtSrc -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

# 6. Copy file cau hinh va binaries khac
$rootFiles = @(
    ".env.example", "requirements.txt", "pytest.ini", "run_tests.py",
    "focus_context_config.json", "ni_focus_config.json",
    "goal_profiles.json", "goal_memory.json", "long_term_memory.json",
    "project.godot", "export_presets.cfg", "Eris.sln", "Eris.csproj"
)
foreach ($f in $rootFiles) {
    $p = Join-Path $Root $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $Target $f) -Force }
}

# config/ folder
$CfgDst = Join-Path $Target "config"
New-Item -ItemType Directory -Force -Path $CfgDst | Out-Null
Copy-Item -Force (Join-Path $Root "config\secrets.example.json") (Join-Path $CfgDst "secrets.example.json")
if (-not (Test-Path (Join-Path $CfgDst "secrets.json"))) {
    if (Test-Path (Join-Path $Root "config\secrets.json")) {
        Copy-Item -Force (Join-Path $Root "config\secrets.json") (Join-Path $CfgDst "secrets.json")
    }
}

# 7. Viet lai file run_eris.bat de tro dung vao local .venv
$BatContent = @"
@echo off
REM Launcher Eris -- inject local venv Python vao PATH de dung phien ban dong bo
set "PATH=%~dp0.venv\Scripts;%PATH%"
cd /d "%~dp0"
start "" "%~dp0Eris.exe"
"@
$BatContent | Out-File -Encoding ascii (Join-Path $Target "run_eris.bat")

# Tu tao logs va memory_core cho sach
foreach ($dir in @("memory_core", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Target $dir) | Out-Null
}

$pycCount = (Get-ChildItem $TgtSrc -Recurse -Filter *.pyc -File).Count
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "[HOAN THANH] Da thiet lap ban build tu chua cuc bo!" -ForegroundColor Green
Write-Host "Vi tri build: $Target"
Write-Host "So file pyc : $pycCount"
Write-Host "Khoi dong Eris bang cach chay file: $Target\run_eris.bat"
Write-Host "==========================================" -ForegroundColor Green
