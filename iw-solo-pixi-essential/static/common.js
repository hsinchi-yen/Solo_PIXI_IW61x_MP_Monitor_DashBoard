const navSections = [
  {
    label: "Overview",
    items: [
      ["overview", "/", "📊", "Dashboard"],
      ["work-orders", "/work-orders.html", "📋", "Work Orders"],
      ["fail-list", "/fail-list.html", "⚠️", "Fail List"],
    ],
  },
  {
    label: "Analysis",
    items: [
      ["ai-report", null, "⬛", "AI Report — Latest WO"],
      ["bt-analysis", "/bt-analysis.html", "📡", "BT Analysis"],
      ["wifi-analysis", "/wifi-analysis.html", "📶", "WiFi Analysis"],
      ["advanced", "/advanced.html", "📈", "Advanced Analytics"],
      ["fail-analysis", "/fail-analysis.html", "🔍", "Fail Analysis"],
    ],
  },
  {
    label: "Management",
    items: [
      ["admin", "/admin.html", "🔧", "DB Tweak"],
      ["alignment", "/alignment.html", "⚖️", "Data Alignment"],
      ["upload", "/upload.html", "⬆️", "Upload"],
    ],
  },
];

const themes = ["graphite", "lab", "ember"];
const themeLabels = { graphite: "Graphite", lab: "Lab", ember: "Ember" };
const state = { charts: {}, failListPage: 1 };

document.addEventListener("DOMContentLoaded", () => {
  setupChrome();
  routePage();
  document.querySelectorAll("[data-refresh]").forEach((button) => button.addEventListener("click", routePage));
});

function setupChrome() {
  const page = document.body.dataset.page || "overview";
  document.querySelector(".sidebar").innerHTML = `
    <div class="logo">
      <div class="brand-mark">
        <div class="brand-square">IW</div>
        <div>
          <div class="org-name">TechNexion · Produxion</div>
          <div class="brand-title">IW Solo PIXI MP</div>
        </div>
      </div>
      <div class="brand-sub">IW61x / IW611 Production QC</div>
    </div>
    <div class="sidebar-header">
      <span class="live-pulse" title="Live"></span>
      <div class="clock" id="clock">--:--:--</div>
      <div class="clock-date" id="clockDate"></div>
    </div>
    <nav class="nav">${navSections.map((section) => renderNavSection(section, page)).join("")}</nav>
    <div class="sidebar-footer">
      <div class="theme-label">🎨 Theme</div>
      <div class="ds-theme-chips" id="themeChips">${themes.map((t) => `<div class="ds-theme-chip" data-theme-val="${t}">${themeLabels[t]}</div>`).join("")}</div>
      <select class="theme-select" id="themeSelect" style="display:none">${themes.map((t) => `<option value="${t}">${themeLabels[t]}</option>`).join("")}</select>
      <span class="badge" id="api-status">API: checking...</span>
      <span class="badge" id="llm-status">LLM: checking...</span>
      <div class="last-refresh-text" id="last-refresh">—</div>
    </div>
  `;
  startClock();
  initThemeChips();
  startApiStatusPoll();
}

function renderNavSection(section, page) {
  const links = section.items
    .map(([key, href, icon, label]) => {
      if (!href) return `<a href="javascript:void(0)" id="nav-${key}" title="AI Report (requires LLM connection)">${icon} ${label}</a>`;
      return `<a class="${key === page ? "active" : ""}" data-page="${key}" href="${href}">${icon} ${label}</a>`;
    })
    .join("");
  return `<div class="section-label">${section.label}</div>${links}`;
}

function startClock() {
  function tick() {
    const now = new Date();
    const clock = document.getElementById("clock");
    const clockDate = document.getElementById("clockDate");
    if (clock) clock.textContent = now.toTimeString().slice(0, 8);
    if (clockDate) clockDate.textContent = now.toLocaleDateString(undefined, { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });
  }
  tick();
  setInterval(tick, 1000);
}

