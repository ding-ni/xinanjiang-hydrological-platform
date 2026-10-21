[CmdletBinding()]
param([switch]$SkipFrontendBuild,[switch]$SkipSeed,[switch]$SkipInstaller)
$ErrorActionPreference='Stop'; $root=Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $build=Join-Path $root ('build\run-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $build | Out-Null
    $seedRoot=Join-Path $root 'seed\runtime-final'
    if (-not $SkipSeed -and -not (Test-Path -LiteralPath (Join-Path $seedRoot 'seed-manifest.json'))) { & python scripts\build_seed.py --output $seedRoot; if ($LASTEXITCODE) { throw 'Seed build failed.' } }
    if (-not (Test-Path -LiteralPath (Join-Path $seedRoot 'seed-manifest.json'))) { throw "Seed manifest missing: $seedRoot" }
    if (-not $SkipFrontendBuild) {
        $env:VITE_ENABLE_MODEL_BACKEND='true'; $env:VITE_MODEL_API_BASE='/api/model'; $env:VITE_XUFAN_STANDALONE='true'
        Push-Location frontend
        try { & npm.cmd run build:unified; if ($LASTEXITCODE) { throw 'Frontend build failed.' } }
        finally { Pop-Location; Remove-Item Env:VITE_ENABLE_MODEL_BACKEND,Env:VITE_MODEL_API_BASE,Env:VITE_XUFAN_STANDALONE -ErrorAction SilentlyContinue }
    }
    $pyDist=Join-Path $build 'pyinstaller-dist'; $pyWork=Join-Path $build 'pyinstaller-work'
    & pyinstaller --noconfirm --clean --distpath $pyDist --workpath $pyWork installer\standalone.spec
    if ($LASTEXITCODE) { throw 'PyInstaller build failed.' }
    $app=Join-Path $build 'app'; New-Item -ItemType Directory -Path $app | Out-Null
    $pyApp=Get-ChildItem -LiteralPath $pyDist -Directory | Select-Object -First 1
    Copy-Item -Path (Join-Path $pyApp.FullName '*') -Destination $app -Recurse -Force
    Copy-Item -LiteralPath backend -Destination (Join-Path $app 'backend') -Recurse -Force
    Copy-Item -LiteralPath config -Destination (Join-Path $app 'config') -Recurse -Force
    Copy-Item -LiteralPath (Join-Path frontend 'public\data\projects') -Destination (Join-Path $app 'frontend\public\data\projects') -Recurse -Force
    Copy-Item -LiteralPath (Join-Path frontend 'dist-unified') -Destination (Join-Path $app 'frontend\dist-unified') -Recurse -Force
    $filteredApp=Join-Path $build 'app-xufan'
    & python scripts\stage_app.py --source $app --output $filteredApp
    if ($LASTEXITCODE) { throw 'Filtering non-Xufan static data failed.' }
    $platformExe=Join-Path $filteredApp '南安溪徐畈平台.exe'
    $runtimeCheck=Join-Path $filteredApp 'backend\check_runtime.py'
    if (-not (Test-Path -LiteralPath $platformExe) -or -not (Test-Path -LiteralPath $runtimeCheck)) { throw 'Packaged runtime preflight files are missing.' }
    $previousRuntimeDir=$env:XAJ_RUNTIME_DIR
    try {
        $env:XAJ_RUNTIME_DIR=Join-Path $build 'runtime-preflight'
        & $platformExe --xaj-script $runtimeCheck
        if ($LASTEXITCODE) { throw 'Packaged numerical runtime preflight failed; installer was not produced.' }
    } finally {
        if ($null -eq $previousRuntimeDir) { Remove-Item Env:XAJ_RUNTIME_DIR -ErrorAction SilentlyContinue }
        else { $env:XAJ_RUNTIME_DIR=$previousRuntimeDir }
    }
    if (-not $SkipInstaller) {
        $isccCommand=Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if (-not $isccCommand) { $isccCommand=Get-Command iscc -ErrorAction SilentlyContinue }
        $isccPath=$isccCommand.Source
        if (-not $isccPath) {
            $knownIscc=@(
                (Join-Path ${env:LOCALAPPDATA} 'Programs\Inno Setup 6\ISCC.exe'),
                'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
                'C:\Program Files\Inno Setup 6\ISCC.exe'
            ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if ($knownIscc) { $isccPath=$knownIscc }
        }
        if (-not $isccPath) { throw 'Inno Setup ISCC.exe was not found.' }
        $output=Join-Path $root ('dist\install-path-'+(Get-Date -Format 'yyyyMMdd-HHmmss')); New-Item -ItemType Directory -Path $output | Out-Null
        $env:XUFAN_APP_SOURCE=$filteredApp; $env:XUFAN_SEED_SOURCE=$seedRoot; $env:XUFAN_OUTPUT_DIR=$output
        $iss=Get-ChildItem -LiteralPath installer -Filter '*.iss' | Select-Object -First 1
        & $isccPath $iss.FullName
        if ($LASTEXITCODE) { throw 'Inno Setup compilation failed.' }
        & python scripts\write_manifest.py
        if ($LASTEXITCODE) { throw 'Release manifest generation failed.' }
        & python scripts\verify_release.py
        if ($LASTEXITCODE) { throw 'Release package verification failed.' }
    }
    Write-Output "Build directory: $build"
} finally { Pop-Location }
