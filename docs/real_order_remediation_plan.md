# 实盘下单链路修复方案

> 范围：`D:\开发\eth_trading_bot` 为主，覆盖与 `D:\开发\quant_system_rebuild` 的执行交接契约。本文档只描述修复计划、审查方法和测试矩阵，不代表已经启用真实下单。

## 当前结论

最近一次排查显示，系统没有下单是预期阻断：

- bot scheduler 最新周期没有生成 `latest_candidate_execution_package.json`。
- real worker 最近审计事件为 `candidate_execution_package_missing`，所以 worker 没有可消费的候选执行包。
- 最新 cycle 的实盘 gate 是 `allowed=false`，原因包括 `cycle_blocked_or_degraded`、`action_not_executable`。
- 当前策略侧 `effective_action=observe_only`，`trigger_ready=false`，`position_size_pct=0`，`executable_size_pct=0`。
- research health 仍是 `fresh_but_unqualified` / `qualified_candidate_count=0`，没有合格 live 候选。

这次修复不应该为了“能下单”而降低策略、风控、research gate。修复目标是让真实下单链路的安全边界一致、状态一致、幂等可靠、诊断可信。

## 修复原则

1. worker 是最后一道安全边界，不能只信任候选包。
2. 候选包只能表示“经过上游审查的意图”，不能替代 worker 自检。
3. 手动确认、kill switch、实盘 gate、幂等、状态写入都必须在 worker 层再次确认。
4. 真实仓位状态只能有一个 canonical state source，dashboard、worker、scheduler 必须读写一致。
5. quant 的 `execution_allowed` 和 bot 的 `real_auto_submit_allowed` 必须语义分离，不能复用同一个词表达不同级别的允许。
6. 所有修复先用 dry-run / fake adapter / tmp runtime 验证，不在修复过程中启动真实下单。

## 修复顺序

### Phase 0：冻结实盘风险面

修复前先确认：

- 不启动 `scripts\manage_runtime_stack.cmd start -EnableRealOrders`。
- 如果需要跑 runtime 验证，只跑 dry-run 或 tmp runtime。
- 如需在已有实盘栈上操作，先确认 kill switch 状态，必要时先启用 kill switch。
- 不修改 API key、仓位、交易所订单，不手工补单。

可能问题：

- 当前 runtime 仍可能有 worker 进程在轮询；代码修复前不要让它消费新候选包。
- 旧候选包如果未过期，可能被 worker 读到；修复测试要使用隔离的 tmp runtime。

### Phase 1：补齐 worker 级手动确认门

问题：

- `BotConfig.manual_entry_confirmation_required=True`，但 `_apply_manual_entry_confirmation_gate` 只在 `RuntimeMode.REAL` 里执行。
- `--enable-real-orders` 的 preflight cycle 用 `SIMULATED_REAL` 做规划，然后把 payload 标成 `runtime_mode=real`。
- scheduler 写候选包，worker 只查 package/gate/preflight，不查 manual confirmation。

修复逻辑：

- 改成 `proposal -> confirmation -> executable package` 三段协议，不能只在候选包里补字段。
- scheduler 在 gate/preflight 已通过但缺人工确认时，只写 `runtime/candidate_proposals/{proposal_id}.json`，不写 `latest_candidate_execution_package.json`。
- proposal 必须包含 human preview、entry risk 摘要、待确认命令集合、`confirmation_scope` 和 `expected_token` 展示信息；不包含 API key、passphrase 或其它交易所密钥。
- 用户通过终端运行确认命令：
  - `python scripts\confirm_candidate_execution.py --proposal-id <proposal_id> --confirm-token <token>`
  - dashboard 后续只能调用同一确认协议，不能另起一套写包逻辑。
- token 交付路径必须明确：scheduler/status/dashboard 显示 proposal path 和 confirm command；人工从该输出复制 token 后提交。若 future dashboard 按钮确认，也必须落到同一个 confirmation artifact。
- 确认脚本只写 `runtime/candidate_confirmations/{proposal_id}.json`：
  - `proposal_id`
  - `confirmed_at`
  - `confirmed_by`
  - `confirmation_scope`
  - `scope_hash`
  - `token_fingerprint`
  - `hash_algorithm`
  - `confirmation_contract_version`
- confirmation artifact 不写入明文 token。
- `confirmation_scope` 固定为 dict schema：
  - `contract_version`
  - `proposal_id`
  - `package_id`
  - `generated_at`
  - `expires_at`
  - `runtime_mode`
  - `exchange_symbol`
  - `command_fingerprints`
- 每个 `command_fingerprints` item 必须包含：
  - `target`
  - `operation`
  - `command_type`
  - `idempotency_key`
  - `payload_sha256`
