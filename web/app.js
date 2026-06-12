const $ = (id) => document.getElementById(id);

const fields = ["minPrice", "maxPrice", "minMarketCap", "maxMarketCap", "minVolume", "maxVolume", "chiNext", "star", "st", "pattern", "maxInspect", "sortBy", "limitMinDays", "maGapPct", "adjustmentPct"];
const statusEl = $("status");
const rowsEl = $("rows");
const recEl = $("recommendation");
const runBtn = $("runBtn");
const patternHelpBtn = $("patternHelpBtn");
const patternHelpClose = $("patternHelpClose");
const patternHelp = $("patternHelp");
const patternHelpBody = $("patternHelpBody");
const chartModal = $("chartModal");
const chartTitle = $("chartTitle");
const chartLarge = $("chartLarge");
const chartClose = $("chartClose");

let lastResults = [];
const favoriteCodes = new Set(JSON.parse(localStorage.getItem("favoriteCodes") || "[]"));
const excludedCodes = new Set(JSON.parse(localStorage.getItem("excludedCodes") || "[]"));

const patternNotes = [
  {
    title: "量价齐升",
    items: [
      "最后一个交易日收盘价高于前一日收盘价。",
      "最后一个交易日成交量高于前一日 1.08 倍，并高于近 5 日均量 1.05 倍。",
      "MA5 > MA10，MA10 不明显弱于 MA20，且 MA5 较上一日继续上行。",
      "额外加分：股价在 MA5、MA10 上方，且离近 20 日低点不远。",
    ],
  },
  {
    title: "缩量回调",
    items: [
      "近 10 日高点必须出现在此前交易日，避免把当天再拉升的票算进去。",
      "以近 10 日高点为参照，当前回撤在 1.5%-12% 之间。",
      "当前收盘价没有明显跌破 MA20，允许在 MA20 附近小幅波动。",
      "最后一日成交量必须低于前高当天成交量的 68%。",
      "回调段平均成交量必须低于上涨段平均成交量的 78%。",
      "最后一日成交量还要低于近 5 日均量的 88%，避免回调末端已经重新放量。",
      "MA10 仍在 MA20 上方，表示中短期趋势没有明显走坏。",
      "近 3 日至少出现过回调或横盘整理。",
      "当天涨幅不超过 2.5%，且价格不能已经重新贴近近 10 日高点。",
    ],
  },
  {
    title: "放量突破",
    items: [
      "最后一个交易日收盘价突破此前近 20 日高点。",
      "最后一个交易日成交量高于近 5 日均量 1.25 倍。",
      "额外加分：股价在 MA5、MA10 上方，且止损空间相对可控。",
    ],
  },
  {
    title: "放量后缩量回踩MA10",
    items: [
      "近 30 日内先找最近 2-6 日前出现的短线高点。",
      "高点前 4 日左右必须有放量拉升：最大成交量至少是此前均量的 1.6 倍，且价格涨幅超过 7%。",
      "高点后进入 2-6 日回踩段，回踩深度在 2%-14% 之间。",
      "回踩段平均成交量明显低于拉升段，最后一日成交量低于拉升段最大量的 62%。",
      "最近几日量能要呈逐步缩减状态。",
      "最后一日价格回踩到 MA10 附近，且收盘没有有效跌破 MA10。",
      "当前不能已经重新拉回前高附近，避免选到回踩结束后已拉升的票。",
    ],
  },
  {
    title: "涨停后横盘整理",
    items: [
      "近 25 日内先找到一次涨停或接近涨停：涨幅大于 9.5%，且收盘接近最高价。",
      "涨停后至少小幅调整 5 个交易日，最多观察 18 个交易日。",
      "整理期间不能明显破位，收盘需基本守在涨停日收盘价上方。",
      "当前价格仍围绕涨停日收盘价附近运行，不能涨太远也不能跌太深。",
      "MA5 与 MA10 要靠近，短期均线进入收敛状态。",
      "整理区间要足够窄：最高价不能超过涨停日收盘价太多，最低价不能明显跌回去。",
      "整理段整体斜率要偏平，不能已经二次趋势拉升。",
      "涨停后不能再出现大阳线或二次突破。",
      "当前价格保持在 MA5、MA10 附近或上方。",
      "整理阶段量能不能异常失控放大。",
      "当前不能已经明显脱离整理区，避免追到二次拉升后段。",
    ],
  },
  {
    title: "周线三底5周线承压",
    items: [
      "把最近日K聚合成周K，在近14周内寻找三个周线低点。",
      "最近一个周线低点不能是最低点，要求底部有抬高迹象。",
      "三个低点要处在相对接近的底部区间，避免形态过于发散。",
      "当前周K需要触碰或贯穿5周线。",
      "当前周K在5周线附近承压，不能已经明显远离5周线。",
      "当前价格仍要站在周线底部结构上方。",
    ],
  },
  {
    title: "不限形态",
    items: [
      "不做形态限制，只按价格、板块、ST 状态等基础条件筛选。",
      "排序仍会参考流动性、资金流向、均线位置和止损空间。",
    ],
  },
];

