# ETH Trading Bot

## 项目自述

ETH Trading Bot 是一个面向真实交易执行的机器人壳层。它不负责重新发明策略，也不在本地解释量化因子；它的职责是消费 `quant_system_rebuild` 输出的 judgement / execution handoff，把量化侧的判断转成可审计、可降级、可恢复、可人工接管的执行流程。换句话说，quant 仓库负责“判断该不该做、做多大”，bot 仓库负责“在严格边界内怎样安全地执行”。

这个仓库可以用来做：

- 调用量化引擎获取 `strict-live` judgement 和 execution handoff。
- 在 shadow / simulated-real / real 三种模式下编排执行流程。
- 对接 Binance USDT perpetual，处理签名请求、仓位快照、订单状态和保护单。
- 维护本地 state store、JSONL audit log、network guard、kill switch 和高风险动作锁。
- 管理保护止损，包括 adopt、preview、只读 watcher、分阶段锁盈和缺失保护单补单。
- 在执行前后做风险门校验，保证网络异常、状态冲突、过期 handoff、重复执行和人工确认缺失时不会扩大风险。
- 只读展示 quant 侧给出的 `sizing_tier / sizing_bias`，不解释、不重算、不改变交易 gate。

技术上，这个项目以 Python 为核心，围绕清晰的执行边界拆分为 engine client、orchestrator、exchange adapter、position manager、network guard、state store、audit logger 和 high-risk gate。测试侧使用 pytest 锁定 shadow / simulated-real / real 的行为边界；运行侧使用 PowerShell / CMD 脚本适配 Windows 本地常驻与人工操作。

一句话概括：这个仓库负责“把已经审计过的量化判断安全地变成执行动作”。它不追求在 bot 内部变聪明，而是追求在真实交易边界上足够保守、足够透明、足够容易停下。

ETH Trading Bot 是一个面向实盘执行的机器人壳层，负责消费 `quant_system_rebuild` 输出的 judgement / execution handoff，并把它转换为可审计、可降级、可恢复的交易执行流程。

本仓库不重新发明策略，不生成 research bundle，也不把 sample-fallback 当成实时交易信号。策略判断、research readiness、execution handoff 语义由量化仓库负责；本仓库只处理执行边界、交易所适配、本地状态、审计日志、保护止损和故障降级。

## 当前边界

当前硬边界：

- 交易标的：`ETH`
- 合约：Binance USDT perpetual，默认 `ETHUSDT`
- 杠杆：`10x`
- 主决策周期：`15m`
- 风险辅助周期：`5m`
- 默认运行模式：`shadow`
- 真实执行只允许消费 `strict-live`

明确不做：

- 不支持多币种泛化
- 不支持把 `sample-fallback` 输出直接用于实盘
- 不在 bot 内复制量化策略逻辑
- 不在网络异常时自行改变策略方向
- 不在本地动态放宽止损或扩大风险

## 仓库结构

```text
src/bot/
  config.py             # 运行配置与安全边界
  engine_client.py      # 调用 quant_system_rebuild judgement / handoff
  orchestrator.py       # shadow / simulated-real / real 编排
  exchange_adapter.py   # Binance USDT perpetual 适配
  binance_transport.py  # Binance REST 签名请求
  position_manager.py   # 仓位、订单、保护止损计划
  network_guard.py      # 网络/代理/数据源降级
  state_store.py        # 本地状态持久化
  audit_logger.py       # JSONL 审计日志与脱敏
  execution_risk_gate.py
  execution_summary.py
  action_enums.py

scripts/
  run_shadow_preflight_cycle.py
  run_shadow_live_cycle.py
  run_manual_entry_cycle.py
  preview_protective_stop_replace.py
  adopt_protective_stop.py
  watch_protective_stop_replace.py
  start_protective_stop_watch_readonly.ps1
  start_protective_stop_watch_readonly.cmd

tests/
  pytest regression tests

runtime/
  本地运行状态、审计日志、报告。默认不应作为源码提交。
```

## 安装

建议和量化仓库放在同一父目录：

```text
D:\开发\eth_trading_bot
D:\开发\quant_system_rebuild
```

安装依赖：

```powershell
cd D:\开发\eth_trading_bot
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pip install -e ".[dev]"
```

也可以用当前 Python：

```powershell
python -m pip install -e ".[dev]"
```

## 环境变量

真实交易执行使用交易专用 key：

```powershell
BINANCE_TRADE_API_KEY
BINANCE_TRADE_API_SECRET
```

不要把数据查询 key 和交易执行 key 混用。README 只记录环境变量名，不应记录任何真实 key 明文。

常用可选项：

```powershell
$env:PYTHONPATH="D:\开发\eth_trading_bot\src"
```

如果量化仓库没有安装为 editable package，也需要让 bot 能找到量化源码：