- `scope_hash` 和 `payload_sha256` 使用 canonical JSON + SHA-256。若后续需要防本机伪造，升级为 HMAC-SHA256 并明确 secret 存放位置；不能在未定义 secret 来源时假装 HMAC 已生效。
- scheduler 下一轮读取 proposal + confirmation 后，必须同时满足三类条件才写 executable package：
  - confirmation valid：`scope_hash`、`token_fingerprint`、未过期、命令集合未变。
  - current gate valid：用当前周期重新评估 `real_order_gate.allowed=true`、`automation_boundary=real_order_submission_allowed`，不能复用 proposal 生成时的旧 gate。
  - current preflight valid：用当前价格、仓位、订单簿、tick size/step size、余额、风险参数重新跑 preflight，且所有 entry/stop/take-profit 命令仍 `preflight_ready`。
- 如果 confirmation valid 但当前 gate/preflight 已关闭，scheduler 不能写 executable package；必须写 `confirmation_accepted_but_gate_blocked` 或 `confirmation_accepted_but_preflight_stale` 事件，并在 status/dashboard 显示阻断原因。
- confirmation 只能一次性消费。executable package 写出后，应把 confirmation 标记为 `consumed` 并记录 `consumed_by_package_id`；重复消费同一 confirmation 必须阻断。
- executable package 增加 `manual_entry_confirmation` 字段：
  - `required`
  - `matched`
  - `confirmed_at`
  - `confirmed_by`
  - `confirmation_scope`
  - `scope_hash`
  - `token_fingerprint`
  - `hash_algorithm`
  - `confirmation_contract_version`
- worker 在 `_precheck_package` 再次校验：
  - entry 类命令存在；
  - `manual_entry_confirmation.required == true` 时必须 `matched == true`；
  - confirmation scope 必须绑定 proposal/package id、generated_at、expires_at、runtime mode、exchange symbol、命令 idempotency keys 和 payload hash；
  - worker 自己重算 scope hash 和 command fingerprints；
  - 否则返回 `manual_entry_confirmation_required`。

预期效果：

- 自动实盘路径不能绕过手动确认。
- 即使有人手工伪造候选包，worker 也会阻断。

可能问题：

- 如果未来需要“全自动小仓探针”，默认仍需要人工确认；任何跳过都必须显式配置、硬阈值、测试覆盖，例如 `manual_entry_confirmation_required_for_auto_probe=false` + `max_auto_probe_notional_usdt` + `risk_filter_status=pass` + `research_health_status=pass`。不能隐式绕过。
- token scope 设计不当会导致旧 token 可重放；必须绑定 package 和命令集合。
- 现有 `run_manual_entry_cycle.py --confirm-token` 是预览/手动入口，不等于 scheduler/worker 消费链路已经有输入协议；本阶段必须明确谁生成 proposal、谁展示 token、谁提交 confirmation、谁生成 executable package。
- proposal 过期清理由 scheduler 负责：每轮扫描 `runtime/candidate_proposals/`，过期 proposal 写 `proposal_expired` 事件，并从 dashboard/status 的待确认列表移除或标记为 expired。
- confirmation artifact 必须用 exclusive create 或 confirmation lock 写入，不能覆盖已有确认。CLI 和 dashboard 同时确认同一 proposal 时，第二个调用返回 `already_confirmed`；若已有 artifact 的 scope 不一致，返回 `confirmation_conflict` 并报警。
- proposal 列表只允许展示最新未过期 proposal；旧 proposal 只能在审计视图查看，不能继续展示为可确认操作。
- scheduler 写出 executable package 后，worker 仍必须最终复查 kill switch、idempotency、manual confirmation、gate/preflight 快照 freshness，不能把 scheduler 写包视为最终许可。

### Phase 2：统一 StateStore 写入路径

问题：

- preflight cycle 当前把 `state_store_path` 指向 per-cycle 输出目录。
- scheduler 把该 `state_path` 复制进候选包。
- worker 信任候选包里的 `state_path`。
- dashboard 固定读取 `runtime/state_store.json`。

修复逻辑：

- worker 不再信任候选包里的任意 `state_path`。
- real worker 的状态写入来源固定为 `--state-store-path`，默认来自 `BotConfig.state_store_path` / `runtime/state_store.json`。
- package 中可以保留 `planning_state_path` 用于审计，但不能作为 worker state output。
- package 可包含 `canonical_state_path` 用于审计，但 worker、dashboard、status 都不能信任 package 内该字段；canonical path 的观测值必须来自 config/runtime metadata。
- dashboard、scheduler、worker 文案统一区分：
  - `planning_state_path`
  - `canonical_state_path`
  - `worker_state_path`

预期效果：

- 真实提交后的仓位、恢复状态、protective stop 状态、recent idempotency keys 会写到 dashboard 和后续服务实际读取的位置。

