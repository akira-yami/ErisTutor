# Build helper cho eris-uiaccess-broker.exe.
# Điều kiện bắt buộc của Windows: executable uiAccess phải được ký và nên được
# cài vào vị trí bảo vệ như Program Files. Script KHÔNG tự tạo certificate giả.
param(
    [Parameter(Mandatory=$true)][string]$CertificateThumbprint,
    [string]$OutputDir = ".\dist-uiaccess"
)

$ErrorActionPreference = "Stop"
$sourceRoot = Join-Path $PSScriptRoot "../.."
python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }
pyinstaller --noconfirm --clean --onefile `
  --name eris-uiaccess-broker `
  --paths $sourceRoot `
  --manifest (Join-Path $PSScriptRoot "eris-uiaccess-broker.manifest") `
  (Join-Path $PSScriptRoot "server.py") `
  --distpath $OutputDir
if ($LASTEXITCODE -ne 0) { throw "UIAccess broker build failed." }

$exe = Join-Path $OutputDir "eris-uiaccess-broker.exe"
$cert = Get-ChildItem "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
if (-not $cert) {
    $cert = Get-ChildItem "Cert:\LocalMachine\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
}
if (-not $cert) { throw "Không tìm thấy code-signing certificate theo thumbprint đã cấp." }
Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -TimestampServer "http://timestamp.digicert.com" | Out-Null
Write-Host "Đã build + ký: $exe"
Write-Host "Hãy cài executable vào Program Files trước khi bật ERIS_UIACCESS_BROKER_ENABLED=1."