function fmtNumber(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "-";
  return Number(n).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function pct(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "-";
  return `${Number(n).toFixed(2)}%`;
}

function money(n) {
  if (!n) return "-";
  if (n >= 100000000) return `${(n / 100000000).toFixed(2)} 亿`;
  if (n >= 10000) return `${(n / 10000).toFixed(2)} 万`;
  return String(Math.round(n));
}

function marketCapText(n) {
  if (!n) return "市值 -";
  return `市值 ${(n / 100000000).toFixed(0)} 亿`;
}

function volumeText(n) {
  if (!n) return "成交量 -";
  return `量 ${(n / 10000).toFixed(1)} 万手`;
}

function classBySign(n) {
  return Number(n) >= 0 ? "positive" : "negative";
}

function getParams() {
  const params = new URLSearchParams();
  fields.forEach((id) => params.set(id, $(id).value));
  if ($("forceRefresh").checked) params.set("forceRefresh", "true");
  return params;
}

function saveLists() {
  localStorage.setItem("favoriteCodes", JSON.stringify([...favoriteCodes]));
  localStorage.setItem("excludedCodes", JSON.stringify([...excludedCodes]));
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;",
  }[char]));
}

function movingAverage(data, days) {
  return data.map((_, index) => {
    if (index + 1 < days) return null;
    const slice = data.slice(index + 1 - days, index + 1);
    return slice.reduce((sum, x) => sum + x.close, 0) / days;
  });
}

function linePath(values, xFor, yFor) {
  return values.map((value, index) => {
    if (value === null) return "";
    return `${index === values.findIndex((x) => x !== null) ? "M" : "L"} ${xFor(index)} ${yFor(value)}`;
  }).filter(Boolean).join(" ");
}

