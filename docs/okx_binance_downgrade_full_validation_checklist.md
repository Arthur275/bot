# OKX / Binance 降级后全链路校验细则

最后校准时间：2026-05-08，Asia/Shanghai。

本文档是 `eth_trading_bot` 与 `quant_system_rebuild` 在 Binance 数据/执行降级后的完整审查清单。目标不是证明“可以无条件下单”，而是把数据、判断、执行、保护止损、风控闸门、运行状态和人工放行条件逐项锁清楚。

## 当前结论

- 默认真实执行入口已经切到 OKX：`okx_usdt_swap / ETH-USDT-SWAP / https://www.okx.com`。
- OKX 交易专用环境变量为 `OKX_TRADE_API_KEY`、`OKX_TRADE_API_SECRET`、`OKX_TRADE_PASSPHRASE`。数据 key 与交易 key 必须继续分离。
- Binance USDT 永续执行只允许作为显式 legacy fallback：`binance_usdt_perp / ETHUSDT`。不得作为默认实盘入口。
- OKX 公共读、私有账户快照、余额、仓位、挂单、策略挂单、成交、合约信息、ticker 读取链路已经验证过可用。
- 当前 runtime 状态不是实盘提交状态：`candidate_package: missing`，`real_worker: dry_run`，`kill_switch: off`。这表示代码链路和 OKX 读取健康，但尚未产生可执行候选包，也没有启动真实下单提交。
- 当前最新 bot cycle 为 `wait / execution_allowed=false / not_entry_action` 时，不允许生成实盘 entry，也不允许把方向偏好解释成下单许可。

## 审查目标

1. 确认 Binance 降级后，实时运行不硬依赖 Binance 才能继续出判断。
2. 确认 OKX 是 bot 默认交易所，且所有实盘执行字段、签名、请求映射和保护止损语义都正确。
3. 确认量化侧只负责判断与 handoff，bot 侧只负责执行安全边界，不互相越权。
4. 确认没有候选包、非 entry、风控未通过、预检未通过、kill switch、旧包、重复幂等键等情况下不会真实下单。
5. 确认可读信息、预检、真实提交是三层不同状态，不能因为“OKX 信息能拿到”就推导为“已经允许实盘下单”。

## Binance 降级规则

### 必须成立

- `src/bot/config.py` 默认值必须保持：
  - `exchange_venue="okx_usdt_swap"`
  - `exchange_symbol="ETH-USDT-SWAP"`
  - `exchange_api_base_url="https://www.okx.com"`
  - `exchange_api_key_env="OKX_TRADE_API_KEY"`
  - `exchange_api_secret_env="OKX_TRADE_API_SECRET"`
  - `exchange_api_passphrase_env="OKX_TRADE_PASSPHRASE"`
- Binance 执行路径只能在显式配置 `exchange_venue="binance_usdt_perp"` 且 `exchange_symbol="ETHUSDT"` 时启用。
- README 或旧文档里的 Binance 默认执行口径视为历史口径，不能覆盖当前 OKX 默认配置。
- 量化仓库里的 Binance 历史样例、回放样例、claw 样例可以保留，但实时 strict-live 链路不得因为 Binance 限制而直接崩溃。
- `binance_source_health` 只表示 Binance 公开市场数据健康度，不表示账户、余额、仓位、下单、撤单或保护止损健康度。

### 禁止事项

- 禁止把 `BINANCE_API_*` 或 Binance 数据 key 当作真实交易 key 使用。
- 禁止让 bot 在默认配置下回退到 Binance 实盘提交。
- 禁止把 Binance public 451、429、timeout 直接解释为 OKX 账户不可用。
- 禁止在 Binance 数据不可用时生成没有 `consensus_quality` / `source_diagnostics` 的静默判断。

## OKX 执行入口校验

### 环境变量

只检查变量名、是否存在、长度或前后缀，不记录明文。

必需：

```text
OKX_TRADE_API_KEY
OKX_TRADE_API_SECRET
OKX_TRADE_PASSPHRASE
```

可选：

```text
proxy_url / --proxy-url
```

验收标准：

- 三个 OKX 交易变量在新启动进程中可读。
- passphrase 缺失时必须阻断签名请求，不能退回空 passphrase。
- 数据侧变量和交易侧变量不能混用。

### Transport

`src/bot/okx_transport.py` 必须满足：

