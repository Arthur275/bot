param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $RepoRoot
$Python = Join-Path $RepoRoot ".venv_win\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $WorkspaceRoot "quant_system_rebuild\.venv_win\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$env:ETH_BOT_ROOT = $RepoRoot
$env:QUANT_ROOT = Join-Path $WorkspaceRoot "quant_system_rebuild"
$env:PYTHONPATH = $RepoRoot

Write-Output ("ETH runtime dashboard: http://{0}:{1}" -f $HostName, $Port)
Push-Location $RepoRoot
try {
    & $Python -m dashboard.app --host $HostName --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
