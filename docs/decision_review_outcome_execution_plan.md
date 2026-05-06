# 决策审查报告与事后学习执行文档

## 1. 目标

本文档定义在现有 ETH 自动化链路上增加“决策审查报告”和“事后学习”的执行方案。

核心目标不是让系统更容易下单，也不是引入 LLM 来裁决交易，而是补齐两类能力：

```text
1. 解释增强：前端能清楚展示当前量化为什么允许、等待、否决、降级。
2. 事后学习：开仓和平仓完成后，把真实结果回填，用于更新因子治理质量。
```

必须保持现有主链路不变：

```text
因子 / research / 市场数据
  -> factor governance
  -> quant decision
  -> execution handoff
  -> bot candidate package
  -> real order worker
  -> exchange
```

新增审查链路只能旁路运行：

```text
execution handoff / factor summary / risk report
  -> async review worker
  -> latest_decision_review.json
  -> dashboard 审查报告区
```

## 2. 不可突破的边界

审查报告层永远不能做以下事情：

```text
不能设置 execution_allowed=true
不能修改 sizing
不能生成 candidate package
不能触发 real order worker
不能覆盖 risk_filter
不能直接修改 factor_lifecycle
不能让 dashboard 变成实盘控制台
```

审查报告只做三件事：

```text
1. 把当前决策讲清楚。
2. 把潜在风险列出来。
3. 把需要人工或治理层复查的事项写成建议。
```

前端必须长期显示边界提示：

```text
审查报告仅供解释和复盘，不参与自动下单。
审查清晰不代表允许下单，最终以下单链路和风控结果为准。
```

## 3. 前端目标形态

dashboard 保持只读观察面板，新增第四块：

```text
1. 样本采集与因子治理
2. 量化市场判断
3. Bot 下单链路
4. 决策审查报告
```

第四块不是控制台，不放按钮，不提供人工放行，不提供实盘开关。

第一版即使没有 review worker，也应该先展示：

```text
审查状态：unavailable
原因：审查服务未启用或暂无报告
来源 run_id：取当前 handoff / decision
handoff 新鲜度：可用则展示
固定提示：审查不参与自动下单
```

## 4. 审查状态命名

不要使用 `pass`，也不要使用 `blocked_for_review`。

原因：

```text
pass 容易被误解为风控已放行。
blocked_for_review 容易和 execution_allowed=false / risk_filter blocked / scheduler blocked 混淆。
```

统一使用：

```text
clear
watch
needs_attention
unavailable
```

含义：

| 状态 | 含义 | 是否影响下单 |
| --- | --- | --- |
| clear | 审查未发现额外解释性风险 | 否 |
| watch | 有观察项，但不构成审查异常 | 否 |
| needs_attention | 有需要人或治理层复查的问题 | 否 |
| unavailable | 审查不可用、超时、缺输入或未启用 | 否 |

## 5. review_mode 枚举

`review_mode` 必须定义清楚，不能只写自由文本。

建议枚举：

```text
async_light
async_full
daily_integrity_review
daily_outcome_review
outcome_reflection
manual_audit
```

含义：

| 模式 | 触发方式 | 目标 | 时间预算 |
| --- | --- | --- | --- |
| async_light | 独立 worker 轮询最新 handoff | 快速中文解释和风险提示 | 3-5 秒 |
| async_full | 人工或低频任务触发 | 多角度完整审查 | 30-90 秒 |
| daily_integrity_review | 每日批处理 | 检查链路一致性、缺失、异常 | 分钟级 |
| daily_outcome_review | 每日批处理 | 汇总已 resolved 的交易结果 | 分钟级 |
| outcome_reflection | outcome resolved 后触发或批处理 | 生成事后复盘 | 秒到分钟级 |
| manual_audit | 人工指定 run_id | 深度排查某一轮 | 不进实时链路 |

## 6. review report schema

落盘文件：

```text
runtime/reviews/latest_decision_review.json
```

建议 schema：