function initThemeChips() {
  const current = localStorage.getItem("iw-theme") || "graphite";
  applyTheme(current, { skipReload: true });
  document.querySelectorAll(".ds-theme-chip").forEach((chip) => chip.addEventListener("click", () => applyTheme(chip.dataset.themeVal)));
  const select = document.getElementById("themeSelect");
  if (select) {
    select.value = current;
    select.addEventListener("change", () => applyTheme(select.value));
  }
}

function applyTheme(name, opts = {}) {
  localStorage.setItem("iw-theme", name);
  document.body.dataset.theme = name;
  document.querySelectorAll(".ds-theme-chip").forEach((chip) => chip.classList.toggle("active", chip.dataset.themeVal === name));
  const select = document.getElementById("themeSelect");
  if (select) select.value = name;
  if (!opts.skipReload) routePage();
}

async function checkApiStatus() {
  const el = document.getElementById("api-status");
  if (!el) return;
  try {
    const res = await fetch("/health");
    el.className = `badge ${res.ok ? "ok" : "error"}`;
    el.textContent = res.ok ? "API: online" : "API: offline";
  } catch (error) {
    el.className = "badge error";
    el.textContent = "API: offline";
  }
}

function startApiStatusPoll() {
  checkApiStatus();
  setInterval(checkApiStatus, 15000);
}

async function routePage() {
  const page = document.body.dataset.page;
  try {
    await loadFilters();
    if (page === "overview") await overview();
    if (page === "work-orders") await workOrders();
    if (page === "fail-list") await failList();
    if (page === "fail-analysis") await failAnalysis();
    if (page === "bt-analysis") await metricPage("/api/bt-analysis");
    if (page === "wifi-analysis") await metricPage("/api/wifi-analysis");
    if (page === "advanced") await advanced();
    if (page === "alignment") await alignmentPage();
    if (page === "upload") uploadPage();
    if (page === "admin") adminPage();
    status("");
    markRefreshed();
  } catch (error) {
    status(error.message || String(error), "error");
  }
}

