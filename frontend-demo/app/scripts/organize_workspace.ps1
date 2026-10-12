# Perform only the 19 workspace moves approved on 2026-10-09.
# Default behavior is a read-only preview. No deletion, compression or merging.
[CmdletBinding()]
param([switch]$Execute, [string]$ResumeReport)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WorkspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$WorkspacePrefix = $WorkspaceRoot.TrimEnd('\') + '\'
$Plan = @(
    @('前端重构备份', '历史归档/前端快照/前端重构备份'),
    @('前端重构恢复验证', '历史归档/恢复验证/前端重构恢复验证'),
    @('外发演示包_20261008', '历史归档/演示副本/外发演示包_20261008'),
    @('外发演示包_20261008_便携版', '历史归档/演示副本/外发演示包_20261008_便携版'),
    @('南安溪产流汇流一体化平台_产流汇流率定全更新_20260820', '历史归档/旧平台/南安溪产流汇流一体化平台_产流汇流率定全更新_20260820'),
    @('南安溪产流汇流一体化平台_产流汇流率定全更新_20260820.zip', '历史归档/旧平台/南安溪产流汇流一体化平台_产流汇流率定全更新_20260820.zip'),
    @('index.html', '历史归档/静态原型/根目录旧原型.html'),
    @('新安江水文模型原理', '方案文档/模型原理'),
    @('数据库思路', '方案文档/数据库设计'),
    @('【技术报告】典型中小流域洪水预报方案构建项目-终稿.docx', '方案文档/报告与汇报/【技术报告】典型中小流域洪水预报方案构建项目-终稿.docx'),
    @('新安江模型业务化预报闭环_可编辑.pptx', '方案文档/报告与汇报/新安江模型业务化预报闭环_可编辑.pptx'),
    @('generate_logic_reconstruction_plan.py', '方案文档/报告与汇报/工具/generate_logic_reconstruction_plan.py'),
    @('selinco_data_package_20260721', '实际案例/色林错案例/原始资料/selinco_data_package_20260721'),
    @('selinco_stream_networks_20260721', '实际案例/色林错案例/河网资料/selinco_stream_networks_20260721'),
    @('imagegen_outputs', '开发产物/设计素材/imagegen_outputs'),
    @('test-results', '开发产物/历史测试/test-results'),
    @('__pycache__', '开发产物/历史缓存/根目录__pycache__'),
    @('frontend-demo/归档/历史备份', '历史归档/前端快照/frontend-demo内历史备份'),
    @('交付包/备份', '历史归档/资料交付快照/交付包内备份')
)

function Assert-WorkspacePath([string]$Path) {
    $Resolved = [IO.Path]::GetFullPath($Path)
    if (-not $Resolved.StartsWith($WorkspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the workspace: $Resolved"
    }
    return $Resolved
}

function Assert-NoLinkAncestors([string]$Path) {
    $Cursor = [IO.Path]::GetFullPath($Path)
    while ($Cursor.StartsWith($WorkspacePrefix, [StringComparison]::OrdinalIgnoreCase) -or $Cursor -eq $WorkspaceRoot) {
        if (Test-Path -LiteralPath $Cursor) {
            $Item = Get-Item -Force -LiteralPath $Cursor
            if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is not permitted: $Cursor"
            }
        }
        if ($Cursor -eq $WorkspaceRoot) { break }
        $Cursor = [IO.Path]::GetDirectoryName($Cursor)
    }
}

function Get-TreeSnapshot([string]$Path) {
    $RootItem = Get-Item -Force -LiteralPath $Path
    if ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Reparse point is not permitted: $Path"
    }
    $Entries = [Collections.Generic.List[string]]::new()
    [long]$Bytes = 0
    [long]$Files = 0
    [long]$Directories = 0
    if ($RootItem.PSIsContainer) {
        $Prefix = $RootItem.FullName.TrimEnd('\') + '\'
        foreach ($Item in (Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)) {
            if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point in source tree: $($Item.FullName)"
            }
            $Relative = $Item.FullName.Substring($Prefix.Length).Replace('\', '/')
            if ($Item.PSIsContainer) {
                $Directories++
                $Entries.Add("D`t$Relative")
            } else {
                $Files++
                $Bytes += $Item.Length
                $Entries.Add("F`t$Relative`t$($Item.Length)`t$($Item.LastWriteTimeUtc.Ticks)")
            }
        }
    } else {
        $Files = 1
        $Bytes = $RootItem.Length
        # Root filename may be renamed; compare its metadata and content.
        $Entries.Add("F`t.`t$($RootItem.Length)`t$($RootItem.LastWriteTimeUtc.Ticks)")
    }
    $Entries.Sort([StringComparer]::Ordinal)
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Digest = $Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes(($Entries -join "`n")))
        $MetadataSha256 = [BitConverter]::ToString($Digest).Replace('-', '').ToLowerInvariant()
    } finally {
        $Hasher.Dispose()
    }
    $ContentSha256 = if ($RootItem.PSIsContainer) { $null } else { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
    return [pscustomobject]@{
        Files = $Files; Directories = $Directories; Bytes = $Bytes
        MetadataSha256 = $MetadataSha256; ContentSha256 = $ContentSha256
    }
}

