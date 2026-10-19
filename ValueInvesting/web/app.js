const form = document.querySelector("#searchForm");
const codeInput = document.querySelector("#stockCode");
const submitBtn = document.querySelector("#submitBtn");
const statusBox = document.querySelector("#status");
const report = document.querySelector("#report");
const LAST_REPORT_KEY = "value-investing.last-report.v1";
const rankingStatus = document.querySelector("#rankingStatus");
const rankingList = document.querySelector("#rankingList");
const rankingProgress = document.querySelector("#rankingProgress i");
const refreshRanking = document.querySelector("#refreshRanking");
let activeRequest = null;
let rankingTimer = null;

form.addEventListener("submit", (event) => {
  event.preventDefault();
  runCheck(codeInput.value);
});

document.querySelectorAll("[data-code]").forEach((button) => {
  button.addEventListener("click", () => {
    codeInput.value = button.dataset.code;
    runCheck(button.dataset.code);
  });
});

codeInput.addEventListener("input", () => {
  codeInput.value = codeInput.value.replace(/\D/g, "").slice(0, 6);
});

refreshRanking.addEventListener("click", async () => {
  refreshRanking.disabled = true;
  try {
    await fetch("/api/top-health/refresh", { method: "POST" });
    await loadRanking();
  } finally {
    refreshRanking.disabled = false;
  }
});

async function loadRanking() {
  clearTimeout(rankingTimer);
  try {
    const response = await fetch("/api/top-health");
    const data = await response.json();
    renderRanking(data);
    if (data.status === "running" || data.status === "cached" || data.status === "idle") {
      rankingTimer = setTimeout(loadRanking, 2000);
    }
  } catch {
    rankingStatus.textContent = "暂时无法读取自动排行，请稍后点击重新筛选。";
  }
}

