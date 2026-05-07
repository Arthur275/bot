# Bot 与量化系统合并审查问题与可靠修复方案

生成日期：2026-05-07

审查范围：

- `D:\开发\eth_trading_bot`
- `D:\开发\quant_system_rebuild`

说明：

- 本文合并 DS 审查结论和 Codex 审查结论。
- DS 后续已确认 Codex 对 8 条争议点的反驳成立，因此本文按“真实代码事实”重新校准严重级别。
- 本文不记录任何实盘操作结果；审查过程未调用真实下单、撤单接口。
- 目标是给出可直接进入修复队列的可靠方案，而不是保留夸大的 Critical 标签。

## 1. 总结论

外层实盘安全门整体是有效的：strict-live、kill switch、candidate package、automation gate、execution risk gate、adapter live snapshot 校验都存在。当前未发现可绕过所有风控直接提交真实订单的路径。

最大真实风险集中在两个区域：

1. Bot 实盘 worker 通过闸门后，保护止损确认、止损清理、结果状态、幂等恢复存在高风险缺陷。
2. Quant 因子治理会混入未结算 handoff 样本，把“允许执行”误当成“结果有利”，污染 lookup 和 governance。

DS 清单中不少问题是真实的，但部分严重级别过高。例如 orderbook 缺失不会按当前代码“错误放行拥挤空头”，而是会把拥挤空头确认降为 neutral；它应作为可观测性和降级语义问题处理，不应列为资金级 Critical。

## 2. 严重级别定义

- P0：可能直接导致真实仓位失去保护、误撤保护单、或把真实执行失败显示为成功。
- P1：可能污染实盘决策、阻断恢复、造成跨系统执行语义不一致。
- P2：可观测性、dashboard 误导、调度器/状态文件可靠性问题。
- P3：维护性、未来兼容性、低概率边界问题。
- 不采纳：与当前代码事实不符；DS 后续已确认这些反驳成立。

## 3. P0 问题

### P0-1. reduce 后刷新止损可能撤掉刚挂上的新保护止损

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:410`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:424`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:428`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:475`

问题：

`_execute_reduce_with_stop_refresh()` 执行 reduce 后再挂新保护止损。只要 stop result accepted，就调用 `_cleanup_open_algo_orders()`。该 cleanup 会取消当前拉到的所有 open algo orders，无法排除刚挂上的新止损。

关键代码闭环：

```python
if stop_commands:
    stop_results = _execute_protective_stop_with_retry(adapter=adapter, commands=stop_commands, attempts=3)
    results.extend(stop_results)
    if stop_results and all(result.accepted for result in stop_results):
        _cleanup_open_algo_orders(adapter=adapter)

def _cleanup_open_algo_orders(*, adapter: RealOrderAdapter) -> list[dict[str, Any]]:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    return _cancel_algo_orders(adapter=adapter, open_algo_orders=open_algo_orders)
```

后果：

减仓后剩余仓位可能处于无保护止损状态。

可靠修复：

1. 将“清理所有 algo 单”改成“只清理旧的 bot-owned protective stop”。
2. 挂新止损前记录旧 `algoId/clientAlgoId` 集合。
3. 新止损挂出后，用交易所响应、`clientAlgoId`、side、reduceOnly、quantity、trigger price、symbol 校验确认新止损。
4. 只撤旧单，绝不撤刚确认的新单。

必须补测试：

- reduce 后 open algo orders 同时包含旧 stop 和新 `ethbot-ps-*`，断言只撤旧 stop。
- 新 stop accepted 但无法在 open algo orders 中确认，断言进入 recovery，不执行盲目 cleanup。

### P0-2. algo 清理范围过宽，可能撤掉人工或其他策略的保护单

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:361`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:406`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:475`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:480`
- `D:\开发\eth_trading_bot\src\bot\exchange_adapter.py:1134`

问题：

`fetch_open_algo_orders_raw()` 只按 `ETHUSDT + CONDITIONAL` 拉单。`_cancel_algo_orders()` 对返回列表逐个撤单，只过滤空 `algoId/clientAlgoId`，不校验 bot 归属、订单类型、side、reduceOnly、仓位方向或 client ID 前缀。`exit` 分支也会调用同一个 `_cleanup_open_algo_orders()`，因此退出后清理也存在同根风险。

