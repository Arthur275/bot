# Bot Automation v1 Final Implementation Plan

本文档是 bot 自动开仓、减仓、平仓、止盈止损自动化的最终落地方案。它合并当前代码事实、DS 审查意见和本轮设计结论，作为后续开发唯一执行清单。

## 目标

Bot Automation v1 的目标是让 bot 在量化仓库给出明确执行合同时，自动完成真实交易动作：

- 自动开仓：`entry_long` / `entry_short` / `small_probe`
- 自动保护：入场后必须建立或确认 protective stop
- 自动减仓和平仓：`reduce` / `exit`
- 自动风险收紧：breakeven / trailing / protective stop replace
- 自动记录：audit log 是事实源，DuckDB 是查询索引

本版本不允许 bot 自己发明策略语义。bot 只消费 quant handoff 和已有 HighRiskHandoff，不解释 `sizing_tier`，不把 `tp_ladder` 自行转成交易动作，除非 quant 后续明确输出可执行 action。

## 当前代码事实

已存在：

- `ExecutionLayerState`
  - `IDLE`
  - `ENTRY_PENDING`
  - `POSITION_OPEN`
  - `REDUCE_PENDING`
  - `EXIT_PENDING`
  - `RECONCILING`
  - `DEGRADED`
  - `BLOCKED`
- `ShadowOrchestrator.run_cycle()`
  - 已能消费 quant handoff
  - 已能构建 execution plan
  - 已能构建 execution commands
  - 已能在 `RuntimeMode.REAL` 下调用 real adapter
- `HighRiskGate`
  - 已覆盖 `trailing` / `reduce` / `exit`
  - 已校验 expiry、runtime mode、strict-live、symbol、position、risk filter、snapshot、lock、kill switch
- `CommandExecutionResult`
  - 当前关键执行字段多数藏在 `details`
  - 顶层字段不足，不适合作为 DuckDB ingest 稳定 schema
- `audit_logger.py`
  - 已记录 execution results
  - 应继续作为事实源
- bot 仓库当前没有 DuckDB 基础设施
  - 没有 `src/bot/analysis/duckdb_store.py`
  - 没有 `src/bot/analysis/runtime_dataset.py`

## 非目标

Bot Automation v1 不做以下事情：

- 不复制 quant 的 research / policy / factor 逻辑
- 不直接 import quant 的 DuckDB 工具
- 不建立第二套并行 execution ledger 作为事实源
- 不让 bot 从 `tp_ladder` 自行推导止盈单
- 不让 bot 从 `reduce_conditions` 自行决定减仓
- 不在 `sample-fallback` 下真实交易
- 不在 `DEGRADED` / `RECONCILING` / `BLOCKED` 状态下提交新 entry
- 不支持非 ETH、非 ETHUSDT、非 10x perpetual

## 总体架构

```text
quant strict-live
  -> handoff / HighRiskHandoff
  -> bot runtime scheduler
  -> ShadowOrchestrator
  -> PositionManager
  -> ExchangeAdapter / BinancePerpAdapter
  -> AuditLogger
  -> bot DuckDB ingest
  -> runtime summaries
```

事实源和索引边界：

- audit log 是事实源
- runtime state 是当前恢复点
- DuckDB 是查询索引
- markdown/json summary 是只读报告

## 两层状态机

### ExecutionLayerState

`ExecutionLayerState` 继续表示仓位和执行层事实状态。它由 `state_store.py` 维护，不被替换。

含义：

- 当前是否空仓
- 当前是否持仓
- 是否有 entry/reduce/exit pending
- 是否需要 reconciliation
- 是否处于 degraded/blocked

### AutomationState

新增 `AutomationState`，只描述自动化流程进度，不覆盖 `ExecutionLayerState`。

建议枚举：

