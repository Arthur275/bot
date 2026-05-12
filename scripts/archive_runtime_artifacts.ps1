param(
    [string]$BotRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path,
    [string]$QuantRoot = "",
    [string]$RunId = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [int]$MinAgeHours = 12,
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ExistingDirectory {
    param([string]$PathValue)
    $resolved = Resolve-Path -LiteralPath $PathValue
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Container)) {
        throw "Expected directory: $PathValue"
    }
    return $resolved.Path
}

function Get-FullPath {
    param([string]$PathValue)
    return [System.IO.Path]::GetFullPath($PathValue)
}

function Assert-PathUnder {
    param(
        [string]$ChildPath,
        [string]$ParentPath
    )
    $child = Get-FullPath $ChildPath
    $parent = (Get-FullPath $ParentPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $prefix = $parent + [System.IO.Path]::DirectorySeparatorChar
    if (-not ($child.Equals($parent, [System.StringComparison]::OrdinalIgnoreCase) -or $child.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "Unsafe path outside expected root: child=$child parent=$parent"
    }
}

function Test-NameMatches {
    param(
        [string]$Name,
        [string[]]$Patterns
    )
    foreach ($pattern in $Patterns) {
        if ($Name -like $pattern) {
            return $pattern
        }
    }
    return $null
}

function Get-RuntimeArchiveCandidates {
    param(
        [string]$RepoName,
        [string]$RepoRoot,
        [string[]]$Patterns,
        [datetime]$Cutoff
    )
    $runtimeRoot = Join-Path $RepoRoot "runtime"
    if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
        return @()
    }
    Assert-PathUnder -ChildPath $runtimeRoot -ParentPath $RepoRoot
    $items = Get-ChildItem -LiteralPath $runtimeRoot -Force
    $candidates = @()
    foreach ($item in $items) {
        $matchedPattern = Test-NameMatches -Name $item.Name -Patterns $Patterns
        if (-not $matchedPattern) {
            continue
        }
        if ($item.LastWriteTime -gt $Cutoff) {
            continue
        }
        $archiveRoot = Join-Path $RepoRoot (Join-Path "archive\runtime" $RunId)
        $destination = Join-Path $archiveRoot $item.Name
        Assert-PathUnder -ChildPath $item.FullName -ParentPath $runtimeRoot
        Assert-PathUnder -ChildPath $destination -ParentPath (Join-Path $RepoRoot "archive\runtime")
        $candidates += [pscustomobject]@{
            repo = $RepoName
            pattern = $matchedPattern
            type = if ($item.PSIsContainer) { "directory" } else { "file" }
            source = $item.FullName
            destination = $destination
            length = if ($item.PSIsContainer) { $null } else { $item.Length }
            last_write_time = $item.LastWriteTime.ToString("o")
        }
    }
    return $candidates
}

function Invoke-RuntimeArchive {
    param(
        [string]$RepoName,
        [string]$RepoRoot,
        [object[]]$Candidates
    )
    if ($Candidates.Count -eq 0) {
        return
    }
    $archiveRoot = Join-Path $RepoRoot (Join-Path "archive\runtime" $RunId)
    if ($Execute) {
        New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
        Assert-PathUnder -ChildPath $archiveRoot -ParentPath (Join-Path $RepoRoot "archive\runtime")
    }
    foreach ($candidate in $Candidates) {
        if (-not $Execute) {
            continue
        }
        if (Test-Path -LiteralPath $candidate.destination) {
            throw "Archive destination already exists: $($candidate.destination)"
        }
        Move-Item -LiteralPath $candidate.source -Destination $candidate.destination
    }
    if ($Execute) {
        $manifest = [pscustomobject]@{
            generated_at = (Get-Date).ToString("o")
            run_id = $RunId
            repo = $RepoName
            min_age_hours = $MinAgeHours
            items = $Candidates
        }
        $manifestPath = Join-Path $archiveRoot "manifest.json"
        $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    }
}

$botRootResolved = Resolve-ExistingDirectory $BotRoot
if (-not $QuantRoot) {
    $QuantRoot = Join-Path (Split-Path -Parent $botRootResolved) "quant_system_rebuild"
}
$quantRootResolved = Resolve-ExistingDirectory $QuantRoot

$cutoff = (Get-Date).AddHours(-1 * $MinAgeHours)
$botPatterns = @("*probe*", "shadow_live*", "shadow_preflight*", "pytest_*")
$quantPatterns = @("ccxt_probe_*", "route_c_probe*", "obs_*", "pytest_tmp", "_heartbeat_check*.py")

$allCandidates = @()
$botCandidates = @(Get-RuntimeArchiveCandidates -RepoName "eth_trading_bot" -RepoRoot $botRootResolved -Patterns $botPatterns -Cutoff $cutoff)
$quantCandidates = @(Get-RuntimeArchiveCandidates -RepoName "quant_system_rebuild" -RepoRoot $quantRootResolved -Patterns $quantPatterns -Cutoff $cutoff)
$allCandidates += $botCandidates
$allCandidates += $quantCandidates

if ($Execute) {
    Invoke-RuntimeArchive -RepoName "eth_trading_bot" -RepoRoot $botRootResolved -Candidates $botCandidates
    Invoke-RuntimeArchive -RepoName "quant_system_rebuild" -RepoRoot $quantRootResolved -Candidates $quantCandidates
}

[pscustomobject]@{
    mode = if ($Execute) { "execute" } else { "dry_run" }
    run_id = $RunId
    min_age_hours = $MinAgeHours
    candidate_count = $allCandidates.Count
    candidates = $allCandidates
} | ConvertTo-Json -Depth 6