关键代码闭环：

```python
if action == "exit":
    _cleanup_open_algo_orders(adapter=adapter)

def _cancel_algo_orders(*, adapter: RealOrderAdapter, open_algo_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canceled: list[dict[str, Any]] = []
    for order in open_algo_orders:
        algo_id = str(order.get("algoId") or "")
        client_algo_id = str(order.get("clientAlgoId") or "")
        if not algo_id and not client_algo_id:
            continue
        canceled.append(adapter.cancel_algo_order_raw(algo_id=algo_id, client_algo_id=client_algo_id))
    return canceled
```

后果：

可能误撤人工止损或其他策略止损。

可靠修复：

1. 统一 bot-owned client ID 前缀，例如 `ethbot-ps-`、`ethbot-be-`、`ethbot-ts-`。
2. 只有匹配 bot-owned 前缀的订单允许自动撤。
3. 撤单前校验 symbol、algoStatus、orderType、reduceOnly、side 与 live position 是否匹配。
4. 对非本 bot 订单只报告 `external_algo_order_present`，不自动撤。

必须补测试：

- open algo orders 混入 `manual-stop` 和 `ethbot-ps-*`，断言只撤 `ethbot-ps-*`。
- bot 前缀但 side 或 reduceOnly 不匹配，断言拒绝撤并报告异常。

### P0-3. 保护止损确认逻辑过弱

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:456`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:470`

问题：

`_protective_stop_confirmed_active()` 只判断 `open_algo_orders` 非空。任何 ETHUSDT 条件单都会被当作保护止损确认；一个人工 trailing stop、其他策略的 conditional stop，甚至方向/数量/触发价完全不匹配的条件单，都足以让确认逻辑返回成功。这是 P0 中最危险的静默假确认之一，因为它会把“没有保护止损”伪装成“保护已生效”。

关键代码闭环：

```python
def _protective_stop_confirmed_active(*, adapter: RealOrderAdapter) -> bool:
    open_algo_orders = adapter.fetch_open_algo_orders_raw()
    return bool(open_algo_orders)
```

后果：

止损实际没挂好，但 worker 认为保护成功。

可靠修复：

新增 `find_matching_protective_stop(runtime_snapshot, command, open_algo_orders)`，至少校验：

- `algoStatus` 处于 active 状态。
- 类型为保护止损类型。
- `reduceOnly=true`。
- side 与当前仓位方向相反。
- quantity 与当前仓位数量匹配。
- trigger price 与请求止损价在容忍范围内。
- `clientAlgoId` 是 bot protective stop 前缀。
- symbol 为 `ETHUSDT`。

必须补测试：

- 无关条件单存在时确认失败。
- 错方向、错数量、非 reduceOnly 时确认失败。
- 正确保护止损存在时确认通过。

### P0-4. real_order_worker 顶层状态掩盖部分失败

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:151`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:157`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:69`
- `D:\开发\eth_trading_bot\dashboard\data_sources.py:210`
- `D:\开发\eth_trading_bot\dashboard\static\app.js:271`

问题：

命令执行后，worker 顶层总是写 `status="submitted"`。即使某个 `CommandExecutionResult.accepted=False`，CLI 仍可能返回 0，dashboard 也把 `submitted` 显示为绿色。

关键代码闭环：

```python
result_payload = {
    "status": "submitted",
    "results": [result.model_dump(mode="json") for result in results],
}

