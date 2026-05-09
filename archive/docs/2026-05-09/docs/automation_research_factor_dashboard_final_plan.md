# ETH 自动化实盘准备与因子治理最终方案

## 1. 目标

本文档是 DS 审查意见和 GPT 工程方案的融合版本，用于指导下一阶段代码实现、测试、审查和提交固化。

核心目标不是放松风控让系统更容易下单，而是让系统具备可解释、可测试、可审计的实盘触发条件：

```text
实时数据 / research / 因子
  -> 量化市场判断
  -> execution handoff
  -> bot candidate package
  -> real order worker
  -> 交易所
```

职责边界必须保持清晰：

```text
因子指向量化。
量化指向 bot。
bot 只执行合格 handoff。
worker 只执行通过安全门控的 candidate package。
dashboard 只做中文实时观察，不做人工控制台。
```

## 2. 当前已有能力

当前系统已经具备以下基础链路：

```text
1. 一键 runtime stack manager
2. dashboard HTTP/API 服务
3. factor ingest loop
4. quant run-cycle loop
5. quant run-cycle 产出 decision / execution handoff
6. bot scheduler 消费 handoff
7. bot scheduler 产出 candidate package 或 blocked audit
8. real order worker 支持 dry-run 轮询
9. kill switch 文件级安全边界
10. factor_values / factor_lookup 表和 build_factor_lookup 能力
11. dashboard 三块观察面板
12. DOM contract / HTTP API / 数据源测试
```

一键入口：

```text
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd
```

常用命令：

```powershell
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd start
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd status
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd stop
```

前端地址：

```text
http://127.0.0.1:8765
```

API 地址：

```text
http://127.0.0.1:8765/api/overview
```

## 3. 三条业务链路

第一块：样本采集 / 因子治理

```text
quant ingest-summary
  -> factor_values
  -> factor_lookup
  -> factor lookup summary
  -> Factor 面板
```

第二块：bot 下单链路

```text
bot scheduler
  -> candidate package / blocked audit
  -> real order worker
  -> Bot 面板
```

第三块：量化市场判断

```text
quant run-cycle
  -> decision.json
  -> execution_handoff.json / handoff.json
  -> Quant 面板
```

## 4. 自动下单的正确理解

自动下单不是“因子足够就下单”。

正确关系是：

```text
因子 -> 增强或约束量化判断
research -> 约束量化证据质量
量化 -> 决定是否允许执行
bot -> 把允许执行的 handoff 变成 candidate package
worker -> 经过安全门控后提交订单
```

自动开仓至少需要满足：

```text
1. handoff 存在且未过期
2. action 是 entry_long / entry_short 或策略允许的 entry 类动作
3. direction 明确
4. execution_allowed=true
5. risk_filter_status=pass
6. runtime_vetoes=[]
7. sizing 合法
8. confidence 达到策略门槛
9. candidate package 生成成功
10. worker 读取到未消费 package
11. kill switch off
12. worker mode 允许真实提交
13. exchange / margin / account risk gate 通过
```

## 5. 因子闭环的最终设计

因子不能直接进入 bot，也不能绕过量化直接触发开仓。

最终链路：

```text
实时因子 / handoff evidence
  -> DuckDB factor_values
  -> DuckDB factor_lookup
  -> FactorGovernanceEvaluator
  -> FactorPolicyBridge
  -> DecisionEngine / SizingPolicy
  -> ExecutionHandoff
  -> bot
```

关键边界：

```text
DuckDB 负责存储和统计。
factor_lookup 负责历史查表。
FactorGovernanceEvaluator 负责判断因子质量和生命周期。
FactorPolicyBridge 负责把治理后的因子结论翻译给量化。
DecisionEngine / SizingPolicy 负责最终交易判断。
bot 只负责执行 handoff。
```

## 6. factor_grade 与 lifecycle 分离

DS 纠正意见采纳：不能用 approved / watch / deprecated / veto 替换已有 factor_grade。

已有 factor_grade 必须保留：

```text
core
enhancer
reference
```

factor_grade 表达“因子重要性”：

```text
core：核心方向 / 触发因子
enhancer：增强型因子
reference：参考型因子
```

