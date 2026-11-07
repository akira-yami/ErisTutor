# Eris Desktop Launcher (PowerShell)
# ============================================================================
# Cho phép khởi chạy Eris trực tiếp từ PowerShell với đường dẫn động:
#   .\run_eris.ps1              Khởi chạy giao diện Godot Desktop
#   .\run_eris.ps1 -Runtime     Khởi chạy Python runtime trực tiếp (console)
# ============================================================================
[CmdletBinding()]
param(
    [switch]$Runtime,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir

$isRuntimeMode = $Runtime -or
    ($RemainingArgs -contains "--runtime") -or
    ($RemainingArgs -contains "-r") -or
    ($RemainingArgs -contains "--no-gui") -or
    ($RemainingArgs -contains "/boost") -or
    ($RemainingArgs -contains "/runtime")

if ($isRuntimeMode) {
    & (Join-Path $ScriptDir "run_runtime.bat") @RemainingArgs
} else {
    & (Join-Path $ScriptDir "run_eris.bat") @RemainingArgs
}