```json
{
  "schema": "decision_review_v1",
  "version": 1,
  "generated_at": "2026-05-05T00:00:00Z",
  "source_run_id": "eth-15m-20260505T000000Z-abcd1234",
  "handoff_id": "handoff-...",
  "source_handoff_age_sec": 42,
  "source_stale_threshold_sec": 180,
  "source_stale": false,
  "review_mode": "async_light",
  "review_status": "clear",
  "latency_ms": 1200,
  "timeout": false,
  "fallback_used": false,
  "structured_fields_accepted": true,
  "bull_case": [],
  "bear_case": [],
  "risk_findings": [],
  "execution_findings": [],
  "governance_review_suggestions": [],
  "unresolved_questions": [],
  "summary": "审查结果仅供观察，不参与自动下单。"
}
```

必须展示和存储的溯源字段：

```text
source_run_id
handoff_id
source_handoff_age_sec
source_stale
review_mode
review_status
latency_ms
timeout
fallback_used
```

## 7. freshness 规则

审查报告必须检查来源 handoff 的新鲜度。

```text
source_handoff_age_sec = now - handoff.generated_at
source_stale_threshold_sec = 180
source_stale = source_handoff_age_sec > source_stale_threshold_sec
```

如果 `source_stale=true`：

```text
review_status 至少为 watch
summary 必须说明审查基于过期 handoff
dashboard 必须显示来源已过期
```

如果找不到 handoff：

```text
review_status = unavailable
source_stale = true
fallback_used = false
```

## 8. data_source_quality 规则

构建 review report 时，不能只检查 handoff。审查层引用的关键数据源也必须显式标记质量，避免“缺数据但显示 clear”。

建议在 report 中加入：

```json
{
  "data_source_quality": {
    "handoff_available": true,
    "factor_lookup_available": true,
    "factor_summary_available": true,
    "risk_report_available": true,
    "candidate_package_available": false,
    "worker_audit_available": true,
    "outcome_samples_available": false
  }
}
```

降级规则：

```text
handoff 缺失：review_status = unavailable。
handoff 存在但 factor_lookup 缺失：review_status 至少为 watch，summary 必须说明因子 lookup 不可用。
handoff 存在但 factor_summary 缺失：review_status 至少为 watch，summary 必须说明样本采集摘要不可用。
risk_report 缺失：review_status 至少为 watch，不能输出 clear。
worker_audit 缺失：execution_findings 必须提示执行链路审计不可用。
outcome_samples 缺失：不影响实时解释，但 daily_outcome_review / outcome_reflection 必须 unavailable。
```

原则：

```text
缺关键数据源不能静默忽略。
缺数据时可以解释“当前无法判断”，不能假装审查清晰。
```

## 9. evidence 规则

审查报告不能用量化输出证明量化输出。

禁止把这些作为核心证据：

```text
confidence=0.71
execution_allowed=true
sizing_tier=small
risk_filter_status=veto
```

允许引用原始或近原始证据：

```text
funding_rate
open_interest_delta
trend_direction_4h
volatility_regime
research_health.freshness
factor_lookup rows / sample_count
net_expectancy_pct
stop_hit_rate
avg_mfe / avg_mae
worker audit status
candidate package missing reason
```

审查报告可以解释 `execution_allowed=false`，但不能把 `execution_allowed=false` 当成“空头理由”本身。

## 10. governance suggestion 边界

`governance_review_suggestions` 只能触发建议，不能直接改因子 lifecycle。

建议落盘：

```text
runtime/reviews/governance_suggestions.json
```

建议格式：

```json
{
  "factor_name": "crowding_warning",
  "source_run_id": "eth-15m-...",
  "suggested_action": "manual_governance_review",
  "reason": "连续多轮触发 veto，但 lookup 仍显示 active，需要复查近期失效风险。",
  "actionable": false
}
```

字段黑名单：

```text
governance_suggestions 的 JSON 中不得出现以下字段名：

allow_entry
set_sizing
bypass_veto
override_risk
force_execution
execution_allowed
submit_order
candidate_package
```

这不是为了防已有代码逻辑，而是为了防止后续实现者把建议文件误接成执行信号。

