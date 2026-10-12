param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $Root "code"
Push-Location $Root
try {
    & $Python -m calibration_platform.cli (Join-Path $Root "config\calibration_routing_2017.json")
    if ($LASTEXITCODE -ne 0) { throw "routing calibration failed" }
} finally {
    Pop-Location
}
