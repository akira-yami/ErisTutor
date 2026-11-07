<#
make_test_copy.ps1
Tao ban test "nen pyc" cua ERIS sang thu muc dich.
- Copy src/ (gom gguf models + config) sang <Target>\src
- Bien dich .py -> .pyc ngay tai TARGET (repo goc khong bi dong vao)
- Xoa .py va __pycache__ trong target\src (chi con .pyc)
- Copy env / config / build settings / entry scripts

Usage:
  powershell -ExecutionPolicy Bypass -File build\make_test_copy.ps1 `
      -Target "$env:USERPROFILE\Documents\eris" [-NoModels]
#>
param(
    [string]$Target = "",
    [switch]$NoModels
)

$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$Root = (Resolve-Path "$ScriptDir\..").Path
$Py   = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

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

Write-Host "=== ERIS test copy ===" -ForegroundColor Cyan
Write-Host "Source : $Root"
Write-Host "Target : $Target"
Write-Host "Python : $Py"

New-Item -ItemType Directory -Force -Path $Target | Out-Null
$TgtSrc = Join-Path $Target "src"

# 1) Copy src/ -> target\src  (bo cache / test / pyc cu; tuy chon bo models)
$xd = @("__pycache__", ".hypothesis", ".pytest_cache")
$xf = @("*.pyc", "*.pyo")
if ($NoModels) { $xf += @("*.gguf") }

$rcArgs = @($(Join-Path $Root "src"), $TgtSrc, "/E", "/MT:16", "/R:1", "/W:1", "/NFL", "/NDL", "/NP")
$rcArgs += "/XD"; $rcArgs += $xd
$rcArgs += "/XF"; $rcArgs += $xf
Write-Host "`n[1/4] Copy src/ ..." -ForegroundColor Yellow
& robocopy @rcArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy src failed ($LASTEXITCODE)" }

# 2) Bien dich .py -> .pyc (legacy, dat canh .py) trong target
Write-Host "[2/4] Compile .py -> .pyc ..." -ForegroundColor Yellow
& $Py -m compileall -b -q $TgtSrc
if ($LASTEXITCODE -ne 0) { Write-Host "  (compileall bao loi mot so file, xem tren)" -ForegroundColor DarkYellow }

# 3) Xoa .py + __pycache__ trong target\src
Write-Host "[3/4] Strip .py source ..." -ForegroundColor Yellow
Get-ChildItem $TgtSrc -Recurse -Filter *.py -File | Remove-Item -Force
Get-ChildItem $TgtSrc -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

# 3b) Launcher entry: Godot fallback chay `python src/vision.py`. Sau khi strip chi
# con vision.pyc -> tao vision.py mong, exec vision.pyc nhu __main__ (logic van la pyc).
$launcher = @'
# Auto-generated launcher cho ban test (.pyc). Chay vision.pyc nhu __main__.
import os, runpy
_d = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(_d, "vision.pyc"), run_name="__main__")
'@
Set-Content -Path (Join-Path $TgtSrc "vision.py") -Value $launcher -Encoding UTF8

# 4) Copy env / config / build settings / entry scripts
Write-Host "[4/4] Copy env / config / settings ..." -ForegroundColor Yellow
$rootFiles = @(
    ".env", ".env.example", ".env.bak_20260613_233429", ".env.bak_20260614_083614",
    "requirements.txt", "pytest.ini", "run_tests.py",
    "focus_context_config.json", "ni_focus_config.json",
    "goal_profiles.json", "goal_memory.json", "long_term_memory.json",
    "project.godot", "export_presets.cfg", "Eris.sln", "Eris.csproj"
)
foreach ($f in $rootFiles) {
    $p = Join-Path $Root $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $Target $f) -Force }
}

# config/ + build/ folders
foreach ($d in @("config", "build")) {
    $sp = Join-Path $Root $d
    if (Test-Path $sp) {
        & robocopy $sp (Join-Path $Target $d) /E /NFL /NDL /NP /XD __pycache__ | Out-Null
    }
}

# 4b) Launcher: uu tien Python di kem ban test, khong gan venv cua may build.
# Target can python/ hoac .venv/ cung version Python va du dependency (deploy_setup.ps1).
$bat = @"
@echo off
REM Ban test can Python cung version da compile va du dependency.
setlocal
set "PATH=%~dp0python;%~dp0python\Scripts;%~dp0.venv\Scripts;%PATH%"
cd /d "%~dp0"
start "" "%~dp0Eris.exe"
"@
Set-Content -Path (Join-Path $Target "run_eris.bat") -Value $bat -Encoding ASCII

# Tom tat
$bytes = (Get-ChildItem $Target -Recurse -File -Force | Measure-Object Length -Sum).Sum
$pyc   = (Get-ChildItem $TgtSrc -Recurse -Filter *.pyc -File).Count
Write-Host "`n=== DONE ===" -ForegroundColor Green
Write-Host ("Target size : {0:N1} MB" -f ($bytes/1MB))
Write-Host ("pyc files   : $pyc")
Write-Host ("Entry       : $Target\src\vision.pyc  (chay: `$env:ERIS_PROJECT_ROOT='$Target'; & '$Py' '$Target\src\vision.pyc')")