if ["running", "fresh", "ok", "pass", "allowed", "submitted", "active"].includes(raw)) return "green";
```

后果：

交易所拒单、止损失败、entry 成功但 stop 失败等半成功状态会被误显示为健康。

可靠修复：

1. 增加结果汇总状态：
   - `submitted_all_accepted`
   - `partial_failed`
   - `all_failed`
   - `unknown_after_exception`
   - `blocked`
2. `partial_failed/all_failed/unknown_after_exception` 返回非 0，或至少写入红色 recovery 状态。
3. dashboard 将失败类状态显示为红色。
4. 如果保护止损失败，state store 必须设置 `recovery_required=true` 和 `protective_stop_required=true`。

必须补测试：

- entry accepted、stop rejected 时，顶层状态不是 green。
- stop timeout 后 dashboard 红色，state 进入 recovery。

## 4. P1 问题

### P1-1. 幂等逻辑把失败结果当作 completed

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:304`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:327`

问题：

`_check_idempotency()` 只要看到 `real_order_worker_command_result`，就把 idempotency key 记为 completed，不检查单条 result 是否 accepted。

后果：

失败或未知状态会阻止后续合法修复或重试。

可靠修复：

1. 只有单条 result `accepted=true` 且有明确交易所订单号/确认状态时，才能记为 completed。
2. `rejected/timeout/error` 进入 `recoverable` 或 `pending_recovery`。
3. 对 timeout 后可能已提交的情况，先 reconciliation，再决定是否重试。

必须补测试：

- 第一次 stop rejected，第二次同 key 不应返回 `idempotency_key_already_completed`。
- timeout 后进入 reconciliation，而不是 completed。

### P1-2. factor lookup 混入未结算 handoff，污染因子治理

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\analysis\factor_dataset.py:774`
- `D:\开发\quant_system_rebuild\src\analysis\factor_dataset.py:799`
- `D:\开发\quant_system_rebuild\src\analysis\factor_dataset.py:1246`
- `D:\开发\quant_system_rebuild\src\analysis\factor_dataset.py:1502`

问题：

`lookup_source` 合并 resolved outcomes 和 unresolved factor values。handoff 插入时，`execution_allowed=True` 的 entry/reduce/exit 会被标成 `favorable`。

后果：

治理表学习的是“当时被允许执行”，不是“事后真的赚钱”。

可靠修复：

1. governance lookup 默认只使用 `decision_outcomes.status='resolved'` 的样本。
2. 未结算 handoff 样本只能进入诊断表，不参与 win rate、expectancy、factor_effect。
3. 如果为了监控保留 unresolved 样本，统一标为 `neutral` 且不计入治理统计。

必须补测试：

- 只有 handoff 且无 resolved outcome 时，不产生 favorable lookup row。
- resolved 正收益才产生 favorable。
- resolved 负收益产生 unfavorable。

### P1-3. risk_filter_status 跨 repo 契约不完整

状态：确认属实；DS 原 Critical 降为 P1。

位置：

- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:118`
- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:389`
- `D:\开发\eth_trading_bot\src\bot\network_guard.py:101`
- `D:\开发\eth_trading_bot\src\bot\high_risk_gate.py:156`
- `D:\开发\eth_trading_bot\src\bot\automation_gate.py:110`

问题：

量化侧可产出 `research_unavailable/unavailable`，但 bot 的 `network_guard/high_risk_gate` 没完整识别。

重要校正：

这不是直接下单绕过。`automation_gate` 会用 `risk_filter_status != "pass"` 阻断实盘 entry。真实问题是诊断信号和最终执行门控不一致。

可靠修复：

1. 建立共享状态集合：
   - 可执行：`pass`
   - 降级但禁止实盘开仓：`degraded`
   - 阻断：`veto`、`blocked`、`unavailable`、`research_unavailable`
2. `network_guard`、`automation_gate`、`high_risk_gate` 使用同一 helper。
3. 未知状态默认阻断实盘执行。

必须补测试：

- 枚举量化所有 `risk_filter_status`，断言 bot 分类一致。
- `research_unavailable` 下 `allow_entry=false`，high risk 也阻断或明确降级。

### P1-4. probe 仓位阈值不一致：量化 10%，bot 2%

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\policy\sizing_policy.py:12`
- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:366`
- `D:\开发\eth_trading_bot\src\bot\execution_risk_gate.py:27`
- `D:\开发\eth_trading_bot\src\bot\execution_risk_gate.py:114`

问题：

量化 small probe cap 是 10%，bot 执行风险门 cap 是 2%。bot 会静默截断。

后果：

研究、预期仓位、实盘仓位不一致，dashboard 也可能误导用户。

可靠修复：

