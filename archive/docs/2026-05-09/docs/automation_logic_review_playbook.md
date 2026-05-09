# ETH 自动化逻辑审查手册

## 1. 用途

这份文档不是实现计划，而是每次审查代码、排查策略问题、让 DS 或 GPT 扫逻辑 bug 时使用的思维框架。

目标是先检查“系统逻辑是否自洽”，再检查代码细节。

核心原则：

```text
先审边界，再审链路。
先审因果，再审字段。
先审谁有决策权，再审谁读取了数据。
```

## 2. 最大边界

任何时候都不能混淆这四层：

```text
因子：提供证据
治理：判断证据是否可信
量化：做交易决策
bot/worker：执行交易决策
```

禁止出现：

```text
因子直接指挥 bot
DuckDB 直接决定开仓
dashboard 控制实盘开关
bot 自己判断策略方向
worker 绕过 execution_allowed
```

正确方向：

```text
因子 -> 治理 -> 量化 -> handoff -> bot -> worker -> 交易所
```

## 3. 因子逻辑审查

先分清“当前实现审查”和“目标态审查”。

当前实现已经有：

```text
factor_grade_summary
factor_grade = core / enhancer / reference
```

目标态才要求新增：

```text
factor_lifecycle = active / watch / deprecated
factor_effect = support / oppose / veto / neutral
```

因此，在 lifecycle / effect enum 和字段尚未落地前，审查时不能把“缺少 lifecycle/effect 字段”直接判为当前代码 bug；只能判为“目标态缺口”。

审查问题：

```text
这个因子是谁采集的？
这个因子存在哪里？
这个因子是否只是被记录，还是参与了量化？
如果参与量化，它是否先经过治理？
它的 factor_grade 是什么？
如果目标态已实现，它的 lifecycle 是什么？
如果目标态已实现，它的 effect 是什么？
```

必须区分：

```text
factor_grade = core / enhancer / reference
factor_lifecycle = active / watch / deprecated
factor_effect = support / oppose / veto / neutral
```

赋值责任：

```text
factor_grade：由 factor evidence / handoff 汇总逻辑按因子重要性归类。
factor_lifecycle：目标态由 FactorGovernanceEvaluator 根据 lookup 质量、样本、成本和新鲜度设置。
factor_effect：目标态由 FactorGovernanceEvaluator 根据同 regime / direction 下的历史表现和风险效果设置。
```

逻辑红线：

```text
未经治理的因子不能提高 execution_allowed。
未经治理的因子不能提高 sizing。
watch 因子不能放大仓位。
deprecated 因子不能参与量化加权。
veto 是当前风险效果，不是 factor_grade。
```

## 4. 好因子的判定审查

看到“好因子”“有效因子”“support factor”时，必须追问：

```text
样本数够不够？
是不是同 regime？
是不是同 direction？
胜率是不是靠少数极端样本撑起来？
stop_hit_rate 是否过高？
avg_mae 是否过大？
gross expectancy 是否为正？
扣除手续费、滑点、spread 后 net expectancy 是否为正？
lookup 是否过期？
最近是否失效？
```

当前实现注意：

```text
如果 factor_lookup 表尚未包含 gross_expectancy_pct / estimated_cost_pct / net_expectancy_pct，
不能用“缺少 net_expectancy_pct”判定当前实现有 bug。
正确结论应是：成本后收益治理尚未落地，不能声称因子已完成实盘级治理。
```

如果没有这些字段或测试，就不能称为已完成“目标态治理”。

尤其注意：

```text
win_rate 高不等于好因子。
sample_count 小不等于好因子。
MFE 高但 MAE 更高不等于好因子。
未扣成本的收益期望不等于实盘可用。
```

## 5. DuckDB 审查

DuckDB 的正确角色：

```text
存储样本
聚合历史表现
生成 factor_lookup
提供治理输入
```

DuckDB 的错误角色：

```text
直接决定开仓
直接放大仓位
直接绕过量化
直接给 bot 下指令
```

审查问题：

```text
factor_values 是否只是记录？
factor_lookup 是否只是统计？
有没有代码直接把 lookup row 当作 execution_allowed？
有没有代码只看 win_rate 就提高 sizing？
有没有代码在 lookup stale 时仍放大仓位？
```

## 6. 量化决策审查

量化层才有交易决策权。

审查问题：

```text
action 是谁决定的？
direction 是谁决定的？
confidence 是谁调整的？
sizing 是谁调整的？
execution_allowed 是谁最终确定的？
runtime_vetoes 是否参与了最终判断？
```

硬规则：