FactorGovernanceEvaluator 后续可以增加可选输入：

```python
evaluate(..., review_suggestions=None)
```

规则：

```text
不传 review_suggestions 时完全忽略。
传入时只能增加“需关注/需复查”的解释字段。
不能因为 review suggestion 直接把 active 改成 deprecated。
不能因为 review suggestion 直接把 watch 改成 active。
真正 lifecycle 仍由样本数、净期望、胜率、stop_hit、MFE/MAE、新鲜度、漂移等硬指标决定。
```

## 11. decision_outcomes 设计

`decision_outcomes` 不是每轮 decision 立即生成最终结果。

交易可能持有数小时甚至数天，因此必须按仓位生命周期回填：

```text
decision_run_id：产生开仓/加仓意图的 run_id
handoff_id：对应 handoff
candidate_package_id：bot 生成的候选执行包
order_id / client_order_id：真实或 dry-run 执行标识
entry_at：实际成交开仓时间
entry_price：实际或模拟成交价
exit_at：实际成交平仓时间
exit_price：实际或模拟平仓价
resolved_at：outcome 最终可计算时间，通常等于仓位关闭时间
holding_bars：entry 到 exit 跨了多少根 15m bar
raw_return_pct：不扣成本收益
estimated_cost_pct：手续费、滑点、spread 估算成本
net_return_pct：扣成本后收益
mfe_pct：持仓期间最大浮盈
mae_pct：持仓期间最大浮亏
stop_hit：是否触发止损
status：open / resolved / abandoned / timeout / partial
```

状态含义：

| status | 含义 |
| --- | --- |
| open | 已入场但尚未平仓，不能用于胜率统计 |
| resolved | 仓位已关闭，收益和风险字段完整 |
| abandoned | 决策产生但没有实际进入仓位 |
| timeout | 超过最大观察窗口仍无法确认结果 |
| partial | 部分成交或部分退出，结果需要特殊处理 |

`resolved_at` 必须是 close time，不是 decision time。

`holding_bars` 必须按交易周期计算，当前 ETH 15m 系统使用 15m bar。

## 12. outcome 回填时机

outcome 的回填触发顺序：

```text
1. bot candidate package 产生后，记录 pending linkage。
2. worker audit 确认 submitted / skipped / blocked。
3. 如果真实或 dry-run 成交，写入 entry_at / entry_price，status=open。
4. 当 exit / stop / reduce-to-zero / position flat 被确认后，写入 exit_at / exit_price / resolved_at。
5. 计算 raw_return_pct / estimated_cost_pct / net_return_pct / mfe_pct / mae_pct / stop_hit。
6. status=resolved 后，才允许进入 factor_lookup 和 governance 的统计样本。
```

不能把 still-open 仓位用于 win_rate 或 net expectancy。

如果同一轮 decision 对应多次 partial fill：

```text
使用同一个 decision_run_id
按 order_id / fill_id 记录明细
最终 outcome 聚合到 position close 后再 resolved
```

## 13. factor_lookup 反哺规则

`decision_outcomes` 和 `factor_lookup` 的关系：

```text
decision_outcomes 是事后真实结果表。
factor_lookup 是按 factor / regime / direction 聚合后的可用性查询表。
```

更新方式建议：

```text
resolved outcome 写入后，不立即逐行改 lookup。
由 build_factor_lookup 定时或按批次重建聚合。
```

原因：

```text
lookup 是聚合结果，批量重建更容易保证一致性。
可以避免部分 outcome 刚写入时导致 lookup 中间态。
```

聚合字段至少包括：

```text
sample_count
win_rate
stop_hit_rate
avg_mfe
avg_mae
gross_expectancy_pct
estimated_cost_pct
net_expectancy_pct
last_observed_at
```

治理硬规则：

```text
net_expectancy_pct <= 0 的因子不能作为 support 提升仓位。
sample_count 不足的因子不能从 watch 升为 active。
stop_hit_rate 过高的因子不能作为 support。
lookup 过期的因子不能作为 active support。
```

