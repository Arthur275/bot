# Dashboard 与 Quant P1 修复方案：Reason Code 中文化与 Edge Metadata 落地

更新日期：2026-05-09

## 1. 目标

这份文档用于收敛两个 P1 问题的最终修复范围：

1. Dashboard 原因标签正文中仍会漏出机器码或半翻译英文。
2. Quant 决策 metadata 没有稳定落地 `estimated_cost_pct` 和 `net_edge_pct`，导致 Dashboard 量化指标图缺少“估算成本”和“净优势”。

涉及仓库：

```text
D:\开发\eth_trading_bot
D:\开发\quant_system_rebuild
```

## 2. 审查结论

### 2.1 Reason code 漏出属实

运行态 `/api/overview` 中 `quant.reason_codes` 返回的是机器码，例如：

```text
judgement_not_ok
research_not_ready
diagnostic:data_source
risk_filter:unknown
```

前端 `dashboard/static/app.js` 的 `renderReasonChips()` 之前会把原始 code 再渲染到 chip 正文：

```javascript
appendText(chip, "small", displayCode(row.code));
```

`displayCode()` 只做下划线替换，遇到 `risk_filter:unknown` 这类复合码就会显示成半中文半英文。正确修法不是继续补更多英文替换规则，而是正文只显示中文解释，原始 code 只保留在 hover/title 或结构化 payload 中。

### 2.2 Edge/cost 缺值根因不在 ECharts

Dashboard 图表没有值，是因为 quant 最新 `decision.json` 的 metadata 缺少：

```text
estimated_cost_pct
net_edge_pct
```

这不能在前端造值。字段必须从 Quant 的 execution cost / edge 链路落到 decision metadata，再由 `/api/overview` 透传给图表。

### 2.3 仅修两个 metadata helper 还不够

基础 helper 需要修，但审查发现 runtime 写 artifact 的路径还有一个容易漏掉的入口。

非 artifact 路径：

```text
run_scheduler_cycle_from_snapshots()
  -> run_scheduler_cycle_from_feature_matrix()

run_research_decision_pipeline_from_snapshots()
  -> run_research_decision_pipeline_from_feature_matrix()
```

这两条路径会进入 `*_from_feature_matrix()`，因此只要 helper 修好，metadata 可以被补齐。

Artifact/runtime 路径：

```text
run_scheduler_cycle_with_artifacts_from_live_snapshot_bundle()
  -> run_scheduler_cycle_with_artifacts_from_feature_matrix()

run_research_decision_pipeline_with_artifacts_from_live_snapshot_bundle()
  -> run_research_decision_pipeline_with_artifacts_from_feature_matrix()
```

当前 `*_with_artifacts_from_feature_matrix()` 已经把 execution cost 合进 `risk_input`，但 metadata 只做 `merge_feature_matrix_metadata(...)`，没有调用 `_merge_execution_cost_metadata(...)`。如果 runtime 走 artifact 写盘路径，`decision.json` 仍可能缺少 `estimated_cost_pct` / `net_edge_pct`。

因此本次 P1 必须同时覆盖：

1. scheduler 非 artifact metadata helper。
2. runner 非 artifact metadata helper。
3. scheduler artifact-producing feature matrix 路径。
4. runner artifact-producing feature matrix 路径。

## 3. Dashboard 修复方案

文件：

```text
D:\开发\eth_trading_bot\dashboard\static\app.js
```

### 3.1 补齐重点中文映射

至少补齐：

```javascript
judgement_not_ok: "量化判断未返回可执行结果",
"diagnostic:data_source": "数据源异常",
"risk_filter:unknown": "风控状态未知",
```

### 3.2 chip 正文不再展示原始 code

修改前：

```javascript
appendText(chip, "strong", text(row.text || row.code));
appendText(chip, "small", displayCode(row.code));
```

修改后：

```javascript
appendText(chip, "strong", text(row.text || row.code));
if (row.code && row.code !== "none") {
  chip.title = `原始代码：${row.code}`;
}
```

原则：

- 页面可见正文只显示中文解释。
- 原始机器码可以保留，但只能放在 hover/title 或结构化数据中。
- 不能再用 `displayCode(row.code)` 作为 chip body 的兜底展示。

### 3.3 可选后端增强

后续可以让 `dashboard/data_sources.py` 输出统一的：

```text
{ code, text }
```

并复用现有 reason text enrichment。这个增强不是本次 P1 的必要条件，当前 P1 的关键是前端不再把 raw code 放进可见正文。

## 4. Quant 修复方案

### 4.1 必改文件

```text
D:\开发\quant_system_rebuild\src\interfaces\scheduler.py
D:\开发\quant_system_rebuild\src\interfaces\runner.py
```