```text
execution_allowed=true 必须来自完整链路，不能 mock 后直接写入运行产物。
risk_filter_status 不是 pass 时不能真实 entry。
runtime_vetoes 非空时不能真实 entry。
DEGRADED 不允许 entry，包括 small_probe。
```

DEGRADED 恢复审查：

```text
DEGRADED 不能只进入、不退出。
必须能说明进入 DEGRADED 的来源：quant risk_filter / research degraded，还是 bot execution API failure。
quant DEGRADED 的退出应来自下一轮 research / risk_filter 重新计算后恢复 pass。
bot execution DEGRADED 的退出应来自 API success / reconciliation success 后清理 failure count 和 recovery flag。
consecutive_api_failure_count 降到阈值以下或清零后，空仓空闲状态应能回到 IDLE/OBSERVING。
如果 DEGRADED 退出条件缺失，系统可能永远无法再开仓。
```

## 7. research 审查

research_not_ready 不是未知 bug，它是 risk_filter 的明确结果。

审查问题：

```text
research health 是否刷新？
research alias 是否过期？
research gate 为什么关闭？
runtime_vetoes 是否本轮重新计算？
旧 research_not_ready 是否残留到新一轮？
```

红线：

```text
上一轮 research_not_ready 不能污染下一轮。
research ready 不等于可以下单。
research degraded 不等于可以 probe 实盘。
research blocked 必须让 execution_allowed=false。
```

注意：

```text
research degraded 是 quant 证据质量降级。
bot execution degraded 是执行层 API / runtime 状态降级。
两者不能写入同一个状态源，也不能互相冒充。
```

## 8. handoff 审查

handoff 是量化到 bot 的唯一交易意图边界。

审查问题：

```text
handoff 是否存在？
handoff 是否新鲜？
action 是否是 entry/reduce/exit/trailing/wait 中的合法动作？
direction 是否明确？
execution_allowed 是否明确？
execution_block_reason 是否可解释？
runtime_vetoes 是否为空？
risk_filter_status 是否 pass？
factor_lookup_version 是否可追踪？
factor_lookup_stale 是否被处理？
```

红线：

```text
handoff 缺失不能生成 candidate package。
execution_allowed=false 不能生成可执行 candidate package。
handoff 过期不能执行。
blocked handoff 可以展示，但不能执行。
```

## 9. bot / worker 审查

bot 和 worker 不负责策略判断。

bot 负责：

```text
读取 handoff
验证 handoff 是否允许执行
生成 candidate package
记录 blocked audit
```

worker 负责：

```text
读取 candidate package
检查 kill switch
检查幂等
检查过期
检查 risk gate
构造交易所请求
dry-run 或真实提交
写 audit
```

审查问题：

```text
bot 有没有自己判断行情？
worker 有没有绕过 candidate package？
worker 有没有绕过 execution_allowed？
pending idempotency 是否会阻止重复提交？
expired package 是否会阻止提交？
kill switch 是否启动前和提交前都检查？
```

RECONCILING 审查：

```text
RECONCILING 中禁止 entry。
RECONCILING 只能做对账、保护性止损修复、降低风险动作。
RECONCILING 不能和 ENTRY_SUBMITTING / HIGH_RISK_SUBMITTING 并存。
position=FLAT 且无 open protective stop 时，RECONCILING 应能退出到 IDLE/OBSERVING。
position!=FLAT 且 protective stop active 时，RECONCILING 应能退出到 POSITION_OPEN / POSITION_PROTECTED。
position!=FLAT 且 protective stop missing 时，应进入 protective stop repair；repair 失败保持 RECONCILING 并 alert。
连续 N 次 reconcile 失败应保持禁止 entry，并产生可见告警。
partial fill、ghost order、stop repair failure、position size mismatch 都必须能解释为什么仍在 RECONCILING。
```

## 10. 前端审查

dashboard 是观察面板，不是控制台。

允许：

```text
中文展示
实时刷新
状态解释
reason code 翻译
三块链路展示
```

不允许：

```text
人工开仓按钮
人工关闭风控按钮
EnableRealOrders 热切换
策略覆盖按钮
前端直接写 runtime 控制文件
```

审查问题：

```text
字段是否中文？
是否展示真实 API 数据？
是否有写死假数据？
是否使用 innerHTML 拼接运行时数据？
是否会无限追加 DOM？
API 失败是否有 banner？
数据过期是否有提示？
```

## 11. 安全审查

每次涉及实盘执行，必须检查：

