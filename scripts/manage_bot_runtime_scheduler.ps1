param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [int]$IntervalSec = 300,
    [string]$RuntimeRoot = "",
    [string]$AnalysisDbPath = "",
    [switch]$EnableRealOrders
)

$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $botRoot
$pythonExe = Join-Path $workspaceRoot "quant_system_rebuild\.venv_win\Scripts\python.exe"
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $botRoot "runtime\bot_runtime_scheduler"
}
$controlRoot = Join-Path $env:USERPROFILE ".codex\memories\eth_trading_bot_scheduler"
$pidPath = Join-Path $controlRoot "scheduler.pid"
$stdoutPath = Join-Path $RuntimeRoot "scheduler_stdout.log"
$stderrPath = Join-Path $RuntimeRoot "scheduler_stderr.log"

function Get-SchedulerProcess {
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
    $matches = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*bot_runtime_scheduler.py*" -and $_.CommandLine -like "*$botRoot*"
        }
    )
    foreach ($match in $matches) {
        $proc = Get-Process -Id $match.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            $processes += $proc
        }
    }
    return @($processes | Sort-Object Id -Unique)
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
New-Item -ItemType Directory -Force -Path $controlRoot | Out-Null

if ($Action -eq "status") {
    $proc = Get-SchedulerProcess
    if (@($proc).Count -eq 0) {
        Write-Output "bot runtime scheduler: stopped"
        exit 1
    }
    foreach ($item in @($proc)) {
        Write-Output ("bot runtime scheduler: running pid={0} started={1}" -f $item.Id, $item.StartTime)
    }
    exit 0
}

if ($Action -eq "stop") {
    foreach ($proc in @(Get-SchedulerProcess)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output "bot runtime scheduler: stopped"
    exit 0
}

$existing = Get-SchedulerProcess
if (@($existing).Count -gt 0) {
    Write-Output ("bot runtime scheduler: already running pid={0}" -f (@($existing)[0].Id))
    exit 0
}

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "$botRoot\src"

$argsList = @(
    "scripts\bot_runtime_scheduler.py",
    "loop",
    "--interval-sec", ([string]$IntervalSec),
    "--runtime-root", $RuntimeRoot
)

if (-not [string]::IsNullOrWhiteSpace($AnalysisDbPath)) {
    $argsList += @("--analysis-db-path", $AnalysisDbPath)
}
if ($EnableRealOrders) {
    $argsList += "--enable-real-orders"
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
Write-Output ("bot runtime scheduler: started pid={0}" -f $proc.Id)
