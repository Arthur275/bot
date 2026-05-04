const runtimeItems = [
  ["factor_collector", "因子采集"],
  ["quant_scheduler", "量化调度"],
  ["bot_scheduler", "机器人调度"],
  ["real_worker", "实盘执行器"],
  ["kill_switch", "熔断开关"],
];

const valueLabels = {
  UNKNOWN: "未知",
  PASS: "通过",
  DEGRADED: "降级",
  ERROR: "错误",
  RUNNING: "运行中",
  STALE: "已过期",
  MISSING: "缺失",
  OK: "正常",
  BLOCKED: "已阻断",
  ALLOWED: "允许",
  IDLE: "空闲",
  FLAT: "空仓",
  LONG: "多头",
  SHORT: "空头",
  WAIT: "等待",
  OBSERVE: "观察",
  POSITION_OPEN: "有持仓",
  position_open: "有持仓",
  degraded: "降级",
  idle: "空闲",
  entry_long: "开多",
  entry_short: "开空",
  exit: "平仓",
  reduce: "减仓",
  wait: "等待",
  observe: "观察",
  long: "多头",
  short: "空头",
  neutral: "中性",
  flat: "空仓",
  pass: "通过",
  degraded: "降级",
  error: "错误",
  ok: "正常",
  allowed: "允许",
  blocked: "阻断",
  yes: "是",
  no: "否",
  none: "无",
  available: "可用",
  missing: "缺失",
};