- 公共请求和私有请求都带 `User-Agent: eth-trading-bot/1.0` 与 `Accept: application/json`。
- 私有请求签名包含 `OK-ACCESS-KEY`、`OK-ACCESS-SIGN`、`OK-ACCESS-TIMESTAMP`、`OK-ACCESS-PASSPHRASE`。
- GET 签名使用 path + query，body 为空字符串。
- POST 签名使用紧凑 JSON body。
- OKX payload `code != "0"` 时抛出 transport error。
- HTTPError、timeout、JSON error 有明确 kind，不吞掉错误。

### Adapter 映射

`src/bot/exchange_adapter.py::OkxUsdtSwapAdapter` 必须满足：

- 仓位：`/api/v5/account/positions`，`instId=ETH-USDT-SWAP`。
- 余额：`/api/v5/account/balance`，优先解析 `totalEq / adjEq`，再解析 `details.USDT.eq/cashBal/availEq`。
- 普通挂单：`/api/v5/trade/orders-pending`。
- 策略挂单：`/api/v5/trade/orders-algo-pending`，`ordType=conditional`。
- 最近成交：`/api/v5/trade/fills`。
- 合约规则：`/api/v5/public/instruments`，读取 `lotSz / minSz / ctVal`。
- ticker：`/api/v5/market/ticker`，读取正数价格。
- entry：`/api/v5/trade/order`，使用 OKX 字段 `instId / tdMode / side / ordType / sz / clOrdId`。
- 保护止损：`/api/v5/trade/order-algo`，使用 `ordType=conditional`、`triggerPx`、`orderPx=-1`、`closeFraction=1`、`algoClOrdId`。
- 撤策略单：`/api/v5/trade/cancel-algos`，body 为数组，至少包含 `algoId` 或 `algoClOrdId`。

### OKX 下单能力判断

分三层判断，不得混淆：

- 读取可用：账户、仓位、余额、挂单、成交等接口能返回 `code=0`。
- 预检可用：entry 和 protective stop 能解析出合法请求体，但不发送真实订单。
- 真实提交可用：必须同时满足候选包、real gate、preflight、runtime snapshot、kill switch、幂等恢复和显式 submit 参数。

当前只可确认读取链路和代码映射健康；在没有可执行 candidate 且 worker 为 `dry_run` 时，不能宣称“已经实盘下单成功”。

## Bot 实盘闸门

真实订单提交必须同时满足以下条件：

- `runtime_mode="real"`。
- `engine_mode="strict-live"`。
- `--submit-real-orders` 显式启用。
- `kill_switch` 文件不存在：`runtime/controls/disable_real_execution.flag`。
- `handoff.execution_allowed=true`。
- `risk_filter_status="pass"`，或满足被代码显式允许的特殊 small probe 规则。
- `effective_action` 属于可执行动作：`entry_long`、`entry_short`、`small_probe`，或保护止损修复动作。
- entry 前 live position 必须为 `FLAT`。
- `execution_plan.place_entry_order=true`。
- `execution_plan.maintain_protective_stop=true`。
- `initial_stop_loss` 存在且 stop distance 合法。
- `preflight` 中 entry order 为 `preflight_ready`。
- `preflight` 中 maintain protective stop 为 `preflight_ready`。
- `candidate_execution_package` 已写出且未过期。
- 幂等键没有处于 pending 或 requires recovery 状态。

任何一项不满足，`real_order_gate.allowed` 必须为 false，`candidate_execution_package` 必须为 skipped 或 worker 必须 blocked/skipped。

## Candidate Package 校验

路径：

```text
D:\开发\eth_trading_bot\runtime\bot_runtime_scheduler\latest_candidate_execution_package.json
```

必须检查：

- `package_id` 非空，能关联 `source_run_id` 或 handoff 生成时间。
- `handoff.generated_at` 不过期。
- `requested_action` 与 `effective_action` 一致或差异有明确原因。
- `real_order_gate.enabled=true` 只表示真实订单开关已打开，不等于允许提交。
- `real_order_gate.allowed=true` 才能进入候选包可执行状态。
- `real_order_gate.automation_boundary="real_order_submission_allowed"`。
- `preflight` 覆盖 entry 与 protective stop。
- `runtime_snapshot.snapshot_valid=true`。
- `position.position_state="FLAT"` 时才允许 entry；已有仓位时 entry 必须阻断。

当前状态 `candidate_package: missing` 的含义是没有可提交候选包，不是 OKX API 失败。