1. 将 probe cap 写入共享配置或 handoff contract。
2. handoff 明确包含 `requested_size_pct`、`executable_size_pct`、`size_cap_source`、`size_cap_reason`。
3. bot 如果截断，必须写入 `size_truncated_by_bot_risk_gate`。

必须补测试：

- 量化请求 8% probe，bot cap 2%，断言执行 2% 且有截断告警。

### P1-5. Kill switch 存在提交前 TOCTOU 窗口

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:100`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:141`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:392`

问题：

worker 在进入锁后检查 kill switch，但执行 `_execute_with_action_closure()` 前不再检查。

可靠修复：

1. 将 `kill_switch_path` 传入 execution closure。
2. 每次风险增加型 `adapter.execute_commands()` 前再次检查 kill switch。
3. 如果 entry 已成功，kill switch 出现后仍允许保护止损补挂，但禁止新的风险增加命令。

必须补测试：

- final snapshot 后、entry 前创建 kill switch，entry 不应提交。
- entry 后、stop 前创建 kill switch，仍应尝试保护止损。

### P1-6. final snapshot 到 submit 之间仍有竞态

状态：部分确认；DS “无最终快照验证”表述不准确。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:120`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:128`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:141`

问题：

代码已有 final snapshot 和 legality check，但从校验结束到真实提交之间仍有窗口。

可靠修复：

1. 在 entry 命令提交前再拉一次 live snapshot。
2. 对 entry 再确认 flat、无 ghost stop、symbol 正确。
3. 对 reduce/exit/protective stop 再确认 live position direction 和 quantity。

必须补测试：

- pending event 后 fake adapter 将仓位从 flat 改为 entered，entry 应阻断。

### P1-7. scheduler 连续失败退出，不区分瞬态和永久故障

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\scripts\quant_runtime_scheduler.py:117`
- `D:\开发\quant_system_rebuild\scripts\quant_runtime_scheduler.py:144`

问题：

所有非零结果都计入 failures，达到阈值后退出 loop。

可靠修复：

1. 区分 `transient_transport`、`data_unavailable`、`policy_blocked`、`code_error`、`config_error`。
2. 瞬态错误进入 degraded heartbeat 并继续 backoff，不直接退出。
3. 代码或配置错误才快速退出。

必须补测试：

- 连续三次 transport 超时不退出 loop。
- 配置错误按预期退出。

### P1-8. factor_bridge 对缺失 research_health 默认中性

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\policy\factor_bridge.py:45`
- `D:\开发\quant_system_rebuild\src\policy\factor_bridge.py:60`
- `D:\开发\quant_system_rebuild\src\policy\factor_bridge.py:137`

问题：

`research_health is None` 时，研究降级完全跳过，最终可能 `confidence_multiplier=1.0`。

可靠修复：

1. strict-live 下缺失 research health 直接 `status="blocked"`。
2. 只有显式 debug/offline 模式允许缺失研究健康时保持 neutral。

必须补测试：

- strict-live + `research_health=None` 产生 blocked factor signal。

