# ETH Trading Bot

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

测试 watcher 一轮后退出：

```powershell
D:\开发\eth_trading_bot\scripts\start_protective_stop_watch_readonly.cmd -MaxIterations 1
```

只读 watcher 明确不会调用撤单/下单端点。看到下面输出代表处于只读监控：

```text
Mode: read-only. No cancel/place endpoint is called.
```

## 保护止损流程

当前第一版目标是：

```text
原始保护止损 -> 达标后自动推进到保本/小幅锁盈
```

核心原则：

- 所有替换动作必须先 preview
- 真实替换必须走 cancel verify -> place verify -> state write
- 本地只允许收紧保护，不允许放宽止损
- 如果交易所已有多张冲突保护单，停止自动处理并要求人工确认
- 保护止损丢失、state 不一致、网络异常都必须进入审计日志

当前启动脚本默认是只读 watcher，用于观察是否达到替换条件，不会撤单或下单。

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
- 保护止损 preview / adopt / watch 脚本
- 只读 watcher Windows 启动脚本
- 对应 pytest 覆盖

仍建议分阶段推进：

1. 多阶段锁盈
2. 保护止损丢失时自动补单
3. trailing stop 接管
4. reduce / exit 自动执行
5. supervisor 常驻和异常通知

越往后风险越高，必须单独做 review 和实盘前演练。