```python
class AutomationState(str, Enum):
    DISABLED = "disabled"
    OBSERVING = "observing"
    ENTRY_PREFLIGHT_READY = "entry_preflight_ready"
    ENTRY_SUBMITTING = "entry_submitting"
    ENTRY_SUBMITTED = "entry_submitted"
    POSITION_PROTECTED = "position_protected"
    REDUCE_PREFLIGHT_READY = "reduce_preflight_ready"
    EXIT_PREFLIGHT_READY = "exit_preflight_ready"
    HIGH_RISK_SUBMITTING = "high_risk_submitting"
    ACTION_BLOCKED = "action_blocked"
    ACTION_FAILED = "action_failed"
```

含义：

- 自动化是否打开
- 当前是否只是观察
- 是否预检通过
- 是否正在提交真实订单
- 是否已经提交
- 是否被安全闸阻断

### 状态机互斥规则

两个状态机必须有硬互斥规则，避免 reconcile 和 submit 同时发生。

强制规则：

- `ExecutionLayerState == RECONCILING` 时，`AutomationState` 强制为 `ACTION_BLOCKED`
- `ExecutionLayerState == BLOCKED` 时，`AutomationState` 强制为 `ACTION_BLOCKED`
- `ExecutionLayerState == DEGRADED` 时，`AutomationState` 只能是 `OBSERVING` 或 `ACTION_BLOCKED`
- `AutomationState in {ENTRY_SUBMITTING, HIGH_RISK_SUBMITTING}` 时，`ExecutionLayerState` 禁止为 `RECONCILING` / `BLOCKED` / `DEGRADED`

允许组合：

| ExecutionLayerState | AutomationState | 含义 |
|---|---|---|
| `IDLE` | `OBSERVING` | 空仓观察 |
| `IDLE` | `ENTRY_PREFLIGHT_READY` | 入场预检通过，等待自动执行开关 |
| `ENTRY_PENDING` | `ENTRY_SUBMITTING` | 正在提交 entry |
| `ENTRY_PENDING` | `ENTRY_SUBMITTED` | entry 已提交，等待交易所确认 |
| `POSITION_OPEN` | `POSITION_PROTECTED` | 持仓且保护已确认 |
| `POSITION_OPEN` | `REDUCE_PREFLIGHT_READY` | 减仓预检通过 |
| `POSITION_OPEN` | `EXIT_PREFLIGHT_READY` | 平仓预检通过 |
| `REDUCE_PENDING` | `HIGH_RISK_SUBMITTING` | 正在提交减仓 |
| `EXIT_PENDING` | `HIGH_RISK_SUBMITTING` | 正在提交平仓 |
| `RECONCILING` | `ACTION_BLOCKED` | 对账中，禁止新动作 |
| `BLOCKED` | `ACTION_BLOCKED` | 被阻断 |
| `DEGRADED` | `OBSERVING` | 降级观察 |
| `DEGRADED` | `ACTION_BLOCKED` | 降级阻断 |

其他组合默认非法，运行时应降级为 `ACTION_BLOCKED` 并记录 reason code。

## CommandExecutionResult 顶层字段

当前 `CommandExecutionResult.details` 已包含不少执行信息，但 DuckDB ingest 不应依赖 `details` 里的动态 key。

需要扩展模型：

```python
class CommandExecutionResult(BaseModel):
    target: str
    status: str
    accepted: bool = True
    simulated: bool = True
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    idempotency_key: str = ""
    client_order_id: str = ""
    exchange_order_id: str = ""
    error_kind: str = ""
```

填充规则：

- `idempotency_key`
  - 来自 `ExecutionCommand.idempotency_key`
- `client_order_id`
  - entry/reduce/exit 来自 prepared request `newClientOrderId`
  - algo protective stop 来自 `clientAlgoId`
- `exchange_order_id`
  - 真实下单后从 Binance response `orderId` / `algoId` 提取
- `error_kind`
  - transport 错误使用 `timeout` / `http_error` / `json_error`
  - mapping 错误使用 `unsafe_request_mapping`
  - config/signing 错误使用 `request_config_error`

`details` 继续保留完整原文，用于审计和故障排查，但结构化分析只依赖顶层稳定字段。

## Audit Log 事实源

不新增并行 execution ledger。原因：

- audit log 已经记录 `execution_results`
- 并行 ledger 容易和 audit log 出现不一致
- DuckDB 可以从 audit log 派生结构化索引

