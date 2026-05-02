param(
    [switch]$AllowMissingRepair,
    [switch]$OpenReportFolder,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$botRoot = Split-Path -Parent $PSScriptRoot
$manager = Join-Path $PSScriptRoot "manage_protective_stop_watch.cmd"
$watchRoot = Join-Path $botRoot "runtime\reports\protective_stop_replace_watch"
$replaceRoot = Join-Path $botRoot "runtime\reports\protective_stop_replace"
$statePath = Join-Path $botRoot "runtime\shared_state\bot_state.json"
$stdoutPath = Join-Path $watchRoot "watch_stdout.log"
$stderrPath = Join-Path $watchRoot "watch_stderr.log"
$latestPreview = Join-Path $replaceRoot "latest_preview.json"

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host ("==== {0} ====" -f $Title)
}

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Show-Status {
    $statusOutput = & $manager status 2>$null
    $running = $LASTEXITCODE -eq 0
    Write-Section "Watcher Status"
    if ($running) {
        Write-Host "Status: RUNNING"
        foreach ($line in $statusOutput) {
            Write-Host ("Detail: {0}" -f $line)
        }
    } else {
        Write-Host "Status: STOPPED"
    }
}

function Show-ReadableSnapshot {
    $preview = Read-JsonFile $latestPreview
    Write-Section "Latest Protective Stop Preview"
    if ($null -eq $preview) {
        Write-Host "No latest_preview.json yet. The watcher will generate reports after it runs."
        return
    }
    $snapshot = $preview.snapshot
    $position = $snapshot.position
    $risk = $preview.risk_change
    $record = $preview.recorded_protective_stop
    Write-Host ("Position: {0} {1} ETH {2}" -f $position.position_state, $position.position_amt, $position.direction)
    Write-Host ("Entry price: {0}" -f $position.entry_price)
    Write-Host ("Mark price: {0}" -f $position.mark_price)
    Write-Host ("Protective stop: {0} {1} @ {2}" -f $record.order_type, $record.side, $record.trigger_price)
    if ($risk) {
        Write-Host ("Current lock stage: {0}" -f $risk.current_lock_stage)
        Write-Host ("Target lock stage: {0}" -f $risk.target_lock_stage)
        Write-Host ("mark buffer: {0:P3}" -f [double]$risk.mark_buffer_pct)
        Write-Host ("Target stop: {0}" -f $risk.target_stop_price)
    }
    if ($preview.blocked_reasons -and $preview.blocked_reasons.Count -gt 0) {
        Write-Host "Not advancing because:"
        foreach ($item in $preview.blocked_reasons) {
            Write-Host ("- {0}" -f $item)
        }
    } else {
        Write-Host "Current state: ready or preview executable."
    }
}

function Show-Paths {
    Write-Section "Useful Paths"
    Write-Host ("State file: {0}" -f $statePath)
    Write-Host ("Watch report folder: {0}" -f $watchRoot)
    Write-Host ("Protective stop preview: {0}" -f $latestPreview)
    Write-Host ("Watcher stdout log: {0}" -f $stdoutPath)
    Write-Host ("Watcher stderr log: {0}" -f $stderrPath)
    Write-Host ""
    Write-Host "Open report folder:"
    Write-Host ('explorer "{0}"' -f $watchRoot)
    Write-Host ""
    Write-Host "Check status:"
    Write-Host ('"{0}" status' -f $manager)
    Write-Host ""
    Write-Host "Stop watcher:"
    Write-Host ('"{0}" stop' -f $manager)
}

if ($Stop) {
    & $manager stop
    Show-Status
    exit 0
}

New-Item -ItemType Directory -Force -Path $watchRoot | Out-Null

& $manager status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    if ($AllowMissingRepair) {
        & $manager start -AllowMissingRepair
    } else {
        & $manager start
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "Protective stop watcher is already running."
}

Show-Status
Show-ReadableSnapshot
Show-Paths

if ($OpenReportFolder) {
    explorer $watchRoot
}
