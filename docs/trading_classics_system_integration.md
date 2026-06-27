# 交易经典原则系统化集成方案

> 日期：2026-05-19  
> 范围：把《日本蜡烛图技术》《海龟交易法则》《股票作手回忆录》《股市趋势技术分析》《交易心理分析》的核心原则，转成 quant/bot 系统可执行的研究、风控、执行、复盘规则。  
> 边界：本文不保存书籍原文，不复刻章节内容，只沉淀可执行原则、审计字段和实现路线。

## 一句话结论

这五本书不能被系统理解成“找神奇形态然后下单”。正确用法是：

1. 蜡烛图给局部价格行为证据。
2. 海龟给趋势突破、ATR 风控、加仓和退出框架。
3. 作手回忆录给交易纪律：顺势、等待、只加盈利仓。
4. 趋势技术分析给趋势结构、支撑阻力、突破有效性。
5. 交易心理给系统执行纪律和复盘偏差约束。

放进系统后，它们应该变成一组规则层，而不是一本“玄学策略书”。

## 版权和实现边界

- 不把任何一本书的全文、大段原文、章节复刻内容写入仓库。
- 不把书中概念直接等价成实盘授权。
- 不允许单一蜡烛图形态直接触发真实下单。
- 不允许“事后看对了”覆盖当时的预设规则。
- 所有规则必须进入回测、shadow outcome、daily review 后才允许影响实盘。

## 五本书到系统模块的映射

| 书 | 系统内角色 | 不能做什么 | 应该落到哪里 |
|---|---|---|---|
| 日本蜡烛图技术 | 局部价格行为、反转/延续证据 | 不能单独作为 entry trigger | factor、candidate explanation、daily review market column |
| 海龟交易法则 | 趋势突破、ATR/N、单位仓位、加仓、退出 | 不能忽略波动率和仓位上限 | research pipeline、risk model、position manager |
| 股票作手回忆录 | 顺势、耐心、只加赢家、不要摊平亏损 | 不能当成主观判断借口 | execution discipline、review checklist |
| 股市趋势技术分析 | 趋势结构、支撑阻力、突破确认、假突破识别 | 不能只画线不验证 | feature engineering、regime classifier |
| 交易心理分析 | 概率思维、一致性、接受亏损、避免冲动 | 不能用来放宽风控 | operator guard、daily review bias check |

## 原则库

### 1. 蜡烛图原则

用途：解释短周期价格行为，辅助判断入场质量和止损位置。

可编码信号：

| Signal | 含义 | 用法 |
|---|---|---|
| `candle_reversal_context` | 反转形态出现在趋势末端或关键支撑阻力附近 | 只提高 review priority，不单独开仓 |
| `candle_continuation_context` | 回调后出现延续形态 | 可作为趋势延续 probe 的辅助因子 |
| `wide_range_candle` | 实体或全振幅明显放大 | 标记波动风险，影响 stop/size |
| `upper_lower_wick_pressure` | 长上影/下影提示局部拒绝 | 用于入场质量评分和止损参考 |
| `gap_or_exhaustion_context` | 急速延伸后出现衰竭迹象 | 禁止追单或降低仓位 |

硬规则：

- 蜡烛形态必须有上下文：趋势、位置、波动率、成交量或多周期结构。
- 单根 K 线不能直接触发真实下单。
- 反转形态只能作为减仓、观望、probe 或反手候选，不直接 full entry。
- 若 `wide_range_candle` 已经超过当日或近期波动阈值，追单需要更高确认。

### 2. 海龟原则

用途：建立 BTC/ETH 趋势策略最硬的骨架。

可编码信号：

| Signal | 含义 | 用法 |
|---|---|---|
| `donchian_breakout_fast` | 短窗口突破 | 试探仓或早期候选 |
| `donchian_breakout_slow` | 长窗口突破 | 主趋势候选 |
| `atr_n` | 波动单位 N | 仓位、止损、加仓距离 |
| `unit_risk_pct` | 每个 unit 的账户风险 | 控制单笔亏损 |
| `pyramid_allowed` | 盈利后允许加仓 | 只能加赢家，不能加输家 |
| `turtle_exit_break` | 反向短窗口突破退出 | 趋势退出逻辑 |

