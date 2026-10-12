param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $Root "code"

& $Python -m integration.cli (Join-Path $Root "config\integrated_2017.json")
if ($LASTEXITCODE -ne 0) { throw "integrated 2017 run failed" }
Write-Host "Integrated outputs: $(Join-Path $Root 'validation\integrated_2017')"
