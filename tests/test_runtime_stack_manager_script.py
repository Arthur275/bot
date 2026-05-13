from __future__ import annotations

from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
SCRIPT_PATH = REPO_ROOT / "scripts" / "manage_runtime_stack.ps1"
CMD_PATH = REPO_ROOT / "scripts" / "manage_runtime_stack.cmd"
BOT_SCHEDULER_MANAGER_PATH = REPO_ROOT / "scripts" / "manage_bot_runtime_scheduler.ps1"
LAUNCH_STACK_PATH = REPO_ROOT / "scripts" / "launch_runtime_stack.ps1"
PLAN_PATH = REPO_ROOT / "docs" / "runtime_stack_manager_plan.md"
FINAL_PLAN_PATH = REPO_ROOT / "docs" / "automation_research_factor_dashboard_final_plan.md"
ARCHIVED_DOCS_ROOT = REPO_ROOT / "archive" / "docs" / "2026-05-09" / "docs"
if not PLAN_PATH.exists():
    PLAN_PATH = ARCHIVED_DOCS_ROOT / "runtime_stack_manager_plan.md"
if not FINAL_PLAN_PATH.exists():
    FINAL_PLAN_PATH = ARCHIVED_DOCS_ROOT / "automation_research_factor_dashboard_final_plan.md"


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _bot_scheduler_manager_script() -> str:
    return BOT_SCHEDULER_MANAGER_PATH.read_text(encoding="utf-8")


def _launch_stack_script() -> str:
    return LAUNCH_STACK_PATH.read_text(encoding="utf-8")


def test_runtime_stack_manager_script_parses_as_powershell() -> None:
    parser = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "$tokens=$null; $errors=$null; "
                "[System.Management.Automation.Language.Parser]::ParseFile("
                f"'{SCRIPT_PATH}', [ref]$tokens, [ref]$errors) | Out-Null; "
                "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert parser.returncode == 0, parser.stdout + parser.stderr


def test_runtime_stack_manager_entrypoints_exist() -> None:
    script = _script()
    cmd = CMD_PATH.read_text(encoding="utf-8")

    assert SCRIPT_PATH.exists()
    assert CMD_PATH.exists()
    assert '[ValidateSet("start", "stop", "status")]' in script
    assert "[switch]$EnableRealOrders" in script
    assert "[switch]$EnableReviewWorker" in script
    assert "[switch]$DisableCoinglassOverlay" in script
    assert "[int]$ResearchHealthIntervalSec = 3600" in script
    assert "[int]$ResearchRefreshEvery = 12" in script
    assert "[int]$ReviewIntervalSec = 300" in script
    assert "[double]$ConsensusRequestTimeoutSec = 15.0" in script
    assert "manage_runtime_stack.ps1" in cmd


def test_runtime_stack_manager_starts_components_in_dependency_order() -> None:
    script = _script()
    expected_order = [
        'Start-ManagedProcess -Name "dashboard"',
        'Start-ManagedProcess -Name "factor_ingest"',
        'Start-ManagedProcess -Name "research_health"',
        'Start-ManagedProcess -Name "quant_judgement"',
        'Wait-ForCondition -Name "quant_judgement"',
        'Start-ManagedProcess -Name "bot_scheduler"',
        'Wait-ForCondition -Name "bot_scheduler"',
        'Start-ManagedProcess -Name "real_worker"',
    ]

    cursor = -1
    for marker in expected_order:
        found = script.find(marker)
        assert found > cursor, marker
        cursor = found


