[CmdletBinding()]
param([int]$Port = 0, [string]$Workspace = '', [string]$Config = '', [switch]$Build, [switch]$SkipBuild, [switch]$CheckOnly, [switch]$NoBrowser, [switch]$VerboseRequests)
$ErrorActionPreference = 'Stop'
$platformRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $platformRoot 'backend\.venv\Scripts\python.exe'
$appPath = Join-Path $platformRoot 'frontend'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run scripts/install_platform.ps1 first.'
}
$oldConfig = $env:XAJ_CONFIG
$oldWorkspace = $env:XAJ_RUNTIME_DIR
$oldUtf8 = $env:PYTHONUTF8
Push-Location $platformRoot
try {
    $env:PYTHONUTF8 = '1'
    if ($Config) { $env:XAJ_CONFIG = $Config }
    if ($Workspace) { $env:XAJ_RUNTIME_DIR = $Workspace }
    $settingsText = & $pythonPath -B backend/platform_config.py
    if ($LASTEXITCODE -ne 0) { throw 'Invalid platform configuration.' }
    $settings = $settingsText | ConvertFrom-Json
    if ($Port -eq 0) { $Port = $settings.port }
    if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be 1024-65535.' }
    & $pythonPath -B backend/check_runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime check failed.' }
    if ($CheckOnly) { return }
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $Port is already in use. Use the existing service or select another port." }
    if ($Build -and -not $SkipBuild) {
        $oldEnable = $env:VITE_ENABLE_MODEL_BACKEND
        $oldApi = $env:VITE_MODEL_API_BASE
        $env:VITE_ENABLE_MODEL_BACKEND = 'true'
        $env:VITE_MODEL_API_BASE = '/api/model'
        Push-Location $appPath
        try {
            & npm.cmd run build:unified
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally {
            Pop-Location
            $env:VITE_ENABLE_MODEL_BACKEND = $oldEnable
            $env:VITE_MODEL_API_BASE = $oldApi
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $settings.frontend 'index.html'))) { throw 'Web application missing. Run install_platform.ps1 -BuildFrontend.' }
    $env:PYTHONUTF8 = '1'
    Write-Host "Workspace: $($settings.workspace)"
    Write-Host "Platform: http://127.0.0.1:$Port/  (Ctrl+C to stop)"
    $serverArguments = @('-B', '-u', 'backend/server.py', '--port', [string]$Port)
    if (-not $NoBrowser) { $serverArguments += '--open-browser' }
    if ($VerboseRequests) { $serverArguments += '--verbose-requests' }
    & $pythonPath @serverArguments
    if ($LASTEXITCODE -ne 0) { throw 'Platform stopped with an error. See the message above.' }
} finally {
    Pop-Location
    $env:XAJ_CONFIG = $oldConfig
    $env:XAJ_RUNTIME_DIR = $oldWorkspace
    $env:PYTHONUTF8 = $oldUtf8
}
