# System Design

## 1. 设计目标

本项目是 `D:\quant_system_rebuild` 的独立执行壳层。

目标只有四个：
- 安全消费现有 judgement / handoff
- 在弱网条件下保持可降级、可恢复
- 把策略动作与执行状态严格分层
- 在真实下单前先完成 shadow / replay / review 收口

## 2. 设计约束

### 硬约束
- 只支持 ETH
- 只支持 10x perpetual
- 15m 是唯一主决策周期
- 5m 只做持仓后风控辅助
- 不复制 research / feature / policy 代码
- 不新增第二套策略语义

### 软约束
- 代理、端口、网络波动是常态
- 交易所状态和本地状态可能短时不一致
- 任意降级分支都必须优先保护已开仓风险

## 3. 外部依赖边界

### 3.1 决策内核依赖
来自 `D:\quant_system_rebuild`：

1. `run_live_judgement(...)`
   - 产出统一 judgement payload
   - 关键字段：`status / mode / entry_mode / research_bundle / decision / diagnostic / issues`

2. `ensure_research_bundle_ready(...)`
   - 产出 research readiness
   - 关键字段：`ready / research_bundle / issues / whitelist_paths / all_results_paths`

3. `build_execution_handoff(...)`
   - 产出执行契约
   - 关键字段：
     - `action`
     - `direction`
     - `position_state`
     - `position_size_pct`
     - `position_cap_pct`
     - `risk_filter_status`
     - `runtime_vetoes`
     - `degrade_flags`
     - `initial_stop_loss`
     - `breakeven_trigger`
     - `trailing_rule`
     - `tp_ladder`
     - `invalidate_conditions`
     - `reduce_conditions`
     - `diagnostic_*`

### 3.2 本项目不接管的内容
- research bundle 生产
- snapshot feature 组装
- overlay 策略逻辑
- policy decision 生成
- observation comparison 逻辑

这些都继续留在原仓。

## 4. 顶层架构

```text
15m orchestrator
  ├─ engine_client
  │   ├─ run_live_judgement(strict-live)
  │   └─ build_execution_handoff(...)
  ├─ network_guard
  ├─ state_store
  ├─ position_manager
  ├─ exchange_adapter
  └─ audit_logger

5m risk loop
  ├─ network_guard
  ├─ state_store
  ├─ position_manager
  ├─ exchange_adapter
  └─ audit_logger
```

## 5. 模块职责

### 5.1 `config.py`
用途：
- 固定 ETH-only / 10x-only / 15m-main / 5m-assist 口径
- 管理代理地址、执行模式、风控开关、shadow 开关

要求：
- 禁止在运行时切到其他 symbol
- 禁止把 leverage 或 instrument scope 做成开放输入

### 5.2 `engine_client.py`
用途：
- 调用原仓 `run_live_judgement(...)`
- 将 judgement 转换为本项目内部可消费对象
- 调用 `build_execution_handoff(...)`

要求：
- 明确区分 `strict-live` 与 `sample-fallback`
- 默认真实主循环只接受 `strict-live`
- 所有 diagnostic 字段必须原样保留进入审计日志

### 5.3 `network_guard.py`
用途：
- 汇总 transport / data_source / pipeline / readiness 诊断
- 决定本轮是否允许新 entry
- 决定是否转入 `degraded` 或 `blocked`

输入：
- judgement `status`
- `diagnostic`
- handoff `risk_filter_status`
- handoff `degrade_flags`
- research bundle readiness

输出：
- `allow_entry`
- `allow_reduce`
- `allow_exit`
- `degraded`
- `blocked`
- `reason_codes`

### 5.4 `state_store.py`
用途：
- 保存本地执行状态
- 保存最近一次 judgement / handoff / execution result
- 保存 open position / open orders / pending action / recovery marker

要求：
- 读写幂等
- 能表达“交易所已成交但本地未确认”的中间态
- 能表达“网络恢复后待对账”的恢复态

### 5.5 `position_manager.py`
用途：
- 消费 handoff 与真实仓位状态
- 生成 execution plan
- 决定何时 entry / reduce / exit / hold

职责边界：
- 它不生成市场观点
- 它只决定如何安全执行已有观点

### 5.6 `exchange_adapter.py`
用途：
- 查询仓位
- 查询挂单
- 下 entry
- 下 protective stop
- 下 reduce
- 下 exit
- 撤销不再有效的非保护性挂单

要求：
- 首版只实现最小动作集合
- 所有下单动作必须幂等
- 任何动作前先检查是否会破坏现有 protective stop

### 5.7 `audit_logger.py`
用途：
- 记录每轮输入、判断、执行计划、真实执行结果、降级原因、恢复行为

要求：
- 区分策略层动作与执行层状态
- 保留 diagnostic 原文
- 能支持后续 shadow/review 抽查

