param(
    [string]$PackagePath = "",
    [string]$AuditLogPath = "",
    [string]$LockPath = "",
    [string]$KillSwitchPath = "",
    [switch]$SubmitRealOrders
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkspaceRoot = Split-Path -Parent $RepoRoot
$Python = Join-Path $RepoRoot ".venv_win\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = Join-Path $WorkspaceRoot "quant_system_rebuild\.venv_win\Scripts\python.exe"
}
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$ArgsList = @(
    (Join-Path $RepoRoot "scripts\real_order_worker.py"),
    "run-once"
)

if ($PackagePath) { $ArgsList += @("--package-path", $PackagePath) }
if ($AuditLogPath) { $ArgsList += @("--audit-log-path", $AuditLogPath) }
if ($LockPath) { $ArgsList += @("--lock-path", $LockPath) }
if ($KillSwitchPath) { $ArgsList += @("--kill-switch-path", $KillSwitchPath) }
if ($SubmitRealOrders) { $ArgsList += "--submit-real-orders" }

& $Python @ArgsList
exit $LASTEXITCODE