新增治理维度应使用 lifecycle：

```text
active
watch
deprecated
```

factor effect 单独表达当前作用：

```text
support
oppose
veto
neutral
```

最终应形成三维结构：

```text
factor_grade = core / enhancer / reference
factor_lifecycle = active / watch / deprecated
factor_effect = support / oppose / veto / neutral
```

规则：

```text
只有 lifecycle=active 的 core/enhancer 因子，才允许增强 confidence 或 sizing。
watch 因子只能展示或轻微辅助，不能提高 execution_allowed。
deprecated 因子不能进入量化加权。
veto 是当前组合的风险效果，不是 factor_grade。
未经治理的因子不能提高 execution_allowed。
未经治理的因子不能提高 sizing。
```

## 7. 当前缺口：治理 evaluator 尚不存在

当前已有：

```text
D:\开发\quant_system_rebuild\src\contracts\factor_governance.py
```

该文件定义的是 schema / spec，不是实际 evaluator。

缺失模块：

```text
D:\开发\quant_system_rebuild\src\policy\factor_governance.py
```

该 evaluator 应负责：

```text
1. 读取 factor_lookup
2. 按 symbol / timeframe / regime_bucket / direction / factor_name / bucket 评估
3. 计算样本质量
4. 计算收益质量
5. 判断 lifecycle
6. 判断 factor_effect
7. 输出可被 FactorPolicyBridge 消费的治理结果
```

## 8. 好因子的判定标准

不能只看 win_rate。

治理层至少需要以下门槛：

```text
min_sample_count
min_win_rate
max_stop_hit_rate
min_gross_expectancy_pct
min_net_expectancy_pct
max_avg_mae
lookup_not_stale
same_regime_only
same_direction_only
```

当前 factor_lookup 已有或接近已有的字段：

```text
sample_count
win_rate
stop_hit_rate
avg_mfe
avg_mae
factor_grade
regime_bucket
direction
factor_value_bucket
lookup_version
```

当前还不能直接声称已具备：

```text
expected_return_after_cost
estimated_fee_pct
estimated_slippage_pct
spread_cost_pct
net_expectancy_pct
```

因此下一步应先补：

```text
gross_expectancy_pct
estimated_cost_pct
net_expectancy_pct
```

再用：

```text
net_expectancy_pct > 0
```

作为 active/support 的必要条件之一。

## 9. 成本计算边界

“扣除手续费和滑点后仍为正”必须落成字段和测试，不能只写成口号。

成本来源建议：

```text
fee_pct：来自交易所费率配置，区分 maker / taker
slippage_pct：来自历史成交或保守默认值
spread_cost_pct：来自 orderbook spread
funding_cost_pct：如持仓周期覆盖 funding，则纳入估算
```

治理层使用：

```text
estimated_cost_pct = fee_pct + slippage_pct + spread_cost_pct + funding_cost_pct
net_expectancy_pct = gross_expectancy_pct - estimated_cost_pct
```

若成本字段缺失：

```text
不能把因子评为 active/support。
最多评为 watch/neutral。
```

## 10. FactorGovernanceEvaluator 输出契约

建议 evaluator 输出结构：

```json
{
  "lookup_version": "factor-lookup-20260505T120000",
  "generated_at": "2026-05-05T12:00:00+08:00",
  "symbol": "ETH",
  "timeframe": "15m",
  "regime_bucket": "trend_long",
  "direction": "long",
  "rows": [
    {
      "factor_name": "trigger_state.entry_timing_score",
      "factor_value_bucket": "0.50-0.75",
      "factor_grade": "core",
      "factor_lifecycle": "active",
      "factor_effect": "support",
      "sample_count": 120,
      "win_rate": 0.57,
      "stop_hit_rate": 0.21,
      "gross_expectancy_pct": 0.0032,
      "estimated_cost_pct": 0.0011,
      "net_expectancy_pct": 0.0021,
      "reason_codes": ["sample_count_ok", "net_expectancy_positive"]
    }
  ]
}
```

## 11. FactorPolicyBridge 输入输出

FactorPolicyBridge 不应直接读取原始 DuckDB 行并自行判断好坏。