可能问题：

- 旧测试依赖 package `state_path` 指向 tmp 文件，需要改成显式传入 canonical tmp runtime。
- 历史 per-cycle state 仍在 `.codex/memories` 或 runtime cycles 下，不能再当作真实状态源。
- 如果 worker、scheduler、dashboard 的 canonical path 来源不一致，状态仍会分叉；实现时必须把 path 来源写入 status 和测试断言。
- dashboard/status 显示 package 内 `canonical_state_path` 时必须标注为 `package_claimed_canonical_state_path`，不能把它显示成真实写入路径。

### Phase 3：把幂等检查移到锁内并保留锁外快查

问题：

- 当前 `_check_idempotency` 在 `WorkerLock` 外执行。
- pending audit 在锁内写入。
- 两个 worker 同时启动时，第二个可能在第一个写 pending 前通过检查，等待锁后继续提交。

修复逻辑：

- 保留锁外 `_check_idempotency` 作为快速返回。
- 获取 `WorkerLock` 后，立即再次 `_check_idempotency`。
- 锁内二次检查必须发生在：
  - kill switch 二次检查之后或之前均可；
  - 但必须在 pending event 和交易提交之前。
- 如果锁内检查发现 completed/pending/recovery key，返回阻断，不提交。
- 把并发防重和崩溃恢复拆开实现：
  - 并发防重：锁内复查 audit，防止两个 worker 消费同一个 idempotency key。
  - 崩溃恢复：pending audit 不能自动当作可重试，也不能永久黑箱阻断。
- pending audit 增加恢复字段：
  - `pending_lease_id`
  - `client_order_ids`
  - `recovery_status`
  - `recovery_required_at`
- audit JSONL 读取必须向后兼容旧行：旧 pending 缺少上述字段时，按 `recovery_status=recovery_required_unknown` 处理，不能自动重提，也不能因为字段缺失崩溃。
- 新增只读 reconciliation 命令或 worker 子命令，通过 exchange client order id 查询订单状态，写入 `recovered_submitted`、`recovered_not_found` 或 `recovery_unknown`。
- 在 recovery artifact 明确确认未提交或已完成前，后续 worker 对同 idempotency key 必须阻断。
- `stale_after_sec` 只解决锁文件接管；不负责清理 pending audit。锁接管后仍必须通过 recovery 协议处理 pending。

预期效果：

- 同一候选包、同一 idempotency key 在并发 worker 下最多有一个进入提交路径。

可能问题：

- audit log 损坏或被截断时，幂等判断可能失效；需要同时加强 JSONL 读取诊断。
- 锁内重复读 audit 会增加极小 IO 成本，可以接受。
- 如果 worker 已向交易所提交订单但在写 completion 前崩溃，下一轮只能通过 exchange reconciliation 恢复，不能直接重提。
- 默认 `stale_after_sec=900` 已有进程存活保护，但实盘恢复文档要说明等待窗口和人工介入方式。
- recovery 命令必须是只读查询，不允许在恢复流程里顺手补单；恢复只负责确定“交易所是否已收到这组 client order id”。

### Phase 4：修正 `status` 的 worker mode 诊断

问题：

- `manage_runtime_stack.ps1 status` 用当前命令是否带 `-EnableRealOrders` 推断 worker mode。
- 如果启动时是 submit-enabled，但 status 没带参数，会显示 `dry_run`。

修复逻辑：

- 启动 worker 时写 runtime metadata，例如 `runtime/real_order_worker/mode.json`：
  - `submit_real_orders`
  - `started_at`
  - `kill_switch_at_start`
  - `command_line`
- status 优先读取 metadata，其次读取进程命令行，最后才 fallback 到参数推断。
- 如果 metadata 与当前 kill switch 冲突，显示 `submit_enabled_but_kill_switch_on`。

预期效果：

- 排障时可以分清“worker 没开 submit”、“没候选包”、“gate 阻断”、“kill switch 阻断”。

可能问题：

- 旧 worker 没有 metadata，status 需要兼容 fallback。
- Windows PowerShell 读取命令行可能受权限影响，不能作为唯一来源。

### Phase 5：明确 quant -> bot 执行契约

问题：

- quant 侧 `execution_allowed=true` 有时表示“量化层允许小探针/候选执行”。
- bot 侧 real gate 要求 `risk_filter_status=pass` 且 cycle 不 degraded。
- 同一个 dashboard 上容易显示“上游可执行，但 bot 不下单”。

修复逻辑：

- 增加 `execution_handoff_contract_version`，并在 quant/bot 两仓库测试里固定版本行为。
- quant handoff 增加或重命名字段：
  - `quant_execution_allowed`
  - `real_auto_submit_candidate`
  - `real_auto_submit_block_reason`
