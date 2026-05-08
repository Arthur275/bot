const runtimeItems = [
  ["factor_collector", "样本采集"],
  ["quant_scheduler", "量化判断"],
  ["bot_scheduler", "机器人调度"],
  ["real_worker", "执行器"],
  ["kill_switch", "熔断开关"],
];

const valueLabels = {
  UNKNOWN: "未知",
  RUNNING: "运行中",
  STALE: "已过期",
  ERROR: "错误",
  OK: "正常",
  PASS: "通过",
  DEGRADED: "降级",
  BLOCKED: "阻断",
  VETO: "否决",
  UNAVAILABLE: "不可用",
  MISSING: "缺失",
  FRESH: "新鲜",
  AGING: "接近过期",
  ALLOWED: "允许",
  DISABLED: "禁用",
  READY: "就绪",
  ON: "开启",
  OFF: "关闭",
  clear: "清晰",
  watch: "观察",
  needs_attention: "需关注",
  unavailable: "不可用",
  async_light: "异步轻审查",
  async_full: "异步完整审查",
  daily_integrity_review: "每日完整性审查",
  daily_outcome_review: "每日结果审查",
  outcome_reflection: "结果复盘",
  manual_audit: "人工审计",
  position_open: "有持仓",
  degraded: "降级",
  idle: "空闲",
  entry_long: "开多",
  entry_short: "开空",
  small_probe: "小探针",
  exit: "平仓",
  reduce: "减仓",
  wait: "等待",
  observe: "观察",
  observe_only: "仅观察",
  paper_only: "纸面",
  long: "多头",
  short: "空头",
  neutral: "中性",
  flat: "空仓",
  pass: "通过",
  veto: "否决",
  fresh: "新鲜",
  error: "错误",
  ok: "正常",
  allowed: "允许",
  blocked: "阻断",
  yes: "是",
  no: "否",
  none: "暂无",
  available: "可用",
  missing: "缺失",
  dry_run: "模拟执行",
  submit_enabled: "真实提交已启用",
  restricted_two_source: "限制两源共识",
  market_data_restricted_two_source: "市场数据限制两源",
  not_entry_action: "未形成开仓动作",
  no_order_submission: "不提交订单",
  shadow_preflight_only: "影子预检",
  candidate_execution_package_not_allowed: "候选执行包未放行",
  disabled_by_kill_switch: "熔断禁用",
  active: "活跃",
  submitted: "已提交",
  disabled: "禁用",
  present: "存在",
  unknown: "未知",
  handoff_available: "交接包可用",
  factor_lookup_available: "因子查找表可用",
  factor_summary_available: "样本摘要可用",
  risk_report_available: "风险报告可用",
  candidate_package_available: "候选执行包可用",
  worker_audit_available: "执行器审计可用",
  outcome_samples_available: "结果样本可用",
  research_not_ready: "研究未就绪",
  research_stale: "研究已过期",
  factor_lookup_stale: "因子查找表已过期",
  factor_governance_veto: "因子治理否决",
  trigger_ready_for_execution: "触发器已就绪",
  trigger_ready_waiting_execution_gate: "触发器就绪但等待执行闸门",
  trigger_ready_for_small_probe: "触发器允许小探针",
  trigger_ready_but_research_not_enough: "触发器就绪但研究证据不足",
  trigger_ready_but_conviction_not_enough: "触发器就绪但信心不足",
  net_edge_below_cost: "净优势低于成本",
  bundle_missing: "研究包缺失",
  macro_news_veto: "宏观新闻否决",
  ta_overlay_veto: "技术叠加否决",
  "bundle_status:degraded": "研究包状态降级",
  crowding_warning: "拥挤风险预警",
  direction_not_aligned: "方向不一致",
  governance_negative_expectancy: "治理结论为负期望",
  governance_watch: "治理观察",
  high_regime_risk: "高市场状态风险",
  okx_taker_volume_experimental: "OKX 主动成交量实验因子",
  market_data_consensus_unreliable: "市场数据共识不可靠",
  market_data_source_unreliable: "市场数据源不足或不可用",
  data_health_veto: "实时数据健康度过低",
  "overlay_bias:neutral": "叠加偏向中性",
  overlay_present: "叠加信号存在",
  "overlay_source:okx": "叠加来源 OKX",
  regime_alignment: "市场状态一致",
  "regime:long": "大周期多头",
  research_aging: "研究数据接近过期",
  research_degraded: "研究降级",
  research_freshness_degraded: "研究新鲜度降级",
  "risk_filter:veto": "风控否决",
  "risk_filter:degraded": "风控降级",
  runtime_entry_veto: "运行时开仓否决",
  sample_count_low: "样本数偏低",
  "setup:short": "设置层偏空",
  staleness_veto: "数据新鲜度/可用性否决",
  "transition:direction_not_aligned": "状态转换：方向不一致",
  "transition:okx_taker_volume_experimental": "状态转换：OKX 主动成交量实验因子",
  "transition:overlay_bias:neutral": "状态转换：叠加偏向中性",
  "transition:overlay_present": "状态转换：叠加信号存在",
  "transition:overlay_source:okx": "状态转换：叠加来源 OKX",
  trigger_reversal: "触发层反转",
  "truth_candidate_source:all_results_fallback": "真实候选来源为全结果降级回退",
  truth_candidate_unqualified: "真实候选不合格",
  wf_return_drift_high: "走前收益漂移偏高",
  win_rate_low: "胜率偏低",
};