function renderKlineSvg(data, options = {}) {
  const width = options.width || 170;
  const height = options.height || 92;
  const large = Boolean(options.large);
  const markers = options.markers || [];
  const candles = (data || []).slice(-30);
  if (!candles.length) return `<div class="chart-empty">无K线</div>`;

  const top = large ? 18 : 8;
  const priceHeight = large ? height * 0.62 : height * 0.58;
  const volumeTop = top + priceHeight + (large ? 22 : 10);
  const volumeHeight = height - volumeTop - (large ? 24 : 8);
  const left = large ? 42 : 6;
  const right = large ? 16 : 6;
  const innerWidth = width - left - right;
  const slot = innerWidth / candles.length;
  const bodyWidth = Math.max(2, Math.min(large ? 10 : 5, slot * 0.58));
  const maxPrice = Math.max(...candles.map((x) => x.high));
  const minPrice = Math.min(...candles.map((x) => x.low));
  const maxVolume = Math.max(...candles.map((x) => x.volume));
  const priceRange = Math.max(maxPrice - minPrice, 0.01);
  const xFor = (index) => left + slot * index + slot / 2;
  const yFor = (price) => top + ((maxPrice - price) / priceRange) * priceHeight;
  const vFor = (volume) => volumeTop + volumeHeight - (volume / Math.max(maxVolume, 1)) * volumeHeight;
  const ma5 = movingAverage(candles, 5);
  const ma10 = movingAverage(candles, 10);
  const grid = large ? `
    <text x="6" y="${top + 4}" class="chart-axis">${fmtNumber(maxPrice)}</text>
    <text x="6" y="${top + priceHeight}" class="chart-axis">${fmtNumber(minPrice)}</text>
    <line x1="${left}" y1="${top}" x2="${width - right}" y2="${top}" class="chart-grid"/>
    <line x1="${left}" y1="${top + priceHeight}" x2="${width - right}" y2="${top + priceHeight}" class="chart-grid"/>
    <text x="${left}" y="${height - 4}" class="chart-axis">${candles[0].date}</text>
    <text x="${width - right - 74}" y="${height - 4}" class="chart-axis">${candles.at(-1).date}</text>
  ` : "";

  const candleSvg = candles.map((day, index) => {
    const x = xFor(index);
    const up = day.close >= day.open;
    const color = up ? "#d5392f" : "#16824a";
    const yOpen = yFor(day.open);
    const yClose = yFor(day.close);
    const bodyY = Math.min(yOpen, yClose);
    const bodyH = Math.max(1, Math.abs(yOpen - yClose));
    const volumeY = vFor(day.volume);
    return `
      <line x1="${x}" y1="${yFor(day.high)}" x2="${x}" y2="${yFor(day.low)}" stroke="${color}" stroke-width="${large ? 1.4 : 1}"/>
      <rect x="${x - bodyWidth / 2}" y="${bodyY}" width="${bodyWidth}" height="${bodyH}" fill="${up ? "none" : color}" stroke="${color}" stroke-width="${large ? 1.4 : 1}"/>
      <rect x="${x - bodyWidth / 2}" y="${volumeY}" width="${bodyWidth}" height="${volumeTop + volumeHeight - volumeY}" fill="${color}" opacity="0.72"/>
    `;
  }).join("");
  const markerSvg = markers.map((marker) => {
    const index = candles.findIndex((x) => x.date === marker.date);
    if (index < 0) return "";
    const x = xFor(index);
    return `
      <line x1="${x}" y1="${top}" x2="${x}" y2="${volumeTop + volumeHeight}" class="chart-marker"/>
      ${large ? `<text x="${x + 4}" y="${top + 14}" class="chart-marker-label">${escapeHtml(marker.label || "标记")}</text>` : ""}
    `;
  }).join("");

  return `
    <svg viewBox="0 0 ${width} ${height}" class="kline-svg" role="img" aria-label="最近30天K线">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#fff"/>
      ${grid}
      <path d="${linePath(ma5, xFor, yFor)}" class="ma-line ma5"/>
      <path d="${linePath(ma10, xFor, yFor)}" class="ma-line ma10"/>
      ${markerSvg}
      ${candleSvg}
      ${large ? `<text x="${left}" y="13" class="chart-axis">MA5</text><text x="${left + 36}" y="13" class="chart-axis ma10-label">MA10</text>` : ""}
    </svg>
  `;
}

function renderPatternHelp() {
  patternHelpBody.innerHTML = patternNotes.map((group) => `
    <article class="help-item">
      <h3>${group.title}</h3>
      <ul>${group.items.map((item) => `<li>${item}</li>`).join("")}</ul>
    </article>
  `).join("");
}

