@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_unified.ps1" -Workspace "%~dp0runtime-20261012" -Port 18890 %*
if errorlevel 1 pause
