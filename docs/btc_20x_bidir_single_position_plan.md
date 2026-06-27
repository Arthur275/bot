# BTC 20x 双向单仓设计方案

> 日期：2026-05-18  
> 范围：设计 BTC USDT 永续 20x、15m-1h、多空双向、单仓位量化与下单系统。  
> 本文是工程设计和验收清单，不代表已经启用 BTC 实盘，也不授权修改真实下单风险限制。

## 一句话结论

BTC 20x 不能靠“把 ETH 配置里的 symbol 改成 BTC、leverage 改成 20”上线。现在代码里还有多处 ETH/10x 硬编码，会直接拒绝 BTC，或者更危险地把 ETH/BTC 状态混在一起。

正确路线是：

1. 保留现有 ETH 10x 系统不动，新增独立 `btc_20x_bidir` profile。
2. 先做 profile/state/candidate/audit/dashboard 全隔离。
3. 再做 BTC 专用 research、止损、仓位、反手、加回仓逻辑。
4. 最后按 shadow -> paper -> 小额手动确认实盘 -> 自动实盘 的顺序放量。

## 目标

目标系统：

- 标的：BTC USDT perpetual/swap。
- 杠杆：20x，但仓位风险按账户风险和止损距离计算，不按“20x 就开大”计算。
- 周期：1h 判断大方向和结构，15m 做主触发，必要时 5m 只用于执行价/滑点检查，不让 5m 单独决定方向。
- 方向：long 和 short 都允许。
- 仓位模式：单仓位，不同时持有 long 和 short hedge。
- 执行方式：候选包 -> bot gate -> preflight -> worker 二次校验 -> 下单。
- 出场：必须有保护止损，必须有 TP/SL 计划，允许部分止盈、同向加回、强反转后反手。

## 非目标和硬边界

这些在 v1 里不做：

- 不直接把现有 ETH 实盘改成 BTC。
- 不让 ETH 和 BTC 共用同一个 `state_store.json`。
- 不让一个 candidate package 同时代表 ETH 和 BTC。
- 不做同一 tick 的净额反手，也就是不在一个动作里从 long 直接打成 short。
- 不允许没有保护止损的 BTC 20x 开仓。
- 不允许 research 缺失、数据过期、factor lookup 空/坏时自动开仓。
- 不把某次历史出现过的 `59%` 当固定阈值。胜率、thesis、confidence 都必须由 BTC research 校准。
- 不在 research 管线没跑通前启用 BTC 自动真实提交。

## 核心交易语义

### 单仓位状态机

BTC profile 独立维护一个状态机：

| 状态 | 含义 | 允许动作 |
|---|---|---|
| `FLAT` | 没有 BTC 仓位 | 可开 long 或 short |
| `LONG_OPEN` | 持有 BTC long | 可减仓、止盈、止损、同向加回、反转退出 |
| `SHORT_OPEN` | 持有 BTC short | 可减仓、止盈、止损、同向加回、反转退出 |
| `REDUCED_LONG` | long 已部分止盈或主动减仓 | 可继续持有、退出、同向加回 |
| `REDUCED_SHORT` | short 已部分止盈或主动减仓 | 可继续持有、退出、同向加回 |
| `EXIT_PENDING` | 已发出 reduce-only 退出命令，等待交易所确认 | 只能确认/恢复/告警，不能开新仓 |
| `REVERSE_PENDING` | 反手第一阶段，旧方向正在退出 | 只能等旧仓确认归零，不能直接开反向 |

硬约束：

- 每个 profile 同一时间只能有一个 `position_direction`。
- `LONG_OPEN` 收到普通 short 信号时，先走退出/减仓，不直接开 short。
- `SHORT_OPEN` 收到普通 long 信号时，先走退出/减仓，不直接开 long。
- 只有交易所确认 profile 仓位为 `FLAT` 后，才允许反向新开。

### 开仓规则

BTC 开仓必须同时满足：

- profile 是 `btc_20x_bidir`。
- 当前 BTC profile 状态是 `FLAT`。
- 方向信号为 long 或 short，且不是 observe/wait。
- 1h 结构和 15m 触发不冲突。
- 数据健康通过：行情、factor lookup、research bundle、governance 都 fresh。
- research 对应方向合格。long 合格不能自动代表 short 合格。
- stop plan 已生成，且 stop 距离在 BTC 20x profile 允许范围内。
- TP plan 已生成，至少包含 TP1 和最终退出条件。
- 仓位大小按账户风险解算后不超过 margin/notional/leverage 上限。
- bot automation gate、preflight、worker 二次校验全通过。
- v1 小额实盘阶段仍需要人工确认。