- quant handoff 必须同时传递上游可信度诊断字段：
  - `factor_lookup_generated_at`
  - `factor_lookup_age_seconds`
  - `factor_lookup_stale`
  - `factor_lookup_empty`
  - `scoring_chain_frozen`
  - `transition_reason_codes`
- 迁移期双写双读：
  - quant 先同时写旧 `execution_allowed` 和新字段。
  - bot 读取层优先读 `real_auto_submit_candidate`。
  - 读不到 `real_auto_submit_candidate` 时默认 `false`，不能用旧 `execution_allowed=true` 自动推导真实提交许可。
  - dashboard 同时展示旧字段和新字段，明确旧字段是量化层许可。
- bot automation gate 的实盘自动提交判断切到 `real_auto_submit_candidate`；旧 `execution_allowed` 只作为兼容/诊断字段。
- bot/dashboard 不能只信任 quant 传来的 `factor_lookup_stale` 布尔值；必须用 `factor_lookup_generated_at` 自己重算 `factor_lookup_age_seconds`，并与 handoff 字段交叉校验。
- 如果 bot 侧重算发现 factor lookup 超过阈值、`factor_lookup_stale=false` 但 age 超阈值、或 `scoring_chain_frozen=true`，则该 handoff 对真实自动提交不可信，`real_auto_submit_candidate` 必须按 false 处理，并显示上游可信度告警。
- 上游可信度告警不能自动触发真实下单、放宽 research gate、或把 stale/empty lookup 修正成合格；只能阻断并提示人工排查。
- live freshness 阈值默认 `3h`，并通过环境变量显式覆盖：
  - quant producer: `FACTOR_LOOKUP_MAX_AGE_SEC` / `QUANT_FACTOR_LOOKUP_MAX_AGE_SEC`
  - bot risk gate: `BOT_FACTOR_LOOKUP_MAX_AGE_SEC` / `FACTOR_LOOKUP_MAX_AGE_SEC`
  - dashboard: `DASHBOARD_FACTOR_LOOKUP_MAX_AGE_SEC` / `FACTOR_LOOKUP_MAX_AGE_SEC`
- quant scheduler 在每次 `run-cycle` handoff 前检查 `factor_lookup_summary.json`；对缺失、空、过期、未来时间的 lookup 只做一次 bounded rebuild，并重读验证。若重建后仍不健康，只写 `factor_lookup_recovery.status=unhealthy_after_rebuild`，不自动放宽 research gate 或真实提交条件。
- 独立只读监控脚本为 `scripts/diagnostics/monitor_handoff_freshness.py`，返回 JSON；`status=alert` 时退出码为 `2`。
- `scoring_chain_frozen` 由 quant scheduler 在 `run-cycle --scoring-freeze-window` 中检测，默认窗口为 `6` 个 live handoff；命中时同时写入 `transition_reason_codes` 和 `execution_warnings`。
- dashboard 文案区分：
  - “量化可执行”
  - “实盘自动提交可执行”
  - “仅跟踪/仅观察”
  - “上游 handoff 可信度”

预期效果：

- degraded 小探针、research degraded、soft warning 等状态不会被误读成真实下单许可。
- 即使 quant 侧 freshness 检测或 `factor_lookup_stale` 标记失效，bot/dashboard 也能通过时间戳重算发现上游 handoff 不可信。

可能问题：

- 两个仓库要同步改契约和测试；不能只改一边。
- 历史 artifact 缺新字段，读取层要兼容默认值。
- 跨仓同步顺序必须是“兼容读 -> 双写 -> bot 切读新字段 -> dashboard 改文案 -> 移除旧语义依赖”，不能先删旧字段。
- degraded small_probe 可以是 `quant_execution_allowed=true`，但默认 `real_auto_submit_candidate=false`；只有满足明确白名单和安全阈值时才允许进入自动提交候选。
- 历史 handoff 缺 `factor_lookup_generated_at` 或 `factor_lookup_age_seconds` 时，bot 侧必须显示 `handoff_freshness_unknown`，并默认不允许真实自动提交。

### Phase 6：梳理“软警告”与实盘阻断文案

问题：

- `diagnostic_optional_macro_source` 文案是“软警告”。
- 但 `NetworkGuard` 只要 `degraded=True`，最终 `allow_real_entry=False`。

修复逻辑：

- 选择 guard 输出拆分，不保留 OR 决策：
  - `signal_tracking_degraded`
  - `real_entry_blocked`
  - `real_entry_block_reason`
- dashboard 文案改成“对信号跟踪为软警告；对实盘自动开仓仍阻断”，但文案必须消费拆分后的 guard 字段。

预期效果：

- 用户看到“软警告”时不会误以为实盘下单也允许。

可能问题：

