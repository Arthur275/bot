# 每日行情复盘 vs 系统行为设计方案

> 日期：2026-05-19  
> 范围：每天复盘前一日行情，并和 quant/bot 系统实际行为对齐。  
> 本文是复盘制度和脚本设计，不授权真实下单、参数放宽或 gate 绕过。

## 一句话结论

每日复盘要做，但不能从 `audit_log.jsonl` 重新扫一天硬造一套归因。现有系统已经有审计基础设施：`missed_opportunity_audit`、`live_ready_blocking_diagnostics`、`scheduler/daily_review`、`shadow_outcomes`。日报生成器应该优先消费这些产物，只补两件新东西：

1. 昨日市场 K 线摘要。
2. 面向人的事件级时间线、事件级评价和当天总评。

人话版本：系统归因让现有审计模块做，新日报负责把“行情发生了什么”和“系统当时为什么这么做”拼到一张可读报告里。

## 目标

每天生成一份草稿：

```text
docs/daily_reviews/YYYY-MM-DD_market_vs_system.md
```

报告回答四个问题：

1. 昨天市场是否给了交易机会？
2. 系统当时是否健康？
3. 系统在哪一层没有进入真实下单：quant、risk gate、bot/order gate？
4. 事后看，系统行为是正确、漏单、被风控救了，还是系统看不见？

## 非目标和硬边界

- 不把复盘结论直接变成下单许可。
- 不因为事后行情走出来了就倒推“当时应该下”。
- 不跳过现有 audit/gate 重新定义一套归因口径。
- 不在 K 线数据不可用时强写市场判断。
- 不把 `logic_gap` 自动解释成“应该实盘下单”。
- 不把 `observability_gap` 当成策略问题；它首先是证据不足。

## 已有审计基础设施

### 1. Missed Opportunity Audit

位置：

```text
D:\开发\quant_system_rebuild\runtime\analysis\missed_opportunity_audit_YYYYMMDD.json
D:\开发\quant_system_rebuild\src\analysis\missed_opportunity_audit.py
```

用途：系统差异归类的主来源。

| 日报差异归类 | 现有审计分类 | 含义 |
|---|---|---|
| 该下没下 / gate 可能过严 | `logic_gap` | 证据接近可交易，需要设计审查，但不是自动下单许可 |
| 没下是合理策略选择 | `strategy_choice` | 策略有意等待，通常是 trigger/conviction 不够 |
| 风控拦住是合理的 | `reasonable_risk_control` | 硬 gate 或执行安全 gate 拦住 |
| 系统看不懂 / 证据不完整 | `observability_gap` | artifact 不完整或阻断解释不足 |

日报不能重新发明这四类。

### 2. Live Ready Blocking Diagnostics

位置：

```text
D:\开发\quant_system_rebuild\runtime\analysis\live_ready_blocking_diagnostics.json
D:\开发\quant_system_rebuild\runtime\analysis\live_ready_blocking_diagnostics.md
```

用途：当前或目标窗口的 gate 状态。

关键层：

- `research_gate`
- `factor_governance`
- `risk_filter`
- `execution_guard`
- `candidate_package`

日报里“今天为什么不能 live ready”优先从这里读，不要只看单个 `reason_code`。

### 3. Scheduler Daily Review

位置：

```text
D:\开发\quant_system_rebuild\runtime\scheduler\daily_review.json
D:\开发\quant_system_rebuild\runtime\scheduler\daily_review.md
```

用途：聚合统计总览。

可用字段：

- action distribution
- risk filter distribution
- research gate distribution
- sample quality summary
- top reason codes
- recommended actions

注意：该文件可能不是目标日期生成的。日报脚本必须校验 `generated_at` 和目标日期，不能“有文件就信”。

### 4. Shadow Outcomes

位置：

```text
D:\开发\quant_system_rebuild\runtime\analysis\missed_opportunity_shadow_outcomes_YYYYMMDD.json
D:\开发\quant_system_rebuild\src\analysis\missed_opportunity_shadow_outcomes.py
```

用途：判断 `logic_gap` 后续表现，辅助“下了但不理想 / 影子结果如何”。

## 数据 freshness 规则

日报生成器读取任何现有产物前都要做 freshness 校验。