### P1-9. runtime JSON 和 state_store 非原子写入

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\src\bot\state_store.py:70`
- `D:\开发\eth_trading_bot\scripts\bot_runtime_scheduler.py:390`
- `D:\开发\quant_system_rebuild\scripts\quant_runtime_scheduler.py:673`
- `D:\开发\eth_trading_bot\dashboard\data_sources.py:395`

问题：

关键 JSON 直接 `write_text` 覆盖。进程中断可导致半写文件，dashboard 又常把读取失败转成 `{}`。

可靠修复：

1. 实现 `atomic_write_json()`：同目录临时文件、flush/fsync、`os.replace`。
2. state、latest_cycle、candidate package、heartbeat、scheduler status 全部使用原子写。
3. dashboard 区分 `missing`、`invalid_json`、`read_error`。

必须补测试：

- 模拟损坏 `state.json`，系统进入安全 degraded/recovery，而不是崩溃或误判为空。

## 5. P2 问题

### P2-1. ExecutionHandoff 缺少显式 source_run_id

状态：确认属实；DS 原 Critical 降级。

位置：

- `D:\开发\quant_system_rebuild\src\contracts\execution.py:30`
- `D:\开发\quant_system_rebuild\src\interfaces\runner.py:700`
- `D:\开发\eth_trading_bot\dashboard\decision_review.py:319`

问题：

`ExecutionHandoff` 没有 `run_id/source_run_id` 字段。dashboard 当前靠目录名 fallback。

可靠修复：

1. `ExecutionHandoff` 增加 `source_run_id: str = ""`。
2. 从 `metadata["run_id"]` 填入。
3. dashboard 优先读 handoff 字段，保留目录 fallback。

### P2-2. OBSERVE_ONLY 有定义但最终折叠为 WAIT

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\contracts\runtime.py:20`
- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:327`
- `D:\开发\quant_system_rebuild\src\policy\exit_state_machine.py:98`
- `D:\开发\quant_system_rebuild\src\policy\decision_engine.py:346`

问题：

`resolve_base_action()` 能返回 `OBSERVE_ONLY`，但状态机最终多转成 `WAIT`。

可靠修复：

二选一：

1. 若 `observe_only` 是正式外部语义，flat/no setup 时保留它，并在 bot 中明确映射为不可执行。
2. 若不是正式语义，从公开 enum 去掉，避免误导。

### P2-3. orderbook 缺失没有清晰降级标志

状态：部分确认；DS “导致空头错误放行”不成立。

位置：

- `D:\开发\quant_system_rebuild\src\interfaces\live_snapshots.py:367`
- `D:\开发\quant_system_rebuild\src\interfaces\live_snapshots.py:384`
- `D:\开发\quant_system_rebuild\src\policy\realtime_policy_input.py:344`
- `D:\开发\quant_system_rebuild\src\policy\realtime_policy_input.py:438`

事实校正：

当前拥挤多头下，如果没有 orderbook 确认，SHORT 会被转成 `NEUTRAL`，不是放行空头。

真实问题：

orderbook fetch 异常只进 `optional_source_errors`，没有明确 `orderbook_unavailable` 降级标志。

可靠修复：

1. live bundle metadata 写入 `orderbook_status="unavailable"`。
2. handoff 加 `execution_warnings=["orderbook_unavailable"]`。
3. dashboard 显示 orderbook 缺失。

### P2-4. scan_quality_payloads 缺失被静默跳过

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\interfaces\scheduler.py:84`
- `D:\开发\quant_system_rebuild\src\interfaces\scheduler.py:98`
- `D:\开发\quant_system_rebuild\src\interfaces\scheduler.py:366`

问题：

默认 scan quality 文件不存在时直接 continue。

可靠修复：

1. strict-live 下缺失 scan quality artifact 应产生 failed payload。
2. 增加 `scan_quality_missing:<name>` reason code。
3. 只有 debug/offline 模式允许跳过。

### P2-5. dashboard run_id 中文格式化不支持真实 hyphen 格式

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\scripts\quant_runtime_scheduler.py:452`
- `D:\开发\eth_trading_bot\dashboard\static\app.js:257`

问题：

实际 run_id 是 `eth-15m-20260506T145857Z-25ab1c21`，前端只按空格拆分。

可靠修复：

支持空格和 hyphen 两种格式，展示为中文时间框架和可读 UTC 时间。

### P2-6. dashboard worker 状态和 CSS 测试契约不一致

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\dashboard\data_sources.py:210`
- `D:\开发\eth_trading_bot\dashboard\static\app.js:271`
- `D:\开发\eth_trading_bot\dashboard\static\styles.css:2`
- `D:\开发\eth_trading_bot\tests\test_dashboard_data_sources.py:355`

问题：

dashboard 将 `submitted` 视为绿色；CSS 当前是 dark，但测试仍要求 `color-scheme: light`。

可靠修复：

1. 配合 P0-4 状态重构，只有 `submitted_all_accepted` 为绿色。
2. 确认 dashboard 是否正式使用暗色主题；若是，更新测试。

### P2-7. protective stop replace 存在设计级 gap risk

状态：确认属实，但代码已要求显式接受 gap risk。

位置：