def test_runtime_stack_manager_runs_bot_scheduler_through_stable_powershell_wrapper() -> None:
    script = _script()
    start_block = script[script.index("$BotSchedulerWrapper =") : script.index("$botReady = Wait-ForCondition -Name \"bot_scheduler\"")]

    assert "$BotSchedulerArgs" in start_block
    assert "Write-ManagedWrapper -Name \"bot_scheduler_loop\"" in start_block
    assert "while (`$true)" in start_block
    assert "bot_scheduler_child_stdout.log" in start_block
    assert "bot_scheduler_child_stderr.log" in start_block
    assert "bot_scheduler run-once starting" in start_block
    assert "bot_scheduler run-once exit=`$LASTEXITCODE" in start_block
    assert '"scripts\\ops\\bot_runtime_scheduler.py"' in script
    assert (REPO_ROOT / "scripts" / "bot_runtime_scheduler.py").exists()
    assert '"run-once"' in script
    assert "Start-Sleep -Seconds $IntervalSec" in start_block
    assert 'Start-ManagedProcess -Name "bot_scheduler" -FilePath "powershell.exe"' in start_block
    assert '-ArgumentList $BotSchedulerArgs' in start_block
    assert "-Pattern \"bot_scheduler_loop.ps1\"" in start_block


def test_runtime_stack_manager_status_and_stop_do_not_write_wrappers() -> None:
    script = _script()

    assert script.index('if ($Action -eq "status")') < script.index("$BotSchedulerWrapper =")
    assert script.index('if ($Action -eq "stop")') < script.index("$BotSchedulerWrapper =")


def test_runtime_stack_manager_does_not_outer_redirect_bot_scheduler_wrapper() -> None:
    script = _script()
    start_function = script[script.index("function Start-ManagedProcess") : script.index("function Stop-ManagedProcess")]

    assert 'if ($Name -eq "bot_scheduler") {' in start_function
    bot_block = start_function[start_function.index('if ($Name -eq "bot_scheduler") {') : start_function.index("else {")]
    assert "-RedirectStandardOutput" not in bot_block
    assert "-RedirectStandardError" not in bot_block
    assert "-PassThru" in bot_block


def test_runtime_stack_manager_writes_wrapper_files_under_stack_manager() -> None:
    script = _script()

    assert '$WrapperRoot = Join-Path $StackRoot "wrappers"' in script
    assert "function Write-ManagedWrapper" in script
    assert 'Set-Content -LiteralPath $path -Value $Content -Encoding UTF8' in script


def test_runtime_stack_manager_keeps_real_orders_disabled_by_default() -> None:
    script = _script()

    assert '$WorkerSubmitFlag = if ($EnableRealOrders -and -not (Test-Path -LiteralPath $KillSwitchPath))' in script
    assert "real_order_submission=disabled" in script
    assert 'real_worker: not started because kill switch is enabled' in script
    assert 'real_worker: not started because candidate package and latest bot cycle are missing' in script
    assert '$botReady = Wait-ForCondition -Name "bot_scheduler"' in script
    assert '--enable-real-orders' in script
    assert 'if ($EnableRealOrders) {' in script


def test_runtime_stack_manager_treats_enable_real_orders_as_cold_start_mode() -> None:
    script = _script()
    status_block = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]
    start_block = script[script.index("$WorkerSubmitFlag =") :]

    assert '"real_worker: {0} pid={1} mode={2}' in status_block
    assert '$workerMode = if ($EnableRealOrders -and -not $killSwitch)' in status_block
    assert "Start-ManagedProcess" not in status_block
    assert "-SubmitRealOrders" in start_block
    assert '$WorkerSubmitFlag = if ($EnableRealOrders -and -not (Test-Path -LiteralPath $KillSwitchPath))' in start_block
    assert 'manage_real_order_worker.ps1\')\' $WorkerSubmitFlag' in start_block


def test_runtime_stack_manager_keeps_review_worker_as_explicit_sidecar() -> None:
    script = _script()
    status_block = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]
    start_block = script[script.index("$ReviewArgs = @(") :]

    assert 'Get-ManagedProcess -Name "review_worker" -Pattern "review_runtime_decisions.py"' in status_block
    assert 'if ([bool]$EnableReviewWorker) {' in status_block
    assert '$reviewState = Format-ProcessHealth $review' in status_block
    assert '$reviewState = "optional_disabled"' in status_block
    assert '"review_worker: {0} pid={1} age={2} review_status={3}' in status_block
    assert 'Stop-ManagedProcess -Name "review_worker" -Pattern "review_runtime_decisions.py"' in script
    assert 'if ($EnableReviewWorker) {' in start_block
    assert 'Start-ManagedProcess -Name "review_worker"' in start_block
    assert script.find('Start-ManagedProcess -Name "review_worker"') > script.find('Start-ManagedProcess -Name "real_worker"')