```text
dry-run 是否默认？
real-order 是否必须显式开启？
EnableRealOrders 是否冷启动？
kill switch 是否有效？
scheduler lock 与 worker lock 是否分离？
重复启动是否被阻止？
stale lock 是否只清自己的锁？
preflight 是否没有冒充余额检查？
余额 / 保证金是否在 risk gate 检查？
```

红线：

```text
不能热切换实盘。
不能因为 dashboard 显示正常就认为可下单。
不能因为 preflight pass 就认为余额足够。
不能因为进程 running 就认为链路健康。
```

## 12. 状态审查

status 不能只看进程活着。

必须同时看：

```text
dashboard http/api 是否 200
factor heartbeat 是否新鲜
factor_lookup rows 是否正常
quant latest_run_id 是否推进
handoff 是否新鲜
handoff.execution_allowed 是否 true
bot heartbeat 是否新鲜
candidate package 是否存在
worker audit 是否新鲜
worker mode 是否符合预期
kill switch 是否 off
日志最近 N 行是否持续报错
execution_state 是否为 reconciling/degraded/blocked
reconciliation_in_sync 是否 true
consecutive_api_failure_count 是否持续增加
```

常见误判：

```text
进程 running 但 run_id 不推进，可能卡住。
handoff 存在但 execution_allowed=false，不会产可执行 package。
candidate_package missing 不一定是链路坏，可能是策略阻断。
dashboard 无响应通常是服务未启动，不等于数据链路坏。
DEGRADED running 不是健康状态，要看退出条件是否正在恢复。
RECONCILING running 不是健康状态，要看 reconcile 是否推进。
```

## 13. 测试审查

每次改动都要问：

```text
有没有 focused test？
有没有 horizontal test？
有没有覆盖 false -> true？
有没有覆盖 blocked？
有没有覆盖 stale？
有没有覆盖 duplicate？
有没有覆盖 kill switch？
有没有覆盖中文 DOM contract？
```

因子治理必须补的测试：

```text
目标态字段落地后，active/support 可以增强 confidence，但不能绕过 risk_filter。
目标态字段落地后，watch 不能提高 execution_allowed。
目标态字段落地后，deprecated 不参与加权。
目标态字段落地后，veto 进入 veto_factor_codes。
目标态字段落地后，sample_count 不足不能 active。
目标态成本字段落地后，net_expectancy_pct <= 0 不能 support。
lookup stale 不能放大 sizing。
目标态成本字段未落地时，不能声称 active/support 已具备实盘成本后收益验证。
成本字段缺失不能 active/support。
```

DEGRADED / RECONCILING 必须补或保留的测试：

```text
连续 API 失败达到阈值 -> execution_state=DEGRADED。
API success -> consecutive_api_failure_count 清零。
空闲 DEGRADED 在 API success 后可回到 IDLE。
DEGRADED 不允许 entry/small_probe。
runtime needs_reconciliation -> execution_state=RECONCILING。
RECONCILING -> automation_state=ACTION_BLOCKED。
RECONCILING 中不允许 entry submit。
reconciliation 成功后能按 position / protective stop 状态退出。
reconciliation 失败后保持 RECONCILING 并 alert。
```

## 14. 提交审查

跨仓库提交顺序：

```text
先 quant
再 bot
```

提交前检查：

```text
git status
git diff --stat
关键文件 diff
runtime 是否误入 git
cache 是否误入 git
tmp pytest 是否误入 git
DuckDB 文件是否误入 git
```

## 15. 快速扫逻辑 bug 的问题清单

每次扫代码先问这 12 个问题：

```text
1. 这个模块有没有越权做别的层该做的决定？
2. 因子有没有绕过治理直接影响开仓？
3. factor_grade 和 lifecycle 有没有混用？
4. win_rate 有没有被误当成好因子的唯一标准？
5. 成本没有扣除时，有没有错误放大仓位？
6. stale lookup 有没有继续参与增强？
7. research_not_ready 有没有跨轮残留？
8. DEGRADED 有没有错误允许 entry/small_probe？
9. execution_allowed=true 有没有完整因果链？
10. bot/worker 有没有绕过 handoff 或 candidate package？
11. 前端有没有写死状态或提供控制能力？
12. status 有没有把进程 alive 误当成链路 healthy？
```

扩展问题：

```text
13. lifecycle/effect 是当前已实现字段，还是目标态字段？
14. net_expectancy_pct 是真实字段，还是目标态审查要求？
15. factor_lifecycle 到底由谁设置，是否有 evaluator？
16. DEGRADED 有没有明确退出条件？
17. RECONCILING 有没有明确退出条件？
18. RECONCILING 是否阻断了所有新 entry？
```

只要其中一个答案不清楚，就先不要继续加功能，先把边界和测试补清楚。
