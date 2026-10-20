[CmdletBinding()]
param([string]$Python = 'python', [switch]$BuildFrontend, [string]$Wheelhouse = '')
$ErrorActionPreference = 'Stop'
$platformRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $platformRoot 'frontend'
$venv = Join-Path $platformRoot 'backend/.venv'
$runtimePython = Join-Path $venv 'Scripts/python.exe'
if ($BuildFrontend -and (
    -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'package.json')) -or
    -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'package-lock.json')) -or
    -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'src/main.js')) -or
    -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'vite.config.js'))
)) {
    throw 'Frontend source is not present in this portable release. Build with -BuildFrontend from a complete source checkout.'
}
Push-Location $platformRoot
try {
    & $Python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 9))'
    if ($LASTEXITCODE -ne 0) { throw 'This release requires Python 3.9. Use -Python with its executable path.' }
    if (-not (Test-Path -LiteralPath $venv)) {
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    if (-not (Test-Path -LiteralPath $runtimePython)) { throw 'Existing environment is incomplete. Choose a fresh installation directory.' }
    $arguments = @('-m', 'pip', 'install', '-r', 'backend/requirements-lock-py39.txt')
    if ($Wheelhouse) { $arguments += @('--no-index', '--find-links', $Wheelhouse) }
    & $runtimePython @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if ($BuildFrontend) {
        Push-Location 'frontend'
        try {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            & npm.cmd run build:unified
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    & $runtimePython -B backend/check_runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Installed runtime check failed.' }
    Write-Host 'Installation complete. Run the platform launcher.'
} finally { Pop-Location }