const sourceLabels = {
  handoff_available: "交接包",
  factor_lookup_available: "因子查找表",
  factor_summary_available: "样本摘要",
  risk_report_available: "风险报告",
  candidate_package_available: "候选执行包",
  worker_audit_available: "执行器审计",
  outcome_samples_available: "结果样本",
};

const $ = (id) => document.getElementById(id);
let refreshPaused = false;
const chartInstances = {};
const chartPalette = {
  text: "#e5edf7",
  muted: "#93a4b8",
  grid: "rgba(148,163,184,0.18)",
  panel: "#101722",
  green: "#34d399",
  red: "#fb7185",
  yellow: "#fbbf24",
  blue: "#60a5fa",
  gray: "#94a3b8",
};

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return "更新时间未知";
  const n = Number(seconds);
  if (!Number.isFinite(n)) return "更新时间未知";
  if (n < 60) return `${Math.max(0, Math.floor(n))} 秒前`;
  if (n < 3600) return `${Math.floor(n / 60)} 分钟前`;
  return `${Math.floor(n / 3600)} 小时 ${Math.floor((n % 3600) / 60)} 分钟前`;
}

function text(value, fallback = "暂无") {
  if (value === null || value === undefined || value === "") return fallback;
  const raw = String(value);
  return valueLabels[raw] || valueLabels[raw.toUpperCase?.()] || humanizeCode(raw);
}

function humanizeCode(value) {
  return String(value)
    .replace(/[_-]+/g, " ")
    .replace(/\bapi\b/gi, "接口")
    .replace(/\baction\b/gi, "动作")
    .replace(/\bbot\b/gi, "机器人")
    .replace(/\bworker\b/gi, "执行器")
    .replace(/\bresearch\b/gi, "研究")
    .replace(/\bfactor\b/gi, "因子")
    .replace(/\blookup\b/gi, "查找表")
    .replace(/\bhandoff\b/gi, "交接包")
    .replace(/\bruntime\b/gi, "运行时")
    .replace(/\btrigger\b/gi, "触发器")
    .replace(/\bready\b/gi, "就绪")
    .replace(/\bstale\b/gi, "过期")
    .replace(/\bmissing\b/gi, "缺失")
    .replace(/\bblocked\b/gi, "阻断")
    .replace(/\bveto\b/gi, "否决")
    .replace(/\bpass\b/gi, "通过")
    .replace(/\bwait\b/gi, "等待")
    .replace(/\bentry\b/gi, "开仓")
    .replace(/\bexit\b/gi, "平仓")
    .replace(/\brun\b/gi, "运行")
    .replace(/\ball results\b/gi, "全结果")
    .replace(/\bfallback\b/gi, "降级回退")
    .replace(/\btruth\b/gi, "真实")
    .replace(/\bcandidate\b/gi, "候选")
    .replace(/\bsource\b/gi, "来源")
    .replace(/\bunqualified\b/gi, "不合格")
    .replace(/\breturn\b/gi, "收益")
    .replace(/\bdrift\b/gi, "漂移")
    .replace(/\bhigh\b/gi, "偏高")
    .replace(/\blow\b/gi, "偏低")
    .replace(/\bcount\b/gi, "数量")
    .replace(/\bsample\b/gi, "样本")
    .replace(/\bgovernance\b/gi, "治理")
    .replace(/\bnegative\b/gi, "负")
    .replace(/\bexpectancy\b/gi, "期望")
    .replace(/\bregime\b/gi, "市场状态")
    .replace(/\boverlay\b/gi, "叠加")
    .replace(/\bbias\b/gi, "偏向")
    .replace(/\bneutral\b/gi, "中性")
    .replace(/\btransition\b/gi, "状态转换")
    .replace(/\bsetup\b/gi, "设置层")
    .replace(/\bshort\b/gi, "空头")
    .replace(/\blong\b/gi, "多头")
    .replace(/\brisk\b/gi, "风险")
    .replace(/\bfilter\b/gi, "过滤")
    .replace(/\bdegraded\b/gi, "降级")
    .replace(/\baging\b/gi, "接近过期")
    .replace(/\bconfirm\b/gi, "确认层")
    .replace(/\bgate\b/gi, "闸门")
    .replace(/\bstatus\b/gi, "状态")
    .replace(/\baligned\b/gi, "一致")
    .replace(/\bscore\b/gi, "评分")
    .replace(/\bhealth\b/gi, "健康")
    .replace(/\bpresent\b/gi, "存在")
    .replace(/\bissue\b/gi, "问题")
    .replace(/\bby\b/gi, "由于")
    .replace(/\bor\b/gi, "或")
    .replace(/\bconsensus\b/gi, "共识")
    .replace(/\bcrowding\b/gi, "拥挤")
    .replace(/\bwarning\b/gi, "预警")
    .replace(/\bwf\b/gi, "走前")
    .replace(/\btaker\b/gi, "主动成交")
    .replace(/\bvolume\b/gi, "成交量")
    .replace(/\bexperimental\b/gi, "实验")
    .replace(/\bokx\b/gi, "OKX")
    .trim();
}