def test_runtime_stack_manager_status_covers_plan_health_signals() -> None:
    script = _script()

    required_signals = [
        "Get-ManagedProcess",
        "Set-ManagedPid",
        "Get-ListeningProcessId",
        "CommandLineMatches",
        "PidSource",
        "Format-ProcessHealth",
        "command_mismatch",
        "degraded",
        'status.status -like "incomplete_*"',
        "Remove-StalePid",
        "Get-HttpStatus",
        "/api/overview",
        "Test-FreshJsonArtifact",
        "Get-FileAgeSeconds",
        "factor_ingest_latest.json",
        "factor_summary.json",
        "research_health.json",
        "research_auto_refresh",
        "heartbeat.json",
        "handoff.json",
        "execution_handoff.json",
        "latest_cycle.json",
        "latest_candidate_execution_package.json",
        "audit.jsonl",
        "review_worker",
        "latest_decision_review.json",
        "review_runtime_decisions.py",
        "latest_run_id",
        "Get-LogErrorSummary",
        "tail_error_lines",
        "disable_real_execution.flag",
        "SortKey",
    ]
    for signal in required_signals:
        assert signal in script


def test_runtime_stack_manager_removes_reused_pid_when_command_mismatches() -> None:
    script = _script()

    remove_function = script[script.index("function Remove-StalePid") : script.index("function Start-ManagedProcess")]
    assert "[string]$Pattern" in remove_function
    assert "[string]$FilePath" in remove_function
    assert "Get-ManagedProcess -Name $Name -Pattern $Pattern" in remove_function
    assert "[System.IO.Path]::GetFileNameWithoutExtension($FilePath)" in remove_function
    assert "$state.Process.ProcessName -ne $expectedProcessName" in remove_function
    assert "$state.StalePid -or ($state.CommandLineAvailable -and $state.CommandLineMatches -eq $false)" in remove_function
    assert "Remove-StalePid -Name $Name -Pattern $Pattern -FilePath $FilePath" in script


def test_runtime_stack_manager_resolves_managed_processes_with_observed_health_before_pid_file() -> None:
    script = _script()
    managed_function = script[script.index("function Get-ManagedProcess") : script.index("function Remove-StalePid")]
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]

    assert "Get-ListeningProcessId -Port $Port -HostName $PortHost" in managed_function
    assert "Set-ManagedPid -Name $Name -ProcessId $portPid" in managed_function
    assert '-PidSource "port"' in managed_function
    assert "Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid 0" in managed_function
    assert "Set-ManagedPid -Name $Name -ProcessId $patternPid" in managed_function
    assert '-PidSource "command"' in managed_function
    assert 'Get-ManagedProcess -Name "dashboard" -Pattern "dashboard.app" -Port $DashboardPort -PortHost $HostName' in status_function
    assert '$dashboardState -eq "running" -and (-not $dashboard.PortListening -or $homeStatus -ne 200 -or $apiStatus -ne 200)' in status_function
    assert '$dashboardState = "degraded"' in status_function


def test_runtime_stack_manager_status_uses_unified_pid_resolution_for_all_services() -> None:
    script = _script()
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]

    assert 'Repair-PidFromCommandPattern -Name "bot_scheduler"' not in status_function
    assert 'Repair-PidFromCommandPattern -Name "real_worker"' not in status_function
    for service, pattern in [
        ("dashboard", "dashboard.app"),
        ("factor_ingest", "ingest-summary"),
        ("quant_judgement", "run-cycle"),
        ("research_health", "research-health"),
        ("bot_scheduler", "bot_scheduler_loop.ps1"),
        ("real_worker", "manage_real_order_worker.ps1"),
        ("review_worker", "review_runtime_decisions.py"),
    ]:
        assert f'Get-ManagedProcess -Name "{service}" -Pattern "{pattern}' in status_function


