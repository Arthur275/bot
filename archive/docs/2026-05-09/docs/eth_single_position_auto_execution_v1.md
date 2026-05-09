# ETH Single Position Auto Execution v1

本文档是 ETHUSDT 10x 单仓自动交易系统的最终落地设计。它整合当前代码事实、DS 多轮审查结论和执行安全要求，用于指导后续 quant 与 bot 的实现。

## 目标边界

- 只服务 ETHUSDT 合约。
- 只跑 10x。
- 只允许单仓。
- Binance live position 是最终事实源。
- Quant 是交易发起人。
- Bot 只执行，不解释因子，不发明策略。
- Scheduler 不直接下单。

整体链路：

```text
Factor Data Service
  -> Quant Decision Service
  -> Bot Execution Service
  -> Binance
```

## 三块服务

### Factor Data Service

职责：

- 采集行情、盘口、衍生品、研究健康、执行样本。
- 持续写入样本库。
- 离线计算因子胜率、stop hit 率、MFE/MAE 等统计。
- 生成 quant live 决策只读的 factor lookup table。

不做：

- 不下单。
- 不决定 action。
- 不直接影响 bot 执行。

### Quant Decision Service

职责：

- 消费因子和 lookup。
- 通过现有链路输出 ExecutionHandoff。
- 决定 action、direction、execution_allowed、sizing、stop、reduce/exit/stop update 条件。

保留现有结构，不重写为新三层框架：

```text
RiskFilter
  -> DecisionEngine
  -> FactorPolicyBridge / SizingPolicy
  -> ExecutionHandoff
```

逻辑映射：

```text
RiskFilter:
  risk_filter_status / reason_codes / runtime_vetoes / degrade_flags

DecisionEngine:
  action / direction / confidence / thesis_score / adverse_score / trigger_ready

FactorPolicyBridge + SizingPolicy + ExecutionHandoff:
  sizing_tier / sizing_bias / position_size_pct / execution_allowed / initial_stop_loss
  tp_ladder / reduce_conditions / invalidate_conditions / trailing_rule
```

### Bot Execution Service

职责：

- 消费 quant handoff 和 candidate execution package。
- 查 Binance live position / open orders / openAlgoOrders。
- 执行 ETH 单仓开仓、减仓、平仓、保护止损调整。
- 写 audit、state、runtime summary。

不做：

- 不解释 sizing_tier。
- 不把 tp_ladder 自动变成真实止盈单。
- 不根据 reduce_conditions 自己决定减仓。
- 不在 scheduler 内直接 submit。

## Quant Factor Evidence v1

当前 ExecutionHandoff 已有大部分交易字段。真正需要新增的是结构化因子证据。

待新增字段：

```text
supporting_factor_codes: list[str]
opposing_factor_codes: list[str]
veto_factor_codes: list[str]
factor_grade_summary: dict[str, object]
regime_bucket: str
factor_lookup_version: str
factor_lookup_generated_at: str
factor_lookup_stale: bool
```

这些字段需要先加入 quant 的 `contracts/execution.py::ExecutionHandoff`，再在 `interfaces/runner.py::build_execution_handoff` 中填充。

Bot 只展示这些字段，不解释、不重打分。

## Regime Bucket

不能和现有 `RegimeState` 分裂。`regime_bucket` 必须从现有 regime 派生。

建议值域：

```text
trend_long
trend_short
ranging
high_volatility
low_liquidity
unknown
```

派生规则必须使用现有字段：

```text
regime_state.direction
regime_state.regime_type
regime_state.risk_level
regime_alignment
```

## Factor Grade

固定使用字符串 enum，不混用数字和中文描述：

```text
core
enhancer
reference
```

含义：

```text
core:
  可影响 action / execution_allowed / veto。

enhancer:
  可影响 confidence / sizing_bias / sizing_tier。

reference:
  只进入 reasoning_summary / factor notes。
```

映射：

```text
core 强支持 -> 可 entry
core 不足 -> wait 或 small_probe
veto 出现 -> execution_allowed=false
enhancer 冲突 -> sizing_tier 下调
enhancer 支持 -> sizing_bias constructive
reference -> 不直接改变 action
```

## Factor Lookup

胜率反哺必须离线 batch 计算，live 决策只读 lookup。

需要新增或扩展的数据结构：

```text
factor_values:
  sample_id
  run_id
  generated_at
  symbol
  timeframe
  regime_bucket
  factor_name
  factor_value
  factor_grade
  direction
  action

factor_lookup:
  lookup_version
  generated_at
  sample_count
  regime_bucket
  factor_name
  factor_value_bucket
  direction
  win_rate
  stop_hit_rate
  avg_mfe
  avg_mae
```

