param(
    [Parameter(Mandatory=$true)][string]$Url,
    [int]$Attempts = 60,
    [int]$DelayMs = 700
)

$ErrorActionPreference = "SilentlyContinue"

for ($i = 0; $i -lt $Attempts; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 1 | Out-Null
        Start-Process $Url
        exit 0
    } catch {
        Start-Sleep -Milliseconds $DelayMs
    }
}

Start-Process $Url
exit 1