function displayCode(value) {
  return text(value).replace(/_/g, " ");
}

function number(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString() : "0";
}

function pct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "暂无";
  return `${(n * 100).toFixed(2)}%`;
}

function pctField(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "缺失";
  const scaled = Math.abs(n) > 1 ? n : n * 100;
  return `${scaled.toFixed(3)}%`;
}

function scorePct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "缺失";
  const scaled = n <= 1 ? n * 100 : n;
  return `${scaled.toFixed(0)}%`;
}

function compactTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "");
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function chartBaseOption() {
  return {
    backgroundColor: "transparent",
    textStyle: { color: chartPalette.text, fontFamily: '"Microsoft YaHei UI", "Segoe UI", Arial, sans-serif' },
    grid: { left: 42, right: 18, top: 42, bottom: 38 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(15, 23, 42, 0.96)",
      borderColor: "rgba(148, 163, 184, 0.28)",
      textStyle: { color: chartPalette.text },
    },
    legend: { top: 4, right: 8, textStyle: { color: chartPalette.muted } },
    xAxis: {
      type: "category",
      axisLine: { lineStyle: { color: chartPalette.grid } },
      axisLabel: { color: chartPalette.muted },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: chartPalette.grid } },
      axisLabel: { color: chartPalette.muted },
    },
  };
}

function getChart(id) {
  const el = $(id);
  if (!el || !window.echarts) return null;
  if (!chartInstances[id]) chartInstances[id] = echarts.init(el, null, { renderer: "canvas" });
  return chartInstances[id];
}

function setChart(id, option) {
  const chart = getChart(id);
  if (!chart) return false;
  chart.setOption(option, true);
  return true;
}

function listText(value) {
  if (Array.isArray(value)) return value.length ? value.map((item) => text(item)).join(" + ") : "缺失";
  if (value === null || value === undefined || value === "") return "缺失";
  return text(value);
}

function money(value, currency = "$") {
  const n = Number(value);
  if (!Number.isFinite(n)) return "暂无";
  const sign = n > 0 ? "+" : "";
  return `${sign}${currency}${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}`;
}

function rawNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function profitLevel(value) {
  const n = rawNumber(value);
  if (n === null || n === 0) return "gray";
  return n > 0 ? "green" : "red";
}

function formatRunId(value) {
  const raw = String(value || "").trim();
  if (!raw) return "暂无";
  let parts = raw.split(/\s+/);
  if (parts.length < 4) {
    const hyphenMatch = raw.match(/^([A-Za-z0-9]+)-([A-Za-z0-9]+)-(\d{8}T\d{6}Z)-([A-Za-z0-9]+)$/);
    if (hyphenMatch) parts = hyphenMatch.slice(1);
  }
  if (parts.length < 4) return text(raw);
  const [symbol, timeframe, stamp, id] = parts;
  const tf = timeframe.replace(/m$/i, "分钟").replace(/h$/i, "小时").replace(/d$/i, "天");
  const match = stamp.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/i);
  const formattedTime = match
    ? `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]} UTC`
    : stamp;
  return `${symbol.toUpperCase()} ${tf}\n${formattedTime}\n#${String(id).replace(/[_-]+/g, " ")}`;
}

function levelForStatus(value, fallback = "gray") {
  const raw = String(value || "").toLowerCase();
  if (["running", "fresh", "ok", "pass", "allowed", "submitted_all_accepted", "active"].includes(raw)) return "green";
  if (["clear", "watch", "ready"].includes(raw)) return "blue";
  if (["degraded", "stale", "aging", "needs_attention"].includes(raw)) return "yellow";
  if (["blocked", "veto", "error", "on", "partial_failed", "all_failed", "unknown_after_exception"].includes(raw)) return "red";
  if (["missing", "unavailable", "disabled", "off"].includes(raw)) return "gray";
  return fallback;
}

function setBadge(el, label, level) {
  el.className = `badge ${level || levelForStatus(label)}`;
  el.textContent = text(label, "未知");
}

function setPill(el, label, level) {
  el.className = `status-pill ${level || levelForStatus(label)}`;
  el.textContent = label;
}