`factor_value_bucket` 必须按因子类型定义，不能在查询时临时猜。

默认规则：

```text
categorical factor:
  factor_value_bucket = 原始类别值
  例：trend_direction_4h=long -> long

boolean factor:
  factor_value_bucket = true / false

continuous factor:
  优先使用 factor_bucket_config 显式分桶
  如果没有显式配置，使用 rolling quantile quintile bucket:
    p0_p20
    p20_p40
    p40_p60
    p60_p80
    p80_p100
```

连续因子建议先配置显式分桶：

```text
funding_rate:
  negative
  neutral
  elevated
  extreme

top_trader_long_short_ratio:
  low
  neutral
  crowded
  extreme_crowded

open_interest_change:
  falling
  flat
  rising
  spike

orderbook_imbalance:
  sell_pressure
  neutral
  buy_pressure
```

连续因子的 rolling bucket 边界必须写入：

```text
factor_bucket_config.json
```

`factor_bucket_config.json` 必须和 `factor_lookup_version` 绑定。live lookup 只能使用同一版本号下的 bucket 边界，禁止用新边界解释旧 lookup。

lookup 统计口径必须是：

```text
regime_bucket × direction × factor_name × factor_value_bucket
```

禁止用全局 win_rate 代替 per-direction win_rate。

刷新规则：

```text
默认每 24 小时刷新一次。
如果新增样本数 >= 100，也允许提前刷新。
lookup 超过 72 小时未刷新，factor_lookup_stale=true。
stale lookup 不能提升 sizing，只能降级或保持。
```

强支持建议阈值：

```text
sample_count >= 30
win_rate >= 0.60
stop_hit_rate <= 0.35
```

弱支持：

```text
sample_count >= 20
win_rate >= 0.54
```

负面：

```text
sample_count >= 20
win_rate <= 0.46
或 stop_hit_rate >= 0.55
```

阈值后续可以由 research calibration 管理，但 v1 必须有固定默认值。

## Signal Conflict Rules

不能只靠自然语言 summary。

冲突优先级：

```text
veto > opposing > supporting
```

规则：

```text
risk_filter_status=blocked -> execution_allowed=false
risk_filter_status=veto -> execution_allowed=false
trigger_ready=false -> 不允许 full entry
core 支持 + enhancer 反对 -> small_probe 或 conservative
core 支持 + veto -> wait
factor_lookup_stale=true -> 不允许因子 lookup 提升 sizing
```

## DEGRADED 统一规则

Quant 和 bot 必须统一。

最终规则：

```text
DEGRADED 禁止所有新 entry，包括 small_probe。
已有仓位时允许降低风险动作。
```

允许：

```text
exit
reduce
fixed stop tighten
protective stop repair
```

禁止：

```text
entry_long
entry_short
small_probe
启动新的 trailing 接管
扩大仓位
放宽止损
```

当前 quant 存在 degraded small_probe 逻辑，需移除或改为 wait。

## Candidate Execution Package

Scheduler 每轮产出候选执行包，worker 独立消费。

路径建议：

```text
runtime/bot_runtime_scheduler/latest_candidate_execution_package.json
runtime/bot_runtime_scheduler/candidates/candidate_<timestamp>.json
```

Package schema：

```text
package_id
generated_at
expires_at
runtime_mode
engine_mode
symbol
exchange_symbol
action
direction
handoff
execution_plan
execution_commands
preflight
real_order_gate
audit_log_path
state_path
source_cycle_path
```

`expires_at` 固定为：

```text
generated_at + 180 seconds
```

worker 读取 package 时：

```text
now > expires_at -> skip
reason=execution_package_expired
```

180 秒与 manual entry preview 的短时效边界保持一致，避免旧 preflight 被重放。

生成条件：

```text
real_order_gate.enabled=true
real_order_gate.allowed=true
preflight 全部 ready
automation_boundary=real_order_submission_allowed
```

Scheduler 只写 package，不 submit。

## Real Order Worker v1

文件：

```text
scripts/real_order_worker.py
```

触发：

```text
Hermes cron / Windows Task Scheduler 定时调用。
```

worker 必须持有独立锁：

```text
runtime/locks/real_order_worker.lock
```

锁必须支持 stale 检测。

总流程：

```text
1. 读取 candidate_execution_package。
2. 检查 kill switch。
3. 获取 real_order_worker.lock。
4. 重新检查 audit/idempotency。
5. 查询 Binance position + open orders + openAlgoOrders。
6. 校验 action 仍然合法。
7. submit 前最后一次查询 Binance position + openAlgoOrders。
8. 最终确认 action 仍然合法。
9. 写 pending audit。
10. pending 写成功后 submit。
11. refresh Binance。
12. 写 accepted / filled / failed audit。
13. 更新 state_store。
14. release lock。
```