- `D:\开发\eth_trading_bot\scripts\preview_protective_stop_replace.py:623`
- `D:\开发\eth_trading_bot\scripts\preview_protective_stop_replace.py:654`
- `D:\开发\eth_trading_bot\scripts\preview_protective_stop_replace.py:673`

可靠修复：

优先方案：

1. 如交易所支持，使用 amend/modify 替代 cancel->place。
2. 如允许重叠 reduce-only stop，先挂新 stop，确认后撤旧 stop。
3. 如果必须 cancel->place，保持人工专用、强确认、显示 gap 暴露估计。

### P2-8. research freshness unknown / valid_for_live_decision 默认偏乐观

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\research\health.py:330`
- `D:\开发\quant_system_rebuild\src\research\health.py:135`
- `D:\开发\quant_system_rebuild\src\interfaces\research_bundle.py:357`

问题：

缺 dataset timestamp 时 freshness 是 `unknown`，不是 stale；`valid_for_live_decision` 缺省为 True。

可靠修复：

1. strict-live 下 `unknown/missing` 至少 degrade，关键研究包缺元数据时 block entry。
2. `valid_for_live_decision` 缺省改为 False，只有 producer 显式写 True 才可用于 live decision。

### P2-9. degraded 语义在 network_guard 与 automation_gate 之间不一致

状态：确认属实；属于跨系统语义问题，不是实盘绕过。

位置：

- `D:\开发\eth_trading_bot\src\bot\network_guard.py:101`
- `D:\开发\eth_trading_bot\src\bot\automation_gate.py:64`
- `D:\开发\eth_trading_bot\src\bot\automation_gate.py:110`

问题：

`network_guard` 对 `risk_filter_status="degraded"` 只标记 degraded，不一定直接给出和最终执行一致的阻断语义；`automation_gate` 则会阻断 degraded cycle 或 `risk_filter_status != "pass"` 的实盘入场。

后果：

dashboard 或上层诊断可能看到“allow_entry”和最终实盘 gate 不一致。

可靠修复：

1. 将 `degraded` 明确定义为“允许观测/模拟/采样，但禁止自动实盘开仓”。
2. `network_guard` 输出中拆分 `allow_signal_tracking` 与 `allow_real_entry`，避免一个 `allow_entry` 承载两种语义。
3. dashboard 展示最终实盘 gate 时以 `automation_gate` 为准。

### P2-10. reduce 真实执行尚未实现，必须保持 gate 显式阻断

状态：确认属实；当前 adapter fail-safe，但需要把阻断前移到 gate/contract。

位置：

- `D:\开发\eth_trading_bot\src\bot\exchange_adapter.py:1500`
- `D:\开发\eth_trading_bot\src\bot\automation_gate.py:69`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:410`

问题：

`_resolve_reduce_order_request()` 会抛出 `Real reduce order requires an explicit reduce quantity contract`。当前自动 real order gate 会阻断 high-risk auto-submit，但 worker 内仍存在 reduce 执行分支和 reduce stop refresh 逻辑。

后果：

未来一旦 gate 放开 reduce，执行层会失败；若绕到 fake/alternate adapter，P0-1 的 stop cleanup 风险会变成实盘风险。

可靠修复：

1. 在 gate 层显式写入 `real_reduce_not_implemented`，直到 handoff 提供明确 `reduce_qty/reduce_fraction` 并完成 adapter 实现。
2. 实现 reduce 前，worker 不应接收 reduce candidate package。
3. 实现 reduce 后，必须先修 P0-1 的 stop refresh cleanup。

### P2-11. research_unavailable 对已有持仓的退出策略未定义

状态：部分确认；当前逻辑偏“不开新仓，但不强制退出”。

位置：

- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:265`
- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:273`
- `D:\开发\quant_system_rebuild\src\policy\risk_filter.py:281`

问题：

`has_position_exit_veto_from_fields()` 对 research auxiliary veto 采取非强制退出处理。这个设计可以避免研究源短暂不可用导致盲目平仓，但缺少“长期不可用时如何处理已有仓位”的明确策略。

后果：

已有仓位可能在研究长期不可用时只靠其他退出条件管理，dashboard 也难以判断这是设计选择还是漏判。