- 如果简单把 `degraded` 不再阻断实盘，会降低安全边界；不能这样修。

### 审查校正结论

两处容易误判的问题要在实现和 review 中固定口径：

- 候选包单文件写入已经通过 `atomic_write_json` 原子化；真实缺口不是“单文件非原子”，而是 archive package 与 `latest_candidate_execution_package.json` 两个文件之间缺少一致性协议。定稿设计：先写 immutable archive package，再把 `latest_candidate_execution_package.json` 写成 manifest/pointer，只包含 `package_id`、`archive_path`、`archive_sha256`、`generated_at`、`expires_at`、`manifest_version`。worker 先读 latest manifest，再打开 archive 并校验 `archive_sha256`；hash 不匹配或 archive 缺失时阻断。
- exchange 级幂等已经由 `idempotency_key` 派生 client order id：Binance/OKX adapter 都用 `sha256(command.idempotency_key)[:16]` 构造 `ethbot-{alias}-{digest}`。真实缺口不是“没有联动”，而是联动是隐式契约；需要写成明文契约、加独立测试，并让 recovery/reconciliation 用该确定性 client order id 查询交易所订单状态。

## 审查怎么审查

### 代码审查清单

必须逐项过：

- real worker 是否不信任 package 内的安全字段。
- worker 是否在锁内复查 kill switch、idempotency、manual confirmation。
- candidate package 是否不含明文 token / API key / passphrase。
- state path 是否只有 canonical 写入口。
- scheduler 是否只在 gate allowed 且 preflight ready 且 manual confirmation matched 时写候选包。
- dashboard 是否显示真实阻断原因，而不是只显示 missing package。
- quant/bot 字段语义是否一一对应，不能同名不同义。

### 风险审查清单

- 是否新增了任何绕过 `risk_filter_status=pass` 的实盘入口。
- 是否新增了任何绕过 `kill_switch` 的路径。
- 是否允许 degraded/research_unqualified 进入真实提交。
- 是否有旧候选包、旧 token、旧 idempotency key 可重放。
- 是否有 worker 异常后状态被写到错误 state store。

### 可观测性审查清单

- 没有候选包时，status 要显示 `candidate_execution_package_missing`。
- gate 阻断时，status/dashboard 要显示 gate reason codes。
- worker dry-run / submit-enabled 要从实际运行状态读取。
- state path 要显示 canonical path。
- manual confirmation 阻断要显示为安全阻断，不显示为普通 preflight 失败。

## 测试怎么测试

## DSDS 覆盖缺口核对结果

核对口径：

- DSDS 表里的多数项目是“按本修复方案落地后必须补的测试”，不是“当前已经实现但测试缺失”。
- 当前大部分新契约还没有实现，例如 `manual_entry_confirmation`、`planning_state_path`、`canonical_state_path`、`real_auto_submit_candidate`、`quant_execution_allowed`，因此对应测试现在为 0 是符合现状的。
- 表中有几项应从“0 覆盖”修正为“旧逻辑有部分覆盖，但新安全契约没有覆盖”。

### `tests/test_real_order_worker_script.py`

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| worker 阻断缺 manual confirmation 的 entry | 准确缺口 | worker `_precheck_package` 当前不读 `manual_entry_confirmation`，只校验 submit flag、kill switch、gate、runtime mode、engine mode、preflight。 |
| worker 阻断 confirmation scope 不匹配 | 准确缺口 | 当前没有 `confirmation_scope` 字段和校验逻辑。 |
| worker 放行 matched manual confirmation | 准确缺口 | 当前没有 worker 级 manual confirmation 放行路径。 |
| worker 用 canonical state path 写状态，不用 package `state_path` | 准确缺口 | `_resolve_state_path` 当前优先信任 package 内 `state_path`。 |
| worker 锁内重新幂等检查命中 pending/completed | 部分覆盖但缺新契约 | 现有串行 replay/pending/completed 测试存在；缺的是获取 `WorkerLock` 后再次 `_check_idempotency` 的测试。 |