如果 pending audit 写失败：

```text
不 submit
release lock
alert
```

## Pending-First Idempotency

任何真实 submit 前必须先写 pending audit。

Pending 字段：

```text
status=pending
idempotency_key
target
command_type
client_order_id
exchange_order_id
algo_id
endpoint_family
action
snapshot_before
package_id
```

恢复路由：

```text
entry_order / reduce_order / exit_order:
  GET /fapi/v1/order?origClientOrderId=

protective stop / algo:
  GET /fapi/v1/openAlgoOrders
  或 GET /fapi/v1/algoOrder?algoId
```

恢复规则：

```text
Binance 有单:
  同步状态，不重放。

Binance 无单:
  entry/reduce/exit pending -> 重试 1 次。
  protective stop pending -> 重试 2 次。
  仍失败 -> RECONCILING。
```

## ETH 单仓执行规则

### FLAT

只允许：

```text
entry_long
entry_short
small_probe
```

但必须满足：

```text
kill_switch 不存在
worker lock 获取成功
runtime_mode=real
engine_mode=strict-live
execution_allowed=true
risk_filter_status=pass
Binance position=FLAT
无 ghost protective stop
entry preflight_ready
protective stop preflight_ready
initial_stop_loss 存在
idempotency_key 未执行过
client_order_id 已生成
```

### POSITION_OPEN

永远禁止：

```text
entry_long
entry_short
small_probe
```

只允许：

```text
reduce
exit
protective_stop_replace
fixed_stop_tighten
breakeven
```

## 自动开仓

执行：

```text
1. 查 position + openAlgoOrders。
2. 如果已有仓位，block entry。
3. 如果 FLAT 但有 ghost stop，先 cancel ghost stop。
4. ghost stop cancel 失败:
   state=RECONCILING
   reason=ghost_stop_cancel_failed
   alert
   不开仓。
5. 再查一次 position。
6. submit 前最后一次查 position + openAlgoOrders。
7. 仍 FLAT 且无 ghost stop 才写 pending audit。
8. submit entry market order。
9. refresh position。
10. 用实际成交后的 position qty 挂 protective stop。
11. protective stop 失败重试 2 次。
12. 成功:
    state=POSITION_OPEN
    automation_state=POSITION_PROTECTED
13. 失败:
    state=RECONCILING
    reason=protective_stop_missing_after_entry
    禁止继续开新仓。
```

Partial fill：

```text
stop quantity 必须来自 refresh 后的实际 position qty。
禁止使用请求 qty。
```

small_probe：

```text
本质仍是 entry。
必须有 protective stop。
stop quantity = probe 实际 fill qty。
bot 不自行放大仓位。
```

## 自动平仓

条件：

```text
action=exit
Binance position != FLAT
HighRiskGate pass
exit preflight_ready
kill_switch 不存在
idempotency_key 未执行过或 pending 可恢复
```

执行：

```text
1. 写 pending audit。
2. submit reduceOnly / closePosition exit order。
3. refresh position。
4. 如果 FLAT:
   cancel 残留 protective stop / ghost algo orders
   state=IDLE
   automation_state=OBSERVING
5. 如果仍有仓位:
   state=RECONCILING
   reason=partial_exit_or_position_still_open
```

## 自动减仓

条件：

```text
action=reduce
Binance position != FLAT
HighRiskGate pass
reduce preflight_ready
reduce qty <= live position qty
kill_switch 不存在
idempotency_key 未执行过或 pending 可恢复
```

执行：

```text
1. 写 pending audit。
2. submit reduceOnly reduce order。
3. refresh position。
4. 如果 FLAT:
   走 exit cleanup。
5. 如果仍有仓位:
   尝试 place new stop。
   confirm new stop active。
   cancel old stop。
```

Binance 没有 algo stop atomic cancel-replace。不要把 cancel-replace 作为默认路线。

如果交易所不允许两个 stop 并存：

```text
cancel old -> place new
失败 -> RECONCILING
reason=protective_stop_replace_failed_after_reduce
```

## 止损 / 保本 / Stop Tighten

允许：

```text
protective_stop_replace
fixed_stop_tighten
breakeven
```

DEGRADED 下只允许：

```text
fixed stop tighten
protective stop repair
```

DEGRADED 下 trailing 规则：

```text
已有 active trailing:
  继续保留和监控，不主动取消。
  这是已有保护，不算扩大风险。

尚未 active 的 trailing:
  禁止启动新的 trailing 接管。
  只允许 fixed stop tighten / protective stop repair。
```

禁止：