它应该读取治理后的结果，输出量化可用信号：

```text
supporting_factor_codes
opposing_factor_codes
veto_factor_codes
confidence_multiplier
thesis_multiplier
adverse_floor
sizing_bias
factor_lookup_version
factor_lookup_stale
factor_governance_status
reason_codes
```

规则：

```text
active + support -> 可以增强 confidence / thesis / sizing
active + oppose -> 降低 confidence / sizing
active + veto -> 进入 veto_factor_codes
watch -> 只能展示或轻微影响，不能放大仓位
deprecated -> 不参与量化加权
lookup stale -> 不允许放大仓位
sample insufficient -> 不允许放大仓位
cost missing -> 不允许放大仓位
```

## 12. research_not_ready 与 runtime_vetoes

DS 结论采纳：research_not_ready 的来源已明确，不再作为未知根因反复排查。

关键位置：

```text
D:\开发\quant_system_rebuild\src\policy\risk_filter.py
```

规则：

```text
research 类 veto 每轮重新计算。
旧一轮 research_not_ready 不能残留到下一轮。
下一轮 research ready 时，runtime_vetoes 不允许继续携带旧 research_not_ready。
非 research 类外部 veto 可以保留，但必须有明确来源。
```

需要测试：

```text
上一轮 runtime_vetoes=["research_not_ready"]
下一轮 research ready
下一轮 runtime_vetoes=[]
下一轮 handoff.execution_allowed 可以在其他条件通过时变 true
```

### 6.4.1 research ready / degraded / blocked 判定

research health 只说明量化证据是否可参与决策，不直接放行交易。

```text
ready:
- research_health_status=pass
- decision_ready=true
- freshness=fresh 或 aging
- runtime_vetoes 中没有 research_not_ready / research_stale / research_unavailable
- 仍必须继续通过 risk_filter、stop、sizing、runtime gate

degraded:
- decision_ready=true
- research_health_status=degraded 或 freshness=aging
- 可以参与量化判断，但 dashboard 必须展示降级
- 是否允许执行仍由 risk_filter_status、runtime_vetoes 和 execution_allowed 决定

blocked:
- decision_ready=false，或 freshness=stale / unavailable
- research_health_status=veto / blocked / unavailable
- runtime_vetoes 应包含 research_not_ready / research_stale / research_unavailable
- handoff.execution_allowed=false
- bot 不应产生可执行 candidate package
```

关键边界：

```text
aging 不等于 blocked。
degraded 不等于实盘允许。
ready 不等于直接下单。
blocked 必须能在 dashboard 显示中文原因。
```

## 13. DEGRADED 策略边界

当前新策略口径：

```text
risk_filter_status=degraded 时，不允许执行 entry。
small_probe 也不例外。
```

允许保留：

```text
DecisionEngine 可以生成 small_probe 形态。
handoff 可以展示 probe_source / probe_context。
```

但 handoff 必须：

```text
execution_allowed=false
execution_block_reason=risk_filter_not_pass
```

该口径已用于修正旧测试：

```text
test_execution_handoff_blocks_crowding_degraded_trend_continuation_probe
test_execution_handoff_blocks_research_degraded_trend_continuation_probe
test_execution_handoff_blocks_crowding_short_probe_under_crowding_degrade
test_decision_engine_blocks_degraded_contrarian_short_probe_with_time_stop_context
```

## 14. Dashboard 原则

前端保持轻量原生 HTML/CSS/JS，不引入 React / Vue。

不做：

```text
人工下单按钮
EnableRealOrders 热切换
复杂控制台
策略覆盖按钮
```

要做：

```text
1. 三块面板字段完整
2. 所有业务字段中文化
3. 原因 code 中文解释
4. 5 秒自动刷新
5. 显示上次刷新时间
6. API 失败 banner
7. 数据过期提示
8. 页面性能稳定
9. 不使用 innerHTML 拼接运行时数据
10. /api/overview 使用 1 秒内存缓存
```

日志显示限制：

```text
worker audit 默认最近 8 条。
普通日志面板未来最多 20 条。
不在前端渲染完整 JSONL。
不做无限追加 DOM。
```