### 部分止盈后同向加回

DS 提到的缺口是对的：现在 `live_position_not_flat` 全线拦截，会挡住“TP1 吃掉 50% 后，同方向新信号更强，想把仓位加回目标仓”的场景。

BTC 20x v1 应允许同向加回，但只允许“受控恢复”，不是无限加仓。

允许条件：

- 同一个 profile、同一个 symbol、同一个方向。
- 当前仓位不是满目标仓，且已经部分减仓或 TP1 后剩余仓位低于 target。
- 当前仓位有有效保护止损，且交易所确认止损单存在。
- 新信号强于原入场信号，或出现新的结构确认。
- 加回后总 notional 不超过 profile 上限。
- 加回后按新的平均入场价重算 stop/TP。
- 加回产生的新增风险不超过账户风险预算。
- 若原仓已亏损且 stop 未收紧，不允许加回摊平。

建议 v1 规则：

- 只允许加回到“本轮目标仓”，不允许超过目标仓。
- TP1 后最多加回一次。
- 加回必须重新生成 candidate package，不能复用旧入场包。
- 加回命令必须带 `addback_reason`、`previous_tp_stage`、`new_risk_delta`。

### 反手规则

反手可以做，但不能做成“一个信号来了就立刻净额翻过去”。

v1 采用两级反手：

| 模式 | 行为 | v1 默认 |
|---|---|---|
| `reverse_exit_only` | 出现反向信号时，只减仓或平掉旧仓 | 默认开启 |
| `reverse_enter_after_flat` | 强反转时，先 reduce-only 平旧仓，确认 `FLAT` 后，再开反向新仓 | 条件开启 |
| `same_tick_net_reverse` | 同一执行动作里直接从 long 翻 short 或从 short 翻 long | 禁止 |

强反转的最低条件：

- 1h 结构已经反向，15m 出现反向触发。
- 当前方向的 thesis 已失效，而反向 thesis 通过 BTC research gate。
- 旧仓退出命令必须是 reduce-only。
- 交易所回报和 state store 都确认 `FLAT`。
- 反向开仓使用新的 candidate id、idempotency key、stop、TP、risk calculation。
- 止损后或强反转后设置 cooldown，避免连续来回打。

## BTC 20x 风控设计

### 20x 不是止损距离简单减半

DS 说“BTC ATR 更大、20x 等于止损空间小 50%”方向上提醒是对的，但实现上不能简单把 ETH 的 stop distance 除以 2。

正确做法：

```text
position_notional = account_risk_usdt / stop_distance_pct
required_margin = position_notional / leverage
```

也就是说：

- 止损距离来自 BTC 波动率、结构位、ATR、盘口滑点，而不是来自 leverage。
- leverage 影响保证金占用和强平风险，不应该直接决定策略止损。
- 如果 BTC 结构需要 1.2% 止损，但账户风险只允许亏 0.2%，那就缩小仓位，不是硬把止损压到 0.4%。

### 初始风险上限建议

上线前建议先用保守参数：

| 项 | 建议初始值 | 说明 |
|---|---:|---|
| 单笔账户风险 | `0.15%-0.25%` | 指 stop 触发的账户权益损失，不是 margin 比例 |
| 单笔 margin 占用 | `0.5%-1.0%` | 20x 下保证金占用小，但爆仓风险高 |
| 单日最大亏损 | `0.6%-1.0%` | 达到后当天不再开新仓 |
| 连续止损冷却 | `2-4` 根 15m K | 防止震荡里连续打 |
| 反手冷却 | 至少 `1` 根 15m K | 除非强反转并已确认 flat |
| 最大同时仓位 | 1 | 单仓位模式 |

这些不是最终参数，只是启动 paper/shadow 的安全默认。最终要由 BTC research 和 paper/live 小样本校准。

### 止损距离

BTC profile 需要自己的 stop resolver，不能复用 ETH 模板。

当前 quant 侧风险点：

