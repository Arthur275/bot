# Runtime Stack Manager Plan

## 目标

建立一个统一的本地运行栈管理器，用一个入口管理 ETH 自动化运行链路的启动、停止和状态检查。

目标入口：

```powershell
.\scripts\manage_runtime_stack.ps1 start
.\scripts\manage_runtime_stack.ps1 stop
.\scripts\manage_runtime_stack.ps1 status
```

默认模式必须是安全模式：只运行样本采集、量化判断、Bot 调度、候选执行包、预检和看板；不提交真实订单。

真实下单必须显式开启：

```powershell
.\scripts\manage_runtime_stack.ps1 start -EnableRealOrders
```

## 范围

运行栈包含五个子系统：

| 子系统 | 入口 | 前端面板 | 职责 |
| --- | --- | --- | --- |
| Factor ingest | `quant_runtime_scheduler.py ingest-summary --loop` | Factor | 汇总已落盘 cycles，更新 factor summary、DuckDB、lookup/样本健康度 |
| Quant judgement | `quant_runtime_scheduler.py run-cycle --loop` | Quant | 运行 strict-live 市场判断，生成 handoff / decision artifact |
| Bot scheduler | `bot_runtime_scheduler.py loop` | Bot | 消费 quant handoff，运行 bot planning / preflight，生成 candidate execution package |
| Real order worker | `real_order_worker.py run-once` 循环或受控调度 | Bot | 消费 candidate package，写 skipped / blocked / submitted audit；默认不真实提交 |
| Dashboard | `python -m dashboard.app` | 全部 | 只读 HTTP 看板，聚合 runtime artifact |

Dashboard server 已存在：

```powershell
python -m dashboard.app --host 127.0.0.1 --port 8765
```

因此不需要再补 Flask 或 `python -m http.server`。需要做的是把 `dashboard.app` 纳入统一管理器，并确保它由独立进程常驻运行。

## 链路关系

数据链路：

```text
quant run-cycle
  -> runtime/cycles/<run_id>/handoff.json
  -> dashboard Quant 面板

quant ingest-summary
  -> runtime/analysis/factor_ingest_latest.json
  -> runtime/analysis/factor_summary.json
  -> runtime/analysis/quant_analysis.duckdb
  -> dashboard Factor 面板

bot scheduler
  -> runtime/bot_runtime_scheduler/latest_cycle.json
  -> runtime/bot_runtime_scheduler/latest_candidate_execution_package.json
  -> dashboard Bot 面板

real order worker
  -> runtime/real_order_worker/audit.jsonl
  -> dashboard Bot worker events
```

Dashboard 是观察层，不参与决策、不提交订单。

## 启动顺序

统一管理器必须定义启动顺序，不能只并发打开所有进程。

建议顺序：

1. 启动 Dashboard。
2. 启动 Factor ingest。
3. 启动 Quant judgement。
4. 等待 quant handoff 或 quant heartbeat 出现并新鲜。
5. 启动 Bot scheduler。
6. 等待 candidate package 或 bot latest cycle 出现。
7. 根据安全边界决定是否启动 Real order worker。

如果上游没有产物，`status` 必须显示链路断点，例如：

```text
quant judgement: running, handoff stale
bot scheduler: running, waiting for fresh handoff
real worker: stopped, candidate package missing
```

## 安全边界

默认行为：

```text
real order submission: disabled
```

默认模式下，worker 即使运行，也只能产生 `skipped` 或 `blocked` audit，不能提交真实订单。

真实提交必须同时满足：

- 启动命令显式传入 `-EnableRealOrders`
- kill switch 未开启
- candidate package 存在且未过期
- real order gate allowed
- automation boundary 是 `real_order_submission_allowed`
- runtime snapshot 有效
- action 与当前持仓状态合法
- 幂等锁和 worker lock 通过
- 保护止损相关检查通过

Kill switch 文件：

```text
D:\开发\eth_trading_bot\runtime\controls\disable_real_execution.flag
```

如果 kill switch 存在：

- 统一管理器仍可启动 Dashboard、Factor ingest、Quant judgement、Bot scheduler。
- Real order worker 不应以真实提交模式启动。
- `status` 必须显式显示 `kill_switch=enabled`。

## 状态检查

