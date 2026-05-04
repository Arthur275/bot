param(
    [string]$HostName = "127.0.0.1",
    [int]$DashboardPort = 8765,
    [int]$IntervalSec = 300,
    [int]$WorkerIntervalSec = 30,
    [string]$ProxyUrl = "http://127.0.0.1:7897",
    [switch]$EnableRealOrders,
    [switch]$IncludeCoinglassOverlay
)

$ErrorActionPreference = "Stop"

$BotRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $BotRoot
$QuantRoot = Join-Path $WorkspaceRoot "quant_system_rebuild"
$Python = Join-Path $QuantRoot ".venv_win\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $BotRoot ".venv_win\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$BotRuntimeRoot = Join-Path $BotRoot "runtime\bot_runtime_scheduler"
function Start-RuntimeWindow {
    param(
        [string]$Title,
        [string]$Command,
        [string]$WorkingDirectory
    )

    $wrapped = @"
`$Host.UI.RawUI.WindowTitle = '$Title'
Set-Location -LiteralPath '$WorkingDirectory'
$Command
"@

    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $wrapped
    ) -WorkingDirectory $WorkingDirectory -WindowStyle Normal | Out-Null
}

$dashboardCommand = @"
`$env:ETH_BOT_ROOT = '$BotRoot'
`$env:QUANT_ROOT = '$QuantRoot'
`$env:PYTHONPATH = '$BotRoot'
& '$Python' -m dashboard.app --host '$HostName' --port $DashboardPort
"@

$quantCycleArgs = @(
    "scripts\quant_runtime_scheduler.py",
    "run-cycle",
    "--loop",
    "--interval-sec",
    ([string]$IntervalSec),
    "--symbol",
    "ETH",
    "--timeframe",
    "15m",
    "--proxy-url",
    $ProxyUrl,
    "--include-okx-overlay"
)
if ($IncludeCoinglassOverlay) {
    $quantCycleArgs += "--include-coinglass-overlay"
}
$quantCycleArgText = ($quantCycleArgs | ForEach-Object { "'$_'" }) -join " "
$quantCycleCommand = "& '$Python' $quantCycleArgText"

$factorIngestArgs = @(
    "scripts\quant_runtime_scheduler.py",
    "ingest-summary",
    "--loop",
    "--interval-sec",
    ([string]$IntervalSec),
    "--symbol",
    "ETH",
    "--timeframe",
    "15m"
)
$factorIngestArgText = ($factorIngestArgs | ForEach-Object { "'$_'" }) -join " "
$factorIngestCommand = "& '$Python' $factorIngestArgText"

$botSchedulerCommand = @(
    "& '$BotRoot\scripts\manage_bot_runtime_scheduler.ps1'",
    "-Action start",
    "-IntervalSec $IntervalSec",
    "-RuntimeRoot '$BotRuntimeRoot'",
    "-AnalysisDbPath '$BotRuntimeRoot\analysis\bot_runtime.duckdb'"
)
if ($EnableRealOrders) {
    $botSchedulerCommand += "-EnableRealOrders"
}
$botSchedulerCommand = $botSchedulerCommand -join " "

$workerCommand = @"
while (`$true) {
    `$argsList = @()
    if ('$EnableRealOrders' -eq 'True') { `$argsList += '-SubmitRealOrders' }
    & '$BotRoot\scripts\manage_real_order_worker.ps1' @argsList
    Start-Sleep -Seconds $WorkerIntervalSec
}
"@

Start-RuntimeWindow -Title "ETH Dashboard" -Command $dashboardCommand -WorkingDirectory $BotRoot
Start-RuntimeWindow -Title "ETH Factor Ingest" -Command $factorIngestCommand -WorkingDirectory $QuantRoot
Start-RuntimeWindow -Title "ETH Quant Judgement" -Command $quantCycleCommand -WorkingDirectory $QuantRoot
Start-RuntimeWindow -Title "ETH Bot Scheduler" -Command $botSchedulerCommand -WorkingDirectory $BotRoot
Start-RuntimeWindow -Title "ETH Order Worker" -Command $workerCommand -WorkingDirectory $BotRoot

Write-Output "ETH runtime stack started."
Write-Output ("Dashboard: http://{0}:{1}" -f $HostName, $DashboardPort)
if ($EnableRealOrders) {
    Write-Output "Real order submission: ENABLED"
} else {
    Write-Output "Real order submission: disabled; bot runs planning/preflight only."
}
