const runtimeItems = [
  ["factor_collector", "样本采集", "DB"],
  ["quant_scheduler", "量化判断", "Q"],
  ["bot_scheduler", "机器人调度", "C"],
  ["real_worker", "实盘执行器", "▶"],
  ["kill_switch", "熔断开关", "⛨"],
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
  strong_momentum_probe: "强动量试探仓",
  strong_momentum: "强动量",
  probe_retest_momentum: "回踩动量试探",
  probe_research_evidence_gap: "研究证据缺口试探",
  trigger_edge_probe: "触发边缘试探",
  trend_continuation_probe: "趋势延续试探",
  contrarian_short_probe: "反向空头试探",
  strong_consensus_probe: "强共识试探",
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
  full: "完整",
  "async full": "异步完整审查",
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
  waiting: "等待",
  dry_run: "模拟执行",
  submit_enabled: "真实提交已启用",
  consensus_auto: "自动共识",
  unknown_neutral: "未知中性",
  incomplete_snapshot_only: "仅有快照",
  restricted_location: "地区受限",
  factor_collector: "样本采集",
  quant_scheduler: "量化判断",
  bot_scheduler: "机器人调度",
  real_worker: "实盘执行器",
  optional_disabled: "可选禁用",
  OPTIONAL_DISABLED: "可选禁用",
  restricted_two_source: "限制两源共识",
  market_data_restricted_two_source: "市场数据限制两源",
  market_data_consensus_degraded: "市场数据共识降级",
  not_entry_action: "未形成开仓动作",
  higher_timeframe_not_ready: "大周期未就绪",
  setup_ready_waiting_trigger: "结构就绪等待触发",
  waiting_for_trigger: "等待触发器",
  trigger_watch: "等待触发观察",
  shadow_observe: "影子观察",
  no_order_submission: "不提交订单",
  shadow_preflight_only: "影子预检",
  candidate_execution_package_not_allowed: "候选执行包未放行",
  disabled_by_kill_switch: "熔断禁用",
  active: "活跃",
  submitted: "已提交",
  disabled: "禁用",
  present: "存在",
  false: "否",
  true: "是",
  totalEq: "总权益",
  unknown: "未知",
  handoff_available: "交接包可用",
  factor_lookup_available: "因子查找表可用",
  factor_summary_available: "样本摘要可用",
  risk_report_available: "风险报告可用",
  candidate_package_available: "候选执行包可用",
  worker_audit_available: "执行器审计可用",
  outcome_samples_available: "结果样本可用",
  judgement_not_ok: "量化判断未返回可执行结果",
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
  net_edge_pct: "净优势",
  bundle_missing: "研究包缺失",
  macro_news_veto: "宏观新闻否决",
  ta_overlay_veto: "技术叠加否决",
  "bundle_status:degraded": "研究包状态降级",
  crowding_warning: "拥挤风险预警",
  data_health_degraded: "数据健康度降级",
  data_health_score: "数据健康",
  okx_longs_crowded: "OKX 多头拥挤",
  coinglass_funding_extreme: "CoinGlass 资金费率极端",
  edge_estimate_missing: "优势估算缺失",
  edge_missing: "优势估算缺失",
  overlay_bias_conflict: "叠加偏向冲突",
  overlay_mixed: "叠加信号混杂",
  same_direction_crowding: "同向拥挤压仓",
  same_direction_funding_extreme: "同向资金费率极端",
  same_direction_liquidation_risk: "同向清算风险",
  position_cap_zero: "仓位上限为 0",
  position_cap_below_probe_floor: "仓位上限低于试探仓底线",
  capped_by_strong_momentum_probe: "强动量试探仓上限压制",
  strong_momentum_probe_size_cap: "强动量试探仓最高 3%",
  estimated_cost_pct: "估算成本",
  research_veto: "研究否决",
  insufficient_quality_folds: "有效质量折数不足",
  low_passed_trade_share: "通过交易占比偏低",
  wf_trade_count_low: "走前交易数偏低",
  wf_quality_insufficient: "走前质量不足",
  wf_trade_share_low: "走前交易占比偏低",
  walk_forward_missing: "走前验证缺失",
  walk_forward_folds_missing: "走前验证折数缺失",
  walk_forward_fold_details_missing: "走前验证明细缺失",
  wf_dispersion_high: "走前结果分散偏高",
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
  "overlay_source:coinglass": "叠加来源 CoinGlass",
  overlay_crowding_warning: "叠加拥挤预警",
  "overlay_bias:bearish": "叠加偏向偏空",
  "overlay_bias:bullish": "叠加偏向偏多",
  "coinglass_liq_risk_side:long": "CoinGlass 多头清算风险",
  "coinglass_liq_risk_side:short": "CoinGlass 空头清算风险",
  okx_shorts_crowded: "OKX 空头拥挤",
  regime_alignment: "市场状态一致",
  "regime:long": "大周期多头",
  "regime:neutral": "大周期中性",
  supporting_factor: "支持因子",
  opposing_factor: "反对因子",
  research_aging: "研究数据接近过期",
  research_degraded: "研究降级",
  research_freshness_degraded: "研究新鲜度降级",
  "diagnostic:data_source": "数据源异常",
  "risk_filter:unknown": "风控状态未知",
  "risk_filter:veto": "风控否决",
  "risk_filter:degraded": "风控降级",
  runtime_entry_veto: "运行时开仓否决",
  sample_count_low: "样本数偏低",
  "setup:short": "设置层偏空",
  staleness_veto: "数据新鲜度/可用性否决",
  "transition:direction_not_aligned": "状态转换：方向不一致",
  "transition:okx_taker_volume_experimental": "状态转换：OKX 主动成交量实验因子",
  "transition:overlay_bias:neutral": "状态转换：叠加偏向中性",
  "transition:no_entry_alignment": "状态转换：未形成入场一致性",
  no_entry_alignment: "未形成入场一致性",
  regime_4h: "4小时市场状态",
  confirm_1h: "1小时确认层",
  confirm_1h_status: "1小时确认状态",
  trigger_15m: "15分钟触发器",
  trigger_15m_ready: "15分钟触发就绪",
  setup_15m: "15分钟设置层",
  gate_4h: "4小时闸门",
  block_short: "阻断空头",
  score: "评分",
  health: "健康",
  support: "支撑",
  slope: "斜率",
  breakout_down: "下行突破",
  breakout_up: "上行突破",
  reference: "参考",
  oppose: "反对",
  retest: "回踩",
  retest_support: "回踩支撑",
  slope_support: "斜率支撑",
  breakout_support: "突破支撑",
  "trend direction": "趋势方向",
  ARMED: "已准备",
  skipped: "跳过",
  "real_order_worker": "真实订单执行器",
  real_order_worker: "真实订单执行器",
  "candidate_execution_package_missing": "候选执行包缺失",
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
  grid: "rgba(126,166,204,0.18)",
  panel: "#0b1724",
  green: "#37d58a",
  red: "#ff5d6c",
  yellow: "#f6b90a",
  blue: "#58a6ff",
  cyan: "#22d3ee",
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
    .replace(/\b(\d+)h\b/gi, "$1小时")
    .replace(/\b(\d+)m\b/gi, "$1分钟")
    .replace(/\bapi\b/gi, "接口")
    .replace(/\bdata\b/gi, "数据")
    .replace(/\bT(?=\d{2}:\d{2})/g, " ")
    .replace(/\beth\b/gi, "ETH")
    .replace(/\busdt\b/gi, "USDT")
    .replace(/\bokx\b/gi, "OKX")
    .replace(/\bcoinglass\b/gi, "CoinGlass")
    .replace(/\bbitget\b/gi, "Bitget")
    .replace(/\bmexc\b/gi, "MEXC")
    .replace(/\bgate\b/gi, "Gate")
    .replace(/\bbinance\b/gi, "Binance")
    .replace(/\bconsensus\b/gi, "共识")
    .replace(/\bauto\b/gi, "自动")
    .replace(/\baction\b/gi, "动作")
    .replace(/\border\b/gi, "订单")
    .replace(/\breal\b/gi, "真实")
    .replace(/\bpackage\b/gi, "执行包")
    .replace(/\bexecution\b/gi, "执行")
    .replace(/\bskipped\b/gi, "跳过")
    .replace(/\barmed\b/gi, "已准备")
    .replace(/\bretest\b/gi, "回踩")
    .replace(/\bsupport\b/gi, "支撑")
    .replace(/\bslope\b/gi, "斜率")
    .replace(/\btrend\b/gi, "趋势")
    .replace(/\bdirection\b/gi, "方向")
    .replace(/\bbot\b/gi, "机器人")
    .replace(/\bworker\b/gi, "执行器")
    .replace(/\bresearch\b/gi, "研究")
    .replace(/\boptional\b/gi, "可选")
    .replace(/\bdisabled\b/gi, "禁用")
    .replace(/\bfactor\b/gi, "因子")
    .replace(/\blookup\b/gi, "查找表")
    .replace(/\bhandoff\b/gi, "交接包")
    .replace(/\bruntime\b/gi, "运行时")
    .replace(/\btrigger\b/gi, "触发器")
    .replace(/\bconfirm\b/gi, "确认层")
    .replace(/\bready\b/gi, "就绪")
    .replace(/\bstale\b/gi, "过期")
    .replace(/\bunavailable\b/gi, "不可用")
    .replace(/\bmissing\b/gi, "缺失")
    .replace(/\bblocked\b/gi, "阻断")
    .replace(/\bveto\b/gi, "否决")
    .replace(/\bok\b/gi, "正常")
    .replace(/\bflag\b/gi, "标记")
    .replace(/\bdegrade\b/gi, "降级")
    .replace(/\bpass\b/gi, "通过")
    .replace(/\bwaiting\b/gi, "等待")
    .replace(/\bwait\b/gi, "等待")
    .replace(/\bentry\b/gi, "开仓")
    .replace(/\bexit\b/gi, "平仓")
    .replace(/\bopen\b/gi, "打开")
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
    .replace(/\btrade\b/gi, "交易")
    .replace(/\bshare\b/gi, "占比")
    .replace(/\binsufficient\b/gi, "不足")
    .replace(/\bquality\b/gi, "质量")
    .replace(/\bfolds\b/gi, "折数")
    .replace(/\bestimate\b/gi, "估算")
    .replace(/\bedge\b/gi, "优势")
    .replace(/\bextreme\b/gi, "极端")
    .replace(/\bfunding\b/gi, "资金费率")
    .replace(/\blongs\b/gi, "多头")
    .replace(/\bcrowded\b/gi, "拥挤")
    .replace(/\bsource\b/gi, "来源")
    .replace(/\blocation\b/gi, "地区")
    .replace(/\brestricted\b/gi, "受限")
    .replace(/\bsample\b/gi, "样本")
    .replace(/\bgovernance\b/gi, "治理")
    .replace(/\bnegative\b/gi, "负")
    .replace(/\bexpectancy\b/gi, "期望")
    .replace(/\bregime\b/gi, "市场状态")
    .replace(/\boverlay\b/gi, "叠加")
    .replace(/\bbias\b/gi, "偏向")
    .replace(/\bneutral\b/gi, "中性")
    .replace(/\bbearish\b/gi, "偏空")
    .replace(/\bbullish\b/gi, "偏多")
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
    .replace(/\bfreshness\b/gi, "新鲜度")
    .replace(/\bhealth\b/gi, "健康")
    .replace(/\bpresent\b/gi, "存在")
    .replace(/\bno\b/gi, "否")
    .replace(/\balignment\b/gi, "一致性")
    .replace(/\bbreakout\b/gi, "突破")
    .replace(/\bdown\b/gi, "下行")
    .replace(/\bup\b/gi, "上行")
    .replace(/\breference\b/gi, "参考")
    .replace(/\boppose\b/gi, "反对")
    .replace(/\bissue\b/gi, "问题")
    .replace(/\bby\b/gi, "由于")
    .replace(/\bor\b/gi, "或")
    .replace(/\bcrowding\b/gi, "拥挤")
    .replace(/\bwarning\b/gi, "预警")
    .replace(/\bwf\b/gi, "走前")
    .replace(/\btaker\b/gi, "主动成交")
    .replace(/\bvolume\b/gi, "成交量")
    .replace(/\bexperimental\b/gi, "实验")
    .trim();
}