`status` 不能只检查进程是否存在。必须同时检查进程、HTTP、runtime artifact 和日志。

建议信号：

| 信号 | 目的 |
| --- | --- |
| PID 文件是否存在 | 定位由管理器启动的进程 |
| PID 是否仍存活 | 发现进程退出 |
| PID 命令行是否匹配 | 避免 PID 复用误判 |
| stale PID 清理 | crash 后避免残留 PID 误导 status |
| Dashboard `/` HTTP 200 | 确认可打开页面 |
| Dashboard `/api/overview` HTTP 200 | 确认数据 API 可用 |
| Factor heartbeat / ingest timestamp | 判断样本/因子链是否新鲜 |
| Quant heartbeat / handoff timestamp | 判断市场判断是否新鲜 |
| Bot heartbeat / latest cycle timestamp | 判断 bot scheduler 是否新鲜 |
| latest cycle run_id 是否推进 | 发现进程活着但卡住 |
| candidate package timestamp / expiry | 判断 worker 是否有可消费对象 |
| worker audit timestamp | 判断执行器是否在工作 |
| worker audit 最近状态 | 区分 skipped / blocked / submitted / error |
| 日志最后 N 行错误率 | 发现 heartbeat 新鲜但每轮失败 |
| kill switch | 真实下单总闸门 |

建议输出分层：

```text
dashboard: ok http=200 api=200
factor_ingest: ok pid=... age=...
quant_judgement: degraded pid=... handoff_age=... latest_run_id=...
bot_scheduler: waiting_for_handoff pid=...
real_worker: disabled kill_switch=false submit_real_orders=false
```

## 进程与日志

统一管理器应为每个子系统维护独立 PID 和日志。

建议目录：

```text
D:\开发\eth_trading_bot\runtime\stack_manager\
  pids\
    dashboard.pid
    factor_ingest.pid
    quant_judgement.pid
    bot_scheduler.pid
    real_worker.pid
  logs\
    dashboard_stdout.log
    dashboard_stderr.log
    factor_ingest_stdout.log
    factor_ingest_stderr.log
    quant_judgement_stdout.log
    quant_judgement_stderr.log
    bot_scheduler_stdout.log
    bot_scheduler_stderr.log
    real_worker_stdout.log
    real_worker_stderr.log
```

`stop` 必须只停止由这些 PID 文件标记、且命令行匹配的进程，避免误杀无关 Python 进程。

## Docker 判断

当前不需要 Docker Desktop。

理由：

- 现有链路已经基于 Windows venv、PowerShell、WSL/本地代理运行。
- Docker 会引入 API key、proxy、文件挂载、网络和交易环境重新配置。
- 当前主要问题是本地进程编排和状态治理，不是环境封装。

后续只有在需要长期无人值守、部署到固定主机或隔离依赖时，再评估 Docker 或 Windows Task Scheduler。

## 实施步骤

1. 新增 `scripts/manage_runtime_stack.ps1` 和 `.cmd` 包装入口。
2. 定义子系统配置表：名称、启动命令、工作目录、PID 文件、stdout/stderr 日志、健康检查。
3. 实现 `start`：按依赖顺序启动，写 PID，输出 dashboard URL。
4. 实现 `status`：检查 PID、HTTP、artifact freshness、run_id 推进、日志错误、kill switch。
5. 实现 `stop`：按反向顺序停止 worker、bot scheduler、quant judgement、factor ingest、dashboard。
6. 加默认安全模式：不传 `-EnableRealOrders` 时禁止真实提交。
7. 加 kill switch 前置逻辑：kill switch 存在时不启动真实提交 worker。
8. 加测试：PowerShell 静态解析、路径契约、dashboard API、status artifact 解析。

## 验收标准

- 一条命令可启动本地看板和三块运行链路。
- 浏览器可稳定打开 `http://127.0.0.1:8765`。
- `status` 能识别：
  - dashboard 不可访问
  - quant handoff stale
  - bot scheduler 停止
  - candidate package missing / expired
  - worker audit stale
  - kill switch enabled
  - PID stale
  - 日志连续失败
- 默认启动不会真实下单。
- 真实下单只能通过显式 `-EnableRealOrders` 进入，并仍受 kill switch 和 worker 内部 gate 约束。
