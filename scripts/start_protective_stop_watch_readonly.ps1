param(
    [int]$MaxIterations = 0
)

$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $botRoot
$pythonExe = Join-Path $workspaceRoot "quant_system_rebuild\.venv_win\Scripts\python.exe"

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "$botRoot\src"

Set-Location $botRoot

& $pythonExe scripts\watch_protective_stop_replace.py `
    --state-path "$botRoot\runtime\shared_state\bot_state.json" `
    --report-root "$botRoot\runtime\reports\protective_stop_replace" `
    --watch-report-root "$botRoot\runtime\reports\protective_stop_replace_watch" `
    --proxy-url http://127.0.0.1:7897 `
    --max-iterations $MaxIterations
