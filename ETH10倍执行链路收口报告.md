# ETH 10倍执行链路收口报告

日期：2026-05-01

## 当前结论

当前系统已经具备“判断仓 strict-live -> 机器人 shadow -> Binance preflight”的无下单验证链路。

最近一次真实链路输出为：

- `requested_action=wait`
- `effective_action=wait`
- `direction=long`
- `execution_allowed=false`
- `execution_block_reason=not_entry_action`
- `command_targets=[]`
- `preflight_statuses=[]`

解释：当前行情存在 long bias，但不是可执行 entry，因此不允许真实下单。

## 已完成

- 杠杆安全层：机器人侧新增 `ExecutionRiskGate`，按账户风险、止损距离、10 倍杠杆反推可执行仓位。
- 仓位截断：entry payload 使用 `executable_size_pct`，不再直接用信心仓位。
- small probe 限制：`small_probe` 有账户风险与仓位硬上限。
- 状态闭环修复：被拦截 entry 不再污染 observed position。
- Binance preflight：支持真实请求解析和签名，不提交订单。
- preflight 脚本：`scripts/run_shadow_preflight_cycle.py`
- 长时间采样脚本：`scripts/run_shadow_preflight_sampler.py`
- 判断仓 Route C：live orderbook 已接入 FeatureMatrix。
- 判断仓成本扣减：fee、slippage、funding 进入净 edge，负 edge 禁入。
- 判断仓 contrarian short 出口：`consensus=neutral + setup=short + trigger=short + crowding=true` 可放 `small_probe`。
- 判断仓 ATR/波动止损：替代固定止损模板。
- 路径迁移：运行默认路径适配 `D:\开发\quant_system_rebuild` 和 `D:\开发\eth_trading_bot`。

## 仍未进入真实下单的原因

当前不是代码阻塞，而是最近真实行情输出为 `wait`：

- `execution_allowed=false`
- `execution_block_reason=not_entry_action`
- `place_entry_order=false`

在这个状态下，即使方向为 long，也只能理解为 bias，不是交易指令。

## 上线前必须满足

真实下单前至少要看到一次完整 preflight 成功样本：

- `effective_action=small_probe` 或 `entry_long/entry_short`
- `execution_allowed=true`
- `place_entry_order=true`
- `executable_size_pct > 0`
- `stop_distance_pct > 0`
- `preflight_statuses` 包含 `preflight_ready`
- entry preflight 有 `quantity` 和 `newClientOrderId`
- stop preflight 有 `stopPrice` 和 `closePosition=true`

## 长时间采样命令

建议先采样 24 小时，每 15 分钟一次：

```powershell
Set-Location D:\开发\eth_trading_bot
$env:PYTHONDONTWRITEBYTECODE='1'
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe scripts\run_shadow_preflight_sampler.py --samples 96 --interval-sec 900 --quant-root D:\开发\quant_system_rebuild --sample-root C:\Users\左秋三\.codex\memories\eth_bot_shadow_preflight_samples --cycle-output-root C:\Users\左秋三\.codex\memories\eth_bot_shadow_preflight_cycles --proxy-url http://127.0.0.1:7897
```

快速烟测一轮：

```powershell
Set-Location D:\开发\eth_trading_bot
$env:PYTHONDONTWRITEBYTECODE='1'
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe scripts\run_shadow_preflight_sampler.py --samples 1 --interval-sec 1 --quant-root D:\开发\quant_system_rebuild --sample-root C:\Users\左秋三\.codex\memories\eth_bot_shadow_preflight_samples_smoke --cycle-output-root C:\Users\左秋三\.codex\memories\eth_bot_shadow_preflight_cycles_smoke --proxy-url http://127.0.0.1:7897
```

采样输出：

- `samples.jsonl`
- 每轮独立 `audit.jsonl`
- 每轮独立 `state.json`

## 已验证

- 机器人仓全量：`151 passed`
- 判断仓关键套件：`78 passed, 2 deselected`
- 真实 preflight 链路：跑通
- `git diff --check`：两仓通过，仅 LF/CRLF warning

说明：`2 deselected` 是 artifact 写文件权限相关用例，不是策略链路失败。

## 不建议现在做

- 不建议直接切真实下单。
- 不建议继续大拆 `decision_engine.py` 或 `exchange_adapter.py`，当前更需要真实采样证据。
- 不建议把项目移出 `D:\开发`，路径已经适配。