```powershell
$env:PYTHONPATH="D:\开发\eth_trading_bot\src;D:\开发\quant_system_rebuild\src"
```

## 运行模式

### shadow

只生成执行计划、状态迁移和审计日志，不调用真实下单接口。用于验证 judgement 到 execution plan 的映射。

### simulated-real

更接近真实执行路径，但仍受测试/模拟边界约束。用于验证风控门、确认 token、状态写入、审计日志和异常分支。

### real

真实执行模式。只允许 `engine_mode=strict-live`，并受以下边界保护：

- 必须使用 `BINANCE_TRADE_API_KEY / BINANCE_TRADE_API_SECRET`
- 默认需要人工确认 token 才允许主动 entry
- 网络、research readiness、runtime guard 异常时禁止新开仓
- 入场后优先维护交易所侧保护止损

## 常用命令

运行测试：

```powershell
cd D:\开发\eth_trading_bot
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest -q
```

只跑保护止损 watcher 相关测试：

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_watch_protective_stop_replace_script.py tests\test_preview_protective_stop_replace_script.py -q
```

启动只读保护止损 watcher：

```powershell
D:\开发\eth_trading_bot\scripts\start_protective_stop_watch_readonly.cmd
```

后台管理 watcher：

```powershell
D:\开发\eth_trading_bot\scripts\manage_protective_stop_watch.cmd start
D:\开发\eth_trading_bot\scripts\manage_protective_stop_watch.cmd status
D:\开发\eth_trading_bot\scripts\manage_protective_stop_watch.cmd stop
```

允许保护止损丢失补单的后台监控：

```powershell
D:\开发\eth_trading_bot\scripts\manage_protective_stop_watch.cmd start -AllowMissingRepair
```

测试 watcher 一轮后退出：

```powershell
D:\开发\eth_trading_bot\scripts\start_protective_stop_watch_readonly.cmd -MaxIterations 1
```

只读 watcher 明确不会调用撤单/下单端点。看到下面输出代表处于只读监控：

```text
Mode: read-only. No cancel/place endpoint is called.
```

## 一键 runtime stack

当前推荐用统一管理器启动本地自动化链路和只读 dashboard：

```powershell
cd D:\开发\eth_trading_bot
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd start
```

查看状态：

```powershell
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd status
```

停止：

```powershell
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd stop
```

默认启动内容：

```text
dashboard       -> http://127.0.0.1:8765
factor_ingest   -> quant ingest-summary loop
quant_judgement -> quant run-cycle strict-live loop
bot_scheduler   -> consume handoff and write candidate package or blocked audit
real_worker     -> dry-run worker loop
```

默认是 dry-run，不会真实提交订单。真实提交必须显式冷启动：

```powershell
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd start -EnableRealOrders
```

实盘安全边界：

- `-EnableRealOrders` 只在启动时生效；dry-run 和 real-order 之间需要 stop -> start
- kill switch 文件存在时不会启用真实提交：`runtime\controls\disable_real_execution.flag`
- worker submit 前会再次检查 kill switch
- status 会显示 dashboard HTTP/API、factor age、quant latest run id、bot latest sample、candidate package、worker mode/audit、kill switch 和近期日志错误
- `candidate_package: missing` 通常表示当前策略没有允许执行的 package，不代表 runtime stack 没启动

## Runtime dashboard

页面地址：

```text
http://127.0.0.1:8765
```

API 地址：

```text
http://127.0.0.1:8765/api/overview
```

Dashboard 是只读观察页，不提供运行控制。三块主面板：

- 样本采集与因子治理：sample、lookup、`factor_grade / factor_lifecycle / factor_effect`、win rate、stop hit rate、net expectancy
- 量化市场判断：action、direction、confidence、sizing、research、risk、handoff、reason codes
- bot 下单链路：candidate package、automation boundary、worker audit、kill switch、runtime status

前端使用原生 HTML/CSS/JS，5 秒刷新，`/api/overview` 有 1 秒内存缓存。运行时数据用 DOM API 渲染，不拼接运行时 `innerHTML`。

## 保护止损流程

合并版第一版目标：

```text
原始保护止损 -> 保本/小幅锁盈 -> 多阶段锁盈 -> 止损丢失补单
```

当前内置 ratchet 阶段：

```text
long:
+0.50% mark buffer -> stop = entry +0.30%
+0.90% mark buffer -> stop = entry +0.60%
+1.30% mark buffer -> stop = entry +0.90%