# Validate the exact plan against the user-reviewed table before any mutation.
$PreviousRun = $null
if ($ResumeReport) {
    $ReportPath = Assert-WorkspacePath (Join-Path $WorkspaceRoot $ResumeReport)
    Assert-NoLinkAncestors $ReportPath
    $PreviousRun = Get-Content -Raw -LiteralPath $ReportPath | ConvertFrom-Json
    if ($PreviousRun.WorkspaceRoot -ne $WorkspaceRoot -or $PreviousRun.Operations.Count -ne $Plan.Count) {
        throw 'The resume report does not match this workspace and reviewed plan.'
    }
}
$PlanRows = @(Get-Content -LiteralPath (Join-Path $WorkspaceRoot '目录整理执行清单.md') | Where-Object {
    $_ -match '^\|\s*\d+\s*\|\s*`[^`]+`\s*\|\s*`[^`]+`\s*\|'
})
if ($PlanRows.Count -ne $Plan.Count) { throw 'The reviewed plan does not contain exactly 19 rows.' }
$Operations = [Collections.Generic.List[object]]::new()
for ($Index = 0; $Index -lt $Plan.Count; $Index++) {
    $null = $PlanRows[$Index] -match '^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|'
    if ([int]$Matches[1] -ne $Index + 1 -or $Matches[2] -ne $Plan[$Index][0] -or $Matches[3] -ne $Plan[$Index][1]) {
        throw "Reviewed plan mismatch at row $($Index + 1)."
    }
    $Source = Assert-WorkspacePath (Join-Path $WorkspaceRoot $Plan[$Index][0])
    $Target = Assert-WorkspacePath (Join-Path $WorkspaceRoot $Plan[$Index][1])
    Assert-NoLinkAncestors $Source
    Assert-NoLinkAncestors $Target
    $Previous = if ($PreviousRun) { $PreviousRun.Operations[$Index] } else { $null }
    if ($Previous -and ($Previous.Id -ne $Index + 1 -or $Previous.Source -ne $Source -or $Previous.Target -ne $Target -or -not $Previous.Before)) {
        throw "Resume report mismatch at row $($Index + 1)."
    }
    $SourceExists = Test-Path -LiteralPath $Source
    $TargetExists = Test-Path -LiteralPath $Target
    $AlreadyMoved = -not $SourceExists -and $TargetExists -and $Previous -and $Previous.Status -in @('verified', 'moved-awaiting-verification')
    if (-not $AlreadyMoved) {
        if (-not $SourceExists) { throw "Missing source: $Source" }
        if ($TargetExists) { throw "Target already exists; no merge or overwrite: $Target" }
    }
    $Operations.Add([pscustomobject]@{
        Id = $Index + 1; Source = $Source; Target = $Target
        SourceRelative = $Plan[$Index][0]; TargetRelative = $Plan[$Index][1]
        Before = $(if ($Previous) { $Previous.Before } else { $null }); After = $null
        Status = $(if ($AlreadyMoved) { 'moved-awaiting-verification' } else { 'pending' })
    })
}

