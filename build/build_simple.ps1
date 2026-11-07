# build\build_simple.ps1
# ============================================================================
# Dung/cap nhat phan PYTHON cua ban chay thu ERIS (khong PyInstaller).
# - Compile .py -> .pyc, xoa .py goc (tru vision.py entry point).
# - Copy gguf trong src/ theo.
# - GIU NGUYEN Godot export (Eris.exe/.pck/data_*), .env va config/secrets.json
#   neu da co (an toan khi build de len ban da export Godot + dien key).
#
# Chay tu goc repo:
#   powershell -ExecutionPolicy Bypass -File build\build_simple.ps1
# Tham so:
#   -Out <duong dan>   Thu muc dau ra (dong: ERIS_DEPLOY_DIR hoac Documents\eris)
# ============================================================================
param(
    [string]$Out = ""
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

Write-Host "[build] Repo   : $Repo"
Write-Host "[build] Output : $Out"
Write-Host ""

New-Item -ItemType Directory -Force -Path $Out | Out-Null

# -- 1. Thay rieng thu muc src/ (Python) -------------------------------------
$SrcDst = Join-Path $Out "src"
if (Test-Path $SrcDst) {
    Write-Host "[build] Xoa src/ cu (giu nguyen Godot/config/.env)..."
    Remove-Item -Recurse -Force $SrcDst
}
Write-Host "[build] Copy src/ moi..."
Copy-Item -Recurse -Force (Join-Path $Repo "src") $Out

Write-Host "[build] Compile .py -> .pyc (compileall)..."
python -m compileall -q -b "$SrcDst"

Write-Host "[build] Xoa .py goc (giu vision.py entry point)..."
Get-ChildItem -Recurse -Filter "*.py" "$SrcDst" | Where-Object {
    $_.Name -ne "vision.py"
} | Remove-Item -Force

# Khong ship embedding model nang (~8GB). Memory recall fallback rule-based.
$EmbInBuild = Join-Path $SrcDst "paraphrase-multilingual-MiniLM-L12-v2"
if (Test-Path $EmbInBuild) {
    Write-Host "[build] Bo embedding model nang khoi build..."
    Remove-Item -Recurse -Force $EmbInBuild
}

# -- 2. Files phu tro (an toan ghi de) ---------------------------------------
Copy-Item -Force (Join-Path $Repo "requirements.txt") (Join-Path $Out "requirements.txt")
Copy-Item -Force (Join-Path $Repo ".env.example") (Join-Path $Out ".env.example")

# -- 3. .env: chi tao neu CHUA co (giu key tester da dien) -------------------
$EnvDst = Join-Path $Out ".env"
if (-not (Test-Path $EnvDst)) {
    Copy-Item -Force (Join-Path $Repo ".env.example") $EnvDst
    Write-Host "[build] Tao .env moi tu example (dien GEMINI_API_KEY sau)."
} else {
    Write-Host "[build] Giu nguyen .env hien co (khong de len key da dien)."
}

# -- 4. config/secrets: chi ship example, giu secrets.json neu co ------------
$CfgDst = Join-Path $Out "config"
New-Item -ItemType Directory -Force -Path $CfgDst | Out-Null
Copy-Item -Force (Join-Path $Repo "config\secrets.example.json") (Join-Path $CfgDst "secrets.example.json")
if (Test-Path (Join-Path $CfgDst "secrets.json")) {
    Write-Host "[build] Giu nguyen config/secrets.json hien co."
}

# -- 5. Thu muc state --------------------------------------------------------
foreach ($dir in @("memory_core", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Out $dir) | Out-Null
}

# -- 6. Tong ket -------------------------------------------------------------
$TotalMB = [math]::Round((Get-ChildItem -Recurse $Out -File | Measure-Object -Property Length -Sum).Sum / 1MB)
$hasGodot = Test-Path (Join-Path $Out "Eris.exe")
Write-Host ""
Write-Host "=========================================="
Write-Host "[build] HOAN THANH (chi cap nhat Python): $Out"
Write-Host "[build] Tong kich thuoc: ~${TotalMB} MB"
Write-Host "[build] Godot Eris.exe co san: $hasGodot"
if (-not $hasGodot) {
    Write-Host "[build] >> Chua co Eris.exe - nho export Godot body vao thu muc nay."
}
Write-Host "=========================================="