### `tests/test_bot_runtime_scheduler_script.py`

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| gate allowed 但 manual missing 时只写 proposal，不写 executable candidate | 准确缺口 | `_write_candidate_execution_package` 当前只看 gate、automation boundary、preflight。 |
| proposal 展示 confirm command，confirmation artifact 不含明文 token | 准确缺口 | 当前没有 proposal/confirmation/executable 三段协议。 |
| scheduler 校验 confirmation scope 后才写 candidate | 准确缺口 | 当前没有 `candidate_confirmations` 读取和 scope hash 校验。 |
| confirmation valid 但当前 gate/preflight 已关闭时不写 candidate | 准确缺口 | 当前方案需要补跨周期重评，避免旧 proposal 在市场变化后放行。 |
| 过期 proposal 被清理或标记 expired | 准确缺口 | 当前没有 proposal 过期清理/status 展示规则。 |
| 同一 proposal 并发确认只成功一次 | 准确缺口 | 当前没有 confirmation lock 或 exclusive create 语义。 |
| 候选包含 confirmation metadata 且无明文 token | 准确缺口 | 当前候选包没有 `manual_entry_confirmation` metadata，也没有 token 泄漏断言。 |
| 候选包区分 planning state 与 canonical state | 准确缺口 | 当前只写 `state_path`，没有 `planning_state_path` / `canonical_state_path` 区分。 |
| archive/latest 双文件一致性 | 准确缺口 | 单文件 `_write_json` 已原子化；最新设计固定为 latest manifest 指向 immutable archive 并校验 `archive_sha256`。 |

### `tests/test_automation_gate.py`

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| degraded / non-pass risk 仍不能 real auto submit | 部分覆盖 | 旧 gate 已测试 `risk_filter_status != pass` 会阻断；缺的是新字段 `real_auto_submit_candidate=false` 的语义测试。 |
| `real_auto_submit_candidate` 与旧字段兼容 | 准确缺口 | 当前 gate 输出没有该字段。 |

### `tests/test_runtime_stack_manager_script.py`

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| status 从 mode metadata 文件读取 submit-enabled | 准确缺口 | 当前没有 `runtime/real_order_worker/mode.json` 读取逻辑。 |
| status 没带 `-EnableRealOrders` 时不误报 dry-run | 准确缺口 | 当前 `$workerMode` 由本次 status 命令参数推断，可能和实际 worker 启动模式不一致。 |
| dashboard/status 不信 package 内 canonical path | 准确缺口 | canonical state path 必须来自 config/runtime metadata；package 内路径只能显示为 untrusted claim。 |

### `tests/test_execution_handoff_block_reason.py`（quant）

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| degraded small probe: `quant_execution_allowed=true` 但 `real_auto_submit_candidate=false` | 部分覆盖但缺新契约 | quant 现在已有 degraded small probe 的旧行为测试，也有强探针允许测试；缺的是拆分字段后的双层语义测试。 |
| risk pass + trigger ready + size > 0 + research pass -> real candidate | 准确缺口 | 当前没有 `real_auto_submit_candidate=true` 字段和对应综合条件测试。 |
| handoff 传递 factor lookup freshness / scoring freeze 诊断字段 | 准确缺口 | Phase 5 必须把 `factor_lookup_generated_at`、`factor_lookup_age_seconds`、`factor_lookup_stale`、`scoring_chain_frozen`、`transition_reason_codes` 纳入跨仓契约。 |
| bot/dashboard 独立重算 factor lookup age | 准确缺口 | 不能只信 quant 的 `factor_lookup_stale`；当 age 超阈值或 stale flag 与 age 冲突时，真实自动提交必须阻断并告警。 |

### 并发测试

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| 两个 worker 同一 package，锁内重现幂等检查 | 准确缺口 | 现有测试覆盖串行重复，不覆盖两个 worker 竞争同一 idempotency key 的竞态窗口。 |
| pending 崩溃后必须 recovery，不自动重提 | 准确缺口 | 现有 pending 阻断测试存在，但缺少通过 client order id 查询交易所并写 recovery artifact 的流程测试。 |
| 旧 pending audit 缺 recovery 字段也不崩溃、不重提 | 准确缺口 | 新 pending schema 必须向后兼容历史 JSONL 行。 |

### `tests/test_confirm_candidate_execution_script.py`

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| CLI 用 proposal id + token 写 confirmation artifact | 准确缺口 | 当前没有 `scripts/confirm_candidate_execution.py`。 |
| token fingerprint 使用固定 hash algorithm，artifact 不含明文 token | 准确缺口 | 当前没有 confirmation artifact schema。 |
| scope 不匹配、过期 proposal、命令 hash 改变时拒绝确认 | 准确缺口 | 当前没有 proposal/confirmation scope 校验。 |
| 已存在 confirmation 时返回 already_confirmed 或 conflict | 准确缺口 | 当前没有并发确认写入协议。 |

### `tests/test_exchange_idempotency_contract.py`

| 方案要求 | 核对结论 | 说明 |
|---|---|---|
| Binance/OKX client order id 稳定派生自 command idempotency key | 部分覆盖但缺明文契约 | adapter 层已有派生逻辑和若干测试；缺独立契约测试和 recovery 查询复用断言。 |
| recovery 使用同一派生 client order id 查询交易所订单 | 准确缺口 | 当前 pending recovery 没有利用该确定性映射。 |

### DSDS 结论修正

DSDS 的方向是对的，真实缺口主要集中在实盘安全边界。但表述应调整为：