function markRefreshed() {
  const el = document.getElementById("last-refresh");
  if (el) el.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function loadFilters() {
  const filter = document.getElementById("workOrderFilter");
  if (!filter) return;
  const current = filter.value;
  const data = await getJson("/api/filter-options");
  filter.innerHTML = `<option value="">All work orders</option>${(data.work_orders || []).map((wo) => `<option value="${wo}">${wo}</option>`).join("")}`;
  filter.value = current;
}

async function overview() {
  const wo = workOrderParam();
  const [summary, trend, hourly] = await Promise.all([getJson(`/api/summary${wo}`), getJson(`/api/yield-trend${wo}`), getJson(hourlyThroughputUrl(wo))]);
  document.getElementById("kpis").innerHTML = [
    kpi("Attempts", summary.attempts.total),
    kpi("PASS", summary.attempts.PASS),
    kpi("FAIL", summary.attempts.FAIL),
    kpi("STOP", summary.attempts.STOP),
    kpi("Attempt Yield", `${summary.attempts.yield_pct}%`),
    kpi("Unique Units", summary.data_quality.unique_units_excluding_unknown),
    kpi("Unknown MAC", summary.data_quality.unknown_mac_attempts),
    kpi("Any-Pass Yield", `${summary.unit_yield.any_pass.yield_pct}%`),
    kpi("Current Hour Throughput", hourly.current_hour ? hourly.current_hour.total : 0),
  ].join("");
  renderYieldViews(summary.unit_yield);
  chart("yieldChart", "line", trend.map((r) => r.date), [{ label: "Yield %", data: trend.map((r) => r.yield_pct), borderColor: css("--accent"), backgroundColor: css("--accent") }]);
  renderHourlyThroughput(hourly);
}

function hourlyThroughputUrl(wo) {
  return wo ? `/api/hourly-throughput${wo}&hours=24` : `/api/hourly-throughput?hours=24`;
}

function renderHourlyThroughput(hourly) {
  const meta = document.getElementById("hourlyMeta");
  if (meta) {
    meta.textContent = hourly.current_hour
      ? `rolling ${hourly.hours}h · latest hour ${formatHourLabel(hourly.current_hour.hour)}: ${hourly.current_hour.total} tests`
      : `rolling ${hourly.hours}h · no data yet`;
  }
  chart(
    "hourlyThroughputChart",
    "bar",
    hourly.buckets.map((b) => formatHourLabel(b.hour)),
    [{ label: "Tests/hour", data: hourly.buckets.map((b) => b.total), backgroundColor: css("--accent-2") }]
  );
}

function formatHourLabel(iso) {
  const d = new Date(iso);
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:00`;
}

async function workOrders() {
  const [orders, retries] = await Promise.all([getJson("/api/work-order-summary"), getJson("/api/retries")]);
  renderWorkOrdersTable(orders);
  table("retries", retries, ["work_order", "mac1", "attempt_count", "pass_count", "fail_count", "stop_count", "first_attempt", "last_attempt"]);
}

function renderWorkOrdersTable(rows) {
  const target = document.getElementById("workOrders");
  if (!target) return;
  if (!rows || rows.length === 0) {
    target.innerHTML = `<p class="eyebrow">No data</p>`;
    return;
  }
  target.innerHTML = `<table><thead><tr><th>work_order</th><th>total</th><th>pass</th><th>fail</th><th>stop</th><th>unknown_mac</th><th>AI report</th></tr></thead><tbody>${rows
    .map(
      (r) => `<tr><td>${format(r.work_order)}</td><td>${format(r.total)}</td><td>${format(r.pass)}</td><td>${format(r.fail)}</td><td>${format(r.stop)}</td><td>${format(r.unknown_mac)}</td>
      <td><button class="button ai-btn" title="Requires LLM connection" onclick="openAiSummary('${r.work_order}','zh',${r.yield_pct ?? 0})">中文</button>
      <button class="button ai-btn" title="Requires LLM connection" onclick="openAiSummary('${r.work_order}','en',${r.yield_pct ?? 0})">EN</button></td></tr>`
    )
    .join("")}</tbody></table>`;
}

async function failAnalysis() {
  const wo = workOrderParam();
  const [analysis, list] = await Promise.all([getJson(`/api/fail-analysis${wo}`), getJson(`/api/fail-list${wo}`)]);
  chart("failStepChart", "bar", analysis.fail_steps.map((r) => r.step), [{ label: "Count", data: analysis.fail_steps.map((r) => r.count), backgroundColor: css("--danger") }]);
  chart("categoryChart", "doughnut", analysis.categories.map((r) => r.category), [{ data: analysis.categories.map((r) => r.count), backgroundColor: [css("--danger"), css("--warn"), css("--accent-2")] }]);
  table("failList", list.rows, ["work_order", "result", "mac1", "start_time", "fail_step_num", "fail_step_name", "fail_message", "source_file"]);
}

async function failList() {
  const wo = workOrderParam();
  const sep = wo ? "&" : "?";
  const data = await getJson(`/api/fail-list${wo}${sep}page=${state.failListPage}&page_size=50`);
  if (data.total > 0 && data.rows.length === 0 && state.failListPage > 1) {
    state.failListPage = 1;
    return failList();
  }
  table("failListTable", data.rows, ["work_order", "result", "mac1", "start_time", "fail_step_num", "fail_step_name", "fail_message", "source_file"]);
  const start = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const end = Math.min(data.total, data.page * data.page_size);
  const meta = document.getElementById("failListMeta");
  if (meta) meta.textContent = `Showing ${start}-${end} of ${data.total}`;
  const pager = document.getElementById("failListPager");
  if (pager) {
    pager.innerHTML = `<button class="button" id="failListPrev" ${data.page <= 1 ? "disabled" : ""}>Prev</button><span>Page ${data.page}</span><button class="button" id="failListNext" ${end >= data.total ? "disabled" : ""}>Next</button>`;
    document.getElementById("failListPrev").addEventListener("click", () => {
      state.failListPage = Math.max(1, state.failListPage - 1);
      failList();
    });
    document.getElementById("failListNext").addEventListener("click", () => {
      state.failListPage += 1;
      failList();
    });
  }
}

async function metricPage(endpoint) {
  const data = await getJson(endpoint);
  chart("standardChart", "bar", data.by_standard.map((r) => r.standard), [{ label: "Measurements", data: data.by_standard.map((r) => r.count), backgroundColor: css("--accent-2") }]);
  table("metricTable", data.top_metrics, ["metric_name", "count", "avg", "min", "max"]);
}

async function advanced() {
  const data = await getJson(`/api/advanced${workOrderParam()}`);
  document.getElementById("advancedKpis").innerHTML = [
    kpi("Duration Count", data.duration.count),
    kpi("Avg Sec", data.duration.avg_sec),
    kpi("Min Sec", data.duration.min_sec ?? "-"),
    kpi("Max Sec", data.duration.max_sec ?? "-"),
  ].join("");
  table("advancedRetries", data.retries, ["work_order", "mac1", "attempt_count", "pass_count", "fail_count", "stop_count"]);
}

function uploadPage() {
  const form = document.getElementById("uploadForm");
  if (!form || form.dataset.ready) return;
  form.dataset.ready = "1";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = new FormData(form);
    const response = await fetch("/api/upload/", { method: "POST", body });
    document.getElementById("uploadResult").textContent = JSON.stringify(await response.json(), null, 2);
  });
}

function adminPage() {
  const login = document.getElementById("loginForm");
  if (login && !login.dataset.ready) {
    login.dataset.ready = "1";
    login.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(login).entries());
      const response = await postJson("/api/admin/login", data);
      localStorage.setItem("iw-admin-token", response.token);
      document.getElementById("adminResult").textContent = JSON.stringify(response, null, 2);
    });
  }
}

async function alignmentPage() {
  const form = document.getElementById("alignmentForm");
  if (form && !form.dataset.ready) {
    form.dataset.ready = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());
      const response = await fetch("/api/admin/alignment-targets", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Token": localStorage.getItem("iw-admin-token") || "" },
        body: JSON.stringify({ targets: [{ work_order: data.work_order, target_total: Number(data.target_total) }] }),
      });
      document.getElementById("adminResult").textContent = JSON.stringify(await response.json(), null, 2);
      await loadAlignmentTargets();
    });
  }
  await loadAlignmentTargets();
}

async function loadAlignmentTargets() {
  const data = await getJson("/api/admin/alignment-targets");
  table("alignmentTargets", data.targets, ["work_order", "target_total"]);
}

function renderYieldViews(views) {
  const rows = Object.entries(views).map(([name, row]) => ({ view: name.replace("_", " "), ...row }));
  table("yieldViews", rows, ["view", "total", "PASS", "FAIL", "STOP", "yield_pct"]);
}

function kpi(label, value) {
  return `<div class="kpi"><span>${label}</span><strong>${value}</strong></div>`;
}

function table(targetId, rows, columns) {
  const target = document.getElementById(targetId);
  if (!target) return;
  if (!rows || rows.length === 0) {
    target.innerHTML = `<p class="eyebrow">No data</p>`;
    return;
  }
  target.innerHTML = `<table><thead><tr>${columns.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((c) => `<td>${format(row[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function chart(id, type, labels, datasets) {
  const canvas = document.getElementById(id);
  if (!canvas || !window.Chart) return;
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(canvas, {
    type,
    data: { labels, datasets },
    options: { responsive: true, plugins: { legend: { labels: { color: css("--text") } } }, scales: type === "doughnut" ? {} : { x: { ticks: { color: css("--muted") }, grid: { color: css("--line") } }, y: { ticks: { color: css("--muted") }, grid: { color: css("--line") } } } },
  });
}

function workOrderParam() {
  const filter = document.getElementById("workOrderFilter");
  return filter && filter.value ? `?work_order=${encodeURIComponent(filter.value)}` : "";
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} failed: ${response.status}`);
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error(`${url} failed: ${response.status}`);
  return response.json();
}

function status(text, kind = "ok") {
  const box = document.getElementById("status");
  if (!box) return;
  box.className = `status ${kind}`;
  box.textContent = text;
}

function css(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

function format(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}