function renderRecommendation(stock) {
  if (!stock) {
    recEl.classList.add("hidden");
    recEl.innerHTML = "";
    return;
  }

  recEl.classList.remove("hidden");
  recEl.innerHTML = `
    <div class="rec-head">
      <div>
        <div class="rec-title">${escapeHtml(stock.name)} <span class="stock-code">${stock.code}</span></div>
        <p>系统推荐为当前筛选结果中综合评分最高的研究候选，最近交易日：${stock.lastDate || "-"}</p>
      </div>
      <span class="badge">评分 ${fmtNumber(stock.score, 1)}</span>
    </div>
    <div class="plan-grid">
      <div class="metric"><span>现价</span><strong>${fmtNumber(stock.tradePlan.close)}</strong></div>
      <div class="metric"><span>参考买入</span><strong>${fmtNumber(stock.tradePlan.buyPrice)}</strong></div>
      <div class="metric"><span>止损</span><strong>${fmtNumber(stock.tradePlan.stopLoss)}</strong></div>
      <div class="metric"><span>止盈</span><strong>${fmtNumber(stock.tradePlan.takeProfit)}</strong></div>
    </div>
    <ol class="reasons">
      ${stock.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}
      <li>成交额 ${money(stock.amount)}，换手率 ${pct(stock.turnover)}，流动性优先级较高。</li>
    </ol>
    <div class="disclaimer">仅用于技术面研究和复盘，不构成投资建议；真实交易还需要结合基本面、公告、行业、仓位和个人风险承受能力。</div>
  `;
}

function openChart(index) {
  const stock = lastResults[index];
  if (!stock) return;
  chartTitle.textContent = `${stock.name} ${stock.code} 最近 30 天 K 线`;
  chartLarge.innerHTML = renderKlineSvg(stock.chartData, { width: 860, height: 430, large: true, markers: stock.chartMarkers || [] });
  chartModal.classList.remove("hidden");
}

function detailsText(stock) {
  const d = stock.details || {};
  const items = [];
  if (d.limitDate) items.push(`涨停日 ${d.limitDate}`);
  if (d.daysAfterLimit !== undefined) items.push(`整理 ${d.daysAfterLimit} 天`);
  if (d.maGapPct !== undefined) items.push(`MA距 ${d.maGapPct}%`);
  if (d.adjustmentFromLimitPct !== undefined && d.adjustmentFromLimitPct !== null) items.push(`偏离 ${d.adjustmentFromLimitPct}%`);
  if (d.rangePct !== undefined && d.rangePct !== null) items.push(`区间 ${d.rangePct}%`);
  if (d.weeklyLow1) items.push(`周底 ${d.weeklyLow1}/${d.weeklyLow2}/${d.weeklyLow3}`);
  if (d.weeklyMa5) items.push(`5周线 ${d.weeklyMa5}`);
  if (d.nearestLowLiftPct !== undefined && d.nearestLowLiftPct !== null) items.push(`近底抬高 ${d.nearestLowLiftPct}%`);
  return items.length ? items.join(" / ") : stock.notes.slice(0, 2).join(" / ");
}