硬规则：

- 仓位按波动率算，不按感觉算。
- 加仓只能发生在已有仓位盈利、趋势继续确认、总风险没超限时。
- 亏损仓不允许摊平。
- 初始止损必须随 entry 一起生成，不能先开仓后想止损。
- 20x BTC 必须重新计算 ATR/N、最小止损、最大止损和单位仓位。

### 3. 作手纪律原则

用途：约束系统和人工操作。

可编码规则：

| Rule | 系统表达 |
|---|---|
| 顺势交易 | `regime_direction` 与 entry direction 冲突时，full entry 禁止 |
| 等待明确信号 | `trigger_ready=false` 时不能因为行情“看起来要动”强行下单 |
| 只加盈利仓 | `pyramid_allowed` 必须要求 unrealized PnL > 0 |
| 不摊平亏损 | losing position 下禁止同向加仓 |
| 市场永远优先 | signal 失效时退出，不用解释市场错了 |
| 大钱来自大波段 | 避免过早全平，TP 要和 trailing exit 共存 |

系统警戒：

- 人工 override 必须写明：预设规则依据、风险、止损、失效条件。
- 若 override 理由是“感觉到底/到顶”，默认不通过。

### 4. 趋势技术分析原则

用途：构建结构化 market regime。

可编码信号：

| Signal | 含义 | 用法 |
|---|---|---|
| `trend_structure_hh_hl` | 高高低高，多头结构 | 多头 regime |
| `trend_structure_ll_lh` | 低低高低，空头结构 | 空头 regime |
| `support_resistance_zone` | 关键区间 | 入场/止损/止盈参考 |
| `breakout_confirmed` | 突破后站稳或回踩确认 | 提升候选等级 |
| `breakout_failed` | 假突破 | 降级或反向候选 |
| `volume_confirmation` | 成交量配合 | 提升趋势可信度 |

硬规则：

- 趋势线、支撑阻力必须用算法定义，不允许人工画线直接进实盘。
- 突破必须区分 `touch`、`close_break`、`hold_after_break`。
- 假突破要能进入复盘统计，不能只在图上口头解释。

### 5. 交易心理原则

用途：把人工和系统都锁进一致性。

可编码规则：

| Bias / Risk | 系统约束 |
|---|---|
| 事后偏差 | daily review 必须区分“预设规则”与“事后价格” |
| 确认偏误 | 如果每天都判断系统合理，必须触发 bias check |
| 报复交易 | 连续亏损或手动 override 后进入 cooldown |
| 害怕错过 | FOMO entry 需要更严格 gate |
| 不接受亏损 | 禁止移动止损远离市场 |
| 结果导向 | 单笔盈亏不能直接改规则，必须看样本分布 |

## 集成到现有系统的方式

### A. Research Pipeline

新增研究主题：

| Theme | 输出 |
|---|---|
| `candle_context_features` | 蜡烛图上下文特征，不直接下单 |
| `donchian_turtle_breakout` | fast/slow breakout 候选 |
| `atr_unit_risk_model` | ATR/N、unit size、stop distance |
| `trend_structure_classifier` | HH/HL、LL/LH、range、failed breakout |
| `psychology_bias_metrics` | override、追单、止损移动、连续亏损后行为 |

验收：

- 每个 theme 必须有回测。
- 每个 theme 必须有 shadow outcome。
- 每个 theme 必须能进入 daily review。

### B. Candidate Generation

新增 candidate 字段：

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

原则：

- 这些字段先只解释和降级，不直接放行真实订单。
- 只有经过 research 的字段才能成为 entry gate。
- `psychology_guard != clean` 时不能提高仓位，只能降低或拦截。

### C. Risk Model

必须新增或改造：