function setError(message = "") {
  const banner = $("errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function clearElement(el) {
  while (el.firstChild) {
    el.removeChild(el.firstChild);
  }
}

function setText(id, value, fallback = "暂无") {
  const el = $(id);
  if (el) el.textContent = text(value, fallback);
}

function appendText(parent, tag, value, className = "") {
  const el = document.createElement(tag);
  if (className) el.className = className;
  el.textContent = value;
  parent.appendChild(el);
  return el;
}

function renderRuntime(runtime, review) {
  const grid = $("runtimeGrid");
  clearElement(grid);
  for (const [key, label] of runtimeItems) {
    const item = runtime[key] || {};
    const card = document.createElement("div");
    card.className = `flow-card ${item.level || levelForStatus(item.label)}`;
    appendText(card, "span", label, "flow-title");
    appendText(card, "strong", text(item.label, "未知"), "flow-status");
    appendText(card, "small", fmtAge(item.age_sec), "flow-age");
    grid.appendChild(card);
  }
}

function renderOptionalWorkers(optionalWorkers) {
  const grid = $("runtimeGrid");
  const review = optionalWorkers?.decision_review || {};
  const card = document.createElement("div");
  card.className = `flow-card ${review.level || levelForStatus(review.label)}`;
  appendText(card, "span", "决策审查", "flow-title");
  appendText(card, "strong", text(review.label || "OPTIONAL_DISABLED", "未知"), "flow-status");
  appendText(card, "small", review.enabled ? fmtAge(review.age_sec) : "optional", "flow-age");
  grid.appendChild(card);
}

function renderDetails(id, entries) {
  const dl = $(id);
  clearElement(dl);
  for (const [key, value] of entries) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = Array.isArray(value) ? value.map((item) => text(item)).join("，") || "暂无" : text(value);
    dl.append(dt, dd);
  }
}

function renderSignalList(id, rows, nameKey = "name") {
  const ul = $(id);
  clearElement(ul);
  const normalizedRows = rows && rows.length > 0 ? rows.slice(0, 8) : [{ [nameKey]: "none", count: 0 }];
  for (const row of normalizedRows) {
    const li = document.createElement("li");
    appendText(li, "span", text(row[nameKey]), "signal-name");
    appendText(li, "strong", number(row.count), "signal-count");
    ul.appendChild(li);
  }
}

function renderChips(id, rows, level = "") {
  const wrap = $(id);
  clearElement(wrap);
  const normalizedRows = rows && rows.length > 0 ? rows.slice(0, 10) : ["none"];
  for (const value of normalizedRows) {
    const chip = document.createElement("span");
    chip.className = `chip ${value === "none" ? "gray" : level}`;
    chip.textContent = text(value);
    wrap.appendChild(chip);
  }
}

function severityForReason(code, fallback = "watch") {
  const raw = String(code || "").toLowerCase();
  if (!raw || raw === "none") return "watch";
  if (
    raw.includes("data_health_veto") ||
    raw.includes("conflict_veto") ||
    raw.includes("net_edge_below_cost") ||
    raw.includes("runtime_entry_veto") ||
    raw.includes("risk_filter:veto") ||
    raw.includes("risk_filter:blocked") ||
    raw.includes("blocked") ||
    raw.endsWith("_veto") ||
    raw.includes(":veto")
  ) {
    return "hard";
  }
  if (
    raw.includes("degraded") ||
    raw.includes("degrade") ||
    raw.includes("edge_estimate_missing") ||
    raw.includes("market_data_restricted_two_source") ||
    raw.includes("risk_filter:degraded") ||
    raw.startsWith("degrade_flag:")
  ) {
    return "degraded";
  }
  return fallback || "watch";
}

function severityLabel(severity) {
  if (severity === "hard") return "阻断";
  if (severity === "degraded") return "降级";
  return "观察";
}

function normalizeReasonRows(rows, fallbackCodes = []) {
  const items = [];
  for (const row of rows || []) {
    if (typeof row === "string") items.push({ code: row, text: text(row) });
    else if (row?.code || row?.text) items.push({ code: row.code || row.text, text: row.text || row.code });
  }
  for (const code of fallbackCodes || []) {
    if (code && !items.some((item) => item.code === code)) items.push({ code, text: text(code) });
  }
  return items;
}

function renderReasonChips(id, rows, level = "") {
  const wrap = $(id);
  clearElement(wrap);
  const normalizedRows = rows && rows.length > 0 ? rows.slice(0, 8) : [{ code: "none", text: "暂无" }];
  for (const row of normalizedRows) {
    const severity = row.code === "none" ? "watch" : severityForReason(row.code, level === "red" ? "hard" : level === "yellow" ? "degraded" : "watch");
    const chip = document.createElement("span");
    chip.className = `chip reason-chip ${row.code === "none" ? "gray" : severity}`;
    appendText(chip, "span", severityLabel(severity), "reason-severity");
    appendText(chip, "strong", text(row.text || row.code));
    appendText(chip, "small", displayCode(row.code));
    wrap.appendChild(chip);
  }
}

