# ETH Trading Bot Framework

独立交易机器人框架，服务于 `D:\quant_system_rebuild` 已经收口的 research / judgement 能力。

这个项目的目标不是重写策略，也不是复制 research pipeline，而是把当前量化仓已经稳定的判断契约，包装成一个可安全执行、可审计、可降级、可回放的交易机器人壳层。

## 项目边界

### 只做这些
- 只做 **ETH**
- 只做 **10x 永续**
- **15m** 是唯一主决策周期
- **5m** 只做持仓后的风险辅助、保护单健康检查、断连恢复后对账
- 网络抖动、代理切换、端口漂移视为常态输入
- 优先支持 **shadow mode / replay mode / strict-live consumption**，再进入真实执行

### 明确不做这些
- 不在本项目里重写 `research bundle`、`feature matrix`、`policy engine`
- 不把 `sample-fallback` 当实时交易信号
- 不做多交易所泛化抽象
- 不支持 BTC 主交易、山寨币扩展、跨周期多机器人
- 不把执行异常反向改写成策略异常

## 与 `D:\quant_system_rebuild` 的分工

### 原仓继续负责
- research bundle / readiness gate
- live judgement
- execution handoff contract
- observation / comparison / review
- scheduler 与 artifact 语义

### 新机器人项目负责
- orchestration
- exchange adapter
- position / order state store
- network guard / degrade guard
- protective stop 管理
- 审计日志
- shadow / replay / real execution 切换

## 复用的核心入口

新机器人只消费现有稳定入口，不复制其内部逻辑：

- `src/interfaces/live_judgement.py`
  - `run_live_judgement(...)`
- `src/interfaces/research_bundle.py`
  - `ensure_research_bundle_ready(...)`
- `src/interfaces/runner.py`
  - `build_execution_handoff(...)`
- `src/interfaces/scheduler.py`
  - `run_scheduler_cycle(...)`
- `src/interfaces/execution_tracking.py`
  - observation / review 相关批次能力

其中最关键的是两层契约：

1. **judgement payload**
   - 给出 `status / mode / research_bundle / decision / diagnostic / issues`
2. **execution handoff**
   - 给出 `action / direction / position_state / risk_filter_status / initial_stop_loss / breakeven_trigger / trailing_rule / tp_ladder / invalidate_conditions / reduce_conditions`

## 运行模式

### 1. strict-live
- 读取真实网络和实时快照
- 只有该模式下的判断，才有资格进入实时交易语境
- 网络异常、代理失败、数据源异常必须进入诊断和降级分支

### 2. sample-fallback
- 只用于工程链路回放、shadow 验证、comparison、报告联调
- 它依赖本地样本和已有 research alias
- 它不是假数据生成器，但也不是实时市场

### 3. shadow mode
- 机器人消费判断，但不真实下单
- 只生成 execution plan、状态迁移和审计日志
- 用来验证策略动作和执行动作映射是否稳定

## 决策与执行分层

### 策略层动作
由原仓输出，机器人不得修改语义。

当前应以 `PositionAction` 的真实契约值为准：
- `entry_long`
- `entry_short`
- `wait`
- `reduce`
- `exit`
- `observe_only`
- `paper_only`
- `small_probe`

其中首版真实执行优先围绕：
- `entry_long`
- `entry_short`
- `wait`
- `reduce`
- `exit`

对 `observe_only / paper_only / small_probe`，必须显式建模处理规则，不能默默当成普通开仓动作。

### 执行层状态
由机器人本地维护：
- `idle`
- `entry_pending`
- `position_open`
- `reduce_pending`
- `exit_pending`
- `degraded`
- `blocked`

原则：
- 策略说什么，执行层只负责安全消费
- 执行失败不等于策略失效
- 网络抖动不等于市场观点切换

## 15m / 5m 责任边界

### 15m 主循环
每轮只做一次主判断：
1. 拉取 `strict-live` judgement
2. 校验 research readiness
3. 构造 execution handoff
4. 结合真实仓位与挂单状态生成 execution plan
5. 写入 audit log

### 5m 风险辅助循环
只有以下条件满足时才运行：
- 已有持仓
- 已有挂单待确认
- 上轮处于 `degraded`
- 网络恢复后需要重新对账

5m 循环只允许：
- 检查 protective stop 是否存在
- 触发 breakeven 推进
- 触发 trailing 收紧
- 触发 reduce 条件后的减仓动作
- 校验本地状态与交易所状态一致性

5m 循环不允许：
- 生成新的独立开仓观点
- 脱离 15m judgement 自行改方向
- 因临时抖动而放宽止损

## 止盈止损原则

机器人不重新发明一套 exit 语义，而是直接围绕 handoff 字段执行：
- `initial_stop_loss`
- `breakeven_trigger`
- `trailing_rule`
- `tp_ladder`
- `invalidate_conditions`
- `reduce_conditions`

执行原则：
1. 入场后优先落交易所侧硬保护单
2. 本地动态管理只做收紧，不做放宽
3. 网络不稳时，不扩大风险暴露
4. 网络恢复后，先对账，再继续动作

## 弱网与降级

网络不稳不是例外，而是正式状态机输入。

### 典型输入
- 代理不可用
- Clash 节点切换
- 端口漂移
- 单次请求超时
- 连续 transport error
- data source 缺字段
- pipeline 自身 blocked

### 推荐响应
- `transport`：有限重试，连续失败后进入 `degraded`，禁止新 entry
- `data_source`：保持仓位保护，不推进新仓位扩张
- `pipeline`：直接 `blocked`
- research bundle 非 ready：禁止新 entry，仅允许风险收敛动作

### 降级底线
- 不加仓
- 不取消 protective stop
- 不把网络问题写成策略反转
- 恢复后必须先同步 position / open orders / recent fills

## 计划中的模块

- `src/bot/config.py`
- `src/bot/engine_client.py`
- `src/bot/orchestrator.py`
- `src/bot/network_guard.py`
- `src/bot/state_store.py`
- `src/bot/position_manager.py`
- `src/bot/exchange_adapter.py`
- `src/bot/audit_logger.py`

详见 `docs/system_design.md`。

## 推荐推进顺序

### Phase A
- 配置层
- engine client
- state store
- audit logger

### Phase B
- shadow mode
- decision → execution plan 映射
- replay / review 验证

### Phase C
- 最小 exchange adapter
- entry / stop / reduce / exit
- 幂等下单与状态同步

### Phase D
- 5m 风险辅助循环
- breakeven / trailing / reduce 管理
- 断连恢复后的重新对账

### Phase E
- 代码审查
- 弱网演练
- shadow 稳定性复核
- 实盘前收口

## 文档

- `docs/system_design.md`：系统设计、状态机、模块边界
- `docs/code_review_checklist.md`：开发与代码审查清单

## 当前状态

当前仓库已经完成 shadow-first 核心框架首版：
- `config.py`
- `engine_client.py`
- `network_guard.py`
- `state_store.py`
- `position_manager.py`
- `exchange_adapter.py`
- `audit_logger.py`
- `orchestrator.py`
- 对应单元测试

当前仍未完成：
- 真实交易所 adapter 对接
- 幂等下单与真实仓位/挂单同步
- 5m 风险辅助循环的真实保护单推进
- 与 `quant_system_rebuild` observation/review 的实盘级联调
