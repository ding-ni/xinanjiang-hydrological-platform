[CmdletBinding()]
param([string]$Label = 'data-chain-unification')
$ErrorActionPreference = 'Stop'
$platformRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $platformRoot 'backend/.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'snapshot_platform.py') --label $Label
if ($LASTEXITCODE -ne 0) { throw 'Snapshot failed. See the message above.' }
