param([string]$Workspace = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
$delivery = Join-Path $Workspace '后端文件/南安溪产流汇流模型_20261008/南安溪产流汇流模型_20261008/空间可视化人工调参版'
$archive = Join-Path $Workspace '后端文件/南安溪产流汇流模型_20261008.zip'
$target = Join-Path $Workspace 'backend/vendor/model'
if (Test-Path -LiteralPath $target) { throw '活动模型目录已经存在，拒绝覆盖。' }
if (-not (Test-Path -LiteralPath "$delivery/model/code")) { throw '找不到合作者模型源码。' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $Workspace "历史归档/统一系统接入基线/$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$front = Join-Path $Workspace 'frontend-demo/app'
$frontSnapshot = Join-Path $backup 'frontend-demo/app'
$frontFiles = @()
foreach ($name in @('src','public','tests','scripts')) {
    $frontFiles += @(Get-ChildItem -LiteralPath (Join-Path $front $name) -File -Recurse | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' })
}
$frontFiles += @(Get-ChildItem -LiteralPath $front -File)
$manifestFiles = @()
foreach ($file in $frontFiles) {
    $relative = $file.FullName.Substring($front.Length + 1)
    $destination = Join-Path $frontSnapshot $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
    $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $sourceHash) { throw "备份校验失败：$relative" }
    $manifestFiles += @{path=$relative.Replace('\','/'); sha256=$sourceHash; bytes=$file.Length}
}
$model = Join-Path $delivery 'model'
$vendorFiles = @()
foreach ($name in @('code','config','scripts','tests','data_interface')) {
    $vendorFiles += @(Get-ChildItem -LiteralPath (Join-Path $model $name) -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -notin @('.pyc','.log','.pid') })
}
$vendorFiles += Get-Item -LiteralPath (Join-Path $model 'requirements.txt')
$vendorManifest = @()
foreach ($file in $vendorFiles) {
    $relative = $file.FullName.Substring($model.Length + 1)
    $destination = Join-Path $target $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
    $digest = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if ($digest -ne (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash) { throw "模型复制校验失败：$relative" }
    $vendorManifest += @{path=$relative.Replace('\','/'); sha256=$digest; bytes=$file.Length}
}
@{schemaVersion=1; receivedAt='2026-10-09'; createdAt=(Get-Date).ToString('o'); archive=$archive; archiveSha256=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash; frontend=$manifestFiles; vendor=$vendorManifest; note='只复制，不移动、不删除；原始交付及其中旧校验清单保留。'} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $backup 'baseline-manifest.json') -Encoding UTF8
@{backup=$backup; frontendFiles=$frontFiles.Count; vendorFiles=$vendorFiles.Count; vendorRoot=$target} | ConvertTo-Json
