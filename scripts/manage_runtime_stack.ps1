param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [string]$HostName = "127.0.0.1",
    [int]$DashboardPort = 8765,
    [int]$IntervalSec = 300,
    [int]$WorkerIntervalSec = 30,
    [int]$ReviewIntervalSec = 300,
    [int]$ResearchHealthIntervalSec = 3600,
    [int]$ResearchRefreshEvery = 12,
    [double]$ConsensusRequestTimeoutSec = 15.0,
    [int]$DependencyWaitSec = 30,
    [string]$ProxyUrl = "http://127.0.0.1:7897",
    [switch]$EnableRealOrders,
    [switch]$EnableReviewWorker,
    [switch]$DisableCoinglassOverlay
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

$StackRoot = Join-Path $BotRoot "runtime\stack_manager"
$PidRoot = Join-Path $StackRoot "pids"
$LogRoot = Join-Path $StackRoot "logs"
$WrapperRoot = Join-Path $StackRoot "wrappers"
$BotRuntimeRoot = Join-Path $BotRoot "runtime\bot_runtime_scheduler"
$BotAnalysisDb = Join-Path $BotRuntimeRoot "analysis\bot_runtime.duckdb"
$BotSchedulerLockPath = Join-Path $BotRuntimeRoot "scheduler.lock"
$KillSwitchPath = Join-Path $BotRoot "runtime\controls\disable_real_execution.flag"
$FreshResearchRoot = Join-Path $QuantRoot "runtime\fresh_research"
$FreshResearchWhitelistPath = Join-Path $FreshResearchRoot "whitelist.json"
$FreshResearchAllResultsPath = Join-Path $FreshResearchRoot "all_results.json"
$FreshResearchDispatchRequestPath = Join-Path $FreshResearchRoot "dispatch_request.json"
$PathSep = [System.IO.Path]::PathSeparator

New-Item -ItemType Directory -Force -Path $PidRoot, $LogRoot, $WrapperRoot, $BotRuntimeRoot | Out-Null

function Quote-Arg {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Get-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $Path -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-AgeSeconds {
    param($Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $null
    }
    try {
        $raw = [string]$Value
        $dt = [datetimeoffset]::Parse($raw)
        return [int]([datetimeoffset]::UtcNow - $dt.ToUniversalTime()).TotalSeconds
    }
    catch {
        try {
            $naive = [datetime]::Parse([string]$Value)
            return [int]((Get-Date).ToUniversalTime() - $naive.ToUniversalTime()).TotalSeconds
        }
        catch {
            return $null
        }
    }
}

function Get-FileAgeSeconds {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return [int]((Get-Date) - (Get-Item -LiteralPath $Path).LastWriteTime).TotalSeconds
    }
    catch {
        return $null
    }
}

function Test-FreshJsonArtifact {
    param(
        [string]$Path,
        [string[]]$TimestampFields = @("generated_at", "finished_at"),
        [int]$FreshAfterSec = 1800
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $payload = Get-JsonFile $Path
    foreach ($field in $TimestampFields) {
        if ($null -ne $payload -and $null -ne $payload.$field) {
            $age = Get-AgeSeconds ($payload.$field)
            if ($null -ne $age) {
                return $age -le $FreshAfterSec
            }
        }
    }
    $fileAge = Get-FileAgeSeconds $Path
    return $null -ne $fileAge -and $fileAge -le $FreshAfterSec
}

function Get-LatestQuantCycle {
    $cyclesRoot = Join-Path $QuantRoot "runtime\cycles"
    if (-not (Test-Path -LiteralPath $cyclesRoot)) {
        return $null
    }
    $cycleDirs = Get-ChildItem -LiteralPath $cyclesRoot -Directory -ErrorAction SilentlyContinue
    $latestSchedulerCycle = $cycleDirs |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "scheduler_status.json") } |
        ForEach-Object {
            $statusPath = Join-Path $_.FullName "scheduler_status.json"
            $status = Get-JsonFile $statusPath
            if ($null -ne $status -and [string]$status.status -like "incomplete_*") {
                return
            }
            $timestamp = if ($null -ne $status -and $null -ne $status.generated_at) { [string]$status.generated_at } else { $null }
            $sortKey = [datetimeoffset]::MinValue
            if (-not [string]::IsNullOrWhiteSpace($timestamp)) {
                try {
                    $sortKey = [datetimeoffset]::Parse($timestamp).ToUniversalTime()
                }
                catch {
                    $sortKey = [datetimeoffset]::MinValue
                }
            }
            if ($sortKey -eq [datetimeoffset]::MinValue) {
                $sortKey = [datetimeoffset](Get-Item -LiteralPath $statusPath).LastWriteTimeUtc
            }
            [pscustomobject]@{
                Directory = $_
                SortKey = $sortKey
            }
        } |
        Sort-Object SortKey -Descending |
        Select-Object -First 1
    if ($null -ne $latestSchedulerCycle) {
        return $latestSchedulerCycle.Directory
    }
    return $cycleDirs |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "decision.json") } |
        ForEach-Object {
            $decisionPath = Join-Path $_.FullName "decision.json"
            $decision = Get-JsonFile $decisionPath
            $timestamp = if ($null -ne $decision -and $null -ne $decision.generated_at) { [string]$decision.generated_at } else { $null }
            $sortKey = [datetimeoffset]::MinValue
            if (-not [string]::IsNullOrWhiteSpace($timestamp)) {
                try {
                    $sortKey = [datetimeoffset]::Parse($timestamp).ToUniversalTime()
                }
                catch {
                    $sortKey = [datetimeoffset]::MinValue
                }
            }
            if ($sortKey -eq [datetimeoffset]::MinValue) {
                $sortKey = [datetimeoffset](Get-Item -LiteralPath $decisionPath).LastWriteTimeUtc
            }
            [pscustomobject]@{
                Directory = $_
                SortKey = $sortKey
            }
        } |
        Sort-Object SortKey -Descending |
        Select-Object -ExpandProperty Directory |
        Select-Object -First 1
}