### 5.8 `orchestrator.py`
用途：
- 组织 15m 主循环
- 组织 5m 风险辅助循环
- 串联 network guard、position manager、exchange adapter 和 audit logger

## 6. 状态分层

### 6.1 策略层动作
来自 handoff，不可改写。

当前应直接遵守 `PositionAction`：
- `entry_long`
- `entry_short`
- `wait`
- `reduce`
- `exit`
- `observe_only`
- `paper_only`
- `small_probe`

其中首版执行闭环先覆盖：
- `entry_long`
- `entry_short`
- `wait`
- `reduce`
- `exit`

`observe_only / paper_only / small_probe` 需要单独定义执行映射，不能被隐式折叠成普通 entry。

### 6.2 执行层状态
本地运行时状态：
- `idle`
- `entry_pending`
- `position_open`
- `reduce_pending`
- `exit_pending`
- `degraded`
- `blocked`

### 6.3 关键原则
- 执行异常不覆盖策略语义
- 对账延迟不等于策略切换
- 风险处理优先级高于新开仓

## 7. 15m 主循环

```text
start cycle
  -> engine_client 拉取 strict-live judgement
  -> readiness / diagnostic / handoff 校验
  -> network_guard 评估是否可执行
  -> exchange_adapter 拉取真实 position + orders
  -> position_manager 生成 execution plan
  -> audit_logger 落盘本轮审计
  -> 若为 shadow mode: 结束
  -> 若为 real mode: 执行 entry/reduce/exit/protective actions
  -> 更新 state_store
```

### 15m 主循环规则
- 每轮只允许一个主判断来源
- 没有 `strict-live` judgement 时，不执行新 entry
- `blocked` 时不进入交易动作
- `degraded` 时只允许风险收敛动作

## 8. 5m 风险辅助循环

触发条件：
- 已持仓
- 有保护单需要核查
- 有 pending 执行动作待确认
- 网络刚恢复，需要对账
- 上轮有 `degraded` 标记

动作范围：
- 检查 protective stop 是否缺失
- 将 stop 推进到 breakeven
- 执行 trailing 收紧
- 执行允许的 reduce
- 对账 position / orders / fills

禁止事项：
- 不生成新 entry
- 不独立切换方向
- 不放宽 stop
- 不因临时信号噪声取消硬保护

## 9. 风控与降级状态机

### 9.1 输入分类
- `transport`
- `data_source`
- `pipeline`
- `research_not_ready`
- `exchange_desync`

### 9.2 状态流转
```text
normal
  -> degraded   (transport 连续失败 / data source 异常 / exchange 对账异常)
  -> blocked    (pipeline blocked / contract invalid / critical protection missing)

degraded
  -> normal     (恢复后已完成对账，保护单存在，状态一致)
  -> blocked    (发现保护缺口或关键契约缺失)
```

### 9.3 状态约束
- `normal`：允许按策略执行
- `degraded`：禁止新 entry，只做保护和收敛
- `blocked`：停止执行，等待下一轮或人工介入

## 10. 止盈止损与仓位管理

### 10.1 入场后第一优先级
- 确保交易所侧存在可生效 protective stop

### 10.2 动态管理原则
- 只收紧，不放宽
- 只在 handoff 明确提供触发条件时推进
- 本地动作必须能在下一轮恢复时重新推导

### 10.3 建议映射
- `initial_stop_loss` → 初始保护单
- `breakeven_trigger` → 推 stop 到保本的阈值
- `trailing_rule` → 分段或连续 trailing 规则
- `tp_ladder` → 分批止盈 / 分批减仓
- `invalidate_conditions` → thesis 失效后的退出约束
- `reduce_conditions` → 允许减仓的条件集合

## 11. 审计与回放

每一轮至少记录：
- judgement 原始摘要
- research readiness 摘要
- execution handoff 摘要
- network_guard 判定
- 真实仓位与挂单快照
- execution plan
- 实际执行结果
- state transition

shadow mode 重点检查：
- 策略动作到执行动作的映射是否稳定
- 降级时是否仍可能扩大风险
- 5m 循环是否越权生成观点

## 12. 开发阶段

### Phase A：基础骨架
- config
- engine_client
- state_store
- audit_logger

### Phase B：shadow orchestration
- 15m 主循环
- execution plan 生成
- replay / review

### Phase C：真实执行最小闭环
- exchange adapter
- protective stop
- entry / reduce / exit

### Phase D：5m 风险辅助
- trailing
- breakeven
- 对账恢复

### Phase E：审查与实盘前收口
- 弱网演练
- 状态机审查
- 重复下单检查
- protective stop 缺口检查

## 13. 测试重点

- judgement → handoff 字段映射
- degraded / blocked 判定
- 对账恢复流程
- protective stop 永不缺席
- trailing / breakeven 只收紧
- 5m 循环不生成新观点
- shadow mode 与 real mode 的动作边界