function renderRanking(data) {
  const current = Number(data.progress?.current || 0);
  const total = Number(data.progress?.total || 0);
  const sourceText = data.heat_source ? ` · 热度来源：${data.heat_source}` : "";
  const updateText = data.updated_at ? ` · 更新：${new Date(data.updated_at).toLocaleString("zh-CN")}` : "";
  rankingStatus.textContent = `${data.message || "等待筛选"}${sourceText}${updateText}`;
  rankingProgress.style.width = `${total ? Math.round(current / total * 100) : 0}%`;
  refreshRanking.disabled = data.status === "running";

  if (!data.results?.length) {
    rankingList.innerHTML = `<div class="ranking-empty">${escapeHtml(data.status === "error" ? data.message : "正在计算健康排行，首次启动可能需要一段时间…")}</div>`;
    return;
  }
  rankingList.innerHTML = data.results.map((item, index) => `
    <button class="ranking-item" type="button" data-ranking-code="${escapeHtml(item.code)}">
      <header><em>NO.${index + 1}</em><span>${Number(item.score).toFixed(1)} 分</span></header>
      <strong>${escapeHtml(item.name)}</strong>
      <small>${escapeHtml(item.code)} · ${escapeHtml(item.grade)} · 热度 #${escapeHtml(item.heat_rank ?? "—")}</small>
    </button>
  `).join("");
  document.querySelectorAll("[data-ranking-code]").forEach((button) => {
    button.addEventListener("click", () => {
      codeInput.value = button.dataset.rankingCode;
      runCheck(button.dataset.rankingCode);
      statusBox.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

async function runCheck(rawCode, useRankingSnapshot = false) {
  const code = String(rawCode || "").replace(/\D/g, "");
  if (!/^\d{6}$/.test(code)) {
    setStatus("请输入正确的 6 位 A 股代码。", "error");
    codeInput.focus();
    return;
  }

  if (activeRequest) activeRequest.abort();
  activeRequest = new AbortController();
  submitBtn.disabled = true;
  const tradePlan = document.querySelector("#tradePlan");
  const hasPlan = Boolean(tradePlan.querySelector(".trade-stance"));
  if (!hasPlan) {
    tradePlan.innerHTML = `<div class="trade-empty">正在计算买入、止盈和止损…</div>`;
  }
  setStatus(
    hasPlan
      ? `正在更新 ${code} 的买入计划，当前仍显示上次结果…`
      : useRankingSnapshot
        ? `正在读取 ${code} 的排行榜完整快照…`
        : `正在获取 ${code} 的行情、财务和历史数据…`,
    "loading"
  );

  try {
    const endpoint = useRankingSnapshot ? `/api/top-health/${code}` : `/api/health/${code}`;
    const response = await fetch(endpoint, { signal: activeRequest.signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "检测失败，请稍后重试。");
    if (!payload.trade_plan) {
      payload.trade_plan = await fetchTradePlan(code, activeRequest.signal);
    }
    renderReport(payload);
    setStatus(
      `${payload.stock.name}（${payload.stock.code}）${payload.snapshot ? "排行快照" : "最新检测"}完成 · 数据可信度 ${payload.confidence.level}`,
      ""
    );
    report.classList.remove("report-pending");
    history.replaceState(null, "", `?code=${code}${payload.snapshot ? "&source=ranking" : ""}`);
  } catch (error) {
    if (error.name !== "AbortError") setStatus(error.message || "网络请求失败。", "error");
  } finally {
    submitBtn.disabled = false;
    activeRequest = null;
  }
}

function renderReport(data) {
  const stock = data.stock;
  text("#stockMeta", `${stock.code} · 最新价 ${format(stock.price, "元")} · 涨跌 ${signed(stock.change_pct, "%")}`);
  text("#stockName", stock.name || stock.code);
  text("#overview", data.summary.overview);
  text("#totalScore", Number(data.score).toFixed(1));
  text("#grade", data.grade);
  document.querySelector("#scoreRing").style.setProperty("--score", Math.max(0, Math.min(100, data.score)));
  document.querySelector("#reportTags").innerHTML = [
    `评级：${data.grade}`,
    `可信度：${data.confidence.level}（${data.confidence.score}%）`,
    `财报期：${data.raw_metrics.financials.report_date || "未获取"}`,
    ...(data.snapshot ? [`排行快照：${new Date(data.snapshot.updated_at).toLocaleString("zh-CN")}`] : []),
    ...(data.snapshot?.refresh_in_progress ? ["排行榜正在刷新，当前展示上次快照"] : []),
  ].map((value) => `<span class="tag">${escapeHtml(value)}</span>`).join("");

  document.querySelector("#dimensions").innerHTML = data.dimensions.map(renderDimension).join("");
  renderList("#strengths", data.summary.strengths, "暂未识别出明显优势。");
  const risks = [...data.summary.risks];
  if (!risks.length) risks.push("当前规则未识别出显著风险，仍需核对公告和原始财报。");
  renderList("#risks", risks);

  const metrics = data.dimensions.flatMap((dimension) => dimension.metrics);
  document.querySelector("#metrics").innerHTML = metrics.map((metric) => `
    <article class="metric">
      <span>${escapeHtml(metric.label)}</span>
      <strong>${metric.value === null ? "—" : escapeHtml(format(metric.value, metric.unit))}</strong>
      <small>指标健康度 ${metric.score.toFixed(1)} / 100</small>
    </article>
  `).join("");
  const tradePlan = document.querySelector("#tradePlan");
  try {
    tradePlan.innerHTML = renderTradePlan(data.trade_plan);
  } catch (error) {
    tradePlan.innerHTML = `<div class="trade-empty">买入计划暂时无法展示：${escapeHtml(error.message || "未知错误")}</div>`;
  }
  rememberReport(data);

  document.querySelector("#sources").innerHTML = Object.entries(data.data_sources).map(([key, source]) => {
    const names = { quote: "实时行情", financials: "财务指标", history: "历史日线" };
    const date = source.fetched_at ? new Date(source.fetched_at).toLocaleString("zh-CN") : "未获取";
    return `<div class="source-row"><b>${names[key]}</b>：${escapeHtml(source.source)} · ${escapeHtml(date)}${source.stale ? "（缓存）" : ""}</div>`;
  }).join("");
  text("#limitations", data.summary.limitations);
  text("#warnings", data.warnings.join(" "));
  text("#disclaimer", data.disclaimer);
}

function renderDimension(dimension) {
  const items = dimension.metrics.map((metric) => `
    <li class="metric-status-${metric.status}">
      <b>${escapeHtml(metric.label)}</b> ${metric.value === null ? "—" : escapeHtml(format(metric.value, metric.unit))}
      <br>${escapeHtml(metric.explanation)}
    </li>
  `).join("");
  return `
    <article class="dimension-card">
      <header><h3>${escapeHtml(dimension.name)}</h3><strong>${dimension.score} / ${dimension.max_score}</strong></header>
      <div class="bar"><i style="width:${Math.max(0, Math.min(100, dimension.percentage))}%"></i></div>
      <details>
        <summary>查看 ${dimension.metrics.length} 项依据</summary>
        <ul>${items}</ul>
      </details>
    </article>
  `;
}

function rememberReport(data) {
  if (!data?.stock?.code) return;
  try {
    localStorage.setItem(LAST_REPORT_KEY, JSON.stringify(data));
  } catch {
    /* 浏览器拒绝写入时，当前页面结果仍然保留。 */
  }
}

function readRememberedReport() {
  try {
    const data = JSON.parse(localStorage.getItem(LAST_REPORT_KEY) || "null");
    if (!data?.stock?.code || !/^\d{6}$/.test(String(data.stock.code))) return null;
    if (!Array.isArray(data.dimensions) || !data.summary || !data.confidence) return null;
    return data;
  } catch {
    return null;
  }
}

async function fetchTradePlan(code, signal) {
  const response = await fetch(`/api/health/${code}`, { signal });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) return null;
  return payload.trade_plan || null;
}

function renderTradePlan(plan) {
  if (!plan) {
    return `<div class="trade-empty">这份结果生成时还没有买入计划。重新检测，或等排行榜刷新后再看。</div>`;
  }
  if (!plan.available) {
    return `<div class="trade-empty">${escapeHtml(plan.reason || "暂无法生成买入计划。")}</div>`;
  }

  const prices = [
    ["买入价格", plan.buy_price, `较现价 ${formatGap(plan.buy_vs_price_pct)}`, plan.buy_analysis, "buy"],
    ["止盈价格", plan.take_profit_price, `较买入价 ${formatGap(plan.take_profit_vs_buy_pct)}`, plan.take_profit_analysis, "take"],
    ["止损价格", plan.stop_loss_price, `较买入价 ${formatGap(plan.stop_loss_vs_buy_pct)}`, plan.stop_loss_analysis, "stop"],
  ];
  const facts = [
    ["盈亏比", plan.reward_risk ? `1 : ${Number(plan.reward_risk).toFixed(2)}` : "—"],
    ["参考仓位", plan.position],
    ["观察周期", plan.horizon || "—"],
    ["估值锚", plan.fair_price ? `${formatPrice(plan.fair_price)}${plan.fair_pe ? ` · ${Number(plan.fair_pe).toFixed(1)} 倍` : ""}` : "—"],
  ];
  const batches = plan.batches?.length
    ? `<article><h3>分批方式</h3><ul>${plan.batches.map((batch) => `
        <li><b>${escapeHtml(batch.label)} ${escapeHtml(batch.weight)}</b> · ${formatPrice(batch.price)}<br>${escapeHtml(batch.note)}</li>
      `).join("")}</ul></article>`
    : "";

  return `
    <div class="trade-stance trade-${escapeHtml(plan.action_code)}">
      <div>
        <b>${escapeHtml(plan.action)}</b>
        <p>${escapeHtml(plan.summary)}</p>
      </div>
      <span>${plan.recommended ? "可执行参考" : "暂不执行"}</span>
    </div>
    ${plan.history_note ? `<p class="trade-note">${escapeHtml(plan.history_note)}</p>` : ""}
    <div class="trade-prices">
      ${prices.map(([label, value, gap, analysis, kind]) => `
        <article class="trade-card trade-card-${kind}">
          <span>${label}</span>
          <strong>${formatPrice(value)}</strong>
          <em>${escapeHtml(gap)}</em>
          <p>${escapeHtml(analysis)}</p>
        </article>
      `).join("")}
    </div>
    <div class="trade-facts">
      ${facts.map(([label, value]) => `
        <article><span>${label}</span><strong>${escapeHtml(value)}</strong></article>
      `).join("")}
    </div>
    <div class="trade-notes">
      ${batches}
      <article>
        <h3>失效条件</h3>
        <p>${escapeHtml(plan.invalidation)}</p>
      </article>
    </div>
    <p class="trade-method">${escapeHtml(plan.method || "")}</p>
  `;
}

function renderList(selector, items, fallback = "") {
  const values = items.length ? items : (fallback ? [fallback] : []);
  document.querySelector(selector).innerHTML = values.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function setStatus(message, type) {
  statusBox.textContent = message;
  statusBox.className = `status${type ? ` ${type}` : ""}`;
}

function text(selector, value) {
  document.querySelector(selector).textContent = value ?? "";
}

function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  const digits = number > 0 && number < 1 ? 3 : 2;
  return `${number.toFixed(digits)}元`;
}

function formatGap(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  if (Math.abs(number) < 0.05) return "持平";
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}%`;
}

function format(value, unit = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  if (unit === "元" && Math.abs(number) >= 1e8) return `${(number / 1e8).toFixed(2)}亿元`;
  if (unit === "元" && Math.abs(number) >= 1e4) return `${(number / 1e4).toFixed(1)}万元`;
  const digits = Math.abs(number) >= 100 ? 1 : 2;
  return `${number.toFixed(digits)}${unit}`;
}

function signed(value, unit) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}${unit}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const params = new URLSearchParams(location.search);
const initialCode = params.get("code");
const remembered = readRememberedReport();
const openingCode = /^\d{6}$/.test(initialCode || "") ? initialCode : remembered?.stock?.code;
if (remembered && (!initialCode || remembered.stock.code === openingCode)) {
  try {
    codeInput.value = remembered.stock.code;
    renderReport(remembered);
    report.classList.remove("report-pending");
    setStatus(`已恢复 ${remembered.stock.name || remembered.stock.code} 的上次结果，正在更新买入计划…`, "loading");
  } catch {
    localStorage.removeItem(LAST_REPORT_KEY);
  }
}
if (/^\d{6}$/.test(openingCode || "")) {
  codeInput.value = openingCode;
  runCheck(openingCode);
}
loadRanking();
