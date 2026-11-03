@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_backend.ps1" -Action start
if errorlevel 1 pause