audit event 必须包含：

- `runtime_mode`
- `engine_mode`
- `judgement`
- `handoff`
- `runtime_snapshot_before`
- `execution_plan`
- `automation_state`
- `execution_commands`
- `execution_results`
- `runtime_snapshot_after`
- `state`
- `reason_codes`

真实执行相关字段必须通过 `CommandExecutionResult` 顶层字段进入 audit。

## Bot DuckDB 设计

bot 仓库自建 DuckDB 基础设施，不 import quant。

新增模块：

```text
src/bot/analysis/__init__.py
src/bot/analysis/duckdb_store.py
src/bot/analysis/runtime_dataset.py
```

默认数据库：

```text
runtime/analysis/bot_runtime.duckdb
```

最小表：

### bot_cycles

每个 bot runtime cycle 一行。

关键字段：

- `run_id`
- `generated_at`
- `runtime_mode`
- `engine_mode`
- `symbol`
- `exchange_symbol`
- `quant_action`
- `bot_effective_action`
- `execution_layer_state`
- `automation_state`
- `blocked`
- `degraded`
- `reason_codes`
- `audit_log_path`
- `state_path`

### bot_command_samples

每个 command/result 一行。

关键字段：

- `run_id`
- `generated_at`
- `target`
- `operation`
- `status`
- `accepted`
- `simulated`
- `reason`
- `idempotency_key`
- `client_order_id`
- `exchange_order_id`
- `error_kind`
- `command_type`
- `runtime_mode`

### bot_runtime_summaries

每轮 runtime snapshot 和执行摘要一行。

关键字段：

- `run_id`
- `generated_at`
- `position_state_before`
- `position_direction_before`
- `position_size_pct_before`
- `protective_stop_present_before`
- `position_state_after`
- `position_direction_after`
- `position_size_pct_after`
- `protective_stop_present_after`
- `primary_target`
- `primary_status`
- `has_primary_failure`
- `has_auxiliary_failure`

## 自动开仓规则

只有全部满足才允许真实 entry：

- 显式启用真实自动执行：`--enable-real-orders`
- `runtime_mode == real`
- `engine_mode == strict-live`
- quant action in `entry_long` / `entry_short` / `small_probe`
- `execution_allowed == true`
- `risk_filter_status == pass`
- `runtime_snapshot.snapshot_valid == true`
- Binance 当前仓位为 FLAT
- `ExecutionLayerState` 不在 `RECONCILING` / `BLOCKED` / `DEGRADED`
- entry preflight ready
- protective stop command 可构建
- protective stop 不允许被静默跳过
- kill switch 不存在
- idempotency key 未执行过
- `CommandExecutionResult.client_order_id` 可生成

开仓提交后：

- 先记录 `AutomationState=ENTRY_SUBMITTING`
- 提交 entry
- 刷新 runtime snapshot
- 提交或确认 protective stop
- 若 protective stop 失败，进入 recovery/degraded，不允许扩大仓位

## 自动减仓和平仓规则

减仓和平仓必须走 HighRiskGate，不允许只靠普通 `ExecutionHandoff` 自由执行。

允许真实 reduce/exit 的条件：

- `HighRiskGate.allowed == true`
- `runtime_mode == real`
- `engine_mode == strict-live`
- handoff 未过期
- position state 为 ENTERED
- direction 与交易所仓位一致
- runtime snapshot valid
- 无 kill switch
- 无 high-risk lock
- handoff_id 未执行过
- idempotency key 未执行过

提交前：

- 写 high-risk lock
- `AutomationState=HIGH_RISK_SUBMITTING`

提交后：

- 刷新 runtime snapshot
- 更新 state_store
- 清理 lock
- 记录 audit

失败时：

- 保留失败 audit
- 清理或标记 lock 状态
- 进入 `ACTION_FAILED` 或 `ACTION_BLOCKED`
- 不重试无限次

## 自动止盈止损规则

保护止损、保本、移动止损分三类：

- protective stop
  - 入场后必须存在
  - 可由 entry 流程维护
