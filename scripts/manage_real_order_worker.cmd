@echo off
setlocal
set "REPO_ROOT=%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\manage_real_order_worker.ps1" %*
exit /b %ERRORLEVEL%
