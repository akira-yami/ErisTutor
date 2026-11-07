# build\clean_build.ps1
# ============================================================================
# BUILD SACH ERIS: clean .NET artifacts -> Godot export (body) -> Python pyc.
# Giu nguyen .env va config/secrets.json cua tester o thu muc dich.
#
# Chay:
#   powershell -ExecutionPolicy Bypass -File build\clean_build.ps1
# ============================================================================
param(
    [string]$Out   = "",
    [string]$Godot = $env:ERIS_GODOT
)

$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$Repo = Split-Path -Parent $ScriptDir

if (-not $Out) {
    if ($env:ERIS_DEPLOY_DIR) {
        $Out = $env:ERIS_DEPLOY_DIR
    } else {
        $docs = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::MyDocuments)
        if (-not $docs -or -not (Test-Path $docs)) {
            $docs = Join-Path $env:USERPROFILE "Documents"
        }
        $Out = Join-Path $docs "eris"
    }
}

if (-not $Godot) {
    foreach ($name in @("godot-mono", "godot", "godot4")) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($command) { $Godot = $command.Source; break }
    }
}
if (-not $Godot) { throw "Khong tim thay Godot .NET. Truyen -Godot <exe>, dat ERIS_GODOT, hoac them Godot .NET vao PATH." }
$Godot = (Get-Command $Godot -CommandType Application -ErrorAction Stop).Source
$Py   = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "[clean-build] Repo   : $Repo"
Write-Host "[clean-build] Output : $Out"
Write-Host "[clean-build] Godot  : $Godot"
Write-Host "[clean-build] Python : $Py"
Write-Host "==========================================" -ForegroundColor Cyan

if (-not (Test-Path $Godot)) { throw "Khong tim thay Godot: $Godot" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# -- 0. Backup .env + secrets.json (khong de len key tester) -----------------
$TmpBak = Join-Path $env:TEMP "eris_build_bak"
New-Item -ItemType Directory -Force -Path $TmpBak | Out-Null
$envTgt = Join-Path $Out ".env"
$secTgt = Join-Path $Out "config\secrets.json"
if (Test-Path $envTgt) { Copy-Item $envTgt (Join-Path $TmpBak ".env") -Force; Write-Host "[clean-build] Backup .env tester" }
if (Test-Path $secTgt) { Copy-Item $secTgt (Join-Path $TmpBak "secrets.json") -Force; Write-Host "[clean-build] Backup secrets.json tester" }

# -- 1. Clean .NET artifacts cua repo (ep build lai sach) --------------------
Write-Host "`n[1/5] Clean .NET artifacts (.godot\mono, bin, obj)..." -ForegroundColor Yellow
foreach ($p in @((Join-Path $Repo ".godot\mono"), (Join-Path $Repo "bin"), (Join-Path $Repo "obj"))) {
    if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Host "  xoa $p" }
}

# -- 2. Clean export artifacts cu o target -----------------------------------
Write-Host "[2/5] Clean export body cu o target..." -ForegroundColor Yellow
foreach ($p in @("Eris.exe", "Eris.console.exe", "Eris.pck", "data_Eris_windows_x86_64", "src")) {
    $fp = Join-Path $Out $p
    if (Test-Path $fp) { Remove-Item -Recurse -Force $fp; Write-Host "  xoa $p" }
}

# -- 3. Godot export-release (body: Eris.exe/.pck/data_) ---------------------
Write-Host "[3/5] Godot export-release 'Windows Desktop'..." -ForegroundColor Yellow
$ErisExe = Join-Path $Out "Eris.exe"
$prevErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Godot --headless --path $Repo --export-release "Windows Desktop" $ErisExe 2>&1 | Out-Host
$godotExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErrorActionPreference
if ($godotExitCode -ne 0) { throw "Godot export that bai: exit code $godotExitCode" }
if (-not (Test-Path $ErisExe)) { throw "Godot export that bai: khong thay $ErisExe" }
Write-Host "  OK: $ErisExe ($([math]::Round((Get-Item $ErisExe).Length/1MB)) MB)"

# -- 4. Python: copy src -> compile pyc -> strip .py -------------------------
Write-Host "[4/5] Build Python (.pyc)..." -ForegroundColor Yellow
$SrcDst = Join-Path $Out "src"
Copy-Item -Recurse -Force (Join-Path $Repo "src") $Out
& $Py -m compileall -q -b $SrcDst | Out-Host
Get-ChildItem -Recurse -Filter "*.py" $SrcDst | Where-Object { $_.Name -ne "vision.py" } | Remove-Item -Force
Get-ChildItem $SrcDst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$EmbInBuild = Join-Path $SrcDst "paraphrase-multilingual-MiniLM-L12-v2"
if (Test-Path $EmbInBuild) { Remove-Item -Recurse -Force $EmbInBuild; Write-Host "  bo embedding model nang" }

# Launcher: vision.py mong exec vision.pyc nhu __main__
$launcher = @'
# Auto-generated launcher cho ban build (.pyc). Chay vision.pyc nhu __main__.
import os, runpy
_d = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(_d, "vision.pyc"), run_name="__main__")
'@
Set-Content -Path (Join-Path $SrcDst "vision.py") -Value $launcher -Encoding UTF8

# -- 5. Files phu tro + khoi phuc key tester ---------------------------------
Write-Host "[5/5] Copy support files + khoi phuc key tester..." -ForegroundColor Yellow
Copy-Item -Force (Join-Path $Repo "requirements.txt") (Join-Path $Out "requirements.txt")
Copy-Item -Force (Join-Path $Repo ".env.example")     (Join-Path $Out ".env.example")
$CfgDst = Join-Path $Out "config"
New-Item -ItemType Directory -Force -Path $CfgDst | Out-Null
Copy-Item -Force (Join-Path $Repo "config\secrets.example.json") (Join-Path $CfgDst "secrets.example.json")
foreach ($dir in @("memory_core", "logs")) { New-Item -ItemType Directory -Force -Path (Join-Path $Out $dir) | Out-Null }

# Khoi phuc .env + secrets.json tester (neu co backup); neu khong co .env thi tao tu example
if (Test-Path (Join-Path $TmpBak ".env"))        { Copy-Item (Join-Path $TmpBak ".env") $envTgt -Force }
elseif (-not (Test-Path $envTgt))                { Copy-Item (Join-Path $Repo ".env.example") $envTgt -Force }
if (Test-Path (Join-Path $TmpBak "secrets.json")) { Copy-Item (Join-Path $TmpBak "secrets.json") $secTgt -Force }

# -- Tong ket ----------------------------------------------------------------
$TotalMB = [math]::Round((Get-ChildItem -Recurse $Out -File | Measure-Object -Property Length -Sum).Sum / 1MB)
$pyc = (Get-ChildItem $SrcDst -Recurse -Filter "*.pyc" -File).Count
Write-Host "`n==========================================" -ForegroundColor Green
Write-Host "[clean-build] HOAN THANH: $Out"
Write-Host "[clean-build] Eris.exe   : $(Test-Path $ErisExe)"
Write-Host "[clean-build] Eris.pck   : $(Test-Path (Join-Path $Out 'Eris.pck'))"
Write-Host "[clean-build] pyc files  : $pyc"
Write-Host "[clean-build] Tong size  : ~${TotalMB} MB"
Write-Host "==========================================" -ForegroundColor Green
