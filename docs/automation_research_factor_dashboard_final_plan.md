# ETH 自动化实盘准备最终计划

## 目标

本文档合并 DS 审查意见和当前代码复核结论，作为下一阶段执行计划。

核心目标不是放松风控让系统更容易下单，而是让自动化链路具备清晰、可测试、可审计的实盘触发条件：

```text
因子 / research 治理 -> 量化判断 -> execution handoff -> bot candidate package -> worker -> 交易所
```

职责边界：

```text
因子指向量化。
量化指向 bot。
bot 只执行合格 handoff。
前端只做实时观察和解释，不做控制台。
```

## 当前已完成基础

已完成并推送：

```text
bot repo:
- 90d111f Add runtime dashboard and automation stack

quant repo:
- 271f6ba Add execution handoff and factor lookup governance
```

已具备：

```text
1. 一键 runtime stack manager
2. dashboard HTTP/API 服务
3. factor ingest loop
4. quant run-cycle loop
5. quant run-cycle 自动产出 handoff.json
6. bot scheduler 消费 handoff
7. real worker dry-run 轮询
8. candidate package 安全门控
9. factor lookup 表和 CLI 构建能力
10. dashboard 三板块观察面板
```

当前 blocked 状态的正确解释：

```text
candidate_package missing 不是 handoff 缺失。
当前是策略层门控阻止：
- action=wait
- risk_filter_status=veto
- research_not_ready
- execution_allowed=false
```

## DS 审查结论采纳项

### 1. research_not_ready 不再作为未知根因排查

DS 结论正确：`research_not_ready` 的来源已经明确，不需要继续泛泛地“查来源”。

当前代码位置：

```text
D:\开发\quant_system_rebuild\src\policy\risk_filter.py
```

关键行为：

```text
risk_filter.py 中 _resolve_research_gate 相关逻辑会在 research 未就绪时加入：
- runtime_vetoes: research_not_ready
- research_gate_reasons: research_not_ready
```

后续动作应从“排查”改成“修复治理逻辑”：

```text
1. 修 research bundle 健康检查逻辑
2. 修 research 刷新频率
3. 明确 ready / degraded / blocked 的判定标准
4. 补 research 修复后 execution_allowed 转变测试
```

### 2. factor lookup 缺调度接入

DS 结论正确：`factor_lookup` 表、`build_factor_lookup` 函数和 CLI 已存在，但 scheduler 没有周期性调用。

现状：

```text
build_factor_lookup 已存在。
scripts/build_factor_lookup.py 已存在。
interfaces.analysis build-factor-lookup CLI 已存在。
quant_runtime_scheduler.py 尚未把 build-factor-lookup 纳入 loop。
```

因此 `lookup_rows=0` 的主要工程原因不是表不存在，而是调度未接入。

必须补：

```text
quant scheduler 每 N 轮或每 N 分钟运行一次 build_factor_lookup。
运行后写出 factor_lookup_summary.json。
运行后写出 factor_bucket_config.json。
dashboard 读取 lookup 版本、行数、生成时间和新鲜度。
```

### 3. execution_allowed 需要链路测试

DS 结论正确：仅列触发条件不够，必须测试谁设置 `execution_allowed`，以及它如何从 false 变 true。

当前关键位置：

```text
D:\开发\quant_system_rebuild\src\interfaces\runner.py
_resolve_execution_allowed
```

必须补测试：

```text
research_not_ready 存在 -> runtime_vetoes 非空 -> execution_allowed=false
research 修复 -> runtime_vetoes 为空 -> risk_filter_status=pass -> execution_allowed=true
```

验收要求：

```text
不允许靠 mock 绕过核心门控。
测试必须覆盖 DecisionEnvelope -> ExecutionHandoff -> payload 的完整转换。
```

### 4. runtime_vetoes 必须每轮重算，不允许残留

DS 结论正确：如果旧的 `research_not_ready` 残留进下一轮，即使 research 已修复，execution 仍会被错误阻止。

