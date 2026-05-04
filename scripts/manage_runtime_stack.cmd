@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%manage_runtime_stack.ps1" %*
exit /b %ERRORLEVEL%