- breakeven
  - 只能收紧风险
  - 不允许放宽 stop
- trailing
  - 必须 HighRiskGate allowed
  - 必须有 lock stage
  - 必须验证不会降低当前保护强度

`tp_ladder` 当前只作为展示和审计字段，不自动解释为 Binance 止盈单。若未来 quant 输出明确 `take_profit` action，再新增独立 HighRiskHandoff action。

## Runtime Scheduler

正式入口应是一个 bot runtime scheduler，不再依赖临时脚本。

建议命令：

```text
python scripts/bot_runtime_scheduler.py loop --interval-sec 300
```

真实自动执行必须显式加：

```text
--enable-real-orders
```

默认行为：

- 自动消费 quant strict-live
- 自动生成 execution plan
- 自动 preflight
- 自动写 audit
- 自动 ingest DuckDB
- 不提交真实订单

启用真实订单后：

- entry 走自动开仓规则
- reduce/exit/trailing 走 HighRiskGate
- 任一硬闸失败则只记录 `ACTION_BLOCKED`

## 工程质量要求

### 可维护性

实现必须保持模块边界清晰：

- `state_store.py` 只维护执行层事实状态和恢复点
- `AutomationState` 单独放置，不塞进策略 action enum
- `orchestrator.py` 只编排，不直接写 DuckDB
- `exchange_adapter.py` 只负责命令映射、预检和交易所请求
- `audit_logger.py` 继续做事实源写入，不承担查询聚合职责
- `src/bot/analysis/*` 只从 audit 派生索引和摘要

禁止事项：

- 禁止在 scheduler 里写大段交易决策逻辑
- 禁止在 DuckDB ingest 里重新解释策略语义
- 禁止用散落的 `details.get(...)` 作为长期 schema
- 禁止让同一执行事实同时写入两个事实源
- 禁止用字符串拼接 SQL 写动态表名或列名

### 性能

性能目标是长期 5 分钟循环稳定运行，不因日志和索引增长退化。

要求：

- 每轮只 ingest 新 audit/cycle，不全量重建 DuckDB
- DuckDB 表必须有 `run_id`、`generated_at`、`idempotency_key`、`target` 相关索引
- JSON 原文只在 audit 中保存，DuckDB 只保留查询需要的结构化字段
- scheduler heartbeat 和 latest 文件使用小 JSON，避免写入巨型 payload
- runtime summary 生成允许读取 DuckDB 聚合，不反复扫描全部 audit 原文
- 单轮失败不阻塞后续循环，连续失败进入 degraded heartbeat
- 查询报告和 ingest 分离，避免交易路径等待重型统计报告

性能红线：

- 真实下单路径不能等待全量 summary
- 真实下单路径不能依赖 DuckDB 写入成功
- DuckDB 写入失败只能阻断分析索引，不能伪造交易成功或失败
- 网络重试必须有上限，不能在 5 分钟循环里无限阻塞

### 代码风格

- 复用现有 pydantic model 风格
- 新 enum 必须有明确测试覆盖
- 新 CLI 参数必须有默认安全值
- 新文件路径必须在 `runtime/` 下可配置
- 新增依赖必须可选，缺 DuckDB 时给出清晰错误，不影响核心 bot 运行
- 所有真实执行开关命名必须显式包含 `real` 或 `orders`

## 开发阶段

### Phase 1: 文档和模型基础

- 新增本文档
- 新增 `AutomationState`
- 定义状态机互斥函数
- 扩展 `CommandExecutionResult` 顶层字段
- 补测试覆盖字段序列化和向后兼容

### Phase 2: Audit 扩展

- 在 orchestrator audit payload 中加入 automation state
- audit 中区分 runtime snapshot before / after
- adapter 返回结果时填充顶层执行字段
- 测试 `idempotency_key` / `client_order_id` / `exchange_order_id` / `error_kind`

### Phase 3: Bot DuckDB

- 新增 bot 自有 DuckDB store
- 新增 runtime dataset ingest
- 从 audit log 派生三张表
- 写 `bot_runtime_summary.json` / `.md`
- 测试重复 ingest 幂等