必须定义：

```text
runtime_vetoes 是每轮量化判断的计算结果，不是跨轮累积状态。
旧 cycle 的 vetoes 只能用于历史展示，不能进入新 run-cycle 的 risk input。
```

必须补测试：

```text
上一轮 runtime_vetoes=["research_not_ready"]
下一轮 research ready
下一轮 risk input 不携带旧 veto
下一轮 handoff.runtime_vetoes=[]
```

### 5. dashboard 中文化复用已有映射

DS 结论正确：中文原因映射已经部分存在，不应在 dashboard 里随意重写一套。

已有映射位置：

```text
D:\开发\quant_system_rebuild\src\interfaces\live_stack_launcher.py
_display_reason_code_text
```

后续方案：

```text
1. 抽出共享 reason code 映射，或生成 dashboard 可消费的映射 artifact。
2. dashboard 使用同一套中文解释。
3. 前端只负责展示，不重新定义策略含义。
```

### 6. dashboard API 需要轻量缓存

DS 结论正确：前端 5 秒轮询 `/api/overview`，如果每次都重新读所有 runtime 文件，会产生不必要 I/O。

当前现状：

```text
dashboard API:
Cache-Control: no-store
每次请求直接读 runtime artifacts。
```

后续方案：

```text
1. dashboard server 增加 1 秒内存 TTL 缓存。
2. 多浏览器或快速刷新时复用同一份 snapshot。
3. 仍保持前端 5 秒轮询。
4. 出错时不缓存异常过久，避免状态卡死。
```

缓存目标：

```text
降低重复 I/O。
不牺牲观察实时性。
不引入 Redis / 数据库 / 大框架。
```

### 7. 日志条数必须固定

DS 结论正确：“限制日志条数”必须写成具体数字。

当前 dashboard 已读取：

```text
worker audit limit=8
```

最终规则：

```text
worker audit 默认显示最近 8 条。
未来如增加更多日志面板，单面板最多 20 条。
不在前端渲染完整 JSONL。
不做无限滚动。
```

### 8. 锁并发审计必须补

DS 结论正确：需要确认 scheduler lock 和 worker lock 的路径、获取方式、stale 清理不会互相影响。

当前已确认：

```text
bot_runtime_scheduler.py 使用 os.O_CREAT | os.O_EXCL。
scheduler lock 路径是 runtime\bot_runtime_scheduler\scheduler.lock。
```

待审计：

```text
1. real_order_worker 的 lock 文件路径。
2. 两把锁是否不同。
3. stale lock 清理是否只清自己的锁。
4. stop 命令是否只按对应 PID / pattern 停对应进程。
5. 两个 worker 同时启动是否被阻止。
```

### 9. EnableRealOrders 是冷切换，不做热切换

DS 结论正确：当前 `EnableRealOrders` 是启动参数，不是运行时热配置。

当前行为：

```text
manage_runtime_stack.ps1 start -EnableRealOrders
```

会影响：

```text
bot scheduler 是否带 --enable-real-orders
worker loop 是否带 -SubmitRealOrders
```

最终规则：

```text
dry-run -> real-order 必须 stop -> start -EnableRealOrders。
real-order -> dry-run 必须 stop -> start。
前端不提供热切换按钮。
文档必须明确冷切换。
```

### 10. preflight 和余额检查不能混淆

DS 结论正确：request preflight 不等于余额检查。

必须区分：

```text
exchange request preflight:
- 构造请求
- 签名
- 路由映射
- 参数合法性

margin / balance / risk gate:
- 保证金预算
- 仓位上限
- 风险百分比
- 小账户预算限制
```

审计时必须确认：

```text
余额 / 保证金检查是在 ExecutionRiskGate 或对应 risk gate 中完成。
如果 exchange preflight 不查余额，必须标为设计选择，而不是误称已查。
```

### 11. 跨仓库提交顺序

DS 结论正确：quant 和 bot 有跨仓依赖。

推荐顺序：