short:
-0.50% mark buffer -> stop = entry -0.30%
-0.90% mark buffer -> stop = entry -0.60%
-1.30% mark buffer -> stop = entry -0.90%
```

硬规则：

- 多阶段锁盈只进不退，mark 回撤不会下移 stop
- stage 状态写入 `metadata.protective_stop.lock_stage`
- 理论锁盈价写入 `metadata.protective_stop.lock_target_price`
- 目标价计算使用交易所 position snapshot 的 `entry_price`
- 正常替换走 `cancel -> verify removed -> place -> verify active -> state write`
- 保护止损丢失补单走 `place -> verify active -> state write`，不执行 cancel
- 交易所有多张保护单时停止自动处理并要求人工确认
- 无法确认 entry / direction / quantity / snapshot freshness 时 blocked

只读 watcher 用于观察是否达到下一阶段或是否需要缺失补单，不会撤单或下单。显式开启自动替换时仍需要 `--auto-confirm-replace --accept-gap-risk`，缺失补单还需要 `--allow-missing-repair`。

## 与量化仓库的分工

`quant_system_rebuild` 负责：

- strict-live / sample-fallback judgement
- research bundle readiness
- feature matrix
- policy decision
- execution handoff
- scheduler / observation / comparison

`eth_trading_bot` 负责：

- 消费 judgement / handoff
- 映射执行计划
- 维护本地 state
- 审计执行链路
- Binance adapter
- 保护止损维护
- 网络和 runtime 降级

## 安全约束

实盘相关改动必须遵守：

- 新开仓只能来自 strict-live judgement
- sample-fallback 只能用于回放、smoke、shadow、报告链路
- 网络异常时禁止扩大风险
- 执行失败不代表策略失败，必须分开记录
- 本地 state 与交易所状态冲突时，优先停止自动动作并做 reconcile
- 日志必须脱敏，不能写入真实 API secret

## 当前状态

仓库已经具备：

- bot config / state / audit / network guard
- Binance transport 和 exchange adapter
- shadow / simulated-real / real 边界
- manual entry confirmation token
- 保护止损 adopt / preview / watch 脚本
- ratchet 多阶段锁盈
- 保护止损丢失 place-only 补单
- 只读 watcher Windows 启动脚本
- 对应 pytest 覆盖

仍不包含：

1. trailing stop 接管
2. reduce / exit 自动执行
3. 自动开仓
4. Windows service / supervisor 常驻

越往后风险越高，必须单独做 review 和实盘前演练。

## 高风险动作地基

trailing / reduce / exit 不直接接入 watcher 自动执行。真实执行前必须先通过统一 `HighRiskGate`：

- `handoff_id` 必须存在并且未执行过
- `expires_at` 必须未过期
- `runtime_mode=real` 时只能消费 `strict-live`
- kill switch 文件存在时全部 blocked
- 任一高风险动作 lock 存在时全部 blocked
- `NetworkGuard` degraded / blocked 时全部 blocked
- confirm 前必须重新拉 position / orders / account snapshot
- 执行后必须 re-fetch verify
- handoff schema 必须冻结，不接受自由 JSON

trailing 到期规则：

- trailing 规则过期但交易所仍有 active trailing stop：不替换，标记 `trailing_rule_expired`，等待新 handoff 或人工处理
- trailing 不存在且无 fixed stop：只允许补 fixed stop
- fixed stop 只能补到当前 ratchet stage 目标价
- 如果存在 `last_known_protected_price` 且 ratchet 目标价更差，必须 blocked，不能自动降级保护水平

reduce 后保护止损规则：

- reduce 成功后必须重建保护止损数量
- 流程为 `reduce verify -> cancel old stop -> verify removed -> place new stop with new position qty -> verify active -> state write`
- reduce 和 protective stop replace 共用 high-risk lock，不能并行

watcher 重启规则：

- watcher 不恢复 `PREFLIGHT / CONFIRMING` 等内存状态
- 启动后永远 fresh snapshot -> evaluate
- stale auto replace lock 会在启动时清理
- 发现已有 watcher 进程时拒绝双开
### High-risk preview script

`scripts/preview_high_risk_handoff.py` is read-only. It evaluates a trailing / reduce / exit handoff against:

- frozen `version=1` handoff schema
- `handoff_id`, `generated_at`, `expires_at`
- `runtime_mode=real` and `engine_mode=strict-live`
- kill switch, in-flight action lock, and `NetworkGuard`
- live exchange position snapshot
- a single live exchange protective algo stop from `openAlgoOrders`
- action-specific checks for trailing and reduce

Example:

```powershell
cd D:\开发\eth_trading_bot
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='D:\开发\eth_trading_bot\src'

D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe scripts\preview_high_risk_handoff.py `
  --handoff-file D:\path\to\high_risk_handoff.json `
  --state-path D:\开发\eth_trading_bot\runtime\shared_state\bot_state.json `
  --report-root D:\开发\eth_trading_bot\runtime\reports\high_risk_handoff_preview `
  --proxy-url http://127.0.0.1:7897
```

Reports are written to `runtime/reports/high_risk_handoff_preview/latest_preview.json`.

Important: preview output never enables real trailing / reduce / exit execution. It only states whether the handoff passes the gate and what the expected post-action state would be.
