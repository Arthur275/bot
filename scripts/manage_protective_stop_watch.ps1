param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [switch]$AllowMissingRepair,
    [double]$WatchIntervalSec = 30.0,
    [double]$HeartbeatEverySec = 300.0
)

$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $botRoot
$pythonExe = Join-Path $workspaceRoot "quant_system_rebuild\.venv_win\Scripts\python.exe"
$watchRoot = Join-Path $botRoot "runtime\reports\protective_stop_replace_watch"
$controlRoot = Join-Path $env:USERPROFILE ".codex\memories\eth_trading_bot_watch"
$pidPath = Join-Path $controlRoot "watch.pid"
$stdoutPath = Join-Path $watchRoot "watch_stdout.log"
$stderrPath = Join-Path $watchRoot "watch_stderr.log"

function Get-WatchProcess {
    $processes = @()
    if (Test-Path -LiteralPath $pidPath) {
        $rawPid = (Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
        $processId = 0
        if ([int]::TryParse([string]$rawPid, [ref]$processId)) {
            $pidProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($null -ne $pidProcess) {
                $processes += $pidProcess
            }
        }
    }
    $escapedScript = "*watch_protective_stop_replace.py*"
    $cmdMatches = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $escapedScript -and $_.CommandLine -like "*$botRoot*" }
    foreach ($match in $cmdMatches) {
        $proc = Get-Process -Id $match.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            $processes += $proc
        }
    }
    return @($processes | Sort-Object Id -Unique)
}

New-Item -ItemType Directory -Force -Path $watchRoot | Out-Null
New-Item -ItemType Directory -Force -Path $controlRoot | Out-Null

if ($Action -eq "status") {
    $proc = Get-WatchProcess
    if (@($proc).Count -eq 0) {
        Write-Output "protective stop watcher: stopped"
        exit 1
    }
    foreach ($item in @($proc)) {
        Write-Output ("protective stop watcher: running pid={0} started={1}" -f $item.Id, $item.StartTime)
    }
    exit 0
}

if ($Action -eq "stop") {
    foreach ($proc in @(Get-WatchProcess)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output "protective stop watcher: stopped"
    exit 0
}

$existing = Get-WatchProcess
if (@($existing).Count -gt 0) {
    Write-Output ("protective stop watcher: already running pid={0}" -f (@($existing)[0].Id))
    exit 0
}

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "$botRoot\src"

$argsList = @(
    "scripts\watch_protective_stop_replace.py",
    "--state-path", (Join-Path $botRoot "runtime\shared_state\bot_state.json"),
    "--report-root", (Join-Path $botRoot "runtime\reports\protective_stop_replace"),
    "--watch-report-root", $watchRoot,
    "--proxy-url", "http://127.0.0.1:7897",
    "--watch-interval-sec", ([string]$WatchIntervalSec),
    "--heartbeat-every-sec", ([string]$HeartbeatEverySec)
)

if ($AllowMissingRepair) {
    $argsList += "--allow-missing-repair"
}

$proc = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $argsList `
    -WorkingDirectory $botRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value ([string]$proc.Id) -Encoding ASCII
Write-Output ("protective stop watcher: started pid={0}" -f $proc.Id)