| 产物 | 校验方式 | 不通过时 |
|---|---|---|
| `missed_opportunity_audit_YYYYMMDD.json` | 文件名日期匹配，`inputs.start_ts/end_ts` 覆盖目标日 | 运行现有 builder/CLI 重建 |
| `live_ready_blocking_diagnostics.json` | `inputs.decision_path/handoff_path` 的 cycle 日期接近目标日，或显式生成目标日诊断 | 标记 stale，不作为目标日报结论 |
| `scheduler/daily_review.json` | `generated_at` 是目标日或覆盖目标窗口 | 标记 stale，仅放到附录 |
| `shadow_outcomes_YYYYMMDD.json` | 文件名日期匹配，`inputs.audit_path` 指向目标日 audit | 缺失则跳过 shadow outcome，不阻塞日报 |
| K 线数据 | 目标日 15m/1h 覆盖完整 | 不可用时市场摘要标 `market_data: unavailable` |

硬规则：

- 产物 stale 时不能静默使用。
- stale 产物可以展示在“输入质量”里，但不能当作当天结论。
- 日期统一使用 UTC，因为 cycle id 和 handoff 时间主要是 UTC。

## K 线数据来源

当前缺口是市场 K 线 pipeline 不稳定，所以日报脚本按降级顺序取数据。

### 优先级 1：Binance API

目标：

- ETHUSDT 或 BTCUSDT
- 15m klines
- 1h klines
- 时间窗口：目标日 00:00:00Z 到次日 00:00:00Z
- 走 proxy，短超时，失败快速返回

输出：

- open / high / low / close
- day return
- intraday range
- max 15m candle range
- high/low 时间
- 主要趋势段或震荡段

### 优先级 2：本地样本

如果 API 不通：

- 查找 quant/bot runtime 或 samples 中目标日期附近的本地 K 线/行情样本。
- 如果只有旧样本，只能标注 `sample_stale`，不能假装是目标日行情。

### 优先级 3：不可用

如果 API 和本地样本都不可用：

```text
market_data_status = unavailable
```

日报仍可生成，但必须明确：

- 市场摘要为空。
- 不能判断“系统是否错过行情”。
- 只能复盘系统健康和 gate 行为。

## 报告结构

### 0. 输入质量

放在最前面。

原因：如果系统一整天 `research_stale` 或 transport 坏了，读者应该先知道“系统当天看不见”，而不是先看一大段 wait 时间线。

字段：

| 项 | 状态 | Source | Trace |
|---|---|---|---|
| market_data | ok / stale / unavailable | binance_api / local_sample / unavailable | candle_count、覆盖时间、source_path 或 API endpoint |
| missed_opportunity_audit | ok / stale / regenerated / missing | existing_artifact / regenerated | source_path、inputs.start_ts/end_ts |
| live_ready_diagnostics | ok / stale / missing | existing_artifact / regenerated | source_path、inputs.decision_path cycle timestamp |
| scheduler_daily_review | ok / stale / missing | existing_artifact | source_path、generated_at |
| shadow_outcomes | ok / missing / skipped | existing_artifact | source_path、inputs.audit_path |

### 1. 系统健康度诊断

优先展示：

- dominant blocking reason
- research gate
- factor governance
- risk filter
- execution guard
- candidate package
- transport/proxy/market consensus 状态

建议输出：

```text
system_health: degraded
dominant_blocking_reason: research_gate:research_stale
primary_fault_domain: data_infrastructure
```

fault domain 枚举：

| fault_domain | 含义 |
|---|---|
| `healthy` | 数据和 gate 基本可用 |
| `data_infrastructure` | market data、proxy、transport、consensus、factor lookup 等坏 |
| `research_unavailable` | research stale/missing/not_ready |
| `strategy_wait` | 系统健康，但策略条件不满足 |
| `risk_control_block` | 系统健康，但风控硬拦 |
| `bot_execution_block` | quant/risk 过了，但 bot/order/preflight/worker 拦 |
| `unknown` | 证据不足 |

### 2. 市场摘要

只有 K 线数据可用时才写市场结论。

字段：

- symbol
- date UTC
- 15m candle count
- 1h candle count
- open / high / low / close
- day return %
- intraday range %
- max 15m candle range %
- high time / low time
- main regime: trend_up / trend_down / range / volatile_reversal / unknown
- notable windows

如果不可用：

```text
market_data_status: unavailable
market_summary: skipped
impact: cannot judge missed/correct market opportunity from price action
```

### 3. 决策时间线

时间线必须是事件级，不是每 5 分钟一行。

只列这些事件：

- `action` 变化
- `trigger_ready` 翻转
- `risk_filter_status` 变化
- `research_gate_status` 变化
- `execution_allowed` 变化
- `real_order_gate.allowed` 变化
- candidate package 写入/缺失状态变化
- order/preflight 状态变化