def test_runtime_stack_manager_records_scheduler_lock_pid_for_venv_launcher() -> None:
    script = _script()

    resolve_function = script[script.index("function Resolve-StartedProcessId") : script.index("function Start-ManagedProcess")]
    assert '$Name -ne "bot_scheduler" -or $startedProcessName -eq "powershell"' in resolve_function
    assert "[string]$FilePath" in resolve_function
    assert "$BotSchedulerLockPath" in resolve_function
    assert "(Get-Date).AddSeconds(10)" in resolve_function
    assert "Start-Sleep -Milliseconds 250" in resolve_function
    assert "[int]::TryParse([string]$payload.pid, [ref]$lockPid)" in resolve_function
    assert "return $lockPid" in resolve_function
    start_function = script[script.index("function Start-ManagedProcess") : script.index("function Stop-ManagedProcess")]
    assert "$managedPid = Resolve-StartedProcessId -Name $Name -StartedPid $proc.Id -FilePath $FilePath" in start_function
    assert '$Name -eq "bot_scheduler" -and $startedProcessName -ne "powershell"' in start_function
    assert "$patternPid = Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid $proc.Id" in start_function
    assert "Set-Content -LiteralPath (Get-PidPath $Name) -Value ([string]$managedPid)" in start_function


def test_runtime_stack_manager_can_find_wrapper_process_by_command_pattern() -> None:
    script = _script()

    find_function = script[script.index("function Find-ProcessIdByCommandPattern") : script.index("function Start-ManagedProcess")]
    assert "Get-CimInstance Win32_Process" in find_function
    assert '$_.CommandLine -like "*$Pattern*"' in find_function
    assert "$_.ParentProcessId -eq $StartedPid" in find_function
    assert "$_.CommandLine -like \"*$BotRoot*\"" in find_function
    assert '$_.CommandLine -like "*$QuantRoot*"' in find_function
    assert '$_.CommandLine -like "*$WorkspaceRoot*"' in find_function


def test_runtime_stack_manager_repairs_scheduler_pid_from_wrapper_process() -> None:
    script = _script()

    repair_function = script[script.index("function Repair-PidFromCommandPattern") : script.index("function Write-ManagedWrapper")]
    assert "Find-ProcessIdByCommandPattern -Pattern $Pattern -StartedPid 0" in repair_function
    assert "Set-ManagedPid -Name $Name -ProcessId $patternPid" in repair_function
    start_function = script[script.index("function Start-ManagedProcess") : script.index("function Stop-ManagedProcess")]
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]
    assert "Repair-PidFromCommandPattern -Name $Name -Pattern $Pattern" in start_function
    assert 'Repair-PidFromCommandPattern -Name "bot_scheduler" -Pattern "bot_scheduler_loop.ps1"' not in status_function


def test_runtime_stack_manager_does_not_treat_fresh_bot_artifact_as_running_when_pid_visibility_fails() -> None:
    script = _script()
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]

    assert '$botState = Format-ProcessHealth $bot' in status_function
    assert '$botState -eq "stale_pid" -and (Test-BotReady)' not in status_function


def test_runtime_stack_manager_orders_quant_cycles_by_artifact_timestamp_not_directory_mtime() -> None:
    script = _script()

    latest_function = script[script.index("function Get-LatestQuantCycle") : script.index("function Clear-StaleBotSchedulerLock")]
    assert "scheduler_status.json" in latest_function
    assert "$status.generated_at" in latest_function
    assert "$latestSchedulerCycle" in latest_function
    assert "LastWriteTimeUtc" in latest_function
    assert "$decision.generated_at" in latest_function
    assert latest_function.count("Sort-Object SortKey -Descending") == 2
    assert "Sort-Object LastWriteTime -Descending" not in latest_function