- worker manual confirmation、canonical state path、scheduler candidate metadata、status metadata、quant/bot 新字段：当前确实没有覆盖。
- worker idempotency：不是 0 覆盖，而是“串行覆盖已有，并发 + 锁内复查缺失”。
- automation gate：不是 0 覆盖，而是“旧字段阻断已有，新字段 `real_auto_submit_candidate` 未覆盖”。
- quant handoff：不是完全 0 覆盖，而是“旧 `execution_allowed` 行为已有部分测试，新拆分契约未覆盖”。
- quant -> bot handoff 可信度：当前 incident 文档已有诊断方案，但 Phase 5 执行契约也必须包含 freshness 字段和 bot 侧重算规则，否则 bot dashboard 会继续依赖 quant 内部 stale 判断。
- candidate package 写入：不是单文件原子性缺失，而是 archive/latest 双文件一致性缺口。
- exchange idempotency：不是没有从 worker key 联动到交易所 client order id，而是缺明文契约、独立测试和 recovery 复用。
- proposal/confirmation：不能只校验旧 scope，必须在写 executable 前用当前周期重跑 gate/preflight，并清理过期 proposal。
- state path 观测：package 内 `canonical_state_path` 是不可信声明，dashboard/status 必须显示 config/runtime metadata 的真实路径。

按风险排序：

1. P0：proposal/confirmation/executable 输入协议、跨周期 gate/preflight 重评、proposal 过期清理、worker 人工确认、锁内幂等、pending recovery、canonical state path。
2. P1：scheduler 不写无确认候选包、候选包 metadata/token 安全、confirmation 并发写、archive/latest 一致性。
3. P2：runtime status 真实 mode、quant/bot 字段拆分、exchange idempotency 明文契约。
4. P3：dashboard/文案兼容旧字段。

### 最小单元测试

在 `eth_trading_bot`：

- `tests/test_real_order_worker_script.py`
  - worker 在 entry package 缺 manual confirmation 时阻断。
  - worker 在 manual confirmation scope 不匹配时阻断。
  - worker 在 manual confirmation matched 时才允许进入 fake adapter。
  - worker 使用 canonical state path，不使用 package `state_path` 写状态。
  - worker 在锁内二次幂等检查命中 pending/completed 时不调用 adapter。

- `tests/test_bot_runtime_scheduler_script.py`
  - gate allowed 但 manual confirmation missing 时只写 proposal，不写 executable candidate package。
  - proposal 输出 proposal path、human preview、confirm command。
  - scheduler 读取 matching confirmation 后才写 executable candidate package。
  - scheduler 在 confirmation valid 但当前 gate/preflight blocked 时不写 candidate，并输出 `confirmation_accepted_but_gate_blocked` 或 `confirmation_accepted_but_preflight_stale`。
  - scheduler 清理或标记过期 proposal，dashboard/status 不再显示为可确认。
  - scheduler 拒绝 scope hash/token fingerprint 不匹配的 confirmation。
  - latest manifest 指向 immutable archive，worker 校验 `archive_sha256` 后才消费。
  - candidate package 字段包含 manual confirmation metadata，但不包含明文 token。
  - package 区分 planning state 和 canonical state。

- `tests/test_confirm_candidate_execution_script.py`
  - CLI 用 `--proposal-id` + `--confirm-token` 写 confirmation artifact。
  - confirmation artifact 包含 `scope_hash` / `token_fingerprint` / `hash_algorithm`，不包含明文 token。
  - 过期 proposal、命令 payload hash 改变、token 错误时拒绝确认。
  - 并发确认同一 proposal 时只允许一个 artifact 成功写入；重复确认返回 `already_confirmed`，scope 不一致返回 `confirmation_conflict`。

- `tests/test_automation_gate.py`
  - degraded / non-pass risk 仍不能 real auto submit。
  - 新字段 `real_auto_submit_candidate` 与旧字段兼容。

- `tests/test_runtime_stack_manager_script.py`
  - status 从 mode metadata 显示 submit-enabled。
  - status 未带 `-EnableRealOrders` 时不误报 dry-run。
  - dashboard/status 的 canonical state path 来自 config/runtime metadata，不来自 package claim。

在 `quant_system_rebuild`：

- `tests/test_execution_handoff_block_reason.py`
  - degraded small probe 可以 `quant_execution_allowed=true`，但 `real_auto_submit_candidate=false`。
  - risk pass、trigger ready、size positive、research pass 时才允许 real candidate。
  - handoff 必须输出 `factor_lookup_generated_at`、`factor_lookup_age_seconds`、`factor_lookup_stale`、`scoring_chain_frozen`、`transition_reason_codes`。
  - stale/empty factor lookup 或 scoring-chain frozen 时，`real_auto_submit_candidate=false`，并输出明确 block reason。

