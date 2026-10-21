[CmdletBinding()]
param([switch]$PrepareOnly, [switch]$SkipSeed, [switch]$SkipInstaller)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageRoot = Join-Path $repoRoot '交付包\南安溪-徐畈独立安装包'
$builder = Join-Path $packageRoot 'scripts\package_release.ps1'
if (-not (Test-Path -LiteralPath $builder -PathType Leaf)) {
    throw "Standalone packaging sources are missing: $builder"
}

# Keep shared application code in the repository as the single source. No /MIR
# or /PURGE: local seeds, dependencies, releases and workspaces are preserved.
foreach ($folder in @('backend', 'frontend')) {
    $source = Join-Path $repoRoot $folder
    $destination = Join-Path $packageRoot $folder
    & robocopy.exe $source $destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD node_modules .venv __pycache__ dist dist-unified test-output test-results /XF '*.pyc' '*.log'
    if ($LASTEXITCODE -ge 8) {
        throw "Could not synchronize $folder; robocopy exit code $LASTEXITCODE"
    }
}
$global:LASTEXITCODE = 0
if ($PrepareOnly) {
    Write-Output "Shared sources synchronized: $packageRoot"
    return
}
& $builder -SkipSeed:$SkipSeed -SkipInstaller:$SkipInstaller