Market 列使用固定格式：

```text
<regime>@<HH:MM>[, range_pct=<x.xx>][, move=<up|down|flat>]
```

示例：

- `range@00:00, range_pct=0.80`
- `breakdown@03:15, move=down`
- `trend_down@06:30`
- `unavailable@all_day`

允许的 regime：

```text
range, trend_up, trend_down, breakdown, breakout, reversal, volatile, unavailable, unknown
```

中间重复周期压缩成延续区间：

| 时间 UTC | 市场状态 | 系统状态 | trigger_ready | risk_filter | gate | 事件级评价 |
|---|---|---|---|---|---|---|
| 00:00-03:15 | range@00:00, range_pct=0.80 | action=wait 延续 39 cycles | false | degraded | no_candidate | correct_wait |
| 03:15 | breakdown@03:15, move=down | action wait -> observe_only | false | veto | research_stale | system_blind |
| 06:30-09:45 | trend_down@06:30 | 延续 observe_only 40 cycles | false | veto | research_stale | system_blind |

压缩规则：

- 同一组 `action + trigger_ready + risk_filter_status + dominant_gate_reason` 连续出现，合并。
- 合并行记录 `cycle_count`。
- `observability_gap` 大量重复时只写区间，不逐条展开。
- 对 `logic_gap`、`trigger_ready=true`、`execution_allowed=true`、candidate 写入这些高价值事件单独展开。

事件级评价和当天总评的关系：

- 时间线里的 `事件级评价` 是每个事件或区间的微观评价。
- 第 6 节 `当天总评` 是整天的总标签，通常取占比最大或风险最高的事件标签。
- 如果当天出现一个高风险 `missed` 或 `wrong_entry`，即使大多数区间是 `correct_wait`，当天总评也应优先反映该高风险事件。

### 4. 三层闸门穿透

日报不能只说“没下单”，要分三层。

| 层 | 问题 | 主要字段 |
|---|---|---|
| quant 层 | 有没有产 candidate / execution intent | `action`, `trigger_ready`, `confidence`, `thesis_score`, `execution_allowed` |
| risk gate 层 | 产了但是否被风控/研究/数据质量拦 | `risk_filter_status`, `degrade_flags`, `reason_codes`, `research_gate_status` |
| bot/order 层 | 过了风控后是否能真实提交 | `real_order_gate.allowed`, `automation_boundary`, `preflight`, `state_store.execution_state`, `last_reason_codes` |

输出格式：

```text
quant_layer: no_candidate
risk_layer: blocked_by_research_stale
bot_layer: not_reached
final_no_order_reason: quant/risk blocked before bot order submission
```

### 5. 差异归类

直接使用 `missed_opportunity_audit.category_counts`。

示例：

| 分类 | 数量 | 解读 |
|---|---:|---|
| logic_gap | 11 | 接近可交易，需要设计审查 |
| strategy_choice | 112 | 策略有意等待 |
| reasonable_risk_control | 29 | 风控合理拦截 |
| observability_gap | 911 | 证据不足，先修观测 |

如果 `shadow_outcomes` 可用，在同节追加一行摘要：

| 来源 | 结果 | 样本 |
|---|---|---|
| shadow_outcomes | 60% favorable | 11 logic_gap samples evaluated |

高优先级：

- `logic_gap` 必须列前 10 条样本。
- `observability_gap` 如果占比过高，当天复盘结论应偏向“证据不足”，而不是策略判断。
- `reasonable_risk_control` 要抽样看是否真是硬风险，不要默认全对。
- `shadow_outcomes` 只用于辅助判断 logic gap 后续表现，不能替代原始 gate 审计。

### 6. 当天总评

人工填写，但必须用固定枚举。

| 评价 | 含义 |
|---|---|
| `hit` | 市场走了系统预判方向，且系统正确入场 |
| `missed` | 市场给了符合策略形态的机会，系统没抓住 |
| `correct_wait` | 系统没动，事后看不动是对的 |
| `wrong_wait` | 系统没动，事后看应该动 |
| `saved` | 系统差点下，风控拦了，事后看拦得对 |
| `wrong_entry` | 系统下了，但入场/止损/止盈有问题 |
| `system_blind` | data/transport/research 坏到无法判断 |

字段定义：

