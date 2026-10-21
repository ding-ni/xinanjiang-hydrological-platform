[CmdletBinding()]
param([switch]$NoBrowser,[switch]$InitializeOnly)
$ErrorActionPreference='Stop'
$appRoot=Split-Path -Parent $PSScriptRoot
$workspace=Join-Path $appRoot 'workspace'
$legacyWorkspace=Join-Path (Join-Path $env:LOCALAPPDATA 'Nananxi-Xufan-Platform') 'workspace'
$seed=Join-Path $appRoot 'seed\runtime'
if (Get-NetTCPConnection -LocalPort 8878 -State Listen -ErrorAction SilentlyContinue) { throw 'Local port 8878 is already in use. Stop the currently running platform before migrating its workspace.' }
$workspaceDatabase=Join-Path $workspace 'database\hydrology.sqlite3'
$legacyDatabase=Join-Path $legacyWorkspace 'database\hydrology.sqlite3'
if (-not (Test-Path -LiteralPath $workspaceDatabase)) {
    if (Test-Path -LiteralPath $workspace) { throw "Install-folder workspace exists but has no database; preserving it and refusing to overwrite: $workspace" }
    if (Test-Path -LiteralPath $legacyDatabase) {
        $migrationStage=Join-Path $appRoot 'workspace.migration'
        if (Test-Path -LiteralPath $migrationStage) { throw "An earlier workspace migration stage exists; preserving it for inspection: $migrationStage" }
        Write-Host "Migrating existing workspace to the installation folder: $workspace"
        & robocopy.exe $legacyWorkspace $migrationStage /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
        $copyCode=$LASTEXITCODE
        if ($copyCode -ge 8) { throw "Workspace copy failed with robocopy exit code $copyCode. Original data remains at $legacyWorkspace; partial copy is preserved at $migrationStage." }
        $global:LASTEXITCODE=0
        if (-not (Test-Path -LiteralPath (Join-Path $migrationStage 'database\hydrology.sqlite3'))) { throw "Migrated workspace is missing its database. Original remains at $legacyWorkspace; inspect $migrationStage." }
        Move-Item -LiteralPath $migrationStage -Destination $workspace
        Write-Host "Workspace copied. The original remains unchanged at: $legacyWorkspace"
    } else {
        if (-not (Test-Path -LiteralPath (Join-Path $seed 'database\hydrology.sqlite3'))) { throw 'The packaged seed database is missing.' }
        New-Item -ItemType Directory -Force -Path $workspace | Out-Null
        Copy-Item -Path (Join-Path $seed '*') -Destination $workspace -Recurse -Force
    }
}
$env:XAJ_APP_ROOT=$appRoot; $env:XAJ_BUNDLED='1'; $env:XAJ_RUNTIME_DIR=$workspace; $env:XAJ_CONFIG=Join-Path $appRoot 'config\platform.json'
if ($InitializeOnly) { Write-Host "Workspace ready: $workspace"; return }
$args=@('--port','8878'); if (-not $NoBrowser) { $args += '--open-browser' }
$exe=Get-ChildItem -LiteralPath $appRoot -Filter '*.exe' -File | Select-Object -First 1
if (-not $exe) { throw 'Platform executable is missing.' }
& $exe.FullName @args
if ($LASTEXITCODE -ne 0) { throw 'Platform server exited with an error.' }
