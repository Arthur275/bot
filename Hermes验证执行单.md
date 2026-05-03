# Hermes 执行单（2026-04-27）

## 目标
只做 `D:\eth_trading_bot` 的证据采集与联调核查，帮助今天主线程更快判断 simulated-real 与 reconciliation 是否已经稳定。

## 你要做的事
1. 只读检查 `D:\eth_trading_bot` 当前 `simulated-real` 相关代码与测试。
2. 优先核查三件事：
   - `userTrades / positionRisk / account` 返回在 reconciliation / audit 里的落点是否完整
   - `breakeven / trailing stop` 在 real mode 下目前到底是硬拦截、半映射，还是存在误放行风险
   - orchestrator → adapter → state_store → audit 这条链在 simulated-real 下是否有明显断点
3. 如果仓内已有可直接运行的测试或命令，执行最小必要验证；如果没有，就只做代码级证据核查。

## 禁止事项
- 不要改代码
- 不要新建设计文档
- 不要发散到 `D:\quant_system_rebuild`
- 不要提出大改架构建议
- 不要猜 Binance 真实语义；证据不足就直接写证据不足

## 交付格式
输出一个简短 markdown 报告，文件名：`D:\eth_trading_bot\Hermes验证报告.md`

报告必须包含 4 段：
1. `核查范围`
2. `已确认事实`
3. `风险点 / 证据不足`
4. `建议我主线程今天先做哪一处`

## 停止条件
- 找到足够证据回答上面三件事就停
- 报告尽量短，结论硬一点，不写成长文