| 文件 | 当前问题 | BTC 需要 |
|---|---|---|
| `D:\开发\quant_system_rebuild\src\policy\exit_plan_builder.py:43` | `thesis_template = 0.012 + 0.018 * max(0.0, 1.0 - assessment.thesis_score)` 是 ETH 风格模板 | 改为 profile-aware resolver |
| `D:\开发\quant_system_rebuild\src\policy\exit_plan_builder.py:45` | `min(0.035, ...)` 使用固定上限 | BTC 20x 需要独立 min/max/ATR clamp |

BTC stop resolver 应输入：

- symbol/profile。
- 方向 long/short。
- 1h ATR、15m ATR。
- 最近 swing high/low。
- 入场触发类型。
- 盘口/滑点估计。
- funding/波动 regime。
- thesis/confidence/research score。

输出：

- `stop_distance_pct`。
- `stop_price`。
- `invalid_reason`。
- `risk_per_unit`。
- `liquidation_buffer_pct`。
- `stop_model_version`。

硬边界：

- stop 太近会被噪音打掉，拒绝。
- stop 太远导致 position_notional 太小或风险不划算，拒绝。
- stop 到强平价 buffer 不足，拒绝。
- 不能为了凑 20x 而把止损压到不合理位置。

### TP/SL 结构

可以沿用“分档止盈”的思想，但 BTC profile 要重新校准。

建议 v1：

- TP1：减 `40%-50%`，用于回收风险。
- TP2：减 `25%-35%`，用于趋势延续。
- Runner：剩余 `15%-25%`，用 trailing/结构破坏退出。
- TP1 后止损至少移动到降低净风险的位置；是否 break-even 由滑点和手续费决定。
- TP1 后若要同向加回，必须重新计算新增风险，不允许只看剩余仓位比例。

## 方向判断：15m-1h 怎么用

15m 到 1h 比 5m 更适合作为 BTC 20x 的主判断周期，因为：

- 噪音比 1m/5m 少。
- 结构高低点更稳定。
- 交易次数不会太密。
- stop 可以更贴近结构，而不是被微小波动反复扫。

但这不等于系统能“精准找到底部和顶部”。更实际的说法是：

- 1h 判断当前处于趋势、震荡、急跌、反抽、突破失败等 regime。
- 15m 判断是否出现可交易触发。
- bottom/top 只作为“风险收益区域”和“结构失效点”识别，不作为抄底摸顶信号。

建议 long 触发：

- 1h 不再创新低，或下跌结构被破坏。
- 15m 出现 reclaim、放量反包、低点抬高、突破回踩成功。
- 下方 stop 可以放在明确结构位外，而不是随便按百分比。

建议 short 触发：

- 1h 上涨结构破坏，或高位突破失败。
- 15m 出现 lower high、跌破关键支撑、反抽不过。
- 上方 stop 可以放在明确 swing high 外。

不要做：

- 单根大阴线后立刻追空，不看滑点和反抽风险。
- 单根大阳线后立刻追多，不看是否已经远离结构 stop。
- 只因为“大方向 59%”就下单。
- long 和 short 共用同一个研究结论。

## 交易经典原则接入

BTC 20x 双向系统可以吸收《日本蜡烛图技术》《海龟交易法则》《股票作手回忆录》《股市趋势技术分析》《交易心理分析》的原则，但不能把任何一本书变成“直接下单许可”。这些内容已经整理成系统原则库：

```text
docs/trading_classics_system_integration.md
```

接入方式：

| 来源 | BTC 20x 中的角色 | v1 用法 |
|---|---|---|
| 日本蜡烛图技术 | 局部价格行为、反转/延续/衰竭上下文 | 辅助 candidate explanation 和追单风险，不单独触发真实下单 |
| 海龟交易法则 | Donchian 突破、ATR/N、单位风险、加仓、趋势退出 | 作为 BTC research 和 position sizing 的核心候选框架 |
| 股票作手回忆录 | 顺势、等待、只加盈利仓、不摊平亏损 | 作为 execution discipline 和 add-back/reverse 约束 |
| 股市趋势技术分析 | 趋势结构、支撑阻力、突破确认、假突破 | 作为 1h regime 和 15m trigger 的结构特征来源 |
| 交易心理分析 | 事后偏差、确认偏误、FOMO、报复交易、止损纪律 | 作为 daily review 和 operator guard 的偏差检查 |

硬边界：