function Clear-StaleBotSchedulerLock {
    if (-not (Test-Path -LiteralPath $BotSchedulerLockPath)) {
        return
    }
    $payload = Get-JsonFile $BotSchedulerLockPath
    $lockPid = 0
    if ($null -ne $payload -and [int]::TryParse([string]$payload.pid, [ref]$lockPid)) {
        $proc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            return
        }
    }
    Remove-Item -LiteralPath $BotSchedulerLockPath -Force -ErrorAction SilentlyContinue
    Write-Output ("bot_scheduler: removed stale lock {0}" -f $BotSchedulerLockPath)
}

function Get-PidPath {
    param([string]$Name)
    return Join-Path $PidRoot "$Name.pid"
}

function Set-ManagedPid {
    param(
        [string]$Name,
        [int]$ProcessId
    )
    Set-Content -LiteralPath (Get-PidPath $Name) -Value ([string]$ProcessId) -Encoding ASCII
}

function Test-CommandLineMatches {
    param(
        [int]$ProcessId,
        [string]$Pattern
    )
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        $cmdLine = [string]$cim.CommandLine
        return [pscustomobject]@{
            Available = $true
            Matches = ($cmdLine -like "*$Pattern*")
        }
    }
    catch {
        return [pscustomobject]@{
            Available = $false
            Matches = $null
        }
    }
}

function Get-ListeningProcessId {
    param(
        [int]$Port,
        [string]$HostName = ""
    )
    if ($Port -le 0) {
        return $null
    }

    try {
        $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        if (-not [string]::IsNullOrWhiteSpace($HostName)) {
            $connections = @($connections | Where-Object {
                $_.LocalAddress -eq $HostName -or $_.LocalAddress -eq "0.0.0.0" -or $_.LocalAddress -eq "::"
            })
        }
        if (@($connections).Count -gt 0) {
            return [int]@($connections | Sort-Object OwningProcess | Select-Object -First 1).OwningProcess
        }
    }
    catch {
    }

    try {
        $pattern = ":{0}\s+" -f $Port
        $lines = @(netstat -ano -p tcp | Select-String -Pattern $pattern)
        foreach ($line in $lines) {
            $parts = @(([string]$line.Line).Trim() -split "\s+")
            if (@($parts).Count -lt 5) {
                continue
            }
            $local = [string]$parts[1]
            $state = [string]$parts[3]
            $owner = [string]$parts[4]
            if ($local -notmatch (":{0}$" -f $Port)) {
                continue
            }
            if ($state -ne "LISTENING") {
                continue
            }
            $ownerPid = 0
            if ([int]::TryParse($owner, [ref]$ownerPid)) {
                return $ownerPid
            }
        }
    }
    catch {
    }
    return $null
}

function New-ManagedProcessState {
    param(
        [string]$Name,
        $ManagedPid,
        [bool]$Alive,
        [bool]$StalePid,
        $CommandLineMatches,
        [bool]$CommandLineAvailable,
        $Process,
        [int]$Port = 0,
        $PortPid = $null,
        [bool]$PortListening = $false,
        [string]$PidSource = "pid_file"
    )
    return [pscustomobject]@{
        Name = $Name
        Pid = $ManagedPid
        Alive = $Alive
        StalePid = $StalePid
        CommandLineMatches = $CommandLineMatches
        CommandLineAvailable = $CommandLineAvailable
        Process = $Process
        Port = $Port
        PortPid = $PortPid
        PortListening = $PortListening
        PidSource = $PidSource
    }
}