function renderRows(results) {
  lastResults = results.filter((stock) => !excludedCodes.has(stock.code));
  if (!lastResults.length) {
    rowsEl.innerHTML = `<tr><td colspan="10" class="empty">没有找到符合条件的股票，可以放宽价格、板块或形态条件</td></tr>`;
    return;
  }

  rowsEl.innerHTML = lastResults.map((stock, index) => `
    <tr class="${favoriteCodes.has(stock.code) ? "fav-row" : ""}">
      <td>
        <span class="stock-name">${escapeHtml(stock.name)}</span>
        <span class="stock-code">${stock.code}${favoriteCodes.has(stock.code) ? " · 已收藏" : ""}${stock.isChiNext ? " · 创业板" : ""}${stock.isStar ? " · 科创板" : ""}${stock.isST ? " · ST" : ""}</span>
      </td>
      <td>${fmtNumber(stock.price)}</td>
      <td class="${classBySign(stock.changePct)}">${pct(stock.changePct)}</td>
      <td>${pct(stock.turnover)}<span class="small">${volumeText(stock.volume)}</span><span class="small">成交额 ${money(stock.amount)}</span><span class="small">${marketCapText(stock.marketCap)}</span></td>
      <td>
        <button class="chart-thumb" type="button" data-chart-index="${index}" aria-label="放大查看 ${escapeHtml(stock.name)} 最近30天K线">
          ${renderKlineSvg(stock.chartData, { markers: stock.chartMarkers || [] })}
        </button>
      </td>
      <td>${fmtNumber(stock.score, 1)}<span class="small">${stock.notes.slice(0, 2).map(escapeHtml).join("；")}</span></td>
      <td><span class="small detail-text">${escapeHtml(detailsText(stock))}</span></td>
      <td>MA5 ${fmtNumber(stock.ma5)}<span class="small">MA10 ${fmtNumber(stock.ma10)} / MA20 ${fmtNumber(stock.ma20)}</span></td>
      <td>买 ${fmtNumber(stock.tradePlan.buyPrice)}<span class="small">损 ${fmtNumber(stock.tradePlan.stopLoss)} / 盈 ${fmtNumber(stock.tradePlan.takeProfit)}</span></td>
      <td>
        <button class="mini-btn" type="button" data-fav-code="${stock.code}">${favoriteCodes.has(stock.code) ? "取消收藏" : "收藏"}</button>
        <button class="mini-btn danger" type="button" data-exclude-code="${stock.code}">排除</button>
      </td>
    </tr>
  `).join("");
}

async function runScreen() {
  runBtn.disabled = true;
  statusEl.textContent = "正在拉取行情并分析 K 线，首次运行可能需要几十秒";
  rowsEl.innerHTML = `<tr><td colspan="10" class="empty">分析中...</td></tr>`;
  recEl.classList.add("hidden");

  try {
    const resp = await fetch(`/api/screen?${getParams().toString()}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "筛选失败");
    const when = new Date(data.updatedAt).toLocaleString("zh-CN");
    const sourceText = data.source === "sina" ? "，已切换新浪备用源" : data.source === "tencent" ? "，已切换腾讯备用源" : "";
    statusEl.textContent = `完成：全市场 ${data.totalQuotes} 只，进入 K 线分析 ${data.inspected} 只，命中 ${data.results.length} 只。数据更新时间 ${when}${sourceText}`;
    renderRecommendation(data.recommendation);
    renderRows(data.results);
  } catch (err) {
    statusEl.textContent = `失败：${err.message}`;
    rowsEl.innerHTML = `<tr><td colspan="10" class="empty">行情接口暂时不可用，稍后重试</td></tr>`;
  } finally {
    runBtn.disabled = false;
  }
}

renderPatternHelp();
runBtn.addEventListener("click", runScreen);
patternHelpBtn.addEventListener("click", () => patternHelp.classList.toggle("hidden"));
patternHelpClose.addEventListener("click", () => patternHelp.classList.add("hidden"));
rowsEl.addEventListener("click", (event) => {
  const favButton = event.target.closest("[data-fav-code]");
  if (favButton) {
    const code = favButton.dataset.favCode;
    if (favoriteCodes.has(code)) favoriteCodes.delete(code);
    else favoriteCodes.add(code);
    saveLists();
    renderRows(lastResults);
    return;
  }
  const excludeButton = event.target.closest("[data-exclude-code]");
  if (excludeButton) {
    excludedCodes.add(excludeButton.dataset.excludeCode);
    saveLists();
    renderRows(lastResults);
    return;
  }
  const button = event.target.closest("[data-chart-index]");
  if (!button) return;
  openChart(Number(button.dataset.chartIndex));
});
chartClose.addEventListener("click", () => chartModal.classList.add("hidden"));
chartModal.addEventListener("click", (event) => {
  if (event.target === chartModal) chartModal.classList.add("hidden");
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") chartModal.classList.add("hidden");
});
