@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_platform.ps1" %*
if errorlevel 1 pause