function Get-ManagedProcess {
    param(
        [string]$Name,
        [string]$Pattern,
        [int]$Port = 0,
        [string]$PortHost = ""
    )
    $portPid = Get-ListeningProcessId -Port $Port -HostName $PortHost
    if ($null -ne $portPid) {
        $portProc = Get-Process -Id $portPid -ErrorAction SilentlyContinue
        if ($null -ne $portProc) {
            $cmd = Test-CommandLineMatches -ProcessId $portPid -Pattern $Pattern
            Set-ManagedPid -Name $Name -ProcessId $portPid
            return New-ManagedProcessState `
                -Name $Name `
                -ManagedPid $portPid `
                -Alive $true `
                -StalePid $false `
                -CommandLineMatches $cmd.Matches `
                -CommandLineAvailable $cmd.Available `
                -Process $portProc `
                -Port $Port `
                -PortPid $portPid `
                -PortListening $true `
                -PidSource "port"
        }
    }

    $pidPath = Get-PidPath $Name
    if (-not (Test-Path -LiteralPath $pidPath)) {
        $patternPid = Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid 0
        if ($null -ne $patternPid) {
            $patternProc = Get-Process -Id $patternPid -ErrorAction SilentlyContinue
            if ($null -ne $patternProc) {
                Set-ManagedPid -Name $Name -ProcessId $patternPid
                return New-ManagedProcessState `
                    -Name $Name `
                    -ManagedPid $patternPid `
                    -Alive $true `
                    -StalePid $false `
                    -CommandLineMatches $true `
                    -CommandLineAvailable $true `
                    -Process $patternProc `
                    -Port $Port `
                    -PortPid $portPid `
                    -PortListening ($null -ne $portPid) `
                    -PidSource "command"
            }
        }
        return New-ManagedProcessState -Name $Name -ManagedPid $null -Alive $false -StalePid $false -CommandLineMatches $false -CommandLineAvailable $true -Process $null -Port $Port -PortPid $portPid -PortListening ($null -ne $portPid)
    }

    $rawPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1
    $processId = 0
    if (-not [int]::TryParse([string]$rawPid, [ref]$processId)) {
        return New-ManagedProcessState -Name $Name -ManagedPid $rawPid -Alive $false -StalePid $true -CommandLineMatches $false -CommandLineAvailable $true -Process $null -Port $Port -PortPid $portPid -PortListening ($null -ne $portPid)
    }

    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        $patternPid = Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid 0
        if ($null -ne $patternPid) {
            $patternProc = Get-Process -Id $patternPid -ErrorAction SilentlyContinue
            if ($null -ne $patternProc) {
                Set-ManagedPid -Name $Name -ProcessId $patternPid
                return New-ManagedProcessState `
                    -Name $Name `
                    -ManagedPid $patternPid `
                    -Alive $true `
                    -StalePid $false `
                    -CommandLineMatches $true `
                    -CommandLineAvailable $true `
                    -Process $patternProc `
                    -Port $Port `
                    -PortPid $portPid `
                    -PortListening ($null -ne $portPid) `
                    -PidSource "command"
            }
        }
        return New-ManagedProcessState -Name $Name -ManagedPid $processId -Alive $false -StalePid $true -CommandLineMatches $false -CommandLineAvailable $true -Process $null -Port $Port -PortPid $portPid -PortListening ($null -ne $portPid)
    }

    $cmd = Test-CommandLineMatches -ProcessId $processId -Pattern $Pattern
    if ($cmd.Available -and $cmd.Matches -eq $false) {
        $patternPid = Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid 0
        if ($null -ne $patternPid) {
            $patternProc = Get-Process -Id $patternPid -ErrorAction SilentlyContinue
            if ($null -ne $patternProc) {
                Set-ManagedPid -Name $Name -ProcessId $patternPid
                return New-ManagedProcessState `
                    -Name $Name `
                    -ManagedPid $patternPid `
                    -Alive $true `
                    -StalePid $false `
                    -CommandLineMatches $true `
                    -CommandLineAvailable $true `
                    -Process $patternProc `
                    -Port $Port `
                    -PortPid $portPid `
                    -PortListening ($null -ne $portPid) `
                    -PidSource "command"
            }
        }
    }

    return New-ManagedProcessState `
        -Name $Name `
        -ManagedPid $processId `
        -Alive $true `
        -StalePid $false `
        -CommandLineMatches $cmd.Matches `
        -CommandLineAvailable $cmd.Available `
        -Process $proc `
        -Port $Port `
        -PortPid $portPid `
        -PortListening ($null -ne $portPid)
}