function renderGovernanceRows(rows) {
  const wrap = $("factorGovernanceRows");
  clearElement(wrap);
  const header = document.createElement("div");
  header.className = "table-row table-head";
  ["因子", "等级", "生命周期", "效果", "样本", "胜率", "净期望"].forEach((label) => appendText(header, "span", label));
  wrap.appendChild(header);
  const normalizedRows = rows && rows.length > 0 ? rows.slice(0, 8) : [];
  if (!normalizedRows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-row";
    empty.textContent = "暂无治理行";
    wrap.appendChild(empty);
    return;
  }
  for (const row of normalizedRows) {
    const item = document.createElement("div");
    item.className = "table-row";
    appendText(item, "span", text(row.factor_name));
    appendText(item, "span", text(row.factor_grade));
    appendText(item, "span", text(row.factor_lifecycle));
    appendText(item, "span", text(row.factor_effect));
    appendText(item, "span", number(row.sample_count));
    appendText(item, "span", pct(row.win_rate));
    appendText(item, "span", pct(row.net_expectancy_pct));
    wrap.appendChild(item);
  }
}

function renderAudit(events) {
  const wrap = $("auditEvents");
  clearElement(wrap);
  const normalizedEvents = events && events.length > 0 ? [...events].reverse().slice(0, 12) : [];
  if (!normalizedEvents.length) {
    const item = document.createElement("div");
    item.className = "audit-item";
    appendText(item, "strong", "暂无执行事件");
    appendText(item, "span", "未找到执行器审计记录");
    wrap.appendChild(item);
    return;
  }
  for (const event of normalizedEvents) {
    const payload = event.payload || {};
    const reasons = payload.reason_codes || [];
    const severity = severityForReason(reasons[0] || payload.status || event.event_type, levelForStatus(payload.status) === "red" ? "hard" : "watch");
    const item = document.createElement("div");
    item.className = `audit-item ${severity}`;
    appendText(item, "strong", `${text(event.event_type)} / ${text(payload.status)}`);
    appendText(item, "span", `${text(event.generated_at)} / ${reasons.length ? reasons.map((reason) => text(reason)).join("，") : "无原因代码"}`);
    wrap.appendChild(item);
  }
}

function renderQuality(quality) {
  const wrap = $("reviewSourceQuality");
  clearElement(wrap);
  const header = document.createElement("div");
  header.className = "table-row table-head quality-row";
  appendText(header, "span", "数据源");
  appendText(header, "span", "状态");
  wrap.appendChild(header);
  const source = quality || {};
  for (const key of Object.keys(sourceLabels)) {
    const row = document.createElement("div");
    row.className = "table-row quality-row";
    appendText(row, "span", sourceLabels[key]);
    const status = document.createElement("span");
    status.className = `inline-status ${source[key] ? "green" : key === "outcome_samples_available" ? "gray" : "yellow"}`;
    status.textContent = source[key] ? "可用" : "缺失";
    row.appendChild(status);
    wrap.appendChild(row);
  }
}

function normalizeFinding(item) {
  if (!item) return { title: "暂无", detail: "" };
  if (typeof item === "string") return { title: text(item), detail: "" };
  return {
    title: text(item.text || item.reason || item.suggested_action || item.code || item.factor_name),
    detail: text(item.code || item.factor_name || item.source_run_id || "", ""),
  };
}

function renderFindings(id, rows) {
  const wrap = $(id);
  clearElement(wrap);
  const normalizedRows = rows && rows.length > 0 ? rows.slice(0, 5) : [];
  if (!normalizedRows.length) {
    const empty = document.createElement("div");
    empty.className = "finding-item muted";
    empty.textContent = "暂无";
    wrap.appendChild(empty);
    return;
  }
  for (const row of normalizedRows) {
    const finding = normalizeFinding(row);
    const item = document.createElement("div");
    item.className = "finding-item";
    appendText(item, "strong", finding.title);
    if (finding.detail) appendText(item, "span", finding.detail);
    wrap.appendChild(item);
  }
}

function primaryReason(quant) {
  const rows = normalizeReasonRows(quant.reason_codes || [], quant.risk_reason_codes || []);
  const hard = rows.find((row) => severityForReason(row.code) === "hard");
  const degraded = rows.find((row) => severityForReason(row.code) === "degraded");
  return hard || degraded || rows[0] || null;
}

function buildNoTradeSummary(quant, bot) {
  const action = String(quant.action || bot.latest_cycle?.effective_action || "").toLowerCase();
  const allowedAction = action.startsWith("entry") || action === "small_probe";
  const candidatePresent = Boolean(bot.candidate_package?.present);
  const reason = primaryReason(quant);
  const sourceCount = quant.consensus_source_count ?? (Array.isArray(quant.consensus_sources) ? quant.consensus_sources.length : null);
  const sources = listText(quant.consensus_sources);
  const health = scorePct(quant.data_health_score);
  const meta = [`共识 ${sourceCount ?? "缺失"} 源 ${sources}`, `data health ${health}`];
  if (quant.market_data_mode) meta.push(`mode ${text(quant.market_data_mode)}`);
  if (quant.net_edge_pct !== null && quant.net_edge_pct !== undefined) meta.push(`net edge ${pctField(quant.net_edge_pct)}`);
  if (allowedAction && candidatePresent) {
    return { line: `当前可交易 · 动作：${text(action)}`, meta: meta.join("，") };
  }
  const reasonText = reason ? text(reason.text || reason.code) : text(quant.execution_block_reason || "未形成开仓动作");
  return { line: `当前未交易 · 原因：${reasonText}`, meta: meta.join("，") };
}

