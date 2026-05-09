# Code Review Checklist

## 1. 边界检查
- [ ] 是否仍然把 `D:\quant_system_rebuild` 视为唯一 judgement / research 内核
- [ ] 是否避免在新项目中复制 policy、research、comparison 逻辑
- [ ] 是否硬编码保持 ETH-only、10x-only、15m-main、5m-assist
- [ ] 是否没有把扩展到多币种、多交易所、多周期作为当前实现前提

## 2. 输入模式检查
- [ ] 15m 主循环是否默认只消费 `strict-live`
- [ ] `sample-fallback` 是否只用于 shadow / replay / comparison / 调试
- [ ] 是否明确区分 realtime judgement 与 frozen replay
- [ ] 是否保留并传递 `diagnostic`、`issues`、research readiness 信息

## 3. 策略语义检查
- [ ] 是否直接消费 handoff 的 `action / direction / position_state`
- [ ] 是否按真实 `PositionAction` 处理 `entry_long / entry_short / wait / reduce / exit`
- [ ] 是否对 `observe_only / paper_only / small_probe` 做了显式规则，而不是偷偷折叠成普通 entry
- [ ] 是否没有在执行层偷偷发明第二套策略动作
- [ ] 是否没有把执行失败写成策略失败
- [ ] 是否没有因为网络异常就伪造方向切换

## 4. 执行状态机检查
- [ ] 执行层状态是否与策略动作分离
- [ ] 是否能表达 `entry_pending / reduce_pending / exit_pending / degraded / blocked`
- [ ] 是否能表达交易所已变化但本地尚未确认的中间态
- [ ] 恢复后是否先对账，再继续执行

## 5. 风控与降级检查
- [ ] `transport` 异常是否只触发有限重试与降级，而不是无限重试
- [ ] `data_source` 异常时是否禁止新 entry
- [ ] `pipeline` blocked 时是否立即停止执行动作
- [ ] research bundle not ready 时是否只允许风险收敛动作
- [ ] `degraded` 状态下是否绝不扩大仓位

## 6. 止盈止损检查
- [ ] 入场后是否优先确保 protective stop 已存在
- [ ] 是否围绕 handoff 字段消费 `initial_stop_loss`
- [ ] 是否围绕 handoff 字段消费 `breakeven_trigger`
- [ ] 是否围绕 handoff 字段消费 `trailing_rule`
- [ ] 是否围绕 handoff 字段消费 `tp_ladder`
- [ ] trailing / breakeven 是否只收紧，不放宽
- [ ] 是否不存在取消保护单后未及时补回的窗口

## 7. 下单幂等检查
- [ ] 重复执行同一轮 cycle 时，是否不会重复下同一笔单
- [ ] 网络重试后，是否不会误发重复 entry
- [ ] reduce / exit 是否有幂等键或等价保护
- [ ] 是否先检查现有挂单状态，再决定补单或改单

## 8. 仓位同步检查
- [ ] 是否在每轮执行前拉取真实 position / open orders / recent fills
- [ ] 本地 state_store 是否与交易所状态持续对齐
- [ ] 恢复流程是否优先处理 desync
- [ ] 是否能识别“本地无仓但交易所有仓”与“本地有仓但交易所已平”的分叉

## 9. 5m 风险辅助检查
- [ ] 5m 循环是否只在持仓、挂单、降级恢复等条件下运行
- [ ] 5m 循环是否不生成新的 entry 判断
- [ ] 5m 循环是否不独立切换方向
- [ ] 5m 循环是否只执行保护、收紧、减仓、对账

## 10. 审计与可回放检查
- [ ] 是否记录 judgement / readiness / handoff / execution plan / execution result
- [ ] 是否保留 diagnostic 原文与关键 reason codes
- [ ] 是否能区分 shadow mode 与 real mode
- [ ] 日志是否能支持事后解释：为何执行、为何降级、为何未执行

## 11. 测试覆盖检查
- [ ] 是否有 judgement → handoff → execution plan 映射测试
- [ ] 是否有 degraded / blocked 状态测试
- [ ] 是否有保护单缺口测试
- [ ] 是否有断连恢复后的对账测试
- [ ] 是否有 5m 不生成新观点测试
- [ ] 是否有重复调用不重复下单测试

## 12. 明确红线
以下任一情况出现，都不能视为可进入真实执行：
- [ ] 仍把 `sample-fallback` 当成实时交易输入
- [ ] `degraded` 时仍允许新 entry
- [ ] 保护单可能出现空窗
- [ ] 无法证明恢复后先对账再动作
- [ ] 无法证明执行层没有改写策略层语义