## 保护止损校验

### Entry 同步保护

开仓候选必须同时规划：

- `entry_order`
- `maintain_protective_stop`

验收标准：

- entry 与保护止损 direction 对应同一个方向。
- OKX protective stop 使用反向 side：
  - long 仓保护止损 side 为 sell。
  - short 仓保护止损 side 为 buy。
- OKX 使用 `closeFraction=1`，不能错误地下成加仓单。
- `triggerPx` 来自 handoff 的 `initial_stop_loss` 和 live/reference price 解析，不允许空值。
- `algoClOrdId` 以 bot 生成的稳定前缀与幂等 hash 组成。

### 已有仓位修复

保护止损修复动作允许在有 live position 且缺少保护止损时触发，但必须满足：

- `position_state="ENTERED"`。
- live direction 已知。
- live entry price 有效。
- 当前没有有效 protective stop。
- stop preflight 为 ready。
- 若 kill switch 已出现，普通 entry 不得继续；但已开仓后的保护性补单语义需要按代码中允许的保护分支单独审查。

### 撤旧挂新

替换保护止损前必须：

- 只撤 bot-owned protective algo order。
- 不撤外部人工单。
- 外部保护单存在时必须阻断或要求人工确认，不可静默覆盖。
- 新 stop 下单成功后再确认旧 stop 清理状态。
- open algo order 语义不符合 reduce-only / conditional / trigger price 时必须阻断。

## 量化侧市场数据校验

仓库：`D:\开发\quant_system_rebuild`。

关键模块：

- `src/ingest/market_data_consensus.py`
- `src/interfaces/live_snapshots.py`
- `src/interfaces/okx_overlay.py`
- `src/interfaces/runner.py`
- `src/contracts/execution.py`

### Binance Source Health

必须输出：

```text
binance_source_health
binance_source_failure_reason
source_diagnostics
```

规则：

- HTTP 451 或 restricted location：`unavailable / restricted_location`。
- timeout、SSL EOF、connection reset、HTTP 5xx：核心源不可用时 `unavailable`。
- 429、局部非核心端点失败、延迟过高：`degraded`。
- 没有 diagnostics：`unavailable / missing_diagnostics`。

### Consensus

必须输出：

```text
market_data_mode
persistent_market_data_mode
consensus_quality
consensus_sources
consensus_source_count
consensus_mark_price
consensus_worst_case_price
consensus_price_spread_pct
consensus_direction_vote
binance_mark_price
source_diagnostics
```

验收标准：

- backup exchanges 至少覆盖 OKX、Bitget、Gate、MEXC。
- 每个交易所独立计算 15m/1h/4h 方向，不允许先平均 K 线再算方向。
- outlier 按相对 median 偏离剔除。
- `full / acceptable` 可以正常给策略评估使用。
- `degraded` 必须降低 data health 或 cap sizing。
- `unreliable` 不允许产生方向性 entry 信号。
- Binance restricted 且只有 OKX + Bitget 两源时，只能进入 `restricted_two_source` 特殊保守路径，仓位 cap 必须极小。

### OKX Overlay

OKX overlay 是量化风险输入，不是交易授权。必须检查：

- funding、open interest、mark price、long/short ratio、taker volume 等字段解析失败时有降级标记。
- `okx_funding_crowded_longs / shorts`、`okx_longs_crowded / shorts_crowded`、`okx_oi_expansion` 等只进入风险或 veto 语义。
- OKX overlay 不得绕过 `research_gate`、`risk_filter`、`execution_allowed`。

## ExecutionHandoff 合同

量化输出到 bot 的 `ExecutionHandoff` 必须至少审查这些字段：

```text
generated_at
symbol
timeframe
source_run_id
action
direction
position_state
position_size_pct
requested_size_pct
executable_size_pct
execution_allowed
execution_block_reason
risk_filter_status
runtime_vetoes
degrade_flags
research_gate_status
research_gate_reasons
initial_stop_loss
stop_distance_pct
net_edge_pct
trailing_activation_ratio
trailing_callback_rate_pct
market_data_mode
consensus_quality
binance_source_health
source_diagnostics
sizing_tier
sizing_bias
factor_lookup_version
factor_lookup_stale
reasoning_summary
```

验收标准：