function Remove-StalePid {
    param(
        [string]$Name,
        [string]$Pattern,
        [string]$FilePath
    )
    $state = Get-ManagedProcess -Name $Name -Pattern $Pattern
    $expectedProcessName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath)
    $processNameMismatch = (
        -not [string]::IsNullOrWhiteSpace($expectedProcessName) `
        -and $null -ne $state.Process `
        -and $state.Process.ProcessName -ne $expectedProcessName
    )
    if ($state.StalePid -or ($state.CommandLineAvailable -and $state.CommandLineMatches -eq $false) -or $processNameMismatch) {
        Remove-Item -LiteralPath (Get-PidPath $Name) -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-StartedProcessId {
    param(
        [string]$Name,
        [int]$StartedPid,
        [string]$FilePath
    )
    $startedProcessName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath)
    if ($Name -ne "bot_scheduler" -or $startedProcessName -eq "powershell") {
        return $StartedPid
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -le $deadline) {
        if (Test-Path -LiteralPath $BotSchedulerLockPath) {
            $payload = Get-JsonFile $BotSchedulerLockPath
            $lockPid = 0
            if ($null -ne $payload -and [int]::TryParse([string]$payload.pid, [ref]$lockPid)) {
                $lockProc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
                if ($null -ne $lockProc) {
                    return $lockPid
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }
    return $StartedPid
}

function Find-ProcessIdByCommandPattern {
    param(
        [string]$Pattern,
        [int]$StartedPid
    )
    try {
        $matches = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.CommandLine -like "*$Pattern*" -and (
                    $_.ProcessId -eq $StartedPid `
                    -or $_.ParentProcessId -eq $StartedPid `
                    -or $_.CommandLine -like "*$BotRoot*" `
                    -or $_.CommandLine -like "*$QuantRoot*" `
                    -or $_.CommandLine -like "*$WorkspaceRoot*"
                )
            } |
            Sort-Object CreationDate -Descending)
        if (@($matches).Count -gt 0) {
            return [int]@($matches)[0].ProcessId
        }
    }
    catch {
        return $null
    }
    return $null
}

function Repair-PidFromCommandPattern {
    param(
        [string]$Name,
        [string]$Pattern
    )
    $patternPid = Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid 0
    if ($null -eq $patternPid) {
        return $false
    }
    Set-ManagedPid -Name $Name -ProcessId $patternPid
    return $true
}

function Write-ManagedWrapper {
    param(
        [string]$Name,
        [string]$Content
    )
    $path = Join-Path $WrapperRoot "$Name.ps1"
    Set-Content -LiteralPath $path -Value $Content -Encoding UTF8
    return $path
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$Pattern
    )

    Remove-StalePid -Name $Name -Pattern $Pattern -FilePath $FilePath
    Repair-PidFromCommandPattern -Name $Name -Pattern $Pattern | Out-Null
    $existing = Get-ManagedProcess -Name $Name -Pattern $Pattern
    if ($existing.Alive) {
        Write-Output ("{0}: already running pid={1}" -f $Name, $existing.Pid)
        return
    }

    $stdoutPath = Join-Path $LogRoot "$Name`_stdout.log"
    $stderrPath = Join-Path $LogRoot "$Name`_stderr.log"
    $env:ETH_BOT_ROOT = $BotRoot
    $env:QUANT_ROOT = $QuantRoot
    $env:PYTHONPATH = "$BotRoot$PathSep$(Join-Path $BotRoot 'src')"
    $env:PYTHONDONTWRITEBYTECODE = "1"

    if ($Name -eq "bot_scheduler") {
        $proc = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -WindowStyle Hidden `
            -PassThru
    }
    else {
        $proc = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden `
            -PassThru
    }

    $managedPid = Resolve-StartedProcessId -Name $Name -StartedPid $proc.Id -FilePath $FilePath
    $startedProcessName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath)
    if ($Name -eq "bot_scheduler" -and $startedProcessName -ne "powershell") {
        $patternPid = Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid $proc.Id
        if ($null -ne $patternPid) {
            $managedPid = $patternPid
        }
    }
    Set-Content -LiteralPath (Get-PidPath $Name) -Value ([string]$managedPid) -Encoding ASCII
    Start-Sleep -Milliseconds 500
    $started = Get-Process -Id $managedPid -ErrorAction SilentlyContinue
    if ($null -eq $started) {
        Remove-Item -LiteralPath (Get-PidPath $Name) -Force -ErrorAction SilentlyContinue
        Write-Output ("{0}: failed_to_stay_running pid={1}; see logs in {2}" -f $Name, $managedPid, $LogRoot)
        return
    }
    Write-Output ("{0}: started pid={1}" -f $Name, $managedPid)
}

function Stop-ManagedProcess {
    param(
        [string]$Name,
        [string]$Pattern
    )

    $pidPath = Get-PidPath $Name
    $state = Get-ManagedProcess -Name $Name -Pattern $Pattern
    if (-not $state.Alive) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        Write-Output ("{0}: stopped" -f $Name)
        return
    }
    if ($state.CommandLineAvailable -and -not $state.CommandLineMatches) {
        Write-Output ("{0}: pid={1} command line mismatch; not stopping" -f $Name, $state.Pid)
        return
    }

    Stop-Process -Id $state.Pid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Write-Output ("{0}: stopped pid={1}" -f $Name, $state.Pid)
}

function Get-LogErrorSummary {
    param([string]$Name)
    $stderrPath = Join-Path $LogRoot "$Name`_stderr.log"
    if (-not (Test-Path -LiteralPath $stderrPath)) {
        return "log_missing"
    }
    $tail = Get-Content -LiteralPath $stderrPath -Tail 20 -ErrorAction SilentlyContinue
    if ($null -eq $tail -or @($tail).Count -eq 0) {
        return "no_recent_errors"
    }
    $errorCount = @($tail | Where-Object { $_ -match "error|exception|traceback|blocked|failed" }).Count
    return ("tail_error_lines={0}/20" -f $errorCount)
}

function Get-HttpStatus {
    param([string]$Uri)
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3).StatusCode
    }
    catch {
        return 0
    }
}

function Format-Age {
    param($Seconds)
    if ($null -eq $Seconds) {
        return "unknown"
    }
    return ("{0}s" -f [int]$Seconds)
}