- `label`：当天总评标签，来自上述枚举。
- `rationale`：为什么用这个总标签，必须引用时间线事件或 audit 证据。
- `evidence`：source path、cycle id、K 线窗口、gate reason 等证据。
- `review_confidence`：填表人对当天总评的信心，不是系统 `confidence` 字段。

`review_confidence` 枚举：

| 值 | 含义 |
|---|---|
| `high` | K 线、audit、gate、state 证据完整，结论稳定 |
| `medium` | 主要证据可用，但有少量 stale/missing |
| `low` | 关键市场数据或系统 artifact 缺失，只能给临时判断 |

规则：

- K 线不可用时，不能填 `missed` 或 `wrong_wait`，除非另有可靠市场证据。
- 系统数据基础设施坏时，优先考虑 `system_blind`。
- `logic_gap` 只能提示“审查”，不能自动填 `missed`。
- `correct_wait` 必须写明策略预设标准，不允许只写“后来没涨/没跌”。
- 当天总评不等于时间线里每行的简单多数投票。高风险漏单、错误入场、系统盲区可以覆盖多数正常等待区间。

### 7. 偏差检查

每天最后必须写。

问题：

1. 今天“应该下”的判断，哪些来自策略预设标准，哪些来自事后 K 线？
2. 如果所有没下都被判成合理，是否有确认偏误？
3. 有没有因为数据坏而把策略误判成保守？
4. 有没有因为行情后来走出来，而倒推当时应该开仓？
5. 有没有把 `observability_gap` 当成策略结论？

输出：

```text
hindsight_bias_risk: low / medium / high
confirmation_bias_risk: low / medium / high
notes:
- ...
```

### 8. 下一步

动作必须分类：

| 类型 | 示例 |
|---|---|
| data_fix | 修 proxy、market consensus、factor lookup |
| research_fix | 重新跑 research bundle、修 stale |
| strategy_review | 审查 logic_gap，评估是否补策略形态 |
| gate_review | 检查 risk gate 是否过严 |
| observability_fix | 补 artifact 字段、dashboard 显示 |
| no_action | 系统合理等待，无需改动 |

## 生成器架构

建议脚本：

```text
docs/daily_reviews/generate_daily.py
```

输入：

```text
python docs/daily_reviews/generate_daily.py --date 2026-05-18 --symbol ETHUSDT --timeframe 15m
```

可选参数：

| 参数 | 含义 |
|---|---|
| `--force-regenerate` | 强制重建目标日 audit / shadow outcomes / derived summary，即使已有文件 |
| `--market-source binance_api|local|none|auto` | 指定 K 线来源，默认 `auto` |
| `--proxy-url <url>` | Binance API 请求代理 |
| `--output-dir <path>` | 覆盖默认输出目录 |
| `--dry-run` | 只打印输入 freshness 和计划，不写文件 |

输出：

```text
docs/daily_reviews/2026-05-18_market_vs_system.md
docs/daily_reviews/artifacts/2026-05-18_summary.json
```

### 数据读取顺序

1. 解析目标日期 UTC 窗口：

```text
start = YYYY-MM-DDT00:00:00Z
end = next_dayT00:00:00Z
```

2. 加载或生成 missed opportunity audit：

```text
if --force-regenerate:
    call existing analysis builder/CLI with start_ts/end_ts
elif missed_opportunity_audit_YYYYMMDD.json exists and fresh:
    use it
else:
    call existing analysis builder/CLI with start_ts/end_ts
```

3. 加载 live ready diagnostics：

```text
if diagnostics references target-date cycle:
    use it
else:
    mark stale or regenerate if CLI supports target window
```

4. 加载 scheduler daily review：

```text
if generated_at/date covers target:
    use it
else:
    mark stale
```

5. 加载 shadow outcomes：

```text
if --force-regenerate:
    rebuild from target-day missed opportunity audit
elif shadow_outcomes_YYYYMMDD.json exists:
    use it
else:
    skip
```

6. 获取 K 线：

```text
try Binance API with proxy
fallback local samples
else unavailable
```

7. 构建事件级时间线：

```text
use audit items + scheduler summaries + selected cycle artifacts
compress unchanged runs
highlight transition events
```

### 关键实现原则

- 不重读整天 raw `audit_log.jsonl` 作为第一选择。
- 只在现有审计产物缺失且无法调用 builder 时，才考虑 raw fallback。
- 所有输入都带 `source_path`、`source_generated_at`、`freshness_status`。
- 报告必须能在 K 线不可用时生成系统健康版。
- 生成器只生成草稿，人工评价字段默认 `needs_review`。