- `source_run_id` 必须能追溯到量化 runtime cycle。
- `execution_allowed=false` 时 bot 必须阻断，即使 direction 是 long/short。
- `execution_block_reason` 非空时不得生成实盘 entry candidate。
- `risk_filter_status` 非 pass 时不得自动 entry，除非代码明确支持的受限 small probe 路径全部满足。
- `initial_stop_loss` 缺失时不得 entry。
- `stop_distance_pct <= 0` 或过宽时不得 entry。
- `factor_lookup_stale=true` 必须进入阻断或降级语义，不能静默忽略。

## Cross-Repo 链路

完整链路：

```text
quant_system_rebuild strict-live cycle
  -> handoff.json
  -> eth_trading_bot bot_runtime_scheduler
  -> latest_candidate_execution_package.json
  -> real_order_worker
  -> OKX adapter
  -> OKX REST
  -> audit_log.jsonl / state_store.json / dashboard
```

每一段必须有可审计产物：

- quant cycle：`scheduler_status.json`、`handoff.json`。
- bot scheduler：latest sample、gate summary、candidate package status。
- real worker：dry_run / blocked / submitted audit event。
- adapter：preflight details 或真实响应摘要。
- dashboard：kill switch、worker mode、candidate、latest judgement、balance/position 状态。

禁止跨层行为：

- quant 不得提交订单。
- bot 不得重算策略方向。
- bot 不得因 OKX 余额充足而覆盖 `execution_allowed=false`。
- dashboard 不得成为交易授权来源。

## Runtime 当前状态检查

只读命令：

```powershell
D:\开发\eth_trading_bot\scripts\manage_runtime_stack.cmd status
```

当前已观察状态：

```text
dashboard: running
factor_ingest: running
quant_judgement: running
candidate_package: missing
real_worker: dry_run
kill_switch: off
```

审查解释：

- `dashboard/factor_ingest/quant_judgement running` 表示运行栈在工作。
- `candidate_package: missing` 表示没有可执行候选包。
- `real_worker: dry_run` 表示 worker 没有实盘提交权限。
- `kill_switch: off` 只是没有熔断文件，不等于允许下单。
- 如果看到 `command_mismatch`，需要检查进程启动命令是否与 manager 期望一致；只要 worker 仍为 `dry_run`，它不是实盘提交状态。

## OKX 只读验证清单

只读 OKX 验证必须覆盖：

- 公共接口：
  - `/api/v5/public/instruments`
  - `/api/v5/market/ticker`
- 私有接口：
  - `/api/v5/account/balance`
  - `/api/v5/account/positions`
  - `/api/v5/trade/orders-pending`
  - `/api/v5/trade/orders-algo-pending`
  - `/api/v5/trade/fills`

验收标准：

- 响应 `code=0`。
- account snapshot `snapshot_valid=true`。
- position 能解析出 `FLAT / ENTERED`。
- balance 能解析 `total_equity_usd` 与 USDT 可用余额。
- open algo orders 能区分 bot-owned 与 external。
- 任何私有接口失败时不得继续真实提交。

最近一次已记录余额：

```text
total_equity_usd: 2115.5507
USDT equity/cash/available: 198.8498
USDT frozen: 0
USDT UPL: 0
```

余额只代表账户可读，不代表允许下单。

## 实盘放行 Go / No-Go

### Go 必须同时满足

- OKX 三个交易环境变量在新进程中存在。
- OKX 公共/私有只读验证全部通过。
- quant 最新 handoff 为 strict-live。
- `execution_allowed=true`。
- `risk_filter_status=pass`。
- `effective_action` 是 entry 或明确允许的保护动作。
- `candidate_package` 存在且未过期。
- `real_order_gate.allowed=true`。
- entry 与 protective stop 预检均 ready。
- live position 与动作匹配。
- 没有 pending 幂等键恢复要求。
- 用户明确说“打开实盘下单”或等价授权，并且启动参数包含 `--submit-real-orders`。

### No-Go 任一出现即阻断

- `effective_action=wait`。
- `execution_allowed=false`。
- `execution_block_reason` 非空。
- `risk_filter_status` 非 pass 且不满足特殊受限 probe。
- `candidate_package: missing`。
- `real_worker: dry_run`。
- `kill_switch` on。
- OKX snapshot invalid。
- 余额、仓位、挂单任一私有接口读取失败。
- 有外部保护单且无法证明 bot 可以接管。
- preflight 缺失或 error。
- 幂等 pending/recovery 未处理。
- Binance source unavailable 且 consensus unreliable。

## 测试矩阵

Bot 侧推荐目标回归：