function displayCode(value) {
  return text(value).replace(/_/g, " ");
}

function displaySummary(value, fallback = "暂无") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value)
    .split(/\s*\|\s*/)
    .map((part) => {
      const [key, ...rest] = part.split("=");
      if (!rest.length) return text(part);
      const valueText = rest.join("=");
      return `${text(key)}：${valueText.split(",").map((piece) => text(piece.trim().replace(/:/g, "："))).join("，")}`;
    })
    .join(" ｜ ");
}

function parseSummaryParts(value) {
  if (value === null || value === undefined || value === "") return [];
  return String(value)
    .split(/\s*\|\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [key, ...rest] = part.split("=");
      const rawKey = rest.length ? key.trim() : "摘要";
      const rawValue = rest.length ? rest.join("=").trim() : part;
      return {
        key: text(rawKey),
        value: rawValue.split(",").map((piece) => text(piece.trim().replace(/:/g, "："))).filter(Boolean).join("，"),
        raw: `${rawKey} ${rawValue}`,
      };
    });
}

function renderSummaryFacts(id, value, fallback = "暂无推理摘要。") {
  const wrap = $(id);
  clearElement(wrap);
  const rows = parseSummaryParts(value);
  if (!rows.length) {
    wrap.textContent = fallback;
    return;
  }
  rows.slice(0, 14).forEach((row) => {
    const item = document.createElement("div");
    item.className = `summary-fact ${levelForDisplay(row.raw, "gray")}`;
    appendText(item, "span", row.key, "summary-key");
    const valueEl = appendText(item, "strong", row.value || "暂无", "summary-value");
    applyValueLevel(valueEl, row.raw, "gray");
    wrap.appendChild(item);
  });
}