```text
1. 先提交并推送 quant。
2. 再提交并推送 bot。
```

理由：

```text
bot 依赖 handoff 字段和产物。
如果 bot 先推、quant 未推，远端可能短暂处于不可复现状态。
```

本次已经最终一致：

```text
quant: 271f6ba
bot: 90d111f
```

后续必须按推荐顺序执行。

### 12. gitignore 审计必须做

DS 结论正确：提交前必须确认 runtime 数据、临时测试目录、缓存文件不会入库。

必须检查：

```text
runtime/
.pytest_cache/
.tmp_pytest*/
pytest-cache-files-*/
*.duckdb
*.jsonl runtime 输出
dashboard 临时报告
本地 pid / log / lock
```

验收：

```text
git status 不出现 runtime 产物。
git add 不会误纳入测试缓存。
```

## 最终执行阶段

## Phase 1: research / factor lookup 治理接入

### 目标

让量化判断拥有稳定的 research 和 factor lookup 证据，不再因为调度缺口长期显示：

```text
factor_lookup_version=""
lookup_rows=0
research_not_ready
```

### 具体任务

1. 接入 factor lookup 调度。

```text
quant_runtime_scheduler.py ingest-summary loop 或 run-cycle loop 中，每 N 轮执行 build_factor_lookup。
默认 N 建议为 3 或 6。
如果 IntervalSec=300，则每 15-30 分钟生成一次 lookup。
```

2. 产物路径固定。

```text
runtime/analysis/factor_lookup_summary.json
runtime/analysis/factor_bucket_config.json
runtime/analysis/factor_lookup_summary.md
```

3. dashboard 读取 lookup 产物。

```text
lookup_version
factor_lookup_rows
generated_at
stale
```

4. research 健康修复。

```text
直接调整 research bundle ready 判定和刷新策略。
不再把“查 research_not_ready 来源”作为任务。
```

5. runtime_vetoes 每轮重算测试。

### 验收标准

```text
factor_lookup_version 非空。
lookup_rows > 0，或明确展示样本不足原因。
research_gate_status 能明确显示 open / degraded / blocked。
research_not_ready 不因旧状态残留。
```

## Phase 2: 实盘触发条件链路测试

### 目标

把“什么时候允许生成 candidate package”做成可测试的链路。

### 必须满足

```text
action in entry_long / entry_short / small_probe
execution_allowed=true
risk_filter_status=pass
runtime_vetoes=[]
staleness_veto=false
conflict_veto=false
position_size_pct > 0
initial_stop_loss 存在
protective stop preflight ready
real_order_gate allowed
```

### 测试矩阵

必须覆盖：

```text
research_not_ready -> execution_allowed=false
research ready + risk pass -> execution_allowed=true
risk_filter=veto -> execution_allowed=false
runtime_vetoes 非空 -> execution_allowed=false
staleness_veto=true -> execution_allowed=false
action=wait -> candidate package skipped
action=entry_long + all gates pass -> candidate package written
```

### 重点

这一步不降低安全标准，只验证正确状态下系统确实能从 blocked 进入 candidate。

## Phase 3: dashboard 实时中文观察面板

### 方向

不做控制台，不做 start / stop / real-order 按钮。

页面定位：

```text
实时中文观察面板
原生 HTML / CSS / JS
5 秒轮询
轻量缓存
信息全面
性能稳定
```

### 功能

1. 顶部总览。

```text
最后刷新时间
下次刷新倒计时
dashboard / factor / quant / bot / worker 总状态
kill switch 状态
当前模式 dry-run / real-order
```

2. 样本采集 / 因子板块。

```text
样本数
因子值数量
lookup rows
lookup version
lookup generated_at
lookup stale
top reason codes 中文解释
top degrade flags 中文解释
```

3. 量化市场判断板块。

```text
latest_run_id
action
direction
confidence
risk_filter_status
execution_allowed
execution_block_reason
research_gate_status
research_gate_reasons
supporting / opposing / veto factors
reasoning_summary
```