- 单一蜡烛图形态不能开 BTC 20x 实盘。
- Donchian / turtle breakout 必须经过 BTC long/short 分开回测和 walk-forward。
- ATR/N 用来算仓位和止损，不用来放大杠杆冲动。
- 加仓只允许加盈利仓；亏损仓同向加仓视为摊平，默认禁止。
- 反手必须先退出旧方向并确认 `FLAT`，再用新 candidate 开反向仓。
- 心理规则只能收紧或标记风险，不能用来绕过风控。

新增 candidate 上下文字段建议：

```json
{
  "trading_classic_context": {
    "candle_context": "continuation|reversal|exhaustion|none",
    "turtle_breakout": "fast|slow|none",
    "trend_structure": "trend_up|trend_down|range|failed_breakout|unknown",
    "support_resistance_context": "near_support|near_resistance|mid_range|unknown",
    "psychology_guard": "clean|fomo_risk|revenge_risk|manual_override_risk"
  }
}
```

这些字段第一阶段只进入 shadow、daily review 和 dashboard，不参与自动实盘放行。只有研究证明有效后，才能逐步进入 gate。

## Research Pipeline 迁移

这是最大工程量，不是配置项。

现有 ETH research 管线不能直接证明 BTC 20x 可用。BTC 需要独立 research artifacts：

- BTC 15m/1h OHLCV。
- BTC 永续 funding rate。
- open interest。
- long/short ratio。
- liquidation 数据，如果数据源稳定。
- basis / premium。
- volatility regime。
- exchange fee/slippage/funding cost。
- long 和 short 分开统计。
- walk-forward 分时段、分 regime 统计。
- truth candidate / whitelist / research bundle 独立生成。

最低验收：

| 模块 | 验收条件 |
|---|---|
| 数据 | BTC 数据源 fresh、可复现、时间戳统一 UTC |
| 特征 | BTC feature matrix 独立生成，不复用 ETH symbol |
| 研究 | long/short 分开评估 |
| 回测 | 包含手续费、滑点、资金费率、止损、分档止盈 |
| WF | walk-forward 不只是总收益，要看稳定性、漂移、分散度 |
| 产物 | research bundle 能被现有 handoff/gate 消费 |
| 健康 | research stale/missing/unqualified 能清楚拦截 |

Jesse、Freqtrade、go-trader 这类开源项目可以参考：

- 回测组织方式。
- 策略参数 sweep。
- 交易成本模拟。
- exchange adapter 抽象。
- 风控/仓位模块。

但它们不能直接替代当前系统的 research gate。我们当前最大价值是“handoff -> bot gate -> worker -> audit”的安全链路，这条链路不能为了接第三方框架被绕过。

## ETH -> BTC Profile Migration Checklist

### P0：现在会直接报错或强拦

| 文件 | 当前问题 | 必须修改 | 验证方式 |
|---|---|---|---|
| `src/bot/config.py:116` | `symbol != "ETH"` 直接 `ValueError` | 改成 profile-aware symbol allowlist | `BotConfig(symbol="BTC", profile_id="btc_20x_bidir")` 可构造 |
| `src/bot/config.py:118-119` | `leverage != 10` 直接 `ValueError` | leverage 按 profile 校验，BTC 允许 20 | BTC 20x 通过，ETH 非 10x 仍按配置策略处理 |
| `src/bot/config.py:124-125` | OKX 只允许 `ETH-USDT-SWAP` | OKX symbol allowlist 支持 `BTC-USDT-SWAP` | OKX BTC config 通过 |
| `src/bot/config.py:126-127` | Binance 只允许 `ETHUSDT` | Binance symbol allowlist 支持 `BTCUSDT` | Binance BTC config 通过，若 v1 不做 Binance 则明确拒绝 |
| `scripts/ops/real_order_worker.py:39` | `SUPPORTED_EXCHANGE_SYMBOLS = {"ETH-USDT-SWAP", "ETHUSDT"}` | 加 profile-aware supported symbols | BTC candidate 不被 worker symbol precheck 拒绝 |
| `src/bot/high_risk_gate.py:156` | 只放行 ETH symbol | 改为 profile-aware gate | BTC handoff 不因 ETH-only 被强拦 |

### P1：不会立刻崩，但会造成状态串扰