| 风控项 | 规则 |
|---|---|
| ATR/N stop | 止损距离按 symbol/profile/leverage 计算 |
| Unit sizing | 每个 unit 风险固定，随 ATR 自动缩放 |
| Pyramid | 只加盈利仓，且总风险不超限 |
| No averaging down | 亏损仓同向加仓默认禁止 |
| Breakout failure exit | 突破失败进入退出或减仓逻辑 |
| Stop discipline | 禁止把止损往亏损方向移动 |

### D. Execution / Bot Gate

新增执行守卫：

| Gate | 行为 |
|---|---|
| `classic_context_missing` | 缺少结构上下文时，禁止 full entry |
| `candle_only_signal` | 只有蜡烛图形态时，真实下单禁止 |
| `losing_position_add_blocked` | 亏损仓不准加仓 |
| `pyramid_risk_exceeded` | 加仓后总风险超限则拦截 |
| `fomo_entry_blocked` | 急涨急跌追单且无确认时拦截 |
| `stop_moved_away_blocked` | 止损被人为放远时拦截 |

### E. Daily Review

日报新增检查项：

```markdown
## 经典原则检查

- candle_context:
- turtle_breakout_context:
- trend_structure:
- support_resistance_context:
- position_sizing_discipline:
- pyramid_or_averaging_behavior:
- psychology_bias_flag:
```

复盘标签新增：

| Label | 含义 |
|---|---|
| `missed_trend_breakout` | 符合趋势突破规则但系统没抓 |
| `correct_no_chase` | 行情动了但按规则不能追 |
| `false_breakout_saved` | 系统没追假突破，风控正确 |
| `premature_exit` | 过早止盈，没吃到趋势段 |
| `discipline_violation` | 人工或系统违反止损/加仓纪律 |

## BTC 20x 双向系统特别要求

这些书对 BTC 20x 的启发不是“更激进”，而是“更机械”：

1. BTC 20x 必须以 ATR/N 为风险核心。
2. 双向可以做，但反手必须有单独协议。
3. 反手不是情绪动作，必须满足：
   - 原方向 exit 条件成立；
   - 新方向 trend/breakout 条件成立；
   - spread/slippage/fee 后仍有 edge；
   - cooldown 或确认机制通过。
4. 加仓只允许加盈利方向。
5. 第一档 TP 后可以考虑加回，但必须重新计算总风险。

## 实施路线

### Phase 0：文档入库

- 本文作为原则库。
- 不改变实盘行为。
- 不授权新下单。

### Phase 1：审计字段

新增只读字段到 candidate / daily review：

- candle context
- trend structure
- turtle breakout context
- ATR/N
- support/resistance context
- psychology guard

验收：dashboard 和 daily review 能看见字段，但 gate 不使用。

### Phase 2：Shadow Research

对过去样本做 shadow：

- Donchian breakout 是否提升胜率；
- ATR stop 是否比当前 stop 更稳；
- 蜡烛图上下文是否能过滤差 entry；
- failed breakout 是否能减少追单亏损；
- pyramid 是否改善盈亏比。

验收：至少 30-100 个事件样本，不用单日结论调参。

### Phase 3：Gate 接入

只接入通过研究验证的规则：

- `candle_only_signal` 拦截；
- `losing_position_add_blocked` 拦截；
- `stop_moved_away_blocked` 拦截；
- ATR/N stop profile；
- Donchian breakout candidate。

### Phase 4：实盘小仓验证

- 先 paper；
- 再 shadow live；
- 再 10% probe；
- BTC 20x 不能直接 full size。

## 当前优先级

P0：

- ATR/N 风控模型。
- BTC/ETH profile 隔离。
- 亏损仓禁止加仓。
- stale diagnostics 不得作为当天结论。

P1：

- Donchian breakout research。
- 趋势结构分类。
- failed breakout 检测。
- daily review 经典原则检查。

P2：

- 蜡烛图上下文评分。
- 支撑阻力算法。
- 心理偏差 dashboard。

## 人话版底线

这些书真正能帮系统的地方，不是让它更会猜顶底，而是让它更守纪律：

- 趋势来了敢拿；
- 没信号能等；
- 错了能砍；
- 赢了才加；
- 不因为一根 K 线冲动；
- 不因为事后行情漂亮就改规则。

