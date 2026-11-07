# Build Python runtime cua ERIS thanh thu muc eris-runtime/ bang PyInstaller.
# Chay tu goc repo:  powershell -ExecutionPolicy Bypass -File build\build_runtime.ps1
#
# Tham so:
#   -Target <duong dan>   Copy ket qua vao <Target>\runtime (vd thu muc ban export Godot).

param(
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

Write-Host "[build] Repo: $repo"
Push-Location $repo
try {
    Write-Host "[build] Kiem tra PyInstaller..."
    python -m PyInstaller --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[build] Cai PyInstaller..."
        python -m pip install pyinstaller
    }

    Write-Host "[build] Dong goi runtime (co the mat vai phut vi torch nang)..."
    python -m PyInstaller "build\eris_runtime.spec" --noconfirm --distpath dist --workpath build\_work

    $out = Join-Path $repo "dist\eris-runtime"
    if (-not (Test-Path $out)) {
        throw "Khong thay ket qua build o $out"
    }
    Write-Host "[build] Xong: $out"

    if ($Target -ne "") {
        $dest = Join-Path $Target "runtime"
        Write-Host "[build] Copy sang: $dest"
        if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
        Copy-Item -Recurse -Force $out $dest
        Write-Host "[build] Da dat runtime canh ban export Godot."
    }
    else {
        Write-Host "[build] Copy thu muc dist\eris-runtime  ->  <ban export>\runtime  de hoan tat."
    }
}
finally {
    Pop-Location
}
