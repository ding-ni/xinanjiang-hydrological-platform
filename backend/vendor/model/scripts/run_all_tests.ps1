param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $Root "code"

& $Python -m unittest discover -s (Join-Path $Root "tests\runoff") -v
if ($LASTEXITCODE -ne 0) { throw "runoff tests failed" }
& $Python -m unittest discover -s (Join-Path $Root "tests\routing") -v
if ($LASTEXITCODE -ne 0) { throw "routing tests failed" }
& $Python -m unittest discover -s (Join-Path $Root "tests\integration") -v
if ($LASTEXITCODE -ne 0) { throw "integration tests failed" }
& $Python -m unittest discover -s (Join-Path $Root "tests\calibration") -v
if ($LASTEXITCODE -ne 0) { throw "calibration tests failed" }

Write-Host "All runoff, routing, integration and calibration tests passed."
