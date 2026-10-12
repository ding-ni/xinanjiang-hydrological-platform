param(
    [int]$Port = 8877,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$modelRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$launcher = Get-ChildItem -LiteralPath $modelRoot -Filter "*.ps1" -File | Select-Object -First 1 -ExpandProperty FullName
if (-not $launcher) {
    throw "Model launcher not found"
}
& $launcher -Port $Port -NoBrowser:$NoBrowser
exit $LASTEXITCODE