可靠修复：

1. 增加 `research_unavailable_since` 或连续不可用计数。
2. 短暂不可用：禁止加仓/开仓，保持保护止损。
3. 超过阈值：进入 reduce/exit review 或强制收紧止损。
4. 在 handoff 中显式输出 `research_unavailable_position_policy`。

## 6. P3 问题

### P3-1. HighRiskTrailingRule 与 TrailingRule 类名不同

状态：不是当前 bug，只是未来 schema drift 风险。

位置：

- `D:\开发\quant_system_rebuild\src\contracts\execution.py:126`
- `D:\开发\eth_trading_bot\src\bot\high_risk_gate.py:21`

结论：

JSON 字段是 `trailing_rule`，内部类名不同不影响当前解析。

可靠修复：

增加 cross-repo schema 测试：量化生成 high-risk handoff，bot 用 `HighRiskHandoff.model_validate()` 验证。

### P3-2. `_to_optional_float()` 把 0.0 映射为 None

状态：确认属实，但不是资金级问题。

位置：

- `D:\开发\eth_trading_bot\src\bot\exchange_adapter.py:1375`

可靠修复：

拆成两个 helper：

- `_to_optional_positive_float()`：价格、数量，0 视为无效。
- `_to_optional_float_preserve_zero()`：诊断指标，保留 0。

### P3-3. WorkerLock 只按 mtime 判断 stale

状态：确认属实。

位置：

- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:164`
- `D:\开发\eth_trading_bot\scripts\real_order_worker.py:198`

可靠修复：

读取 lock 文件中的 PID；PID 存活且命令匹配 worker 时不清锁；PID 不存在且超过阈值才清理。

### P3-4. dashboard 中 confidence 文案容易被理解成概率

状态：确认属实。

位置：

- `D:\开发\quant_system_rebuild\src\policy\decision_engine.py:394`
- `D:\开发\eth_trading_bot\dashboard\static\index.html:102`
- `D:\开发\eth_trading_bot\dashboard\static\app.js:551`

可靠修复：

将 dashboard 文案从“置信度”改为“策略信心分”或“信号强度”，并展示降级因子。

### P3-5. state_store 在无实时快照时会 fallback 到 handoff

状态：确认属实；当前有防护条件，但仍应提高可观测性。

位置：

- `D:\开发\eth_trading_bot\src\bot\state_store.py:222`
- `D:\开发\eth_trading_bot\src\bot\state_store.py:236`
- `D:\开发\eth_trading_bot\src\bot\state_store.py:244`

问题：

当没有有效 runtime snapshot 且 state 仍为空/flat 时，state_store 会用 handoff 中的 position 信息补状态。这能保留上下文，但如果 handoff 已过时，可能污染观测状态。

可靠修复：

1. fallback 写入时增加 `state_source="handoff_fallback"`。
2. fallback 只允许在 handoff 未过期、run_id 匹配当前 cycle 时使用。
3. dashboard 标注“非交易所实时快照来源”。

### P3-6. idempotency key 缺少显式 run_id/handoff_id 维度

状态：部分确认；当前更大的问题已列 P1-1。

位置：

- `D:\开发\eth_trading_bot\src\bot\exchange_adapter.py:445`

问题：

当前 key 由 `target:generated_at:action:direction` 组成。一般情况下足够稳定，但不如显式使用 `source_run_id/handoff_id/package_id` 清晰。

可靠修复：

1. idempotency key 改为包含 `source_run_id` 或 `handoff_id`。
2. 同一 package 内不同 command target 保持唯一。
3. 保留旧 key 兼容读取，但新包使用新格式。

### P3-7. entry mark price 优先使用 runtime_snapshot 中的价格

状态：部分确认；当前会 fallback 到 fresh premiumIndex，但应校验快照年龄。

位置：

- `D:\开发\eth_trading_bot\src\bot\exchange_adapter.py:1526`
- `D:\开发\eth_trading_bot\src\bot\exchange_adapter.py:1554`

问题：

`_resolve_entry_mark_price()` 优先使用 runtime snapshot 中的 mark price。若 snapshot_valid 但价格时间戳过旧，可能用到滞后价格计算 quantity。

可靠修复：

1. `AdapterRuntimeSnapshot` 增加 price timestamp 或 age。
2. 超过 TTL 时强制使用 `_fetch_symbol_contract()` 中的 premiumIndex mark price。
3. 记录 `mark_price_source`。

## 7. 复核后不采纳或降级的 DS 条目

### NA-1. orderbook 缺失会错误放行拥挤空头

不采纳原表述。

当前代码在拥挤多头且 orderbook 未确认时返回 `Direction.NEUTRAL`，不是放行 SHORT。保留 P2-3 作为“缺少明确降级标志”的问题。

### NA-2. network_guard degraded 形成真实入场绕过

不采纳“绕过”表述。

`network_guard` 诊断确实不一致，但 `automation_gate` 会阻断 `risk_filter_status != "pass"` 的实盘 entry。

### NA-3. HighRiskTrailingRule 类名不一致是当前 bug

不采纳。

类名不影响 JSON 解析；这是未来 schema drift 风险，已列 P3-1。

### NA-4. ccxt_adapter 把 None 包装为空 dict

不采纳原表述。

当前审查路径中 `_call_exchange_method()` 和 `_build_probe_payload()` 会原样返回 payload；DS 后续确认该反驳成立。

### NA-5. Binance timeout 只靠字符串匹配

不采纳。

当前代码同时检查 `socket.timeout`、`TimeoutError`、`URLError.reason` 和字符串 fallback，不是纯字符串。

### NA-6. action 枚举完全一致

部分保留。

枚举值大体一致，但 `observe_only` 最终语义被折叠为 `wait`，所以不应简单标为完全无问题。

### NA-7. confidence 语义完全一致

部分保留。

取值范围一致，但展示语义不够精确，应按 P3-4 修改 dashboard 文案。

### NA-8. “9 Critical / 9 High / 8 Medium / 5 Low” 原严重度分布

不采纳。

按代码事实重校准后，应优先处理本文 P0/P1。DS 原 Critical 中多项属于 P1/P2 或不成立。

## 8. 推荐修复顺序

1. 修 P0-1、P0-2、P0-3：保护止损确认和 cleanup 改成归属感知、订单语义感知。
2. 修 P0-4、P1-1：worker 顶层状态、dashboard 状态、幂等恢复。
3. 修 P1-5、P1-6：kill switch 二次检查和提交前 live snapshot race。
4. 修 P1-2：factor lookup 排除未结算 handoff favorable 标签。
5. 修 P1-3、P1-4、P2-1：统一 risk_filter_status、probe cap、source_run_id。
6. 修 P1-7、P1-8、P1-9：scheduler 失败分类、missing research health、原子写。
7. 修 P2-9、P2-10、P2-11：degraded 语义、reduce 未实现前置阻断、research unavailable 持仓策略。
8. 修 dashboard 和 P2/P3 可观测性问题。

## 9. 最小验证计划

Bot worker：

- 新 stop 不会被 reduce cleanup 撤掉。
- 非 bot-owned algo stop 不会被撤。
- 无关 algo order 不会确认保护止损。
- entry accepted + stop rejected 显示失败且进入 recovery。
- rejected/timeout idempotency key 不被标记 completed。
- kill switch 在提交前出现时阻断风险增加命令。

Quant：

- 未结算 handoff 不进入 governance favorable lookup。
- 所有 `risk_filter_status` 在 bot 侧有一致分类。
- strict-live 下 `research_health=None` 产生 blocked factor signal。
- 缺失 scan quality artifact 产生明确 gate failure。
- transient scheduler failure 不永久退出 loop。
- research unavailable 已持仓时输出明确持仓策略。

Cross-repo：

- `ExecutionHandoff` 带 `source_run_id`。
- probe size 截断有显式 warning。
- high-risk handoff schema 在 bot 侧验证通过。
- reduce 未实现时 gate 层明确阻断，不产生 worker package。

Dashboard：

- worker partial failure 显示红色。
- hyphen run_id 能中文格式化。
- 暗色/亮色 CSS 契约与测试一致。