| 文件 | 当前问题 | 必须修改 | 验证方式 |
|---|---|---|---|
| `src/bot/config.py:70-74` | 默认 ETH/10x/`ETH-USDT-SWAP` | 增加 profile config，不靠全局默认切换 | ETH 默认不变，BTC 显式 profile |
| `src/bot/config.py:108` | `state_store_path` 默认同一文件 | BTC 用独立 state path 或 per-profile state schema | ETH flat 不等于 BTC flat |
| `src/bot/exchange_adapter.py:1972` | OKX `INST_ID = "ETH-USDT-SWAP"` | adapter 实例化时注入 instId | OKX BTC 请求使用 `BTC-USDT-SWAP` |
| `src/bot/exchange_adapter.py:1056,1357,2123,2476` | leverage fallback `10` | fallback 禁止静默使用 ETH 默认，必须来自 profile | runtime snapshot 缺 leverage 时 fail closed |
| `src/bot/exchange_adapter.py` 多处 Binance `ETHUSDT` | 请求参数、metadata、错误文案都固定 ETH | symbol 从 config/profile 注入 | BTCUSDT 请求不再落到 ETHUSDT |
| dashboard data source | ETH symbol 文案和映射固定 | 增加 profile/symbol 展示 | Dashboard 能区分 ETH/BTC 状态 |

### P2：不会阻止启动，但策略参数不对

| 文件 | 当前问题 | 必须修改 | 验证方式 |
|---|---|---|---|
| `D:\开发\quant_system_rebuild\src\policy\exit_plan_builder.py:43` | ETH stop thesis 模板 | profile-aware stop resolver | BTC/ETH 同 thesis 生成不同 stop model |
| `D:\开发\quant_system_rebuild\src\policy\exit_plan_builder.py:45` | 固定 `0.006..0.035` clamp | BTC 独立 stop clamp | BTC 20x stop 不沿用 ETH 上限 |
| research bundle | 当前 ETH 研究产物 | BTC 独立 bundle | BTC handoff 不引用 ETH research |
| factor lookup | 当前 ETH lookup 语义 | BTC 独立 lookup/version | BTC lookup stale/empty 单独拦截 |

## Profile 和运行时隔离

建议新增 profile id：

```text
btc_20x_bidir_v1
```

profile 必须包含：

```text
profile_id
base_symbol = BTC
exchange_venue = okx_usdt_swap 或 binance_usdt_perp
exchange_symbol = BTC-USDT-SWAP 或 BTCUSDT
leverage = 20
margin_mode = isolated 或 cross
timeframes = [15m, 1h]
position_mode = single_position
direction_mode = bidirectional
state_store_path
candidate_package_dir
audit_log_path
research_bundle_path
factor_lookup_path
risk_profile_version
```

隔离要求：

- ETH 和 BTC 使用不同 runtime root，例如：
  - `runtime/profiles/eth_10x/`
  - `runtime/profiles/btc_20x_bidir/`
- state store 独立。
- candidate package 独立。
- audit log 独立。
- worker lock 独立。
- idempotency key 前缀包含 profile。
- dashboard 必须显示当前 profile，不允许只显示 symbol 小字。

推荐 candidate package 关键字段：

```json
{
  "profile_id": "btc_20x_bidir_v1",
  "symbol": "BTC",
  "exchange_venue": "okx_usdt_swap",
  "exchange_symbol": "BTC-USDT-SWAP",
  "leverage": 20,
  "direction": "long",
  "position_intent": "entry",
  "state_before": "FLAT",
  "risk_profile_version": "btc_20x_v1",
  "research_bundle_id": "...",
  "factor_lookup_version": "...",
  "stop_model_version": "...",
  "execution_handoff_contract_version": "..."
}
```

bot/worker 不能只看 `exchange_symbol` 决定能不能下单，必须同时看 `profile_id + symbol + venue + leverage + state`。

## 自动真实提交边界

BTC 20x 的自动真实提交必须比 ETH 更保守。

### v1 阶段

- shadow/paper 阶段：可以自动生成候选包，不提交真实订单。
- 小额实盘阶段：允许生成真实候选包，但必须人工确认。
- 自动真实提交阶段：只有在 paper 和小额实盘验证通过后才开启。

### 自动提交必须满足

