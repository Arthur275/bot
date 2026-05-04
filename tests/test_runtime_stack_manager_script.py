from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "manage_runtime_stack.ps1"
CMD_PATH = REPO_ROOT / "scripts" / "manage_runtime_stack.cmd"
PLAN_PATH = REPO_ROOT / "docs" / "runtime_stack_manager_plan.md"


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_runtime_stack_manager_entrypoints_exist() -> None:
    script = _script()
    cmd = CMD_PATH.read_text(encoding="utf-8")

    assert SCRIPT_PATH.exists()
    assert CMD_PATH.exists()
    assert '[ValidateSet("start", "stop", "status")]' in script
    assert "[switch]$EnableRealOrders" in script
    assert "manage_runtime_stack.ps1" in cmd


def test_runtime_stack_manager_starts_components_in_dependency_order() -> None:
    script = _script()
    expected_order = [
        'Start-ManagedProcess -Name "dashboard"',
        'Start-ManagedProcess -Name "factor_ingest"',
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


def test_runtime_stack_manager_keeps_real_orders_disabled_by_default() -> None:
    script = _script()

    assert '$WorkerSubmitFlag = if ($EnableRealOrders -and -not (Test-Path -LiteralPath $KillSwitchPath))' in script
    assert "real_order_submission=disabled" in script
    assert 'real_worker: not started because kill switch is enabled' in script
    assert 'real_worker: not started because candidate package and latest bot cycle are missing' in script
    assert '$botReady = Wait-ForCondition -Name "bot_scheduler"' in script
    assert '--enable-real-orders' in script
    assert 'if ($EnableRealOrders) {' in script


def test_runtime_stack_manager_status_covers_plan_health_signals() -> None:
    script = _script()

    required_signals = [
        "Get-ManagedProcess",
        "CommandLineMatches",
        "Format-ProcessHealth",
        "command_mismatch",
        "Remove-StalePid",
        "Get-HttpStatus",
        "/api/overview",
        "Test-FreshJsonArtifact",
        "Get-FileAgeSeconds",
        "factor_ingest_latest.json",
        "factor_summary.json",
        "heartbeat.json",
        "handoff.json",
        "execution_handoff.json",
        "latest_cycle.json",
        "latest_candidate_execution_package.json",
        "audit.jsonl",
        "latest_run_id",
        "Get-LogErrorSummary",
        "tail_error_lines",
        "disable_real_execution.flag",
        "SortKey",
    ]
    for signal in required_signals:
        assert signal in script


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