function Format-ProcessHealth {
    param($State)
    if ($State.StalePid) {
        return "stale_pid"
    }
    if (-not $State.Alive) {
        return "stopped"
    }
    if ($State.CommandLineAvailable -and -not $State.CommandLineMatches) {
        return "command_mismatch"
    }
    return "running"
}

function Test-QuantReady {
    $freshAfterSec = [Math]::Max(1800, $IntervalSec * 2)
    $heartbeat = Join-Path $QuantRoot "runtime\scheduler\heartbeat.json"
    $handoff = Join-Path $QuantRoot "runtime\cycles\latest_strict_live\handoff.json"
    $executionHandoff = Join-Path $QuantRoot "runtime\cycles\latest_strict_live\execution_handoff.json"
    return (Test-FreshJsonArtifact -Path $heartbeat -FreshAfterSec $freshAfterSec) `
        -or (Test-FreshJsonArtifact -Path $handoff -FreshAfterSec $freshAfterSec) `
        -or (Test-FreshJsonArtifact -Path $executionHandoff -FreshAfterSec $freshAfterSec)
}

function Test-BotReady {
    $freshAfterSec = [Math]::Max(900, $IntervalSec * 2)
    $latestCycle = Join-Path $BotRuntimeRoot "latest_cycle.json"
    $candidate = Join-Path $BotRuntimeRoot "latest_candidate_execution_package.json"
    return (Test-FreshJsonArtifact -Path $latestCycle -TimestampFields @("generated_at", "finished_at") -FreshAfterSec $freshAfterSec) `
        -or (Test-FreshJsonArtifact -Path $candidate -TimestampFields @("generated_at") -FreshAfterSec $freshAfterSec)
}

function Wait-ForCondition {
    param(
        [string]$Name,
        [scriptblock]$Condition,
        [int]$TimeoutSec
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(0, $TimeoutSec))
    while ((Get-Date) -le $deadline) {
        if (& $Condition) {
            Write-Output ("{0}: dependency ready" -f $Name)
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Output ("{0}: dependency wait timed out after {1}s" -f $Name, $TimeoutSec)
    return $false
}

function Show-Status {
    $dashboard = Get-ManagedProcess -Name "dashboard" -Pattern "dashboard.app" -Port $DashboardPort -PortHost $HostName
    $factor = Get-ManagedProcess -Name "factor_ingest" -Pattern "ingest-summary"
    $quant = Get-ManagedProcess -Name "quant_judgement" -Pattern "run-cycle"
    $research = Get-ManagedProcess -Name "research_health" -Pattern "research-health"
    $bot = Get-ManagedProcess -Name "bot_scheduler" -Pattern "bot_scheduler_loop.ps1"
    $worker = Get-ManagedProcess -Name "real_worker" -Pattern "manage_real_order_worker.ps1"
    if (-not $worker.Alive) {
        $worker = Get-ManagedProcess -Name "real_worker" -Pattern "real_order_worker.py"
    }
    $review = Get-ManagedProcess -Name "review_worker" -Pattern "review_runtime_decisions.py"

    $homeStatus = Get-HttpStatus -Uri ("http://{0}:{1}/" -f $HostName, $DashboardPort)
    $apiStatus = Get-HttpStatus -Uri ("http://{0}:{1}/api/overview" -f $HostName, $DashboardPort)
    $dashboardState = Format-ProcessHealth $dashboard
    if ($dashboardState -eq "running" -and (-not $dashboard.PortListening -or $homeStatus -ne 200 -or $apiStatus -ne 200)) {
        $dashboardState = "degraded"
    }
    Write-Output ("dashboard: {0} pid={1} http={2} api={3} log={4}" -f $dashboardState, $dashboard.Pid, $homeStatus, $apiStatus, (Get-LogErrorSummary "dashboard"))

    $factorIngest = Get-JsonFile (Join-Path $QuantRoot "runtime\analysis\factor_ingest_latest.json")
    $factorSummary = Get-JsonFile (Join-Path $QuantRoot "runtime\analysis\factor_summary.json")
    $factorAge = Get-AgeSeconds ($factorIngest.generated_at)
    if ($null -eq $factorAge) {
        $factorAge = Get-AgeSeconds ($factorSummary.generated_at)
    }
    if ($null -eq $factorAge) {
        $factorAge = Get-FileAgeSeconds (Join-Path $QuantRoot "runtime\analysis\factor_ingest_latest.json")
    }
    if ($null -eq $factorAge) {
        $factorAge = Get-FileAgeSeconds (Join-Path $QuantRoot "runtime\analysis\factor_summary.json")
    }
    $factorState = Format-ProcessHealth $factor
    Write-Output ("factor_ingest: {0} pid={1} age={2} log={3}" -f $factorState, $factor.Pid, (Format-Age $factorAge), (Get-LogErrorSummary "factor_ingest"))

    $quantHeartbeat = Get-JsonFile (Join-Path $QuantRoot "runtime\scheduler\heartbeat.json")
    $quantHandoff = Get-JsonFile (Join-Path $QuantRoot "runtime\cycles\latest_strict_live\handoff.json")
    if ($null -eq $quantHandoff) {
        $quantHandoff = Get-JsonFile (Join-Path $QuantRoot "runtime\cycles\latest_strict_live\execution_handoff.json")
    }
    $latestQuantCycle = Get-LatestQuantCycle
    $latestQuantStatus = $null
    if ($null -ne $latestQuantCycle) {
        $latestQuantStatus = Get-JsonFile (Join-Path $latestQuantCycle.FullName "scheduler_status.json")
    }
    $quantAge = Get-AgeSeconds ($quantHeartbeat.generated_at)
    if ($null -ne $latestQuantStatus) {
        $statusAge = Get-AgeSeconds ($latestQuantStatus.generated_at)
        if ($null -ne $statusAge) {
            $quantAge = $statusAge
        }
    }
    if ($null -ne $latestQuantCycle -and $null -eq $quantAge) {
        $quantAge = Get-FileAgeSeconds (Join-Path $latestQuantCycle.FullName "decision.json")
    }
    if ($null -eq $quantAge) {
        $quantAge = Get-AgeSeconds ($quantHandoff.generated_at)
    }
    $latestRunId = ""
    if ($null -ne $latestQuantStatus -and -not [string]::IsNullOrWhiteSpace([string]$latestQuantStatus.run_id)) {
        $latestRunId = [string]$latestQuantStatus.run_id
    }
    elseif ($null -ne $latestQuantCycle) {
        $latestRunId = $latestQuantCycle.Name
    }
    elseif ($null -ne $quantHeartbeat.metadata -and $null -ne $quantHeartbeat.metadata.cycle_dir) {
        $latestRunId = Split-Path -Leaf ([string]$quantHeartbeat.metadata.cycle_dir)
    }
    $quantState = Format-ProcessHealth $quant
    Write-Output ("quant_judgement: {0} pid={1} age={2} latest_run_id={3} log={4}" -f $quantState, $quant.Pid, (Format-Age $quantAge), $latestRunId, (Get-LogErrorSummary "quant_judgement"))

    $researchHealth = Get-JsonFile (Join-Path $QuantRoot "runtime\scheduler\research_health.json")
    $researchAuto = if ($null -ne $researchHealth -and $null -ne $researchHealth.metadata) { $researchHealth.metadata.research_auto_refresh } else { $null }
    $researchAge = Get-AgeSeconds ($researchHealth.generated_at)
    $researchStatus = if ($null -ne $researchHealth -and $null -ne $researchHealth.status) { [string]$researchHealth.status } else { "unavailable" }
    $researchRefreshStatus = if ($null -ne $researchAuto -and $null -ne $researchAuto.status) { [string]$researchAuto.status } else { "unavailable" }
    $researchQualifiedCount = if ($null -ne $researchAuto -and $null -ne $researchAuto.qualified_candidate_count) { [int]$researchAuto.qualified_candidate_count } else { 0 }
    $researchState = Format-ProcessHealth $research
    Write-Output ("research_health: {0} pid={1} age={2} status={3} refresh={4} qualified={5} log={6}" -f $researchState, $research.Pid, (Format-Age $researchAge), $researchStatus, $researchRefreshStatus, $researchQualifiedCount, (Get-LogErrorSummary "research_health"))

    $botHeartbeat = Get-JsonFile (Join-Path $BotRuntimeRoot "heartbeat.json")
    $botCycle = Get-JsonFile (Join-Path $BotRuntimeRoot "latest_cycle.json")
    $botAge = Get-AgeSeconds ($botHeartbeat.generated_at)
    if ($null -eq $botAge) {
        $botAge = Get-AgeSeconds ($botCycle.finished_at)
    }
    $botState = Format-ProcessHealth $bot
    Write-Output ("bot_scheduler: {0} pid={1} age={2} latest_sample={3} log={4}" -f $botState, $bot.Pid, (Format-Age $botAge), $botCycle.sample_id, (Get-LogErrorSummary "bot_scheduler"))

    $candidate = Get-JsonFile (Join-Path $BotRuntimeRoot "latest_candidate_execution_package.json")
    $candidateAge = Get-AgeSeconds ($candidate.generated_at)
    $candidateExpiryAge = Get-AgeSeconds ($candidate.expires_at)
    $candidateState = if ($null -eq $candidate) { "missing" } elseif ($candidateExpiryAge -ne $null -and $candidateExpiryAge -gt 0) { "expired" } else { "present" }
    Write-Output ("candidate_package: {0} age={1} package_id={2}" -f $candidateState, (Format-Age $candidateAge), $candidate.package_id)

    $auditPath = Join-Path $BotRoot "runtime\real_order_worker\audit.jsonl"
    $auditAge = $null
    $auditStatus = "missing"
    if (Test-Path -LiteralPath $auditPath) {
        $lastLine = [string](Get-Content -LiteralPath $auditPath -Encoding UTF8 -Tail 1 -ErrorAction SilentlyContinue | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace($lastLine)) {
            try {
                $event = ConvertFrom-Json -InputObject $lastLine
                $auditAge = Get-AgeSeconds ($event.generated_at)
                if ($null -ne $event.payload -and $null -ne $event.payload.status) {
                    $auditStatus = [string]$event.payload.status
                }
                else {
                    $auditStatus = [string]$event.event_type
                }
            }
            catch {
                $auditStatus = "invalid_json"
            }
        }
    }
    $killSwitch = Test-Path -LiteralPath $KillSwitchPath
    $workerMode = if ($EnableRealOrders -and -not $killSwitch) { "submit_enabled" } elseif ($killSwitch) { "disabled_by_kill_switch" } else { "dry_run" }
    $workerState = Format-ProcessHealth $worker
    $killSwitchState = if ($killSwitch) { "enabled" } else { "off" }
    Write-Output ("real_worker: {0} pid={1} mode={2} audit_age={3} audit_status={4} log={5}" -f $workerState, $worker.Pid, $workerMode, (Format-Age $auditAge), $auditStatus, (Get-LogErrorSummary "real_worker"))
    $reviewPath = Join-Path $BotRoot "runtime\reviews\latest_decision_review.json"
    $reviewPayload = Get-JsonFile $reviewPath
    $reviewAge = Get-AgeSeconds ($reviewPayload.generated_at)
    $reviewStatus = if ($null -ne $reviewPayload -and $null -ne $reviewPayload.review_status) { [string]$reviewPayload.review_status } else { "unavailable" }
    if ([bool]$EnableReviewWorker) {
        $reviewState = Format-ProcessHealth $review
    }
    else {
        $reviewState = "optional_disabled"
    }
    Write-Output ("review_worker: {0} pid={1} age={2} review_status={3} log={4}" -f $reviewState, $review.Pid, (Format-Age $reviewAge), $reviewStatus, (Get-LogErrorSummary "review_worker"))
    Write-Output ("kill_switch: {0} path={1}" -f $killSwitchState, $KillSwitchPath)
}

if ($Action -eq "status") {
    Show-Status
    exit 0
}

if ($Action -eq "stop") {
    Stop-ManagedProcess -Name "review_worker" -Pattern "review_runtime_decisions.py"
    Stop-ManagedProcess -Name "real_worker" -Pattern "manage_real_order_worker.ps1"
    Stop-ManagedProcess -Name "real_worker" -Pattern "real_order_worker.py"
    Stop-ManagedProcess -Name "bot_scheduler" -Pattern "bot_scheduler_loop.ps1"
    Stop-ManagedProcess -Name "quant_judgement" -Pattern "run-cycle"
    Stop-ManagedProcess -Name "research_health" -Pattern "research-health"
    Stop-ManagedProcess -Name "factor_ingest" -Pattern "ingest-summary"
    Stop-ManagedProcess -Name "dashboard" -Pattern "dashboard.app"
    exit 0
}

$DashboardArgs = @("-m", "dashboard.app", "--host", $HostName, "--port", ([string]$DashboardPort))
$FactorArgs = @(
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
$ResearchHealthArgs = @(
    "scripts\quant_runtime_scheduler.py",
    "research-health",
    "--loop",
    "--interval-sec",
    ([string]$ResearchHealthIntervalSec),
    "--degraded-heartbeat-interval-sec",
    ([string]$ResearchHealthIntervalSec),
    "--auto-refresh-worker",
    "--research-refresh-lock-ttl-sec",
    "1800",
    "--feature-matrix-path",
    "runtime\feature_matrix.json",
    "--research-refresh-output-dir",
    "runtime\fresh_research",
    "--research-refresh-reports-dir",
    "runtime\reports",
    "--whitelist-path",
    $FreshResearchWhitelistPath,
    "--all-results-path",
    $FreshResearchAllResultsPath
)
$QuantArgs = @(
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
    "--consensus-request-timeout-sec",
    ([string]$ConsensusRequestTimeoutSec),
    "--refresh-research-aliases-every",
    ([string]$ResearchRefreshEvery),
    "--whitelist-path",
    $FreshResearchWhitelistPath,
    "--all-results-path",
    $FreshResearchAllResultsPath,
    "--research-dispatch-request",
    $FreshResearchDispatchRequestPath,
    "--include-okx-overlay",
    "--include-coinglass-overlay"
)
if ($DisableCoinglassOverlay) {
    $QuantArgs = @($QuantArgs | Where-Object { $_ -ne "--include-coinglass-overlay" })
    $QuantArgs += "--no-include-coinglass-overlay"
}
$BotArgs = @(
    "scripts\ops\bot_runtime_scheduler.py",
    "run-once",
    "--runtime-root",
    $BotRuntimeRoot,
    "--consensus-request-timeout-sec",
    ([string]$ConsensusRequestTimeoutSec),
    "--research-dispatch-request",
    $FreshResearchDispatchRequestPath,
    "--analysis-db-path",
    $BotAnalysisDb,
    "--api-key-env",
    "OKX_TRADE_API_KEY",
    "--api-secret-env",
    "OKX_TRADE_API_SECRET",
    "--api-passphrase-env",
    "OKX_TRADE_PASSPHRASE",
    "--include-coinglass-overlay"
)
if ($DisableCoinglassOverlay) {
    $BotArgs = @($BotArgs | Where-Object { $_ -ne "--include-coinglass-overlay" })
    $BotArgs += "--no-include-coinglass-overlay"
}
if ($EnableRealOrders) {
    $BotArgs += "--enable-real-orders"
}
$BotSchedulerWrapper = Write-ManagedWrapper -Name "bot_scheduler_loop" -Content @"
`$env:ETH_BOT_ROOT = '$(($BotRoot) -replace "'", "''")'
`$env:QUANT_ROOT = '$(($QuantRoot) -replace "'", "''")'
`$env:PYTHONPATH = '$(($BotRoot) -replace "'", "''")$PathSep$((Join-Path $BotRoot 'src') -replace "'", "''")'
`$stdoutPath = '$(Join-Path $LogRoot 'bot_scheduler_child_stdout.log')'
`$stderrPath = '$(Join-Path $LogRoot 'bot_scheduler_child_stderr.log')'
while (`$true) {
    "`$(Get-Date -Format o) bot_scheduler run-once starting" | Add-Content -LiteralPath `$stdoutPath -Encoding UTF8
    & '$(($Python) -replace "'", "''")' $(($BotArgs | ForEach-Object { Quote-Arg ([string]$_) }) -join " ") >> `$stdoutPath 2>> `$stderrPath
    "`$(Get-Date -Format o) bot_scheduler run-once exit=`$LASTEXITCODE" | Add-Content -LiteralPath `$stdoutPath -Encoding UTF8
    Start-Sleep -Seconds $IntervalSec
}
"@
$BotSchedulerArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $BotSchedulerWrapper)

$WorkerSubmitFlag = if ($EnableRealOrders -and -not (Test-Path -LiteralPath $KillSwitchPath)) { "-SubmitRealOrders" } else { "" }
$WorkerLoopCommand = @"
`$env:ETH_BOT_ROOT = '$(($BotRoot) -replace "'", "''")'
`$env:QUANT_ROOT = '$(($QuantRoot) -replace "'", "''")'
`$env:PYTHONPATH = '$(($BotRoot) -replace "'", "''")$PathSep$((Join-Path $BotRoot 'src') -replace "'", "''")'
while (`$true) {
    & '$(Join-Path $BotRoot 'scripts\manage_real_order_worker.ps1')' $WorkerSubmitFlag
    Start-Sleep -Seconds $WorkerIntervalSec
}
"@
$WorkerArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $WorkerLoopCommand)
$ReviewArgs = @(
    "scripts\diagnostics\review_runtime_decisions.py",
    "--loop",
    "--interval-sec",
    ([string]$ReviewIntervalSec),
    "--bot-root",
    $BotRoot,
    "--quant-root",
    $QuantRoot
)

Start-ManagedProcess -Name "dashboard" -FilePath $Python -ArgumentList $DashboardArgs -WorkingDirectory $BotRoot -Pattern "dashboard.app"
Start-ManagedProcess -Name "factor_ingest" -FilePath $Python -ArgumentList $FactorArgs -WorkingDirectory $QuantRoot -Pattern "ingest-summary"
Start-ManagedProcess -Name "research_health" -FilePath $Python -ArgumentList $ResearchHealthArgs -WorkingDirectory $QuantRoot -Pattern "research-health"
Start-ManagedProcess -Name "quant_judgement" -FilePath $Python -ArgumentList $QuantArgs -WorkingDirectory $QuantRoot -Pattern "run-cycle"
Wait-ForCondition -Name "quant_judgement" -Condition { Test-QuantReady } -TimeoutSec $DependencyWaitSec | Out-Null
Clear-StaleBotSchedulerLock
Start-ManagedProcess -Name "bot_scheduler" -FilePath "powershell.exe" -ArgumentList $BotSchedulerArgs -WorkingDirectory $BotRoot -Pattern "bot_scheduler_loop.ps1"
$botReady = Wait-ForCondition -Name "bot_scheduler" -Condition { Test-BotReady } -TimeoutSec $DependencyWaitSec

if ($EnableRealOrders -and (Test-Path -LiteralPath $KillSwitchPath)) {
    Write-Output ("real_worker: not started because kill switch is enabled at {0}" -f $KillSwitchPath)
}
elseif (-not $botReady -and -not (Test-BotReady)) {
    Write-Output "real_worker: not started because candidate package and latest bot cycle are missing"
}
else {
    Start-ManagedProcess -Name "real_worker" -FilePath "powershell.exe" -ArgumentList $WorkerArgs -WorkingDirectory $BotRoot -Pattern "manage_real_order_worker.ps1"
}
if ($EnableReviewWorker) {
    Start-ManagedProcess -Name "review_worker" -FilePath $Python -ArgumentList $ReviewArgs -WorkingDirectory $BotRoot -Pattern "review_runtime_decisions.py"
}

Write-Output ("dashboard_url=http://{0}:{1}" -f $HostName, $DashboardPort)
if ($EnableRealOrders) {
    Write-Output "real_order_submission=enabled"
}
else {
    Write-Output "real_order_submission=disabled"
}