在 `eth_trading_bot` dashboard / handoff 读取测试：

- 历史 handoff 缺 freshness 字段时，dashboard 显示 `handoff_freshness_unknown`，真实自动提交默认 false。
- handoff `factor_lookup_stale=false` 但 bot 侧按 `factor_lookup_generated_at` 重算 age 超阈值时，dashboard 显示上游可信度告警，automation gate 不允许 real auto submit。
- `scoring_chain_frozen=true` 时，dashboard/status 显示红色运营故障，不显示为普通 `not_entry_action`。

- `tests/test_exchange_idempotency_contract.py`
  - Binance/OKX client order id 对同一个 `idempotency_key` 稳定一致。
  - recovery 查询使用同一 client order id 派生函数。

### 并发测试

目标：证明两个 worker 不能提交同一 idempotency key。

做法：

- 使用 fake adapter，不访问交易所。
- 构造同一 candidate package。
- 第一个 worker 进入锁内后写 pending。
- 第二个 worker 等待锁释放后重新检查 audit。
- 断言第二个 worker 返回 `pending_idempotency_key_requires_recovery` 或 `idempotency_key_already_completed`。
- 断言 fake adapter submit count 只有 1。
- 构造“提交后崩溃、只有 pending 无 completion”的 audit，断言 worker 不重提，只进入 recovery-required。
- 构造 exchange 侧存在同 client order id 的订单，断言 recovery artifact 标记 `recovered_submitted`。

### 集成测试

使用 tmp runtime：

- scheduler 生成 cycle，但 gate blocked 时不写 candidate。
- gate allowed + manual missing 时写 proposal、不写 candidate。
- gate allowed + matching confirmation + current preflight ready 时写 candidate。
- matching confirmation 但 current gate/preflight blocked 时不写 candidate。
- 过期 proposal 从待确认列表移除或显示 expired。
- worker dry-run 不提交真实订单。
- worker submit flag missing 返回 `submit_real_orders_flag_missing`。
- kill switch on 返回 `kill_switch_enabled`。

### 回归测试命令

先跑窄范围：

```powershell
.\.venv_win\Scripts\python.exe -m pytest tests\test_real_order_worker_script.py tests\test_bot_runtime_scheduler_script.py tests\test_automation_gate.py --basetemp=.tmp_pytest
```

再跑 runtime/status 相关：

```powershell
.\.venv_win\Scripts\python.exe -m pytest tests\test_runtime_stack_manager_script.py tests\test_dashboard_data_sources.py tests\test_network_guard.py --basetemp=.tmp_pytest
```

再跑 quant 交接契约：

```powershell
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_execution_handoff_block_reason.py tests\test_policy_engine.py --basetemp=.tmp_pytest
```

最后才跑全量：

```powershell
.\.venv_win\Scripts\python.exe -m pytest --basetemp=.tmp_pytest
```

全量通过也不代表可以立刻开真实下单。必须再做 dry-run runtime 验证。

## dry-run 验证流程

1. stop 当前 runtime stack。
2. 使用隔离 runtime root 启动 dry-run。
3. 确认 dashboard 能显示：
   - real gate reason codes；
   - candidate package 状态；
   - worker mode；
   - canonical state path。
4. 构造 fake allowed package，验证 worker dry-run 阻断或模拟提交路径。
5. 不连接真实交易所，不使用真实 API key 做提交验证。

## 实盘恢复前检查

只有以下全部满足，才可以考虑恢复 `-EnableRealOrders`：

- 全量测试通过。
- manual confirmation worker gate 已覆盖。
- state path 不再分叉。
- 幂等锁内复查已覆盖并发测试。
- status 能准确显示 worker submit mode。
- research health 不再 `fresh_but_unqualified`，且 qualified candidate > 0。
- 最新 cycle 的 `effective_action` 是 entry/reduce/exit/protect 中的可执行动作，不是 `observe_only` / `wait`。
- `trigger_ready=true`。
- `position_size_pct` 或 `executable_size_pct` 大于 0。
- `risk_filter_status=pass`。
- `real_order_gate.allowed=true`。
- kill switch 状态明确。

## 推荐落地顺序

1. 先修 Phase 1 worker manual confirmation gate。
2. 再修 Phase 3 幂等锁内复查。
3. 再修 Phase 2 canonical state path。
4. 再修 Phase 4 status 诊断。
5. 最后修 Phase 5/6 契约和文案。

原因：

- Phase 1 和 Phase 3 是真实下单安全边界，优先级最高。
- Phase 2 影响成交后的恢复和 dashboard 正确性，紧随其后。
- Phase 4 到 Phase 6 主要减少排障误判和契约歧义，但不应先于安全边界。