def test_runtime_stack_manager_keeps_scheduler_and_worker_locks_separate() -> None:
    script = _script()

    assert '$BotSchedulerLockPath = Join-Path $BotRuntimeRoot "scheduler.lock"' in script

    bot_scheduler_script = (REPO_ROOT / "scripts" / "ops" / "bot_runtime_scheduler.py").read_text(encoding="utf-8")
    assert 'Path(args.runtime_root) / "scheduler.lock"' in bot_scheduler_script

    from scripts import real_order_worker

    parser = real_order_worker.build_parser()
    args = parser.parse_args(["run-once"])
    assert Path(args.lock_path) == REPO_ROOT / "runtime" / "locks" / "real_order_worker.lock"


def test_runtime_stack_manager_plan_matches_implemented_entrypoint() -> None:
    plan = PLAN_PATH.read_text(encoding="utf-8")
    script = _script()

    assert "manage_runtime_stack.ps1 start" in plan
    assert "manage_runtime_stack.ps1 stop" in plan
    assert "manage_runtime_stack.ps1 status" in plan
    assert "python -m dashboard.app" in plan
    assert "quant_runtime_scheduler.py ingest-summary --loop" in plan
    assert "quant_runtime_scheduler.py run-cycle --loop" in plan
    assert "real_order_submission_allowed" in plan
    assert "dashboard.app" in script
    assert "quant_runtime_scheduler.py" in script
    assert "bot_runtime_scheduler.py" in script
    assert "real_order_worker.py" in script


def test_gitignore_excludes_runtime_cache_temp_and_local_state_artifacts() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    required_patterns = [
        "runtime/",
        ".pytest_cache/",
        ".tmp_pytest*/",
        "*.duckdb",
        "*.pid",
        "*.lock",
        "*.log",
    ]

    for pattern in required_patterns:
        assert pattern in ignore


def test_runtime_stack_manager_refreshes_research_aliases_on_quant_schedule() -> None:
    script = _script()
    quant_args = script[script.index("$QuantArgs = @(") : script.index("if ($DisableCoinglassOverlay)")]

    assert '"--refresh-research-aliases"' not in quant_args
    assert '"--refresh-research-aliases-every"' in quant_args
    assert '([string]$ResearchRefreshEvery)' in quant_args
    assert '"--whitelist-path"' in quant_args
    assert "$FreshResearchWhitelistPath" in quant_args
    assert '"--all-results-path"' in quant_args
    assert "$FreshResearchAllResultsPath" in quant_args
    assert '"--research-dispatch-request"' in quant_args
    assert "$FreshResearchDispatchRequestPath" in quant_args
    assert '"--consensus-request-timeout-sec"' in quant_args
    assert '([string]$ConsensusRequestTimeoutSec)' in quant_args
    assert '"--include-coinglass-overlay"' in quant_args

    bot_args = script[script.index("$BotArgs = @(") : script.index("if ($EnableRealOrders)")]
    assert '"--consensus-request-timeout-sec"' in bot_args
    assert '([string]$ConsensusRequestTimeoutSec)' in bot_args
    assert '"--research-dispatch-request"' in bot_args
    assert "$FreshResearchDispatchRequestPath" in bot_args
    assert '"--api-key-env"' in bot_args
    assert '"OKX_TRADE_API_KEY"' in bot_args
    assert '"--api-secret-env"' in bot_args
    assert '"OKX_TRADE_API_SECRET"' in bot_args
    assert '"--api-passphrase-env"' in bot_args
    assert '"OKX_TRADE_PASSPHRASE"' in bot_args
    assert '"--include-coinglass-overlay"' in bot_args


def test_runtime_stack_manager_runs_research_health_auto_refresh_sidecar() -> None:
    script = _script()
    research_args = script[script.index("$ResearchHealthArgs = @(") : script.index("$QuantArgs = @(")]
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]
    stop_block = script[script.index('if ($Action -eq "stop")') : script.index('Start-ManagedProcess -Name "dashboard"')]

    assert '"research-health"' in research_args
    assert '"--auto-refresh-worker"' in research_args
    assert '"--research-refresh-lock-ttl-sec"' in research_args
    assert '"1800"' in research_args
    assert '"--feature-matrix-path"' in research_args
    assert '"runtime\\feature_matrix.json"' in research_args
    assert '"--research-refresh-output-dir"' in research_args
    assert '"runtime\\fresh_research"' in research_args
    assert '"--research-refresh-reports-dir"' in research_args
    assert '"runtime\\reports"' in research_args
    assert "$FreshResearchWhitelistPath" in research_args
    assert "$FreshResearchAllResultsPath" in research_args
    assert 'Start-ManagedProcess -Name "research_health"' in script
    assert '-Pattern "research-health"' in script
    assert 'Get-ManagedProcess -Name "research_health" -Pattern "research-health"' in status_function
    assert 'Stop-ManagedProcess -Name "research_health" -Pattern "research-health"' in stop_block


