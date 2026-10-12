[CmdletBinding()]
param([ValidateSet('backup','verify','restore')][string]$Operation='backup', [string]$Source='', [string]$Destination='', [string]$Workspace='')
$ErrorActionPreference = 'Stop'
$platformRoot = Split-Path -Parent $PSScriptRoot
$arguments = @('-B', (Join-Path $platformRoot 'backend/workspace_backup.py'), $Operation)
if ($Source) { $arguments += @('--source', $Source) }
if ($Destination) { $arguments += @('--destination', $Destination) }
if ($Workspace) { $arguments += @('--workspace', $Workspace) }
& (Join-Path $platformRoot 'backend/.venv/Scripts/python.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Workspace operation failed.' }