function renderSummary(data) {
  const quant = data.quant || {};
  const bot = data.bot || {};
  const review = data.decision_review || {};
  const performance = data.performance || {};
  const profit = performance.total_profit_usd;
  const equity = performance.account_equity;
  $("summaryProfit").textContent = money(profit);
  $("summaryProfit").className = profitLevel(profit);
  if (performance.ignored_source === "binance_usdt_perp") {
    $("summaryProfitMeta").textContent = "OKX 权益暂无；已忽略 Binance 旧快照";
  } else {
    const source = performance.account_equity_source ? ` · ${displayCode(performance.account_equity_source)}` : "";
    $("summaryProfitMeta").textContent = rawNumber(equity) === null ? "OKX 权益暂无" : `OKX 权益 ${money(equity)}${source}`;
  }
  setText("summaryAction", quant.action);
  setText("summaryRisk", quant.risk_filter_status);
  setText("summaryCandidate", bot.candidate_package?.present ? "present" : "missing");
  setText("summaryReview", review.review_status || "unavailable");
  const noTrade = buildNoTradeSummary(quant, bot);
  $("summaryBlockReason").textContent = noTrade.line;
  $("summaryBlockMeta").textContent = noTrade.meta;
}

function renderCharts(charts) {
  if (!window.echarts) {
    setBadge($("chartRuntimeBadge"), "charts_unavailable", "yellow");
    return;
  }
  const cycleRows = charts?.cycle_status_timeline || [];
  const statusColors = {
    ok: chartPalette.green,
    blocked: chartPalette.red,
    degraded: chartPalette.yellow,
    incomplete_snapshot_only: chartPalette.yellow,
    incomplete_missing_scheduler_status: chartPalette.yellow,
    missing: chartPalette.gray,
  };
  setBadge($("chartRuntimeBadge"), cycleRows.length ? "available" : "missing", cycleRows.length ? "green" : "gray");
  setChart("cycleStatusChart", {
    ...chartBaseOption(),
    xAxis: { ...chartBaseOption().xAxis, data: cycleRows.map((row) => compactTime(row.generated_at)) },
    yAxis: {
      ...chartBaseOption().yAxis,
      min: 0,
      max: 3,
      interval: 1,
      axisLabel: { color: chartPalette.muted, formatter: (value) => ["缺失", "阻断", "降级/不完整", "正常"][value] || "" },
    },
    series: [{
      name: "cycle status",
      type: "bar",
      barWidth: "58%",
      data: cycleRows.map((row) => ({
        value: row.status_value,
        itemStyle: { color: statusColors[row.status] || chartPalette.gray },
        status: row.status,
        run_id: row.run_id,
      })),
    }],
    tooltip: {
      ...chartBaseOption().tooltip,
      formatter: (items) => {
        const item = items?.[0]?.data || {};
        return `${text(item.status || "unknown")}<br/>${formatRunId(item.run_id || "")}`;
      },
    },
  });

  const metricRows = charts?.quant_metric_series || [];
  const metricLabels = metricRows.map((row) => compactTime(row.generated_at));
  setChart("quantMetricsChart", {
    ...chartBaseOption(),
    color: [chartPalette.green, chartPalette.blue, chartPalette.yellow, chartPalette.red],
    xAxis: { ...chartBaseOption().xAxis, data: metricLabels },
    yAxis: { ...chartBaseOption().yAxis, axisLabel: { color: chartPalette.muted, formatter: "{value}%" } },
    series: [
      ["data health", "data_health_score"],
      ["confidence", "confidence"],
      ["net edge", "net_edge_pct"],
      ["cost", "estimated_cost_pct"],
    ].map(([name, key]) => ({
      name,
      type: "line",
      smooth: true,
      showSymbol: false,
      connectNulls: true,
      data: metricRows.map((row) => row[key]),
    })),
  });

  const reasonRows = charts?.reason_code_counts || [];
  setChart("reasonCodesChart", {
    ...chartBaseOption(),
    grid: { left: 118, right: 18, top: 22, bottom: 28 },
    xAxis: { type: "value", splitLine: { lineStyle: { color: chartPalette.grid } }, axisLabel: { color: chartPalette.muted } },
    yAxis: {
      type: "category",
      inverse: true,
      data: reasonRows.map((row) => text(row.code)),
      axisLabel: { color: chartPalette.muted, width: 110, overflow: "truncate" },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: chartPalette.grid } },
    },
    series: [{
      name: "count",
      type: "bar",
      barWidth: 12,
      data: reasonRows.map((row) => row.count),
      itemStyle: { color: chartPalette.yellow, borderRadius: [0, 4, 4, 0] },
    }],
  });

  const consensusRows = charts?.consensus_quality_series || [];
  setChart("consensusChart", {
    ...chartBaseOption(),
    color: [chartPalette.blue, chartPalette.green],
    xAxis: { ...chartBaseOption().xAxis, data: consensusRows.map((row) => compactTime(row.generated_at)) },
    yAxis: [
      { ...chartBaseOption().yAxis, min: 0, max: 3, interval: 1, axisLabel: { color: chartPalette.muted } },
      { type: "value", min: 0, max: 4, splitLine: { show: false }, axisLabel: { color: chartPalette.muted } },
    ],
    series: [
      {
        name: "quality",
        type: "line",
        step: "middle",
        showSymbol: false,
        data: consensusRows.map((row) => row.quality_value),
      },
      {
        name: "source count",
        type: "bar",
        yAxisIndex: 1,
        barWidth: "42%",
        data: consensusRows.map((row) => row.source_count),
      },
    ],
  });
}