## 15. 实盘安全审计

进程与锁：

```text
scheduler.lock 与 worker.lock 路径必须不同。
stale lock 清理只能清理自己的锁。
重复启动 scheduler 必须失败。
重复启动 worker 必须失败。
stop 不能误杀无关 python 进程。
```

模式切换：

```text
dry-run 是默认模式。
real-order 必须显式 -EnableRealOrders。
dry-run -> real-order 必须 stop -> start。
real-order -> dry-run 必须 stop -> start。
worker 启动后不做热切换。
status 必须显示 worker mode。
command_mismatch 必须提示需要重启。
```

kill switch：

```text
flag 存在时 worker 不应真实提交。
flag 存在时 status 显示 kill_switch:on。
启动前检查 kill switch。
提交前再次检查 kill switch。
```

preflight 与余额边界：

```text
exchange request preflight 只表示请求构造、签名、路由、参数合法。
preflight 不等于余额检查。
preflight 不等于保证金检查。
余额 / 保证金 / 仓位预算应在 ExecutionRiskGate 或对应 risk gate 中验证。
dashboard 不应展示“preflight 已检查余额”这类误导文案。
```

## 16. 提交顺序与 gitignore

跨仓库提交顺序：

```text
1. quant repo
2. bot repo
```

原因：

```text
bot 消费 quant 的 handoff 字段和 runtime artifacts。
先提交 quant 可以避免 bot 依赖远端不存在的字段或产物。
```

提交前必须检查：

```text
runtime/
.pytest_cache/
.tmp_pytest*/
pytest-cache-files-*/
*.duckdb
本地 PID / lock / log
临时 dashboard 报告
```

这些不应进入提交。

## 17. 下一步实施顺序

P0：因子治理闭环

```text
1. 新建 src/policy/factor_governance.py
2. 为 factor_lookup 增加 gross_expectancy_pct / estimated_cost_pct / net_expectancy_pct
3. 输出 factor_lifecycle / factor_effect
4. FactorPolicyBridge 消费治理结果
5. 测试 active/watch/deprecated/veto 边界
6. 测试未经治理因子不能提高 execution_allowed / sizing
```

P1：量化触发链路

```text
1. 测试治理后 support 因子能提高 confidence 但不能绕过 risk_filter
2. 测试 veto 因子进入 veto_factor_codes
3. 测试 lookup stale 时不放大仓位
4. 测试 sample_count 不足时只能 watch/neutral
5. 测试 net_expectancy_pct <= 0 时不能 active/support
```

P2：Dashboard 展示增强

```text
1. 展示 factor_grade
2. 展示 factor_lifecycle
3. 展示 factor_effect
4. 展示 sample_count / win_rate / stop_hit_rate / net_expectancy_pct
5. 中文解释治理原因
6. 保持 5 秒刷新和 1 秒 API 缓存
```

P3：安全与提交固化

```text
1. lock / PID / stale lock 审计
2. EnableRealOrders 冷启动审计
3. preflight vs balance/margin 文案和测试审计
4. gitignore 审计
5. focused tests
6. horizontal tests
7. 条件允许时 full tests
8. 先 quant 后 bot 提交
```

## 18. 完成定义

这一阶段真正完成，不是页面能打开，也不是脚本能启动，而是：

```text
1. 一键启动能拉起样本采集、量化判断、bot 链路和 dashboard。
2. dashboard 能实时展示三块状态。
3. factor_lookup 被 scheduler 自动刷新。
4. FactorGovernanceEvaluator 能评估因子生命周期和效果。
5. 因子 grade 与 lifecycle 不混用。
6. 未治理因子不能提高 execution_allowed 或 sizing。
7. research 类 veto 不跨轮残留。
8. DEGRADED 不允许 entry，包括 small_probe。
9. execution_allowed 有 false -> true 的真实链路测试。
10. candidate package allowed / blocked 都可解释。
11. worker dry-run 路径稳定。
12. real-order 路径有显式冷启动开关和 kill switch。
13. 锁、PID、stale lock、重复启动通过审计。
14. runtime / cache / temp 不进入 git。
15. quant 与 bot 提交顺序明确。
```