### Phase 4: Scheduler 稳定化

- scheduler 单实例运行
- `start/status/stop` PowerShell 管理脚本
- 心跳、latest cycle、日志路径稳定
- 禁止重复进程
- 测试 loop 失败降级和 latest 状态

### Phase 5: 自动执行开关

- 新增 `--enable-real-orders`
- 默认 dry-run/preflight only
- 显式开关才可真实提交
- entry 提交前检查所有硬闸
- reduce/exit/trailing 接入 HighRiskGate
- 测试所有阻断路径

### Phase 6: 横向审查和实盘前门槛

- 横向跑 orchestrator / exchange_adapter / high_risk_gate / state_store / scheduler 测试
- 审查 sample-fallback 不进入真实执行
- 审查 degraded 不允许新 entry
- 审查 reconcile 不允许 submit
- 审查重复 idempotency 不重复下单
- 审查 protective stop 失败后进入 recovery

## 测试矩阵

必须覆盖：

- `AutomationState` 与 `ExecutionLayerState` 合法组合
- 非法组合自动降级 `ACTION_BLOCKED`
- `CommandExecutionResult` 顶层字段 model dump
- adapter 模拟成功填充 `idempotency_key`
- real/preflight 生成 `client_order_id`
- exchange response 生成 `exchange_order_id`
- mapping/transport/config 错误生成 `error_kind`
- audit payload 包含 automation state
- DuckDB ingest 不依赖 `details`
- DuckDB ingest 幂等
- scheduler 默认不提交订单
- scheduler 未加 `--enable-real-orders` 时阻断真实执行
- `sample-fallback` + real 被拒绝
- `DEGRADED` 不允许 entry
- `RECONCILING` 不允许 entry submit
- `BLOCKED` 不允许任何新提交
- entry 缺 protective stop 被拒绝
- reduce/exit/trailing 未过 HighRiskGate 被拒绝
- HighRisk lock 存在时阻断
- kill switch 存在时阻断
- 重复 handoff_id / idempotency_key 被拒绝
- DuckDB 不可用时核心 bot cycle 不崩溃
- summary 查询不会阻塞真实执行路径

## 每次完成后的审查要求

每个 phase 完成后必须做两类审查。

### 本地审查

- `git status --short`
- 只确认本 phase 相关文件变化
- 检查是否误改 quant 逻辑
- 检查是否误改旧策略语义
- 检查是否新增不必要依赖
- 检查是否把 `details` 当结构化主索引
- 检查是否把重型统计放进真实执行路径
- 检查是否引入全量扫描或无界重试

### 横向审查

至少覆盖：

- `tests/test_shadow_orchestrator.py`
- `tests/test_exchange_adapter.py`
- `tests/test_high_risk_gate.py`
- `tests/test_state_store.py`
- `tests/test_bot_runtime_scheduler_script.py`
- 新增 DuckDB/runtime dataset 测试

审查问题清单：

- 是否仍然 ETH-only / 10x-only
- 是否仍然只消费 strict-live 做真实执行
- 是否仍然不解释 `sizing_tier`
- 是否仍然不解释 `tp_ladder` 为交易动作
- 是否有重复下单可能
- 是否有 protective stop 空窗
- 是否有 reconcile 和 submit 并发可能
- 是否有 audit 与 DuckDB 不一致可能
- 是否有 DuckDB 故障影响交易事实记录可能
- 是否有 scheduler 重复进程或重复下单可能

## 完成定义

Bot Automation v1 只有在以下全部满足后才算完成：

- `AutomationState` 已实现并测试
- 状态机互斥规则已实现并测试
- `CommandExecutionResult` 顶层字段已实现并测试
- audit log 仍是唯一事实源
- bot DuckDB ingest 从 audit 派生
- scheduler 稳定单实例运行
- 默认不真实下单
- `--enable-real-orders` 显式开关可控
- entry 自动执行硬闸全部测试通过
- reduce/exit/trailing HighRiskGate 路径全部测试通过
- 横向测试通过
- 实盘前检查清单无 gap
- 可维护性审查无阻断项
- 性能审查无阻断项