const $ = (id) => document.getElementById(id);

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return "更新时间未知";
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟前`;
}

function text(value, fallback = "--") {
  if (value === null || value === undefined || value === "") return fallback;
  const raw = String(value);
  return valueLabels[raw] || valueLabels[raw.toUpperCase?.()] || raw;
}

function number(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString() : "0";
}

function pct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "--";
  return `${(n * 100).toFixed(2)}%`;
}

function badge(el, label, level) {
  el.className = `badge ${level || "gray"}`;
  el.textContent = text(label, "未知");
}

function setError(message = "") {
  const banner = $("errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function renderRuntime(runtime) {
  const grid = $("runtimeGrid");
  grid.replaceChildren();
  for (const [key, label] of runtimeItems) {
    const item = runtime[key] || {};
    const card = document.createElement("div");
    const title = document.createElement("span");
    const status = document.createElement("strong");
    const age = document.createElement("small");
    card.className = "status-card";
    title.textContent = label;
    status.className = item.level || "gray";
    status.textContent = text(item.label, "未知");
    age.textContent = fmtAge(item.age_sec);
    card.append(title, status, age);
    grid.appendChild(card);
  }
}

function renderList(id, rows, nameKey = "name") {
  const ul = $(id);
  ul.replaceChildren();
  const normalizedRows = rows && rows.length > 0 ? rows : [{ [nameKey]: "none", count: 0 }];
  for (const row of normalizedRows) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    const count = document.createElement("span");
    name.textContent = text(row[nameKey]);
    count.textContent = number(row.count);
    li.append(name, count);
    ul.appendChild(li);
  }
}

function renderChips(id, rows, level = "") {
  const wrap = $(id);
  wrap.replaceChildren();
  const normalizedRows = rows && rows.length > 0 ? rows : ["none"];
  for (const value of normalizedRows) {
    const chip = document.createElement("span");
    chip.className = `chip ${value === "none" ? "" : level}`;
    chip.textContent = text(value);
    wrap.appendChild(chip);
  }
}

function renderDetails(id, entries) {
  const dl = $(id);
  dl.replaceChildren();
  for (const [key, value] of entries) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = Array.isArray(value) ? value.map((item) => text(item)).join(", ") || "--" : text(value);
    dl.append(dt, dd);
  }
}

function renderAudit(events) {
  const wrap = $("auditEvents");
  wrap.replaceChildren();
  if (!events || events.length === 0) {
    const item = document.createElement("div");
    const title = document.createElement("strong");
    const detail = document.createElement("span");
    item.className = "audit-item";
    title.textContent = "暂无执行事件";
    detail.textContent = "未找到实盘执行审计记录";
    item.append(title, detail);
    wrap.appendChild(item);
    return;
  }
  for (const event of [...events].reverse()) {
    const payload = event.payload || {};
    const item = document.createElement("div");
    const title = document.createElement("strong");
    const detail = document.createElement("span");
    item.className = "audit-item";
    title.textContent = `${text(event.event_type)} / ${text(payload.status)}`;
    const reasons = payload.reason_codes || [];
    detail.textContent = `${text(event.generated_at)} / ${reasons.length ? reasons.map((item) => text(item)).join(", ") : "无原因代码"}`;
    item.append(title, detail);
    wrap.appendChild(item);
  }
}

function render(data) {
  $("updatedAt").textContent = `已更新 ${new Date().toLocaleTimeString()}`;
  renderRuntime(data.runtime || {});

  const factor = data.factor || {};
  badge($("factorLookupBadge"), factor.lookup_status?.label, factor.lookup_status?.level);
  $("factorSamples").textContent = number(factor.total_samples);
  $("factorObservations").textContent = number(factor.unique_observations);
  $("lookupRows").textContent = number(factor.lookup_rows);
  $("factorValues").textContent = number(factor.sample_growth?.factor_values);
  renderDetails("factorDetails", [
    ["查表版本", factor.lookup_version],
    ["机器人样本", factor.sample_growth?.bot_scheduler_samples],
    ["量化数据库", factor.db_available ? "available" : "missing"],
  ]);
  renderList("riskCodes", factor.top_reason_codes || []);
  renderList("degradeFlags", factor.top_degrade_flags || []);

  const quant = data.quant || {};
  const rawRisk = String(quant.risk_filter_status || "UNKNOWN").toUpperCase();
  badge($("quantRiskBadge"), rawRisk, rawRisk === "PASS" ? "green" : rawRisk === "DEGRADED" ? "yellow" : "red");
  $("quantAction").textContent = text(quant.action);
  $("quantDirection").textContent = text(quant.direction);
  $("quantSizing").textContent = text(quant.sizing_tier);
  $("quantConfidence").textContent = pct(quant.confidence);
  $("reasoning").textContent = text(quant.reasoning_summary, "暂无推理摘要。");
  renderDetails("quantDetails", [
    ["市场状态", quant.regime_bucket],
    ["查表版本", quant.factor_lookup_version],
    ["自动化边界", quant.automation_boundary],
    ["执行警告", quant.execution_warnings || []],
  ]);
  renderChips("supportingFactors", quant.supporting_factors || [], "green");
  renderChips("opposingFactors", quant.opposing_factors || quant.degrade_flags || [], "yellow");
  renderChips("vetoFactors", quant.veto_factors || [], "red");

  const bot = data.bot || {};
  const botLevel = bot.execution_state === "position_open" ? "green" : bot.execution_state === "degraded" ? "yellow" : "gray";
  badge($("botStateBadge"), bot.execution_state || "UNKNOWN", botLevel);
  $("botPosition").textContent = text(bot.position_state);
  $("botDirection").textContent = text(bot.position_direction);
  $("botSize").textContent = pct(bot.position_size_pct);
  const candidate = bot.candidate_package || {};
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
    ["预检", cycle.preflight_error || "ok"],
  ]);
  renderAudit(bot.worker_events || []);
}

async function refresh() {
  const response = await fetch("/api/overview", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  render(await response.json());
  setError("");
}

function refreshWithBanner() {
  refresh().catch((error) => {
    console.error(error);
    setError(`刷新失败：${error.message || error}`);
  });
}

$("refreshBtn").addEventListener("click", refreshWithBanner);
refreshWithBanner();
setInterval(refreshWithBanner, 15000);