## 14. TradingAgents 可借鉴点

可借鉴：

| 能力 | 我们的用法 | 边界 |
| --- | --- | --- |
| Bull/Bear Debate | 生成多空解释 | 只展示，不裁决 |
| Structured Output | Pydantic/JSON schema 严格输出 | 不允许自由文本进入执行链 |
| Memory + Reflection | outcome resolved 后做复盘 | 只用于学习和展示 |
| ConditionalLogic | 把 DEGRADED / RECONCILING 等状态规则表化 | 不引入 LangGraph 主链 |
| Checkpointer | 参考更细粒度 crash resume | 不替换当前 worker 安全边界 |

不借鉴：

```text
LLM Portfolio Manager 直接决定 rating
free-text fallback 进入自动下单链路
yfinance 股票数据模型
多 agent 辩论同步阻塞 15m 主链
```

## 15. 多空辩论的延迟边界

多空辩论不能放进实时主链路。

错误接法：

```text
quant run-cycle
  -> bull/bear debate
  -> execution_allowed
  -> bot
```

正确接法：

```text
quant run-cycle
  -> execution handoff
  -> bot / worker

execution handoff
  -> async review worker
  -> dashboard review report
```

理由：

```text
多 agent 辩论可能增加 30-90 秒延迟。
外部 LLM provider 可能超时、限流或失败。
15m 交易链路不能等待解释层。
解释层失败时，主链路必须继续按原风控运行。
```

## 16. review worker 组件

新增独立组件，而不是让 quant scheduler fork 线程。

建议入口：

```text
scripts/review_runtime_decisions.py
```

职责：

```text
轮询最新 handoff / decision / factor summary / risk report。
检查 source freshness。
生成 latest_decision_review.json。
必要时写 governance_suggestions.json。
失败时写 unavailable 状态，而不是让主链路报错。
```

启动方式后续可接入：

```text
scripts/manage_runtime_stack.cmd start
```

但默认第一阶段可以不启动 review worker，dashboard 先展示 unavailable。

## 17. dashboard API 与缓存

`/api/overview` 应继续作为 dashboard 唯一数据入口。

新增字段建议：

```json
{
  "decision_review": {
    "available": false,
    "status": "unavailable",
    "source_run_id": null,
    "handoff_id": null,
    "source_handoff_age_sec": null,
    "source_stale": true,
    "review_mode": null,
    "latency_ms": null,
    "timeout": false,
    "fallback_used": false,
    "summary": "审查报告未启用。",
    "bull_case": [],
    "bear_case": [],
    "risk_findings": [],
    "execution_findings": [],
    "governance_review_suggestions": [],
    "unresolved_questions": []
  }
}
```

缓存规则：

```text
dashboard 5 秒轮询可以保留。
server 端保留 1 秒左右 overview cache，避免频繁读大量 runtime 文件。
review report 通常 15m 或低频更新，不需要单独高频刷新。
```

前端渲染规则：

```text
所有长 reason / veto / transition 字段必须换行，不允许穿出卡片。
所有字段中文化。
缺数据时显示“暂无”，不要显示 undefined / null。
不得使用 innerHTML 拼接 runtime 字符串。
兼容旧浏览器，不使用 replaceChildren。
```

## 18. 实施顺序

DS 修正后的顺序如下。

### P1：前端审查报告展示区

目标：

```text
先把 dashboard 第四块结构固定下来。
没有 review worker 时显示 unavailable。
展示 source_run_id / handoff_id / freshness / 状态说明。
明确展示“审查不参与自动下单”。
manage_runtime_stack.ps1 / .cmd 的 status 输出增加 review_worker: stopped 占位行。
```

验收：

```text
dashboard 可打开。
移动端和桌面端不溢出。
中文字段完整。
DOM contract test 覆盖新增 id。
HTTP API test 覆盖 decision_review 默认结构。
status 输出能看到 review_worker，即使第一阶段未启动。
```

### P2：独立 async review worker

目标：

```text
旁路读取最新 handoff。
生成 latest_decision_review.json。
失败、超时、缺 key、provider 不可用时写 unavailable。
不影响 quant / bot / worker。
```