### 4.2 不要改 RiskFilterInput

`RiskFilterInput` 使用 `extra="forbid"`，本次不要新增：

```text
estimated_cost_pct
net_edge_pct
```

这两个字段应该只作为 decision metadata / handoff metadata 的派生字段存在。

### 4.3 修复 `_merge_execution_cost_metadata()`

两处同名函数都要保持一致：

```python
def _merge_execution_cost_metadata(
    metadata: Mapping[str, Any] | None,
    risk_input: Mapping[str, Any] | None,
) -> dict[str, Any]:
```

先沿用当前拷贝逻辑，把组件成本和 edge 字段从 `risk_input` 带进 metadata：

```python
for key in (
    "estimated_fee_pct",
    "estimated_slippage_pct",
    "estimated_funding_pct",
    "estimated_gross_edge_pct",
    "edge_source",
    "edge_estimate_status",
    "min_net_edge_pct",
):
    if risk_input is not None and key in risk_input:
        merged.setdefault(key, risk_input.get(key))
```

然后派生：

```python
effective_cost_pct = _optional_float(merged.get("estimated_cost_pct"))
if effective_cost_pct is None:
    effective_cost_pct = sum(
        _optional_float(merged.get(key)) or 0.0
        for key in (
            "estimated_fee_pct",
            "estimated_slippage_pct",
            "estimated_funding_pct",
        )
    )
    merged["estimated_cost_pct"] = round(effective_cost_pct, 8)

if _optional_float(merged.get("net_edge_pct")) is None:
    gross_edge_pct = _optional_float(merged.get("estimated_gross_edge_pct"))
    if gross_edge_pct is not None:
        merged["net_edge_pct"] = round(gross_edge_pct - effective_cost_pct, 8)
```

规则：

- 如果 metadata 已有 `estimated_cost_pct`，保留它，并用它作为 effective cost。
- 如果 metadata 已有 `net_edge_pct`，保留它，不重算。
- 如果缺少 `estimated_gross_edge_pct`，不要把 `net_edge_pct` 造为 `0.0`。
- `runner.py` 复用已有 `_optional_float()`。
- `scheduler.py` 可以新增局部 `_optional_float()`，行为要与 runner 一致。

### 4.4 修复 artifact-producing 路径

这是本次文档修复后新增的 P1 必改项。

#### scheduler.py

在 `run_scheduler_cycle_with_artifacts_from_feature_matrix()` 中，不要内联调用 `_merge_execution_cost_risk_input()` 后直接丢给 `risk_input`，而是先保存：

```python
merged_risk_input = _merge_execution_cost_risk_input(risk_input, normalized_matrix)
merged_metadata = merge_feature_matrix_metadata(
    _merge_execution_cost_metadata(metadata, merged_risk_input),
    normalized_matrix,
)
```

然后传入：

```python
risk_input=merged_risk_input,
metadata=merged_metadata,
```

这样所有调用这个入口的路径都会被覆盖，包括：

```text
run_scheduler_cycle_with_artifacts_from_live_snapshot_bundle()
run_scheduler_cycle_with_artifacts_from_sample_snapshot_bundle()
```

#### runner.py

在 `run_research_decision_pipeline_with_artifacts_from_feature_matrix()` 中同样先保存 `merged_risk_input`，并且保留 calibration metadata 的合并顺序：

```python
merged_risk_input = _merge_execution_cost_risk_input(risk_input, normalized_matrix)
merged_metadata = merge_feature_matrix_metadata(
    _merge_execution_cost_metadata(
        _merge_calibration_metadata(metadata, calibration_snapshot),
        merged_risk_input,
    ),
    normalized_matrix,
)
```

然后传入：

```python
risk_input=merged_risk_input,
metadata=merged_metadata,
```

这样 runner / handoff / artifact 写盘路径的语义和 scheduler 保持一致。

## 5. 测试方案

### 5.1 Dashboard

```powershell
cd D:\开发\eth_trading_bot
D:\开发\quant_system_rebuild\.venv_win\Scripts\python.exe -m pytest tests\test_dashboard_data_sources.py -q --basetemp=.tmp_pytest_dashboard_reason_p1
node --check dashboard\static\app.js
node runtime\dashboard_validation\check_layout.cjs
```

必须验证：

- `renderReasonChips()` 不再把 `displayCode(row.code)` 渲染到 chip body。
- 页面不再出现可见 `judgement_not_ok`、`diagnostic:data_source`、`risk_filter:unknown` 或半翻译 reason。
- `hasForbiddenVisibleEnglish=false`。
- `hasVisibleSnakeCase=false`。
- ECharts canvas 仍然非空。

### 5.2 Quant helper 单测

在以下文件补测试：

