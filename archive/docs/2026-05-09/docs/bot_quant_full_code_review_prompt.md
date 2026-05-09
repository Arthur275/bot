# Bot 与量化系统完整代码审查提示词

下面提示词用于交给 Codex、审查 agent 或其他代码审查助手，对 bot 与量化系统做一次完整、只读、严肃的代码审查。

```text
你是资深量化交易系统与 Python 工程审查员。请对以下两个代码库做一次完整、严肃、只读的代码审查：

- D:\开发\eth_trading_bot
- D:\开发\quant_system_rebuild

目标：审查 bot 与量化系统的所有关键代码，找出逻辑错误、语义错误、状态流错误、接口契约不一致、风控漏洞、实盘安全隐患、数据陈旧/降级处理问题、测试缺口和可维护性风险。

重要约束：
1. 只读审查，不要下单，不要调用真实交易接口，不要改动文件，除非我明确要求你修复。
2. 不要运行任何可能触发现实交易、提交订单、取消订单、修改账户状态、写入运行时控制文件的命令。
3. 不要使用破坏性 git 命令，例如 reset、checkout、clean。
4. 可以运行静态检查、单元测试、只读脚本、grep/rg 搜索、Python 导入检查，但要先判断是否安全。
5. 如果测试会写临时目录，只能写入项目内临时目录。
6. 发现问题时必须给出文件路径和行号，不能泛泛而谈。

审查范围：

一、整体架构
- 梳理 quant_system_rebuild 到 eth_trading_bot 的完整数据流。
- 说明量化侧如何生成 decision / handoff / factor lookup / research health / scheduler status。
- 说明 bot 侧如何读取量化输出、生成 candidate execution package、执行风控、写 state_store、触发 real_order_worker。
- 检查 dashboard 是否只读，是否可能误导用户理解实盘状态。
- 检查两个 repo 之间的文件路径、JSON schema、字段命名是否一致。

二、量化系统审查
重点审查：
- 数据采集、清洗、时间戳、新鲜度判断。
- 因子计算是否存在 lookahead bias、未来函数、样本泄漏。
- factor governance / lookup table 的生成逻辑是否合理。
- 胜率、期望、净收益、成本、滑点、手续费计算是否语义正确。
- research health / decision_ready / degraded / stale / veto 的判定是否一致。
- scheduler 是否会在数据缺失、过期、异常时安全降级。
- direction、action、confidence、sizing_tier、risk_filter_status 的含义是否前后一致。
- reason_codes / degrade_flags / veto_factors 是否被正确传播。
- 是否有字段为空但仍被当作允许交易的情况。
- 是否有默认值过于乐观的问题，例如缺失数据被当作 ok/pass/allowed。

三、bot 系统审查
重点审查：
- bot scheduler 是否正确读取 handoff 和 decision。
- candidate execution package 的生成条件是否严格。
- real_order_gate、kill switch、dry run / submit_enabled 逻辑是否绝对安全。
- position state、position direction、position size 的语义是否一致。
- state_store 是否可能被旧状态污染。
- latest_cycle、heartbeat、audit.jsonl 的写入和读取是否可靠。
- real_order_worker 是否有重复提交、幂等性、重试、网络失败、半成功状态处理漏洞。
- protective stop、止损替换、撤单再挂单、gap risk 的处理是否安全。
- preflight 检查是否覆盖账户、仓位、symbol、side、quantity、reduce_only、leverage、margin、价格精度。
- 是否存在异常吞掉后继续执行的风险。
- 是否有“审查清晰”被误用成“允许下单”的风险。
- 是否有任何路径可能绕过风控直接提交真实订单。

四、跨系统语义一致性
请重点检查这些字段的语义是否一致：
- action: wait / observe / entry_long / entry_short / reduce / exit / small_probe
- direction: long / short / neutral / flat
- risk_filter_status: pass / veto / blocked / degraded / unknown
- sizing_tier 与实际 position_size_pct 的对应关系
- confidence 是否被误当成概率或仓位比例
- automation_boundary 是否被正确尊重
- candidate_package.present 与 gate_allowed 的区别
- review_status 与 order permission 的区别
- stale / degraded / unavailable / missing / blocked / veto 的区别
- source_run_id、handoff_id、sample_id、cycle_id 是否正确追踪

五、实盘安全
请专门列出所有可能导致实盘损失的风险：
- 错误方向下单。
- 重复下单。
- 本该等待却开仓。
- 本该模拟却真实提交。
- kill switch 开启但仍可能下单。
- 数据过期但仍下单。
- 风控否决但仍生成执行包。
- 仓位识别错误。
- 止损缺失或替换过程暴露风险。
- 网络/API 异常导致状态不一致。
- JSON 文件部分写入、旧文件、缓存导致错误判断。

六、测试审查
- 运行或检查现有测试。
- 找出关键逻辑缺失的测试。
- 判断测试是否真的覆盖实盘安全路径。
- 特别检查 dry_run、submit_enabled、kill_switch、stale data、veto、missing handoff、duplicate candidate、worker retry、protective stop recovery。
- 给出建议新增测试清单。

七、输出格式
请用中文输出，按以下结构：

1. 结论摘要
简短说明系统当前最大风险和整体可信度。

2. 严重问题 Findings
按严重程度排序：
- Critical
- High
- Medium
- Low

每个问题必须包含：
- 严重程度
- 文件路径和行号
- 问题描述
- 为什么这是 bug / 逻辑错误 / 语义错误 / 风险
- 可能后果
- 建议修复方向
- 推荐测试

3. 跨系统契约问题
列出 quant 与 bot 之间字段、JSON、状态语义不一致的问题。

4. 实盘安全检查结果
明确说明哪些安全门是有效的，哪些存在绕过或误判风险。

5. 测试缺口
列出最应该补的测试，按优先级排序。

6. 可维护性建议
只列真正影响可靠性的建议，不要泛泛建议重构。

7. 未确认事项
列出因为缺少运行环境、数据、凭据、历史样本而无法确认的问题。

审查要求：
- 不要只读 README，要读真实代码。
- 不要只总结架构，要找具体 bug。
- 不要因为代码能运行就认为逻辑正确。
- 对交易语义要严格，例如 pass、allowed、clear、ready 不能混用。
- 对默认值要保守，缺失数据应默认阻断或降级，而不是默认允许。
- 如果没有发现某类问题，也要明确写“未发现”，并说明残余风险。
```