- `profile_id == btc_20x_bidir_v1`。
- 当前 profile 未触发 kill switch。
- handoff fresh。
- factor lookup fresh 且非空。
- scoring chain 未 frozen。
- research 对当前方向合格。
- 没有 `OPERATIONAL_DATA_QUALITY_BLOCK_CODES`。
- stop/TP/preflight 全 ready。
- state store 和交易所仓位一致。
- worker lock 获取成功。
- idempotency key 没有 pending/completed/recovery 冲突。
- worker 二次读取交易所后仍确认风险成立。

如果以上任何一项缺失，真实提交 fail closed。

## 实现阶段

### Phase 0：文档和边界

输出：

- 本文档。
- profile migration checklist。
- BTC 不启用实盘的明确状态。

验收：

- 文档合并。
- 没有改动 live risk limits。
- 没有启动 BTC 实盘。

### Phase 1：Profile 配置层

要做：

- 引入 `profile_id`。
- 允许 ETH 10x 和 BTC 20x 并存。
- 移除 ETH-only / 10x-only 的硬编码校验，改成 profile allowlist。
- 不支持的 symbol/leverage 仍然 fail closed。

验收：

- ETH 现有配置不变。
- BTC config 可以构造。
- 错配如 `profile=eth_10x` + `BTC-USDT-SWAP` 被拒绝。
- 错配如 `profile=btc_20x_bidir` + `ETH-USDT-SWAP` 被拒绝。

### Phase 2：状态和 artifact 隔离

要做：

- BTC 独立 state store。
- BTC 独立 candidate dir。
- BTC 独立 audit log。
- BTC 独立 worker lock。
- dashboard/status 支持 profile 维度。

验收：

- ETH 有仓时，BTC profile 可显示 `FLAT`。
- BTC 有仓时，ETH profile 不被污染。
- candidate package 只能被同 profile worker 消费。

### Phase 3：Exchange adapter BTC 支持

要做：

- OKX adapter 支持动态 `instId`。
- Binance adapter 如果要启用，也支持动态 `symbol`。
- 所有 leverage fallback 从 profile 读取，读不到就 fail closed。
- 下单、撤单、查单、查仓、查条件单都按 profile symbol。

验收：

- OKX BTC 请求参数是 `BTC-USDT-SWAP`。
- 不再出现 BTC profile 调 ETH 查询的情况。
- protective stop 查询只认本 profile、本方向、本 client id 前缀。

### Phase 4：BTC risk/stop/TP

要做：

- 新增 BTC stop resolver。
- 新增 BTC position sizing。
- 新增 BTC TP ladder。
- 新增 liquidation buffer 检查。
- 加回仓和反手都重算风险。

验收：

- 同样 thesis 下，ETH 和 BTC 输出不同 stop/risk。
- 20x BTC stop 太近/太远都会被明确拒绝。
- position size 由账户风险和 stop distance 解算。
- TP1 后加回不会超过总风险预算。

### Phase 5：反手和同向加回状态机

要做：

- 支持 `reverse_exit_only`。
- 支持 `reverse_enter_after_flat`。
- 支持 TP 后同向 add-back。
- 禁止 same-tick net reverse。

验收：

- long 收到普通 short 信号，只减/平，不开 short。
- long 收到强 short 信号，先 reduce-only 平 long，确认 flat 后才允许 short。
- TP1 后同向强信号可生成 add-back candidate。
- 未确认保护止损时 add-back 被拒绝。

### Phase 6：BTC research pipeline

要做：

- BTC 数据源。
- BTC feature matrix。
- BTC scan。
- BTC walk-forward。
- BTC research bundle。
- BTC factor governance。
- long/short 分开评分。

验收：

- BTC research stale/missing/unqualified 明确阻断。
- BTC 合格 research 可以生成 profile handoff。
- dashboard 显示 BTC research health，不混 ETH。

### Phase 7：Shadow / Paper / 小额实盘

上线顺序：

1. shadow：只生成信号和候选包，不下单。
2. paper：完整模拟下单、止损、TP、反手。
3. 小额实盘：只允许人工确认提交。
4. 自动实盘：达到验收指标后才启用。

建议 shadow/paper 最低观察：

- 覆盖趋势、震荡、急跌、急拉、假突破。
- 至少覆盖 long 和 short。
- 至少验证几十笔候选，不能只看一两次命中。
- 重点看最大回撤、连续止损、滑点、止损是否及时、TP 是否真实触发。

## 测试矩阵

### 单元测试