```text
D:\开发\quant_system_rebuild\tests\test_interfaces_scheduler.py
D:\开发\quant_system_rebuild\tests\test_interfaces_runner.py
```

覆盖：

1. 组件成本和 gross edge 都存在时：

```text
estimated_fee_pct = 0.0005
estimated_slippage_pct = 0.0002
estimated_funding_pct = 0.0001
estimated_gross_edge_pct = 0.0030

estimated_cost_pct = 0.0008
net_edge_pct = 0.0022
```

2. gross edge 缺失时：

```text
estimated_cost_pct 存在
net_edge_pct 缺失或为 None
```

3. metadata 已有派生值时：

```text
已有 estimated_cost_pct / net_edge_pct 不被 risk_input 覆盖
```

### 5.3 Quant artifact 路径测试

新增或扩展测试时，必须覆盖 artifact-producing feature matrix 路径：

```text
run_scheduler_cycle_with_artifacts_from_feature_matrix()
run_research_decision_pipeline_with_artifacts_from_feature_matrix()
```

断言输出的 decision metadata 至少包含：

```text
estimated_fee_pct
estimated_slippage_pct
estimated_funding_pct
estimated_cost_pct
edge_source
edge_estimate_status
```

如果测试 fixture 提供了可计算 gross edge 的 ATR/range 输入，还必须断言：

```text
estimated_gross_edge_pct
net_edge_pct
```

运行：

```powershell
cd D:\开发\quant_system_rebuild
.\.venv_win\Scripts\python.exe -m pytest tests\test_interfaces_scheduler.py tests\test_interfaces_runner.py -q --basetemp=.tmp_pytest_edge_metadata_p1
```

如果 Windows pytest 临时目录权限报错，需要记录为环境问题；不能把权限错误误判为业务逻辑失败。

## 6. 运行态验证

Quant 代码改动后，正在运行的 `quant_judgement` 不会自动加载新代码，必须重启 quant 进程或 runtime stack。

推荐流程：

```powershell
cd D:\开发\eth_trading_bot
scripts\manage_runtime_stack.cmd status
scripts\manage_runtime_stack.cmd start -DependencyWaitSec 2
```

等待至少一个新 quant cycle 后验证：

```powershell
$r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/overview' -TimeoutSec 25
$r.charts.quant_metric_series |
  Select-Object -Last 5 sample_id,estimated_cost_pct,net_edge_pct,edge_source,edge_estimate_status
```

期望：

- `estimated_cost_pct` 在组件成本可用时有值。
- `net_edge_pct` 只在 `estimated_gross_edge_pct` 可用时有值。
- 如果 `edge_estimate_status=missing`，`net_edge_pct` 为空是允许的，前端不能造值。

同时抽查最新 cycle 的 `decision.json`：

```powershell
Get-ChildItem D:\开发\eth_trading_bot\runtime\cycles -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 FullName
```

在最新有效 cycle 中确认 metadata 已落地：

```text
estimated_cost_pct
net_edge_pct  # gross edge 存在时
edge_source
edge_estimate_status
```

## 7. 验收标准

本 P1 完成必须同时满足：

1. Dashboard 页面正文不显示 raw reason code 或半翻译英文 reason。
2. 原始 reason code 仍能通过 hover/title 或结构化 payload 找到。
3. scheduler 非 artifact 路径 metadata 有 `estimated_cost_pct`，并在 gross edge 存在时有 `net_edge_pct`。
4. runner 非 artifact 路径 metadata 与 scheduler 语义一致。
5. scheduler artifact/runtime 路径写出的 `decision.json` metadata 有 `estimated_cost_pct`。
6. runner artifact/handoff 路径写出的 metadata 与 scheduler 语义一致。
7. 新 quant cycle 进入 `/api/overview` 后，量化指标图能拿到 `estimated_cost_pct`。
8. 新 quant cycle 在 gross edge 存在时，量化指标图能拿到 `net_edge_pct`。
9. Dashboard 测试、JS 语法检查、浏览器布局联调通过。
10. Quant scheduler/runner 相关测试通过，或明确记录非业务性的 Windows 临时目录权限阻塞。

## 8. 优先级

1. 修 Dashboard reason chip 正文漏码。
2. 修 scheduler / runner 两处 `_merge_execution_cost_metadata()`。
3. 修 scheduler / runner 两处 `*_with_artifacts_from_feature_matrix()`。
4. 补 helper 单测和 artifact 路径测试。
5. 重启 quant，等待新 cycle。
6. 验证 `/api/overview`、最新 `decision.json` 和 ECharts 图表。

P2/P3 另行处理：

- `/api/overview` payload 与缓存刷新性能。
- Dashboard `ConnectionAbortedError` 日志噪声。
- ccxt async `.close()` warning。