function renderTopbar(data) {
  const runtime = data.runtime || {};
  const workerMode = runtime.real_worker?.mode || "";
  const killSwitch = runtime.kill_switch || {};
  const now = new Date();
  $("updatedAt").textContent = `已更新 ${now.toLocaleString()}`;
  const modeText = workerMode === "submit_enabled" ? "真实下单已启用" : "Dry-run / 只读观察";
  setPill($("orderModePill"), modeText, workerMode === "submit_enabled" ? "red" : "blue");
  setPill($("killSwitchPill"), `熔断：${killSwitch.enabled ? "开启" : "关闭"}`, killSwitch.enabled ? "red" : "green");
  $("modeNotice").textContent = `${modeText}；审查报告只读，不参与自动下单，最终以执行链路和风控结果为准。`;
}

function render(data) {
  const factor = data.factor || {};
  const quant = data.quant || {};
  const bot = data.bot || {};
  const review = data.decision_review || {};

  renderTopbar(data);
  renderSummary(data);
  renderRuntime(data.runtime || {}, review);
  renderOptionalWorkers(data.optional_workers || {});
  renderCharts(data.charts || {});

  setBadge($("factorLookupBadge"), factor.lookup_status?.label, factor.lookup_status?.level);
  $("factorSamples").textContent = number(factor.total_samples);
  $("factorObservations").textContent = number(factor.unique_observations);
  $("lookupRows").textContent = number(factor.lookup_rows);
  $("factorValues").textContent = number(factor.sample_growth?.factor_values);
  const governance = factor.governance || {};
  $("factorGovernanceStatus").textContent = text(governance.status || "unknown");
  setBadge($("factorGovernanceBadge"), governance.status || "unknown", levelForStatus(governance.status));
  renderDetails("factorDetails", [
    ["查找表版本", factor.lookup_version],
    ["查找表新鲜度", factor.lookup_status?.label],
    ["分析数据库", factor.db_available ? "available" : "missing"],
    ["机器人样本", factor.sample_growth?.bot_scheduler_samples],
    ["治理时间", governance.generated_at],
  ]);
  renderGovernanceRows(governance.rows || []);
  renderSignalList("riskCodes", factor.top_reason_codes || []);
  renderSignalList("degradeFlags", factor.top_degrade_flags || []);

  const rawRisk = quant.risk_filter_status || "unknown";
  setBadge($("quantRiskBadge"), rawRisk, levelForStatus(rawRisk));
  setText("quantAction", quant.action);
  setText("quantDirection", quant.direction);
  setText("quantRiskStatus", quant.risk_filter_status);
  setText("quantSizing", quant.sizing_tier);
  $("quantConfidence").textContent = pct(quant.confidence);
  $("reasoning").textContent = text(quant.reasoning_summary, "暂无推理摘要。");
  renderDetails("quantDetails", [
    ["市场状态", quant.regime_bucket],
    ["查找表版本", quant.factor_lookup_version],
    ["自动化边界", quant.automation_boundary],
    ["执行阻断原因", quant.execution_block_reason],
    ["执行警告", quant.execution_warnings || []],
  ]);
  setBadge($("marketDataBadge"), quant.market_data_mode || quant.consensus_quality || "unknown", levelForStatus(quant.consensus_quality || quant.market_data_mode));
  renderDetails("marketDataDetails", [
    ["市场数据模式", quant.market_data_mode],
    ["共识质量", quant.consensus_quality],
    ["共识源数量", quant.consensus_source_count],
    ["共识来源", listText(quant.consensus_sources)],
    ["Binance 源状态", quant.binance_source_health],
    ["Binance 失败原因", quant.binance_source_failure_reason],
    ["Data health", scorePct(quant.data_health_score)],
    ["最新不完整 cycle", quant.latest_incomplete_cycle?.present ? quant.latest_incomplete_cycle.status : ""],
  ]);
  const edgeMissing = quant.net_edge_pct === null || quant.net_edge_pct === undefined || quant.net_edge_pct === "";
  setBadge($("edgeCostBadge"), edgeMissing ? "missing" : "available", edgeMissing ? "yellow" : "green");
  renderDetails("edgeCostDetails", [
    ["Net edge", pctField(quant.net_edge_pct)],
    ["估算总成本", pctField(quant.estimated_cost_pct)],
    ["手续费", pctField(quant.estimated_fee_pct)],
    ["滑点", pctField(quant.estimated_slippage_pct)],
    ["资金费率", pctField(quant.estimated_funding_pct)],
    ["Edge 来源", quant.edge_source],
  ]);
  renderReasonChips("quantReasons", normalizeReasonRows(quant.reason_codes || [], quant.risk_reason_codes || quant.degrade_flags || []));
  const research = quant.research || {};
  setBadge($("researchBadge"), research.status || "unknown", levelForStatus(research.status));
  renderDetails("researchDetails", [
    ["决策状态", research.decision],
    ["新鲜度", research.freshness],
    ["可用于决策", research.decision_ready ? "yes" : "no"],
    ["检查时间", research.generated_at],
    ["数据时间", research.dataset_timestamp],
    ["刷新周期", research.refresh_every ? `${research.refresh_every} 轮` : "未启用"],
  ]);
  renderReasonChips("researchReasons", research.reason_texts || [], levelForStatus(research.status));
  renderChips("supportingFactors", quant.supporting_factors || [], "green");
  renderChips("opposingFactors", quant.opposing_factors || quant.degrade_flags || [], "yellow");
  renderChips("vetoFactors", quant.veto_factors || [], "red");

  const botLevel = levelForStatus(bot.execution_state);
  setBadge($("botStateBadge"), bot.execution_state || "unknown", botLevel);
  setText("botExecutionState", bot.execution_state);
  setText("botPosition", bot.position_state);
  setText("botDirection", bot.position_direction);
  $("botSize").textContent = pct(bot.position_size_pct);
  const candidate = bot.candidate_package || {};
  $("candidateState").textContent = candidate.present ? "存在" : "缺失";
  setBadge($("candidateGateBadge"), candidate.gate_allowed ? "allowed" : "blocked", candidate.gate_allowed ? "green" : "gray");
  renderDetails("candidateDetails", [
    ["是否存在", candidate.present ? "yes" : "no"],
    ["执行包", candidate.package_id],
    ["动作", candidate.action],
    ["方向", candidate.direction],
    ["生成时间", candidate.generated_at],
    ["过期时间", candidate.expires_at],
    ["闸门", candidate.gate_allowed ? "allowed" : "blocked"],
    ["命令", candidate.command_targets || []],
  ]);
  const cycle = bot.latest_cycle || {};
  renderDetails("cycleDetails", [
    ["样本", cycle.sample_id],
    ["完成时间", cycle.finished_at],
    ["请求动作", cycle.requested_action],
    ["生效动作", cycle.effective_action],
    ["预检错误", cycle.preflight_error || "ok"],
  ]);
  renderAudit(bot.worker_events || []);

  const reviewStatus = review.review_status || "unavailable";
  setBadge($("reviewStatusBadge"), reviewStatus, levelForStatus(reviewStatus));
  setText("reviewStatus", reviewStatus);
  setText("reviewMode", review.review_mode);
  $("reviewRunId").textContent = formatRunId(review.source_run_id);
  $("reviewRunId").title = review.source_run_id || "";
  $("reviewHandoffAge").textContent = fmtAge(review.source_handoff_age_sec);
  $("reviewLatency").textContent = review.latency_ms === null || review.latency_ms === undefined ? "暂无" : `${number(review.latency_ms)} 毫秒`;
  renderDetails("reviewDetails", [
    ["交接包编号", review.handoff_id],
    ["来源过期", review.source_stale ? "yes" : "no"],
    ["超时", review.timeout ? "yes" : "no"],
    ["是否使用降级结果", review.fallback_used ? "yes" : "no"],
    ["摘要", review.summary],
  ]);
  renderQuality(review.data_source_quality || {});
  renderFindings("reviewBullCase", review.bull_case || []);
  renderFindings("reviewBearCase", review.bear_case || []);
  renderFindings("reviewRiskFindings", review.risk_findings || []);
  renderFindings("reviewExecutionFindings", review.execution_findings || []);
  renderFindings("reviewGovernanceSuggestions", review.governance_review_suggestions || []);
  renderFindings("reviewQuestions", review.unresolved_questions || []);
}

async function refresh() {
  setPill($("refreshState"), "刷新中", "blue");
  const response = await fetch("/api/overview", { cache: "no-store" });
  if (!response.ok) throw new Error(`请求失败，状态码 ${response.status}`);
  render(await response.json());
  setPill($("refreshState"), "刷新正常", "green");
  setError("");
}

function refreshWithBanner() {
  if (refreshPaused) return;
  refresh().catch((error) => {
    console.error(error);
    setPill($("refreshState"), "刷新失败", "red");
    setError(`刷新失败：${error.message || error}`);
  });
}

function togglePause() {
  refreshPaused = !refreshPaused;
  $("pauseBtn").textContent = refreshPaused ? "继续" : "暂停";
  setPill($("refreshState"), refreshPaused ? "已暂停" : "等待刷新", refreshPaused ? "yellow" : "blue");
}

$("pauseBtn").addEventListener("click", togglePause);
$("refreshBtn").addEventListener("click", refreshWithBanner);
window.addEventListener("resize", () => {
  for (const chart of Object.values(chartInstances)) chart.resize();
});
refreshWithBanner();
setInterval(refreshWithBanner, 5000);