foreach ($Operation in $Operations) {
    if ($Operation.Target.StartsWith($Operation.Source + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target is inside its source: $($Operation.Target)"
    }
    foreach ($Other in $Operations) {
        if ($Other.Id -eq $Operation.Id) { continue }
        if ($Other.Source.StartsWith($Operation.Source + '\', [StringComparison]::OrdinalIgnoreCase) -or $Other.Source -eq $Operation.Source -or $Other.Target -eq $Operation.Target) {
            throw "Overlapping entries: $($Operation.Id), $($Other.Id)"
        }
    }
}

foreach ($Operation in $Operations) {
    $SnapshotPath = if ($Operation.Status -eq 'moved-awaiting-verification') { $Operation.Target } else { $Operation.Source }
    $Snapshot = Get-TreeSnapshot $SnapshotPath
    if ($Operation.Before) {
        foreach ($Field in @('Files', 'Directories', 'Bytes', 'MetadataSha256', 'ContentSha256')) {
            if ($Operation.Before.$Field -ne $Snapshot.$Field) { throw "Saved baseline mismatch at row $($Operation.Id), field $Field." }
        }
    } else {
        $Operation.Before = $Snapshot
    }
    if ($Operation.Status -eq 'moved-awaiting-verification') {
        $Operation.After = $Snapshot
        $Operation.Status = 'verified'
        Write-Output ('RESUMED ' + (@{ Id = $Operation.Id; Target = $Operation.TargetRelative; Snapshot = $Snapshot } | ConvertTo-Json -Compress -Depth 5))
    }
    Write-Output ('BASELINE ' + (@{ Id = $Operation.Id; Source = $Operation.SourceRelative; Snapshot = $Operation.Before } | ConvertTo-Json -Compress -Depth 5))
}
if (-not $Execute) {
    Write-Output ('REPORT ' + (@{ Mode = 'preview'; WorkspaceRoot = $WorkspaceRoot; Operations = @($Operations.ToArray()) } | ConvertTo-Json -Compress -Depth 8))
    return
}

$Failure = $null
try {
    foreach ($Operation in $Operations) {
        if ($Operation.Status -eq 'verified') { continue }
        Assert-NoLinkAncestors $Operation.Source
        Assert-NoLinkAncestors $Operation.Target
        $Parent = [IO.Path]::GetDirectoryName($Operation.Target)
        if (-not (Test-Path -LiteralPath $Parent)) { New-Item -ItemType Directory -Path $Parent | Out-Null }
        Assert-NoLinkAncestors $Parent
        if (Test-Path -LiteralPath $Operation.Target) { throw "Target appeared before move: $($Operation.Target)" }
        $MovedItem = Move-Item -LiteralPath $Operation.Source -Destination $Operation.Target -PassThru -ErrorAction Stop
        $Operation.Status = 'moved-awaiting-verification'
        if ([IO.Path]::GetFullPath($MovedItem.FullName).TrimEnd('\') -ne $Operation.Target.TrimEnd('\')) { throw "Unexpected move destination: $($MovedItem.FullName)" }
        $Operation.After = Get-TreeSnapshot $Operation.Target
        if (Test-Path -LiteralPath $Operation.Source) { throw "Source still exists after move: $($Operation.Source)" }
        foreach ($Field in @('Files', 'Directories', 'Bytes', 'MetadataSha256', 'ContentSha256')) {
            if ($Operation.Before.$Field -ne $Operation.After.$Field) {
                throw "Verification mismatch at row $($Operation.Id), field $Field. Stop without deleting or rollback."
            }
        }
        $Operation.Status = 'verified'
        Write-Output ('MOVED ' + (@{ Id = $Operation.Id; Target = $Operation.TargetRelative; Snapshot = $Operation.After } | ConvertTo-Json -Compress -Depth 5))
    }
} catch {
    $Failure = $_.Exception.Message
} finally {
    Write-Output ('REPORT ' + (@{
        Mode = 'execute'; WorkspaceRoot = $WorkspaceRoot; Completed = @($Operations | Where-Object Status -eq 'verified').Count
        Failure = $Failure; Operations = @($Operations.ToArray())
        Verification = 'Recursive file/directory counts, bytes, and relative-path/size/mtime SHA256; standalone files also content SHA256.'
    } | ConvertTo-Json -Compress -Depth 8))
}
if ($Failure) { throw $Failure }