```text
没有仓位时调整 stop
新 stop 放大风险
bot 自己用 tp_ladder 造止盈单
bot 自己用 reduce_conditions 决定减仓
DEGRADED 下启动 trailing 接管
```

执行：

```text
1. 查 position。
2. 查 openAlgoOrders，确认旧 stop active。
3. 如果旧 stop 已触发或不存在:
   refresh position。
   如果 FLAT -> cleanup。
   如果仍有仓位 -> protective stop repair。
4. place new stop。
5. confirm new stop active。
6. cancel old stop。
```

protective stop repair 流程：

```text
1. 查询 Binance position。
2. 查询 openAlgoOrders。
3. 如果 position=FLAT:
   cleanup ghost stop / state。
   不补新 stop。
4. 如果 position!=FLAT:
   必须读取 position.entry_price、position.direction、position_amt。
5. 如果 entry_price 缺失或 position_amt <= 0:
   state=RECONCILING
   reason=protective_stop_repair_missing_position_basis
   alert
   不盲补 stop。
6. 如果 quant handoff 仍有有效 initial_stop_loss:
   优先使用 handoff stop。
7. 如果 handoff stop 不可用:
   用 entry_price + 既定 max_account_risk_pct_per_trade / stop_distance_pct 修复。
8. 新 stop 只能降低风险，不能放宽风险。
9. place stop 后立即确认 openAlgoOrders active。
10. repair 成功 -> POSITION_PROTECTED。
11. repair 失败 2 次 -> 保持 RECONCILING + alert。
```

## API 失败和降级

需要在 state_store 中新增：

```text
consecutive_api_failure_count
last_api_failure_at
```

该字段只属于 bot 的 Binance 执行侧 API 失败，例如：

```text
fetch position timeout
fetch open orders timeout
submit response unknown
openAlgoOrders query failed
```

Quant 的 live bundle / factor source 失败不写 bot state_store。Quant 自己使用 quant runtime scheduler / research health / risk_filter_status 管理降级。

规则：

```text
position refresh 单次失败:
  重试 2 次。

仍失败:
  本轮 skip。
  不随便改 state。

连续 3 次失败:
  state=DEGRADED
  禁止 entry。
  只允许降低风险动作。
```

## RECONCILING 退出

RECONCILING 中禁止 entry。

退出规则：

```text
position=FLAT 且无 open protective stop:
  state=IDLE
  automation_state=OBSERVING

position!=FLAT 且 protective stop active:
  state=POSITION_OPEN
  automation_state=POSITION_PROTECTED

position!=FLAT 且 protective stop missing:
  执行 protective stop repair。
  repair 成功 -> POSITION_PROTECTED。
  repair 失败 -> 保持 RECONCILING + alert。
```

连续 N 次 reconcile 失败：

```text
alert
保持禁止 entry
```

建议 N=3。

## 当前已实现与缺口

已实现：

```text
AutomationState
CommandExecutionResult 顶层字段
audit before/after snapshot
bot DuckDB index
bot scheduler lock / stale lock
kill switch gate
real_order_gate
candidate_execution_package.json
real_order_worker.py
pending-first audit
state_store consecutive_api_failure_count
ExecutionHandoff factor evidence fields
factor_values / factor_lookup
factor lookup CLI
factor_bucket_config.json 自动生成与版本绑定
真实 submit 基础闭环
ghost stop entry cleanup
entry 后 protective stop retry
exit 后 ghost algo cleanup
protective stop repair worker 路径
reduce 后 stop refresh + old algo cleanup
RECONCILING repair candidate package
place stop 后二次 openAlgoOrders active 硬确认
manage_real_order_worker.ps1 / .cmd 外部调度入口
```

未实现：

```text
无代码级缺口。生产启用前仍需人工配置外部调度器和交易所 API 权限。
```

Factor lookup CLI 入口：

```text
python -m interfaces.analysis build-factor-lookup
```

或接入 quant scheduler：

```text
scripts/quant_runtime_scheduler.py build-factor-lookup
```

职责：

```text
1. 读取 factor_samples / factor_values。
2. 按 regime_bucket × direction × factor_name × factor_value_bucket 聚合。
3. 生成 factor_lookup 表。
4. 写 lookup_version / generated_at。
5. 输出 factor_lookup_summary.json / factor_lookup_summary.md。
```

## 最终规则

```text
量化是交易发起人。
因子服务给量化提供证据和胜率。
bot 只执行 ETHUSDT 单仓动作。
没仓位才允许开仓；有仓位永远不再开仓。
任何真实动作都必须 pending audit、worker lock、Binance refresh、幂等恢复、保护止损闭环。
保护缺失、订单不明、仓位不一致，全部进入 RECONCILING，禁止新开仓。
```
