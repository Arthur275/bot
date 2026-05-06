from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from dashboard.decision_review import (
    build_daily_review,
    build_decision_review,
    write_daily_review,
    write_decision_review,
    write_governance_suggestions,
)


def main() -> int:
    args = _parse_args()
    bot_root = Path(args.bot_root).resolve()
    quant_root = Path(args.quant_root).resolve()
    output_path = Path(args.output_path) if args.output_path else bot_root / "runtime" / "reviews" / "latest_decision_review.json"
    if args.daily_review:
        daily_path = Path(args.daily_output_path) if args.daily_output_path else bot_root / "runtime" / "reviews" / "daily" / f"{datetime.now(timezone.utc).date().isoformat()}.json"
        payload = build_daily_review(bot_root=bot_root, quant_root=quant_root, now=datetime.now(timezone.utc))
        write_daily_review(daily_path, payload)
        return 0
    if args.loop:
        while True:
            _run_once(bot_root=bot_root, quant_root=quant_root, output_path=output_path, stale_threshold_sec=args.stale_threshold_sec)
            time.sleep(max(1, int(args.interval_sec)))
    _run_once(bot_root=bot_root, quant_root=quant_root, output_path=output_path, stale_threshold_sec=args.stale_threshold_sec)
    return 0


def _run_once(*, bot_root: Path, quant_root: Path, output_path: Path, stale_threshold_sec: int) -> None:
    started = time.perf_counter()
    try:
        payload = build_decision_review(
            bot_root=bot_root,
            quant_root=quant_root,
            now=datetime.now(timezone.utc),
            stale_threshold_sec=stale_threshold_sec,
            review_mode="async_light",
        )
        payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
        write_decision_review(output_path, payload)
        write_governance_suggestions(bot_root / "runtime" / "reviews" / "governance_suggestions.json", payload.get("governance_review_suggestions", []))
    except Exception as exc:  # pragma: no cover - defensive runtime boundary
        write_decision_review(
            output_path,
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "review_mode": "async_light",
                "review_status": "unavailable",
                "timeout": False,
                "fallback_used": False,
                "structured_fields_accepted": False,
                "summary": f"审查报告不可用：{type(exc).__name__}",
            },
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate sidecar decision review reports for the runtime dashboard.")
    parser.add_argument("--bot-root", default=Path(__file__).resolve().parents[1].as_posix())
    parser.add_argument("--quant-root", default=(Path(__file__).resolve().parents[2] / "quant_system_rebuild").as_posix())
    parser.add_argument("--output-path", default="")
    parser.add_argument("--stale-threshold-sec", type=int, default=180)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=300)
    parser.add_argument("--daily-review", action="store_true")
    parser.add_argument("--daily-output-path", default="")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
