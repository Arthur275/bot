@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%~1"=="" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_protective_stop_watch_readonly.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_protective_stop_watch_readonly.ps1" "%~1" "%~2"
)