## Markdown 模板

```markdown
# Daily Market vs System Review - YYYY-MM-DD

> Symbol: ETHUSDT  
> Timezone: UTC  
> Reviewer: ...  
> Generated at: ...

## 0. 输入质量

| Input | Status | Source | Trace |
|---|---|---|---|
| market_data | ok | binance_api | count=96 15m candles, window=2026-05-18T00:00:00Z..2026-05-19T00:00:00Z |
| missed_opportunity_audit | ok | existing_artifact | source_path=..., start_ts=20260518T000000Z, end_ts=20260519T000000Z |
| live_ready_diagnostics | stale | existing_artifact | source_path=..., decision_cycle_ts=20260505T160027Z |
| scheduler_daily_review | stale | existing_artifact | source_path=..., generated_at=2026-05-04T06:32:30Z |
| shadow_outcomes | skipped | missing | expected_path=... |

## 1. 系统健康度诊断

- system_health:
- dominant_blocking_reason:
- primary_fault_domain:
- research_gate:
- factor_governance:
- risk_filter:
- execution_guard:
- candidate_package:

## 2. 市场摘要

- market_data_status:
- open/high/low/close:
- day_return_pct:
- intraday_range_pct:
- max_15m_range_pct:
- main_regime:
- notable_windows:

## 3. 决策时间线

| Time UTC | Market | System | Trigger | Risk | Gate | Cycle Count | 事件级评价 |
|---|---|---|---|---|---|---:|---|
| ... | range@00:00, range_pct=0.80 | ... | ... | ... | ... | ... | needs_review |

## 4. 三层闸门穿透

| Layer | Status | Evidence | Reason |
|---|---|---|---|
| quant | ... | ... | ... |
| risk_gate | ... | ... | ... |
| bot_order | ... | ... | ... |

## 5. 差异归类

| Category | Count | Meaning |
|---|---:|---|
| logic_gap | ... | ... |
| strategy_choice | ... | ... |
| reasonable_risk_control | ... | ... |
| observability_gap | ... | ... |

## 6. 当天总评

- label:
- rationale:
- evidence:
- review_confidence:

Allowed labels:
`hit`, `missed`, `correct_wait`, `wrong_wait`, `saved`, `wrong_entry`, `system_blind`.

## 7. 偏差检查

- hindsight_bias_risk:
- confirmation_bias_risk:
- which conclusions came from pre-defined strategy rules:
- which conclusions came from hindsight price action:
- notes:

## 8. 下一步

| Type | Action | Owner | Priority |
|---|---|---|---|
| ... | ... | ... | ... |
```

## 第一版落地顺序

### Phase 1：只写模板，手工跑

目标：

- 每天人工填一份。
- 先用现有 audit JSON/MD 复制关键字段，默认来源是 `D:\开发\quant_system_rebuild\runtime\analysis\`。
- K 线先手工补。
- 暂定每天北京时间上午完成前一日 UTC 日报；若跨交易日窗口需要调整，必须在报告头写清楚。
- 填写人必须在报告头记录 `reviewer`；填完后提交到 `docs/daily_reviews/YYYY-MM-DD_market_vs_system.md`。

验收：

- 连续 3 天由人工填出可追溯的复盘，并能稳定回答“没下单是哪一层没过”。
- 能区分 `system_blind` 和 `strategy_choice`。

### Phase 2：生成器草稿

目标：

- `generate_daily.py` 读取现有产物生成草稿。
- K 线 API 可用则自动填市场摘要。
- 不可用则明确标注。

验收：

- 不扫整天 raw log 也能填差异归类。
- stale 输入会被标出。
- 事件时间线能压缩重复 wait/degraded 区间。

### Phase 3：自动每日生成

目标：

- 每天固定时间生成昨日草稿。
- 人工只填事后评价、偏差检查和下一步。

验收：

- 报告不会因为 API 不通失败。
- 报告不会因为旧 audit artifact 误用而给出错误结论。
- dashboard 或 docs 中能追溯过去 7-30 天复盘。

## 成功标准

这套日报成功的标准不是“每天写很多字”，而是：

- 看到行情大动时，能快速回答系统为什么没动。
- 能把数据坏、策略等待、风控拦截、bot 提交阻断分开。
- 能发现长期重复的 `logic_gap`。
- 能发现长期过高的 `observability_gap`。
- 能防止复盘变成事后诸葛亮。
- 能为 BTC 20x 双向系统提供真实训练样本和风控反馈。