```powershell
Set-Location D:\开发\eth_trading_bot
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp_pytest_okx_validation tests/test_config.py tests/test_okx_transport.py tests/test_exchange_adapter.py tests/test_real_order_worker_script.py tests/test_run_shadow_preflight_cycle_script.py tests/test_run_manual_entry_cycle_script.py tests/test_bot_runtime_scheduler_script.py tests/test_high_risk_gate.py tests/test_record_manual_close_outcome_script.py tests/test_adopt_protective_stop_script.py tests/test_preview_protective_stop_replace_script.py tests/test_preview_high_risk_handoff_script.py tests/test_watch_protective_stop_replace_script.py
```

量化侧推荐目标回归：

```powershell
Set-Location D:\开发\quant_system_rebuild
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp_pytest_quant_okx_validation tests/test_market_data_consensus.py tests/test_interfaces_live_snapshots.py tests/test_policy_engine.py tests/test_execution_handoff_block_reason.py tests/test_scripts_quant_runtime_scheduler.py
```

Binance 引用审查：

```powershell
rg -n "BINANCE_TRADE|ETHUSDT|Binance|fapi|binance_usdt_perp" D:\开发\eth_trading_bot\scripts D:\开发\eth_trading_bot\src D:\开发\eth_trading_bot\tests D:\开发\eth_trading_bot\README.md
```

验收标准：

- 默认配置与默认脚本入口不应再指向 Binance。
- Binance 测试可以保留，但必须明确是 legacy fallback。
- 文档中旧 Binance 默认口径需要标记为过时或被本文件覆盖。

## 人工操作 SOP

### 日常只读检查

1. 运行 runtime status。
2. 检查 dashboard 是否 running。
3. 检查 quant latest run id 是否更新。
4. 检查 `candidate_package`。
5. 检查 `real_worker` 是否仍为 `dry_run`。
6. 检查 OKX 余额、仓位、挂单是否可读。
7. 若 `effective_action=wait`，结束，不进入实盘放行。

### 准备实盘但不提交

1. 确认 OKX env 存在。
2. 跑目标回归。
3. 跑 shadow/preflight。
4. 查看 entry 与 protective stop request body。
5. 确认 candidate package 未过期。
6. 确认 kill switch off。
7. 仍保持 worker dry run。

### 真正实盘提交

必须额外满足：

- 用户明确授权打开实盘提交。
- 启动命令显式带 `--submit-real-orders` 或 manager 等价参数。
- 当前 candidate package 与刚审查的 handoff 是同一个 run。
- 提交后立即读取 OKX 仓位、挂单、保护止损和 audit log。

未满足以上条件时，不执行真实提交。

## 事故与回滚

### OKX 私有读取失败

- 立刻阻断真实提交。
- 保留 dry run。
- 检查 env、passphrase、新进程、代理、User-Agent、OKX 权限。
- 不得自动切 Binance 下单。

### Candidate 过期或缺失

- 不复用旧 handoff。
- 等待下一轮 quant cycle。
- 不手写 candidate package。

### 幂等 pending

- worker 必须返回 `pending_idempotency_key_requires_recovery`。
- 先查 OKX open order / algo order / fills。
- 人工确认后再清理或恢复。

### 保护止损异常

- 有仓无保护止损时，优先保护性修复，不开新仓。
- 外部保护单存在时，不自动撤。
- bot-owned 保护单语义不匹配时，阻断并人工确认。

### Binance 默认回归

发现默认配置或默认脚本回到 Binance 时：

- 停止实盘 worker。
- 保持或打开 kill switch。
- 修正默认 venue/env/symbol。
- 重跑 `test_config.py`、OKX transport、adapter、worker、scheduler 目标回归。

## 最终验收口径

可以说“代码正常/链路健康”的条件：

- OKX 默认配置正确。
- OKX 只读接口健康。
- 目标测试通过。
- runtime stack 正常运行。
- 当前没有实盘提交权限或没有 candidate 时，worker 安全地保持 dry run / skipped。

不可以说“已经能实盘下单并已开始”的情况：

- `candidate_package: missing`。
- `real_worker: dry_run`。
- `effective_action=wait`。
- `execution_allowed=false`。
- 用户未明确打开实盘提交。

当前状态应表述为：

```text
OKX 数据读取和执行代码链路已校验为健康；当前没有可执行 candidate，real worker 仍是 dry_run，因此尚未开始真实下单。
```