function displayTimestamp(value) {
  if (value === null || value === undefined || value === "") return "暂无";
  return text(String(value).replace(/T(?=\d{2}:\d{2})/, " "));
}

function displaySourceList(value) {
  if (Array.isArray(value)) return value.length ? value.map((item) => text(item)).join(" + ") : "缺失";
  if (value === null || value === undefined || value === "") return "缺失";
  return String(value)
    .split(",")
    .map((item) => text(item.trim()))
    .filter(Boolean)
    .join(" + ") || text(value);
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

function ratio(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "暂无";
  return `${n.toFixed(2)} / 1.00`;
}

function pctField(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "缺失";
  const scaled = Math.abs(n) > 1 ? n : n * 100;
  return `${scaled.toFixed(3)}%`;
}

function signedPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "待观察";
  const sign = n > 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(3)}%`;
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

function compactDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value, "暂无");
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
}

function chartBaseOption() {
  return {
    backgroundColor: "transparent",
    animationDuration: 450,
    textStyle: { color: chartPalette.text, fontFamily: '"Cascadia Mono", "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif' },
    grid: { left: 42, right: 16, top: 34, bottom: 32 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(5, 12, 22, 0.98)",
      borderColor: "rgba(126, 166, 204, 0.34)",
      textStyle: { color: chartPalette.text },
    },
    legend: { top: 0, right: 6, itemWidth: 14, itemHeight: 7, textStyle: { color: chartPalette.muted, fontSize: 11 } },
    xAxis: {
      type: "category",
      axisLine: { lineStyle: { color: chartPalette.grid } },
      axisLabel: { color: chartPalette.muted, fontSize: 11 },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: chartPalette.grid } },
      axisLabel: { color: chartPalette.muted, fontSize: 11 },
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

function finiteSeriesValues(rows, keys) {
  const values = [];
  for (const row of rows || []) {
    for (const key of keys) {
      const value = Number(row[key]);
      if (Number.isFinite(value)) values.push(value);
    }
  }
  return values;
}

function paddedAxis(values, fallbackMin, fallbackMax, paddingRatio = 0.12) {
  if (!values.length) return { min: fallbackMin, max: fallbackMax };
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    const spread = Math.max(Math.abs(max) * 0.18, fallbackMax > fallbackMin ? (fallbackMax - fallbackMin) * 0.08 : 0.1);
    min -= spread;
    max += spread;
  } else {
    const pad = (max - min) * paddingRatio;
    min -= pad;
    max += pad;
  }
  return {
    min: Math.max(fallbackMin, Number(min.toFixed(4))),
    max: Math.min(fallbackMax, Number(max.toFixed(4))),
  };
}

function hasAnySeriesValue(rows, keys) {
  return finiteSeriesValues(rows, keys).length > 0;
}

function listText(value) {
  return displaySourceList(value);
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

function usdt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "暂无";
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  return `${sign}${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} USDT`;
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
  const [symbol, timeframe, stamp] = parts;
  const tf = timeframe.replace(/m$/i, "分钟").replace(/h$/i, "小时").replace(/d$/i, "天");
  const match = stamp.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/i);
  const formattedTime = match
    ? `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]} 协调时`
    : stamp;
  return `${symbol.toUpperCase()} ${tf}\n${formattedTime}`;
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

function levelForDisplay(value, fallback = "") {
  const raw = String(value ?? "").toLowerCase();
  const label = text(value, "").toLowerCase();
  const merged = `${raw} ${label}`;
  if (!raw && !label) return fallback;
  if (/(阻断|否决|错误|失败|不可用|缺失|过低|不合格|禁用|blocked|veto|error|failed|missing|unavailable)/i.test(merged)) return "red";
  if (/(降级|预警|接近过期|不足|拥挤|偏高|受限|观察|degraded|warning|stale|aging|insufficient|crowded|restricted|watch)/i.test(merged)) return "yellow";
  if (/(正常|运行中|通过|可用|新鲜|清晰|允许|存在|就绪|是|running|ok|pass|available|fresh|clear|allowed|present|ready|yes)/i.test(merged)) return "green";
  if (/(模拟|仅观察|等待|中性|参考|observe|wait|neutral|reference)/i.test(merged)) return "blue";
  return fallback;
}

function applyValueLevel(el, value, fallback = "") {
  if (!el) return;
  el.classList.remove("green", "blue", "yellow", "red", "gray", "value-muted");
  const level = levelForDisplay(value, fallback);
  if (level) el.classList.add(level);
  if (!level) el.classList.add("value-muted");
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
  if (el) {
    el.textContent = text(value, fallback);
    applyValueLevel(el, value);
  }
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
  for (const [key, label, icon] of runtimeItems) {
    const item = runtime[key] || {};
    const card = document.createElement("div");
    card.className = `flow-card ${item.level || levelForStatus(item.label)}`;
    card.dataset.icon = icon;
    appendText(card, "span", label, "flow-title");
    appendText(card, "strong", text(item.label, "未知"), "flow-status");
    appendText(card, "small", `延迟 ${fmtAge(item.age_sec)}`, "flow-age");
    grid.appendChild(card);
  }
}

function renderOptionalWorkers(optionalWorkers) {
  const grid = $("runtimeGrid");
  const review = optionalWorkers?.decision_review || {};
  const card = document.createElement("div");
  card.className = `flow-card ${review.level || levelForStatus(review.label)}`;
  card.dataset.icon = "R";
  appendText(card, "span", "决策审查", "flow-title");
  appendText(card, "strong", text(review.label || "OPTIONAL_DISABLED", "未知"), "flow-status");
  appendText(card, "small", review.enabled ? `延迟 ${fmtAge(review.age_sec)}` : "可选禁用", "flow-age");
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
    applyValueLevel(dd, value);
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

function isStrongMomentumProbe(quant) {
  return String(quant?.probe_source || "").toLowerCase() === "strong_momentum_probe";
}

function boolText(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  return "暂无";
}

function compactPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "暂无";
  const scaled = Math.abs(n) > 1 ? n : n * 100;
  return `${scaled.toFixed(2)}%`;
}

function probeStatus(quant) {
  if (!quant?.probe_source) return { label: "未启用试探仓", level: "gray" };
  if (quant.execution_allowed === false) return { label: "试探仓被阻断", level: "red" };
  if (quant.execution_allowed === true) return { label: "试探仓可执行", level: isStrongMomentumProbe(quant) ? "blue" : "green" };
  return { label: "试探仓观察", level: isStrongMomentumProbe(quant) ? "blue" : "gray" };
}

function uniqueReasonRows(...groups) {
  const seen = new Set();
  const rows = [];
  for (const group of groups) {
    for (const row of normalizeReasonRows(group || [])) {
      const code = row.code || row.text;
      if (!code || seen.has(code)) continue;
      seen.add(code);
      rows.push(row);
    }
  }
  return rows;
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
  normalizedRows.forEach((row, index) => {
    const severity = row.code === "none" ? "watch" : severityForReason(row.code, level === "red" ? "hard" : level === "yellow" ? "degraded" : "watch");
    const chip = document.createElement("div");
    chip.className = `chip reason-chip ${row.code === "none" ? "gray" : severity}`;
    const rank = document.createElement("span");
    rank.className = "reason-rank";
    rank.textContent = String(index + 1);
    chip.appendChild(rank);
    appendText(chip, "span", severityLabel(severity), "reason-severity");
    appendText(chip, "strong", text(row.text || row.code));
    if (row.code && row.code !== "none") {
      chip.title = `原始代码：${row.code}`;
    }
    wrap.appendChild(chip);
  });
}

function renderProbeDiagnostics(quant) {
  const status = probeStatus(quant);
  setBadge($("probeDiagnosticBadge"), status.label, status.level);
  const summary = $("probeDiagnosticSummary");
  const probeSource = quant.probe_source || "";
  if (!probeSource) {
    summary.textContent = "当前没有试探仓信号；普通开仓闸门仍按原规则判断。";
  } else if (isStrongMomentumProbe(quant)) {
    const action = text(quant.requested_action || quant.action);
    const size = compactPct(quant.executable_size_pct ?? quant.position_size_pct);
    const cap = compactPct(quant.position_cap_pct);
    const prefix = quant.execution_allowed === false ? "信号存在，但执行闸门未放行" : "信号已进入执行闸门";
    summary.textContent = `${text(probeSource)}：${prefix}，请求 ${action}，执行仓位 ${size}，风控上限 ${cap}。放行必须同时满足结构强、回踩成立、触发未完全确认、研究层仅为证据不足。`;
  } else {
    summary.textContent = `${text(probeSource)}：按 ${text(quant.probe_risk_tier || "unknown")} 风险层展示，最终仍以执行闸门为准。`;
  }

  const facts = $("probeDiagnosticFacts");
  clearElement(facts);
  const rows = [
    ["请求动作", quant.requested_action || quant.action],
    ["执行允许", boolText(quant.execution_allowed)],
    ["试探来源", quant.probe_source],
    ["风险层", quant.probe_risk_tier],
    ["执行仓位", compactPct(quant.executable_size_pct ?? quant.position_size_pct)],
    ["信号仓位", compactPct(quant.signal_size_pct)],
    ["仓位上限", compactPct(quant.position_cap_pct)],
    ["研究闸门", quant.research_gate_status],
    ["结构方向", quant.setup_direction],
    ["触发方向", quant.trigger_direction],
    ["触发就绪", boolText(quant.trigger_ready)],
    ["结构强度", ratio(quant.setup_strength)],
    ["触发分", ratio(quant.entry_timing_score)],
    ["回踩支撑", boolText(quant.retest_support)],
    ["突破支撑", boolText(quant.breakout_support)],
    ["斜率支撑", boolText(quant.slope_support)],
    ["叠加偏向", quant.overlay_bias],
    ["失效周期", quant.probe_expiry_bars ? `${quant.probe_expiry_bars} ${text(quant.probe_expiry_timeframe || "bar")}` : ""],
  ];
  for (const [label, value] of rows) {
    const item = document.createElement("div");
    appendText(item, "span", label);
    const strong = appendText(item, "strong", text(value));
    applyValueLevel(strong, value);
    facts.appendChild(item);
  }

  const reasons = uniqueReasonRows(
    quant.sizing_reason_codes || [],
    quant.research_gate_reasons || [],
    quant.transition_reason_codes || [],
    quant.runtime_vetoes || [],
    quant.invalidate_conditions || []
  );
  renderReasonChips("probeDiagnosticReasons", reasons);
}

function renderTriggerWatch(triggerWatch) {
  const status = triggerWatch?.status || "idle";
  setBadge($("triggerWatchBadge"), triggerWatch?.label || "暂无等待触发", status === "active" ? "blue" : "gray");
  const summary = $("triggerWatchSummary");
  const current = triggerWatch?.current || {};
  const stats = triggerWatch?.stats || {};
  if (status === "active" && current.sample_id) {
    summary.textContent = `信心分 ${pct(current.confidence)}，${text(current.direction)}方向，触发器未就绪，影子统计只记录后续价格表现。`;
  } else {
    const count = Number(stats.sample_count || 0);
    summary.textContent = count > 0
      ? `已记录 ${count} 个等待触发样本，当前没有新的 60 分等待触发。`
      : `暂无 60 分等待触发样本；阈值为 ${pct(triggerWatch?.threshold_confidence || 0.6)}。`;
  }

  const statsWrap = $("triggerWatchStats");
  clearElement(statsWrap);
  const statsRows = [
    ["样本数", number(stats.sample_count || 0), stats.sample_count],
    ["一周期均值", signedPct(stats.avg_return_1), stats.avg_return_1],
    ["一周期胜率", pct(stats.positive_rate_1), stats.positive_rate_1],
    ["三周期均值", signedPct(stats.avg_return_3), stats.avg_return_3],
    ["三周期胜率", pct(stats.positive_rate_3), stats.positive_rate_3],
    ["六周期均值", signedPct(stats.avg_return_6), stats.avg_return_6],
    ["六周期胜率", pct(stats.positive_rate_6), stats.positive_rate_6],
  ];
  for (const [label, value, raw] of statsRows) {
    const item = document.createElement("div");
    appendText(item, "span", label);
    const strong = appendText(item, "strong", value);
    applyValueLevel(strong, raw, raw === null || raw === undefined ? "gray" : "");
    statsWrap.appendChild(item);
  }

  const rowsWrap = $("triggerWatchRows");
  clearElement(rowsWrap);
  const rows = triggerWatch?.recent || [];
  if (!rows.length) {
    appendText(rowsWrap, "div", "暂无观察记录", "empty-row");
    return;
  }
  for (const row of rows.slice().reverse().slice(0, 4)) {
    const item = document.createElement("div");
    item.className = "trigger-watch-row";
    const head = document.createElement("div");
    appendText(head, "strong", `${text(row.direction)} ${pct(row.confidence)}`);
    appendText(head, "span", displayTimestamp(row.generated_at), "value-muted");
    item.appendChild(head);
    const meta = document.createElement("div");
    appendText(meta, "span", `触发分 ${ratio(row.entry_timing_score)}`);
    appendText(meta, "span", `结构 ${ratio(row.setup_strength)}`);
    appendText(meta, "span", row.price ? `价格 ${Number(row.price).toFixed(2)}` : "价格待补");
    item.appendChild(meta);
    rowsWrap.appendChild(item);
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
    const cells = [
      [text(row.factor_name), row.factor_name],
      [text(row.factor_grade), row.factor_grade],
      [text(row.factor_lifecycle), row.factor_lifecycle],
      [text(row.factor_effect), row.factor_effect],
      [number(row.sample_count), row.sample_count],
      [pct(row.win_rate), row.win_rate],
      [pct(row.net_expectancy_pct), row.net_expectancy_pct],
    ];
    for (const [label, raw] of cells) {
      const cell = appendText(item, "span", label);
      applyValueLevel(cell, raw);
    }
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
    const head = document.createElement("div");
    head.className = "audit-head";
    appendText(head, "strong", "暂无执行事件");
    appendText(head, "span", "未记录", "audit-state gray");
    item.appendChild(head);
    appendText(item, "span", "未找到执行器审计记录", "audit-meta");
    wrap.appendChild(item);
    return;
  }
  for (const event of normalizedEvents) {
    const payload = event.payload || {};
    const reasons = payload.reason_codes || [];
    const severity = severityForReason(reasons[0] || payload.status || event.event_type, levelForStatus(payload.status) === "red" ? "hard" : "watch");
    const item = document.createElement("div");
    item.className = `audit-item ${severity}`;
    const head = document.createElement("div");
    head.className = "audit-head";
    appendText(head, "strong", text(event.event_type));
    const status = appendText(head, "span", text(payload.status || "unknown"), `audit-state ${levelForStatus(payload.status)}`);
    applyValueLevel(status, payload.status, "gray");
    item.appendChild(head);
    appendText(item, "span", displayTimestamp(event.generated_at), "audit-meta");
    const reasonWrap = document.createElement("div");
    reasonWrap.className = "audit-reasons";
    if (reasons.length) {
      reasons.slice(0, 5).forEach((reason) => {
        const chip = appendText(reasonWrap, "span", text(reason), `audit-reason ${severityForReason(reason)}`);
        chip.title = text(reason);
      });
    } else {
      appendText(reasonWrap, "span", "无原因代码", "audit-reason muted");
    }
    item.appendChild(reasonWrap);
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
    const severity = severityForReason(finding.detail || finding.title, levelForDisplay(finding.title, "watch") === "red" ? "hard" : "watch");
    const item = document.createElement("div");
    item.className = `finding-item ${severity}`;
    appendText(item, "strong", finding.title);
    if (finding.detail) appendText(item, "span", finding.detail);
    wrap.appendChild(item);
  }
}

function primaryReason(quant) {
  const executionRows = normalizeReasonRows(
    [quant.execution_block_reason].filter(Boolean),
    quant.transition_reason_codes || []
  );
  const actionableExecution = executionRows.find((row) => row.code && row.code !== "none");
  if (actionableExecution) return actionableExecution;
  const rows = normalizeReasonRows(quant.reason_codes || [], quant.risk_reason_codes || []);
  const hard = rows.find((row) => severityForReason(row.code) === "hard");
  const degraded = rows.find((row) => severityForReason(row.code) === "degraded");
  return hard || degraded || rows[0] || null;
}

function latestCycleLabel(quant, bot) {
  const cycle = bot.latest_cycle || {};
  if (cycle.sample_id) return formatRunId(cycle.sample_id).split("\n")[0];
  if (quant.latest_incomplete_cycle?.present) return text(quant.latest_incomplete_cycle.status);
  return cycle.sample_id ? formatRunId(cycle.sample_id).split("\n")[0] : "暂无";
}

function buildNoTradeSummary(quant, bot) {
  const action = String(quant.action || bot.latest_cycle?.effective_action || "").toLowerCase();
  const allowedAction = action.startsWith("entry") || action === "small_probe";
  const candidatePresent = Boolean(bot.candidate_package?.present);
  const sourceCount = quant.consensus_source_count ?? (Array.isArray(quant.consensus_sources) ? quant.consensus_sources.length : null);
  const sources = listText(quant.consensus_sources);
  const health = scorePct(quant.data_health_score);
  const meta = [`共识 ${sourceCount ?? "缺失"} 源 ${sources}`, `数据健康 ${health}`];
  if (quant.market_data_mode) meta.push(`模式 ${text(quant.market_data_mode)}`);
  if (quant.net_edge_pct !== null && quant.net_edge_pct !== undefined) meta.push(`净优势 ${pctField(quant.net_edge_pct)}`);
  if (allowedAction && candidatePresent) {
    return { line: `当前可交易 · 动作：${text(action)}`, meta: meta.join("；") };
  }
  const reason = primaryReason(quant);
  const reasonText = reason ? text(reason.text || reason.code) : text(quant.execution_block_reason || "not_entry_action");
  const layerReason = quant.execution_layer_reasoning ? ` / ${text(quant.execution_layer_reasoning)}` : "";
  const transitionReason = (quant.transition_reason_codes || [])[0] ? ` / ${text((quant.transition_reason_codes || [])[0])}` : "";
  return { line: `当前未交易 · 原因：${reasonText}${layerReason}${transitionReason}`, meta: meta.join("；") };
}

function renderSummary(data) {
  const quant = data.quant || {};
  const bot = data.bot || {};
  const review = data.decision_review || {};
  const performance = data.performance || {};
  const runtime = data.runtime || {};
  const profit = performance.total_profit_usd;
  const equity = performance.account_equity;
  const candidate = bot.candidate_package || {};
  const action = quant.action || bot.latest_cycle?.effective_action;
  const riskStatus = quant.risk_filter_status || "unknown";
  const riskLevel = levelForStatus(riskStatus);
  $("summaryProfit").textContent = usdt(profit);
  $("summaryProfit").className = profitLevel(profit);
  if (performance.ignored_source === "binance_usdt_perp") {
    $("summaryProfitMeta").textContent = "OKX 权益暂无；已忽略 Binance 旧快照";
  } else {
    const source = performance.account_equity_source ? ` · ${displayCode(performance.account_equity_source)}` : "";
    $("summaryProfitMeta").textContent = rawNumber(equity) === null ? "OKX 权益暂无" : `${usdt(equity)}${source}`;
  }
  setText("summaryAction", action);
  $("summaryAction").className = "headline-value blue";
  setText("summaryDirection", quant.direction || bot.position_direction);
  setText("summaryMarketMode", quant.market_data_mode || quant.consensus_quality);
  setText("summaryRisk", riskStatus);
  $("summaryRisk").className = `headline-value ${riskLevel}`;
  $("summaryRiskScore").textContent = ratio(quant.data_health_score);
  $("summaryRiskBudget").textContent = scorePct(quant.data_health_score);
  $("summaryCandidate").textContent = candidate.present ? "1 个候选" : "缺失";
  $("summarySnapshotSource").textContent = candidate.snapshot_source ? text(candidate.snapshot_source) : listText(quant.consensus_sources);
  $("summaryBundleGenerated").textContent = compactDateTime(candidate.generated_at);
  setText("summaryReview", review.review_status || "unavailable");
  $("summaryReview").className = `headline-value ${levelForStatus(review.review_status || "unavailable")}`;
  $("summaryRealWorker").textContent = text(runtime.real_worker?.mode || runtime.real_worker?.label || "unknown");
  $("summaryLastReview").textContent = review.generated_at ? compactDateTime(review.generated_at) : fmtAge(review.source_handoff_age_sec);
  $("summaryLatestCycle").textContent = latestCycleLabel(quant, bot);
  const noTrade = buildNoTradeSummary(quant, bot);
  $("summaryBlockReason").textContent = action ? text(action) : noTrade.line;
  $("summaryBlockReason").className = `headline-value ${riskLevel}`;
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
    unreliable: chartPalette.red,
    incomplete_snapshot_only: chartPalette.yellow,
    incomplete_missing_scheduler_status: chartPalette.yellow,
    missing: chartPalette.gray,
  };
  const statusOrder = ["blocked", "incomplete_snapshot_only", "degraded", "ok"];
  const statusNames = {
    ok: "正常",
    degraded: "降级",
    incomplete_snapshot_only: "快照未完成",
    incomplete_missing_scheduler_status: "状态缺失",
    blocked: "阻断",
    missing: "缺失",
    unreliable: "不可靠",
  };
  const rowTotal = Math.max(cycleRows.length, 1);
  const counts = statusOrder.map((status) => {
    const count = cycleRows.filter((row) => row.status === status).length;
    return { status, count, ratio: count / rowTotal };
  });
  const countsWrap = $("cycleStatusCounts");
  if (countsWrap) {
    clearElement(countsWrap);
    for (const item of counts.slice().reverse()) {
      const row = document.createElement("div");
      row.className = `status-count ${item.status}`;
      appendText(row, "span", statusNames[item.status] || text(item.status));
      appendText(row, "b", `${item.count} (${(item.ratio * 100).toFixed(1)}%)`);
      countsWrap.appendChild(row);
    }
  }
  setBadge($("chartRuntimeBadge"), cycleRows.length ? "available" : "missing", cycleRows.length ? "green" : "gray");
  setChart("cycleStatusChart", {
    ...chartBaseOption(),
    grid: { left: 102, right: 16, top: 18, bottom: 34 },
    xAxis: {
      type: "category",
      data: cycleRows.map((row, index) => String(index - cycleRows.length + 1)),
      axisLine: { lineStyle: { color: chartPalette.grid } },
      axisLabel: { color: chartPalette.muted, fontSize: 11 },
      axisTick: { show: false },
      splitLine: { show: true, lineStyle: { color: "rgba(126,166,204,0.1)", type: "dashed" } },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 3,
      interval: 1,
      axisLabel: {
        color: chartPalette.muted,
        fontSize: 11,
        formatter: (value) => ["阻断", "快照未完成", "降级", "正常"][value] || "",
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: chartPalette.grid } },
      splitLine: { lineStyle: { color: "rgba(126,166,204,0.1)", type: "dashed" } },
    },
    visualMap: { show: false },
    series: [{
      name: "周期状态",
      type: "scatter",
      symbolSize: 12,
      data: cycleRows.map((row, index) => ({
        value: [String(index - cycleRows.length + 1), statusOrder.indexOf(row.status) >= 0 ? statusOrder.indexOf(row.status) : Math.max(0, Number(row.status_value || 0))],
        itemStyle: { color: statusColors[row.status] || chartPalette.gray },
        status: row.status,
        run_id: row.run_id,
        generated_at: row.generated_at,
      })),
    }],
    tooltip: {
      ...chartBaseOption().tooltip,
      trigger: "item",
      formatter: (item) => {
        const data = item?.data || {};
        return `${statusNames[data.status] || text(data.status || "unknown")}<br/>${compactTime(data.generated_at)}<br/>${formatRunId(data.run_id || "")}`;
      },
    },
  });

  const metricRows = charts?.quant_metric_series || [];
  const metricLabels = metricRows.map((row) => compactTime(row.generated_at));
  const healthAxis = paddedAxis(finiteSeriesValues(metricRows, ["data_health_score", "confidence", "thesis_score", "entry_timing_score"]), 0, 1);
  const pctAxis = paddedAxis(finiteSeriesValues(metricRows, ["net_edge_pct", "estimated_cost_pct"]), -0.01, 0.01);
  const hasHealth = hasAnySeriesValue(metricRows, ["data_health_score"]);
  const hasEdge = hasAnySeriesValue(metricRows, ["net_edge_pct"]);
  const hasCost = hasAnySeriesValue(metricRows, ["estimated_cost_pct"]);
  setBadge($("chartQuantBadge"), metricRows.length ? "available" : "missing", metricRows.length ? (hasHealth ? "green" : "yellow") : "gray");
  const metricNote = $("quantMetricsNote");
  if (metricNote) {
    const notes = [];
    notes.push(hasHealth ? "数据健康来自量化周期。" : "数据健康缺失。");
    notes.push(hasEdge ? "净优势已估算。" : "净优势估算缺失。");
    if (!hasCost) notes.push("成本估算缺失。");
    metricNote.textContent = notes.join(" ");
    metricNote.className = `source-note metric-note ${hasHealth && hasEdge ? "green" : "yellow"}`;
  }
  setChart("quantMetricsChart", {
    ...chartBaseOption(),
    color: [chartPalette.green, chartPalette.blue, chartPalette.cyan, chartPalette.gray, chartPalette.red, chartPalette.yellow],
    grid: { left: 44, right: 44, top: 34, bottom: 32 },
    xAxis: { ...chartBaseOption().xAxis, data: metricLabels },
    yAxis: [
      { ...chartBaseOption().yAxis, ...healthAxis, axisLabel: { color: chartPalette.muted, fontSize: 11, formatter: (value) => Number(value).toFixed(2) } },
      { type: "value", ...pctAxis, splitLine: { show: false }, axisLabel: { color: chartPalette.muted, fontSize: 11, formatter: (value) => `${Number(value).toFixed(2)}%` } },
    ],
    series: [
      ["数据健康", "data_health_score", 0],
      ["信心分", "confidence", 0],
      ["论点分", "thesis_score", 0],
      ["触发分", "entry_timing_score", 0],
      ["净优势", "net_edge_pct", 1],
      ["估算成本", "estimated_cost_pct", 1],
    ].map(([name, key, yAxisIndex]) => ({
      name,
      yAxisIndex,
      type: "line",
      smooth: true,
      showSymbol: true,
      symbolSize: 4,
      connectNulls: false,
      lineStyle: { width: 2 },
      data: metricRows.map((row) => row[key]),
    })),
    tooltip: {
      ...chartBaseOption().tooltip,
      formatter: (items) => {
        const rows = Array.isArray(items) ? items : [items];
        const index = rows[0]?.dataIndex ?? 0;
        const raw = metricRows[index] || {};
        const lines = [`周期 ${index + 1}`, compactTime(raw.generated_at)];
        for (const item of rows) {
          const axis = item.seriesName === "净优势" || item.seriesName === "估算成本" ? "%" : "";
          const value = item.value === null || item.value === undefined ? "缺失" : `${Number(item.value).toFixed(axis ? 3 : 3)}${axis}`;
          lines.push(`${item.marker}${item.seriesName}：${value}`);
        }
        if (!hasEdge && raw.edge_estimate_status) lines.push(`优势估算：${text(raw.edge_estimate_status)}`);
        return lines.join("<br/>");
      },
    },
  });

  const reasonRows = charts?.reason_code_counts || [];
  setChart("reasonCodesChart", {
    ...chartBaseOption(),
    grid: { left: 120, right: 20, top: 18, bottom: 28 },
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
      name: "数量",
      type: "bar",
      barWidth: 9,
      barMaxWidth: 10,
      barCategoryGap: "56%",
      data: reasonRows.map((row) => row.count),
      itemStyle: {
        color: {
          type: "linear",
          x: 0,
          y: 0,
          x2: 1,
          y2: 0,
          colorStops: [
            { offset: 0, color: "#ff5d6c" },
            { offset: 1, color: "#ff7e87" },
          ],
        },
        borderRadius: [0, 2, 2, 0],
      },
      label: { show: true, position: "right", color: chartPalette.text, fontSize: 11 },
    }],
  });

  const consensusRows = charts?.consensus_quality_series || [];
  setChart("consensusChart", {
    ...chartBaseOption(),
    color: [chartPalette.blue, chartPalette.green],
    grid: { left: 36, right: 40, top: 28, bottom: 28 },
    xAxis: { ...chartBaseOption().xAxis, data: consensusRows.map((row) => compactTime(row.generated_at)) },
    yAxis: [
      { ...chartBaseOption().yAxis, min: 0, max: 6, interval: 2, axisLabel: { color: chartPalette.muted } },
      { type: "value", min: 0, max: 1, splitLine: { show: false }, axisLabel: { color: chartPalette.muted } },
    ],
    series: [
      {
        name: "源数量",
        type: "bar",
        barWidth: 8,
        barMaxWidth: 9,
        barCategoryGap: "62%",
        data: consensusRows.map((row) => row.source_count),
        itemStyle: { color: "rgba(88, 166, 255, 0.78)", borderRadius: [2, 2, 0, 0] },
      },
      {
        name: "共识质量",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        showSymbol: true,
        symbolSize: 4,
        data: consensusRows.map((row) => {
          const n = Number(row.quality_value);
          if (!Number.isFinite(n)) return null;
          return n > 1 ? n / 3 : n;
        }),
        lineStyle: { width: 2, color: chartPalette.green },
      },
    ],
  });
}

function updateExecutionStepper(runtime, review) {
  const rows = [
    ["stepCollector", runtime.factor_collector || {}, "label", "age_sec"],
    ["stepQuant", runtime.quant_scheduler || {}, "label", "age_sec"],
    ["stepScheduler", runtime.bot_scheduler || {}, "label", "age_sec"],
    ["stepExecutor", runtime.real_worker || {}, "mode", "age_sec"],
    ["stepReview", review || {}, "review_status", "source_handoff_age_sec"],
  ];
  for (const [id, item, labelKey, ageKey] of rows) {
    const label = item[labelKey] || item.label || "unknown";
    const el = $(id);
    el.textContent = `${text(label, "未知")} · ${fmtAge(item[ageKey])}`;
    applyValueLevel(el, label);
    const row = el.closest("div");
    if (row) {
      row.classList.remove("green", "blue", "yellow", "red", "gray");
      row.classList.add(levelForDisplay(label, "gray"));
    }
  }
}

function renderTopbar(data) {
  const runtime = data.runtime || {};
  const workerMode = runtime.real_worker?.mode || "";
  const killSwitch = runtime.kill_switch || {};
  const now = new Date();
  $("updatedAt").textContent = `最后更新：${now.toLocaleString()}`;
  const modeText = workerMode === "submit_enabled" ? "真实下单已启用" : "模拟执行";
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
  updateExecutionStepper(data.runtime || {}, review);

  setBadge($("factorLookupBadge"), factor.lookup_status?.label, factor.lookup_status?.level);
  $("factorSamples").textContent = number(factor.total_samples);
  $("factorObservations").textContent = number(factor.unique_observations);
  $("lookupRows").textContent = number(factor.lookup_rows);
  $("factorValues").textContent = number(factor.sample_growth?.factor_values);
  const governance = factor.governance || {};
  $("factorGovernanceStatus").textContent = text(governance.status || "unknown");
  ["factorSamples", "factorObservations", "lookupRows", "factorValues"].forEach((id) => applyValueLevel($(id), "available", "green"));
  applyValueLevel($("factorGovernanceStatus"), governance.status || "unknown");
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
  setText("summaryRegime", quant.regime_bucket);
  $("summaryApproval").textContent = primaryReason(quant)?.code ? text(primaryReason(quant).code) : text(quant.execution_block_reason || "pass");
  renderSummaryFacts("reasoning", quant.reasoning_summary, "暂无推理摘要。");
  renderDetails("quantDetails", [
    ["市场状态", quant.regime_bucket],
    ["查找表版本", quant.factor_lookup_version],
    ["自动化边界", quant.automation_boundary],
    ["执行阻断原因", quant.execution_block_reason],
    ["执行层原因", quant.execution_layer_reasoning],
    ["执行机会状态", quant.execution_opportunity_status],
    ["执行警告", quant.execution_warnings || []],
  ]);
  renderTriggerWatch(quant.trigger_watch || {});
  setBadge($("marketDataBadge"), quant.market_data_mode || quant.consensus_quality || "unknown", levelForStatus(quant.consensus_quality || quant.market_data_mode));
  renderDetails("marketDataDetails", [
    ["市场数据模式", quant.market_data_mode],
    ["共识质量", quant.consensus_quality],
    ["共识源数量", quant.consensus_source_count],
    ["共识来源", listText(quant.consensus_sources)],
    ["Binance 源状态", quant.binance_source_health],
    ["Binance 失败原因", quant.binance_source_failure_reason],
    ["数据健康", scorePct(quant.data_health_score)],
    ["最新不完整周期", quant.latest_incomplete_cycle?.present ? quant.latest_incomplete_cycle.status : ""],
  ]);
  const edgeMissing = quant.net_edge_pct === null || quant.net_edge_pct === undefined || quant.net_edge_pct === "";
  setBadge($("edgeCostBadge"), edgeMissing ? "missing" : "available", edgeMissing ? "yellow" : "green");
  renderDetails("edgeCostDetails", [
    ["净优势", pctField(quant.net_edge_pct)],
    ["估算总成本", pctField(quant.estimated_cost_pct)],
    ["手续费", pctField(quant.estimated_fee_pct)],
    ["滑点", pctField(quant.estimated_slippage_pct)],
    ["资金费率", pctField(quant.estimated_funding_pct)],
    ["优势来源", quant.edge_source],
  ]);
  renderReasonChips("quantReasons", normalizeReasonRows(quant.reason_codes || [], quant.risk_reason_codes || quant.degrade_flags || []));
  renderProbeDiagnostics(quant);
  const research = quant.research || {};
  setBadge($("researchBadge"), research.status || "unknown", levelForStatus(research.status));
  renderDetails("researchDetails", [
    ["决策状态", research.decision],
    ["新鲜度", research.freshness],
    ["研究包已生成", research.decision_ready ? "yes" : "no"],
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
  applyValueLevel($("botSize"), bot.position_size_pct);
  const candidate = bot.candidate_package || {};
  $("candidateState").textContent = candidate.present ? "存在" : "缺失";
  applyValueLevel($("candidateState"), candidate.present ? "present" : "missing");
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
  setPill($("refreshState"), "刷新：进行中", "blue");
  const response = await fetch("/api/overview", { cache: "no-store" });
  if (!response.ok) throw new Error(`请求失败，状态码 ${response.status}`);
  render(await response.json());
  setPill($("refreshState"), "刷新：正常", "green");
  setError("");
}

function refreshWithBanner() {
  if (refreshPaused) return;
  refresh().catch((error) => {
    console.error(error);
    setPill($("refreshState"), "刷新：失败", "red");
    setError(`刷新失败：${error.message || error}`);
  });
}

function togglePause() {
  refreshPaused = !refreshPaused;
  $("pauseBtn").textContent = refreshPaused ? "继续" : "暂停";
  setPill($("refreshState"), refreshPaused ? "刷新：暂停" : "刷新：等待", refreshPaused ? "yellow" : "blue");
}

$("pauseBtn").addEventListener("click", togglePause);
$("refreshBtn").addEventListener("click", refreshWithBanner);
window.addEventListener("resize", () => {
  for (const chart of Object.values(chartInstances)) chart.resize();
});
refreshWithBanner();
setInterval(refreshWithBanner, 5000);