验收：

```text
review worker 停掉时主链路正常。
review worker 超时时主链路正常。
dashboard 显示 timeout / unavailable。
review report 包含 version、source freshness、handoff_id。
```

### P3：decision_outcomes 与回填

目标：

```text
建立 pending -> open -> resolved 的 outcome 生命周期。
以 close time 作为 resolved_at。
计算 holding_bars、raw_return、net_return、MFE、MAE、stop_hit。
```

验收：

```text
未平仓 outcome 不参与 win_rate。
resolved outcome 可追溯到 decision_run_id / handoff_id / candidate_package_id。
partial fill 有明确处理。
```

### P4：outcome 反哺 factor_lookup / governance

目标：

```text
build_factor_lookup 消费 resolved outcomes。
更新 net_expectancy_pct、stop_hit_rate、avg_mfe、avg_mae。
FactorGovernanceEvaluator 使用硬指标判断 lifecycle / effect。
review suggestions 只作为需关注信号。
```

验收：

```text
net_expectancy_pct <= 0 不能 support。
watch 不放大仓位。
deprecated 不参与加权。
veto 仍是 effect，不是 grade。
```

### P5：低频 daily review / reflection

目标：

```text
每日汇总 decisions、handoffs、worker audit、resolved outcomes。
生成 daily reflection。
为人工复盘和治理调参提供材料。
```

验收：

```text
按日期分目录或限制最大条数。
dashboard 不扫描无限历史。
daily review 不影响实时链路。
```

## 19. 安全审计点

实施前后必须审计：

```text
review report 没有被 quant decision 读取为放行条件。
review report 没有被 bot scheduler 读取为 candidate package 条件。
review report 没有被 worker 读取为 submit 条件。
governance_suggestions 没有被 quant / bot / worker 读取为执行信号。
governance_suggestions JSON 不包含 allow_entry / set_sizing / bypass_veto / override_risk / force_execution 等危险字段名。
dashboard 没有新增实盘控制按钮。
EnableRealOrders 仍然是冷切换或明确的启动参数。
kill switch 仍然优先于 worker 启动和 submit。
DEGRADED 仍然禁止 entry / small_probe。
RECONCILING 有明确退出规则，不被 review 层覆盖。
```

## 20. 测试清单

前端和 API：

```text
test_dashboard_http_serves_static_and_overview_api
test_dashboard_static_dom_contract
test_dashboard_review_defaults_when_report_missing
test_dashboard_review_long_text_does_not_overflow_contract
```

review worker：

```text
report missing handoff -> unavailable
stale handoff -> watch or unavailable
provider timeout -> unavailable with timeout=true
invalid structured output -> unavailable, not fallback into execution
valid report -> writes version=1 and source ids
missing factor_lookup -> watch
missing risk_report -> watch, never clear
governance_suggestions dangerous field names -> rejected
```

outcome：

```text
entry without exit -> status=open
exit confirmed -> status=resolved
resolved_at equals close time
holding_bars calculated from entry_at to exit_at
open outcome excluded from lookup aggregation
resolved outcome included in lookup aggregation
partial fill handled deterministically
```

governance：

```text
review_suggestions absent -> evaluator output unchanged
review_suggestions present -> adds attention note only
net_expectancy_pct <= 0 -> no support effect
sample_count insufficient -> no active lifecycle
deprecated factor -> no sizing amplification
```

## 21. 最终判断

加入审查报告后，对自动下单链路的直接定量提升是：

```text
0
```

因为它不能改变 `execution_allowed`、不能改变 sizing、不能触发 worker。

真实提升是：

```text
1. dashboard 解释性明显提升。
2. 事后 outcome reflection 补齐当前“只有事前样本、缺少事后结果”的学习闭环。
```

最大风险是：

```text
人看到 review_status=clear 后误以为系统已经允许下单。
```

因此必须坚持：

```text
审查清晰不等于交易放行。
交易放行只看 quant handoff、risk_filter、candidate package、worker safety gate。
```
