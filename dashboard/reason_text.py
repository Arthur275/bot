from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REASON_CODE_TEXT: dict[str, str] = {
    "judgement_not_ok": "量化 judgement 未返回可执行结果",
    "pipeline_blocked": "量化流水线阻塞，机器人侧同步阻塞",
    "research_not_ready": "research 未就绪或已失效",
    "research_stale": "research 数据已过期",
    "research_unavailable": "research 数据不可用",
    "research_aging": "research 数据接近过期",
    "bundle_missing": "research bundle 缺失",
    "wf_quality_insufficient": "walk-forward 质量不足",
    "wf_trade_share_low": "有效交易样本占比偏低",
    "wf_dispersion_high": "胜率离散度偏高",
    "wf_return_drift_high": "收益漂移偏高",
    "research_issue_present": "research 存在待处理问题",
    "diagnostic:transport": "传输层异常",
    "diagnostic:data_source": "数据源异常",
    "diagnostic:pipeline": "量化流水线异常",
    "market_data_consensus_unreliable": "市场数据共识不可靠，禁止开仓",
    "market_data_source_unreliable": "市场数据源不足或不可用，不等同于行情确认转坏",
    "data_health_veto": "实时市场数据健康度过低，禁止开仓",
    "risk_filter:veto": "风控 veto，禁止开仓",
    "risk_filter:blocked": "风控阻塞，禁止开仓",
    "risk_filter:degraded": "风控降级，需继续等待或降低仓位，不等同于硬阻断",
    "runtime_entry_veto": "运行时 veto，禁止开仓",
    "macro_news_veto": "宏观新闻 veto，禁止开仓",
    "staleness_veto": "数据新鲜度/可用性 veto",
    "conflict_veto": "信号冲突 veto",
    "healthy": "系统健康",
    "gate_ok": "门控通过",
    "risk_reduce": "触发风险减仓",
}


def load_reason_code_text_map(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mapping = payload.get("reason_code_text") if isinstance(payload, dict) else None
    if not isinstance(mapping, dict):
        return {}
    return {str(key): str(value) for key, value in mapping.items() if str(key).strip()}


def reason_text(code: Any, mapping: dict[str, str] | None = None) -> str:
    raw = str(code or "").strip()
    if not raw:
        return "无"
    effective_mapping = mapping or REASON_CODE_TEXT
    if raw in effective_mapping:
        return effective_mapping[raw]
    if raw.startswith("bundle_status:"):
        return f"research 状态={raw.split(':', 1)[1]}"
    if raw.startswith("degrade_flag:"):
        return f"降级标记={raw.split(':', 1)[1]}"
    return f"未映射原因：{raw}"


def enrich_reason_codes(codes: Any, *, limit: int = 12, mapping: dict[str, str] | None = None) -> list[dict[str, str]]:
    if not isinstance(codes, list):
        return []
    enriched: list[dict[str, str]] = []
    for code in codes[:limit]:
        raw = str(code or "").strip()
        if raw:
            enriched.append({"code": raw, "text": reason_text(raw, mapping=mapping)})
    return enriched