def test_runtime_stack_manager_uses_wrapper_pattern_for_bot_scheduler_status_and_stop() -> None:
    script = _script()
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]
    stop_block = script[script.index('if ($Action -eq "stop")') : script.index('Start-ManagedProcess -Name "dashboard"')]

    assert 'Get-ManagedProcess -Name "bot_scheduler" -Pattern "bot_scheduler_loop.ps1"' in status_function
    assert 'Stop-ManagedProcess -Name "bot_scheduler" -Pattern "bot_scheduler_loop.ps1"' in stop_block


def test_runtime_stack_manager_uses_wrapper_pattern_for_real_worker_status_start_and_stop() -> None:
    script = _script()
    status_function = script[script.index("function Show-Status") : script.index("$DashboardArgs = @(")]
    stop_block = script[script.index('if ($Action -eq "stop")') : script.index('Start-ManagedProcess -Name "dashboard"')]
    start_block = script[script.index('else {') : script.index('if ($EnableReviewWorker)')]

    assert 'Get-ManagedProcess -Name "real_worker" -Pattern "manage_real_order_worker.ps1"' in status_function
    assert 'Get-ManagedProcess -Name "real_worker" -Pattern "real_order_worker.py"' in status_function
    assert 'Stop-ManagedProcess -Name "real_worker" -Pattern "manage_real_order_worker.ps1"' in stop_block
    assert 'Stop-ManagedProcess -Name "real_worker" -Pattern "real_order_worker.py"' in stop_block
    assert 'Start-ManagedProcess -Name "real_worker" -FilePath "powershell.exe"' in start_block
    assert '-Pattern "manage_real_order_worker.ps1"' in start_block


def test_launch_and_bot_scheduler_manager_keep_coinglass_switch_explicit() -> None:
    launch_script = _launch_stack_script()
    manager_script = _bot_scheduler_manager_script()

    assert "-DisableCoinglassOverlay" in launch_script
    assert "[switch]$DisableCoinglassOverlay" in manager_script
    assert '"--include-coinglass-overlay"' in manager_script
    assert '"--no-include-coinglass-overlay"' in manager_script


def test_launch_and_bot_scheduler_manager_pass_okx_runtime_snapshot_envs() -> None:
    launch_script = _launch_stack_script()
    manager_script = _bot_scheduler_manager_script()

    assert "-ApiKeyEnv OKX_TRADE_API_KEY" in launch_script
    assert "-ApiSecretEnv OKX_TRADE_API_SECRET" in launch_script
    assert "-ApiPassphraseEnv OKX_TRADE_PASSPHRASE" in launch_script
    assert '[string]$ApiKeyEnv = "OKX_TRADE_API_KEY"' in manager_script
    assert '[string]$ApiSecretEnv = "OKX_TRADE_API_SECRET"' in manager_script
    assert '[string]$ApiPassphraseEnv = "OKX_TRADE_PASSPHRASE"' in manager_script
    assert '"--api-key-env", $ApiKeyEnv' in manager_script
    assert '"--api-secret-env", $ApiSecretEnv' in manager_script
    assert '"--api-passphrase-env", $ApiPassphraseEnv' in manager_script


def test_final_plan_documents_research_health_boundaries() -> None:
    plan = FINAL_PLAN_PATH.read_text(encoding="utf-8")

    assert "### 6.4.1 research ready / degraded / blocked 判定" in plan
    assert "aging 不等于 blocked" in plan
    assert "ready 不等于直接下单" in plan
    assert "handoff.execution_allowed=false" in plan