| 测试 | 目的 |
|---|---|
| `BotConfig` BTC profile 构造 | 确认 BTC 20x 不再被 ETH-only 拒绝 |
| profile 错配拒绝 | 防止 BTC 候选包被 ETH worker 消费 |
| state store per-profile | 防止 ETH/BTC 仓位串扰 |
| stop resolver profile-aware | 防止 BTC 沿用 ETH stop |
| position sizing | 确认 size 从 account risk 和 stop distance 得出 |
| reverse state machine | 确认反手必须先 flat |
| add-back state machine | 确认 TP 后可控加回，不无限加仓 |

### 集成测试

| 测试 | 目的 |
|---|---|
| BTC candidate -> bot gate | 确认 profile 字段被完整消费 |
| BTC preflight fake adapter | 确认 stop/TP/order payload 正确 |
| worker symbol precheck | 确认 `BTC-USDT-SWAP` 不被旧白名单拒绝 |
| OKX adapter dynamic instId | 确认请求不用 ETH instId |
| dashboard profile display | 确认用户不会把 ETH 状态看成 BTC 状态 |
| stale research fail closed | 确认坏数据不会触发 BTC 实盘 |

### 回归测试

必须确认 ETH 没被破坏：

- ETH 10x config 仍可启动。
- ETH candidate 仍走原 gate。
- ETH state 仍读写原路径或迁移后的 ETH profile 路径。
- ETH dashboard 显示不变或有清晰 profile 标识。
- ETH worker 不会消费 BTC candidate。
- BTC worker 不会消费 ETH candidate。

## 需要新增的观测字段

dashboard/status 至少显示：

- `profile_id`
- `symbol`
- `exchange_symbol`
- `leverage`
- `position_direction`
- `position_size`
- `state_store_path`
- `candidate_package_path`
- `research_bundle_id`
- `factor_lookup_age`
- `stop_model_version`
- `risk_profile_version`
- `last_entry_block_reason`
- `last_reverse_block_reason`
- `last_addback_block_reason`

这些字段必须来自真实 runtime/config/artifact，不要只从 candidate package 单边相信。

## 开源参考的使用方式

可以参考，但不能照搬。

| 项目类型 | 可以借鉴 | 不能直接拿来替代 |
|---|---|---|
| Jesse | 回测结构、策略生命周期、position API | 当前 bot worker/gate/audit |
| Freqtrade | 参数 sweep、dry-run、策略配置 | 当前实盘安全链路 |
| go-trader 类项目 | exchange adapter 抽象、订单状态机 | research governance |
| vectorbt | 扫描和统计效率 | 真实下单链路 |

实际落地要以当前系统的安全链路为主：

```text
research -> handoff -> bot gate -> preflight -> worker lock -> worker precheck -> exchange -> audit/state/dashboard
```

任何开源参考只能接入这条链路，不能绕过。

## 关键开放问题

上线前必须定：

1. BTC v1 先用 OKX 还是 Binance。
2. BTC 20x 使用 isolated 还是 cross。
3. 初始账户风险上限是多少。
4. TP1/TP2/runner 的比例是否沿用 ETH 思路还是 BTC research 单独定。
5. 反手是否只 paper 开启，还是小额实盘也允许。
6. 自动实盘需要多少 paper/小额实盘样本后开放。
7. dashboard 是做 profile 切换，还是给 BTC 单独页面。

## 最终验收标准

可以进入 BTC 小额实盘前，至少满足：

- P0 ETH/10x 硬编码全部解除或 profile 化。
- ETH/BTC 状态完全隔离。
- BTC research bundle 合格，且 long/short 分开验证。
- BTC stop/TP/position sizing 不再复用 ETH 模板。
- 反手必须两阶段，禁止 same-tick net reverse。
- TP 后同向加回有明确风险上限。
- shadow/paper 验证通过。
- worker 对 BTC profile 有二次校验。
- dashboard 能清楚显示 BTC 是不是能下单，以及不能下单的原因。
- kill switch、manual confirmation、idempotency、audit、state recovery 都覆盖 BTC。

人话版本：

BTC 20x 可以做，而且应该按双向单仓做。但它不是一个“改配置就上线”的改动。现在最大风险不是策略方向，而是系统还把 ETH/10x 当成唯一世界。先把 profile 隔离和 BTC research 做扎实，再谈自动实盘，否则会出现“看起来是 BTC 信号，实际 worker 查的是 ETH 状态/ETH 合约/ETH 风控”的硬事故。