4. Bot 执行 / 下单链路板块。

```text
latest_sample
candidate package present / missing
real_order_gate allowed / blocked
blocked reasons 中文解释
worker audit 最近 8 条
last submitted / skipped / blocked
```

### 性能规则

```text
前端 5 秒轮询 /api/overview。
后端 /api/overview 使用 1 秒 TTL 内存缓存。
worker audit 固定最近 8 条。
其它日志面板最多 20 条。
不渲染完整大 JSON。
不引 React / Vue / npm 构建。
```

### 中文映射规则

```text
优先复用 live_stack_launcher.py 中的 reason code 中文映射。
dashboard 不重新定义策略语义。
未知 reason code 显示原始 code，并标记为“未映射”。
```

## Phase 4: 实盘安全审计

### 目标

确认即使未来打开 `-EnableRealOrders`，系统也不会因为工程问题误下单。

### 审计项

1. kill switch。

```text
kill switch 最高优先级。
kill switch 存在时 worker 不提交。
kill switch 存在时 start -EnableRealOrders 不启动 submit worker 或保持 blocked。
```

2. EnableRealOrders 冷切换。

```text
必须 stop -> start -EnableRealOrders。
不支持前端热切换。
文档明确切换流程。
```

3. candidate package。

```text
必须有 expires_at。
过期 package blocked。
package_id / run_id / idempotency_key 防重复。
```

4. position safety。

```text
FLAT 才能开新仓。
已有仓位不能重复开仓。
已进入仓位时只允许维护止损 / reduce / exit。
```

5. protective stop。

```text
entry package 必须包含 protective stop command。
protective stop preflight 未 ready 时 blocked。
提交后必须校验 protective stop 存在。
```

6. risk / margin。

```text
position cap 生效。
max risk 生效。
margin budget 生效。
balance / equity 约束位置明确。
不能把 request preflight 误当余额检查。
```

7. locks。

```text
bot scheduler lock 和 worker lock 路径不同。
锁获取使用原子方式。
stale lock 只清自己的锁。
并发 worker 不会双提交。
```

8. audit。

```text
submitted / skipped / blocked 全部写 audit。
submit 异常写 error_kind。
idempotency_key 写入状态。
```

## Phase 5: 测试、审查、提交固化

### 测试要求

bot 侧：

```text
dashboard data sources
runtime stack manager
bot runtime scheduler
real order worker
automation gate
automation state
exchange adapter
state store
shadow orchestrator
```

quant 侧：

```text
quant runtime scheduler
execution handoff block reason
interfaces runner handoff payload
interfaces analysis factor lookup
factor dataset lookup extraction
research gate tests
execution_allowed transition tests
runtime_vetoes non-accumulation tests
```

### 审查要求

```text
git diff --stat
git diff 关键文件
确认无 runtime 产物入库
确认无临时目录入库
确认 dashboard API 200
确认 manage_runtime_stack status 正常
```

### 提交顺序

```text
1. quant_system_rebuild 先提交并推送。
2. eth_trading_bot 后提交并推送。
```

### 不做的事

```text
不为了触发实盘而放松 risk_filter。
不在前端添加实盘切换按钮。
不引入 React / Vue。
不引入 Docker。
不把 runtime 数据提交到仓库。
```

## 最终验收定义

只有同时满足以下条件，才认为下一阶段完成：

```text
1. factor lookup 被 scheduler 自动生成。
2. dashboard 显示 lookup version / rows / stale。
3. research ready 场景不再错误产生 research_not_ready。
4. execution_allowed false -> true 的完整链路测试通过。
5. runtime_vetoes 每轮重算，不跨轮残留。
6. dashboard 中文展示全面，5 秒轮询，API 有 1 秒缓存。
7. worker audit 固定最近 8 条。
8. 实盘安全审计清单有对应测试或明确证据。
9. bot 和 quant 横向测试通过。
10. 提交前 gitignore 审计通过。
```

