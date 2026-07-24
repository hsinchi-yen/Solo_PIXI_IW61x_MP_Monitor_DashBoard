window.llmConnected = false;

let modalsReady = false;
const termState = { text: "", charIndex: 0, timer: null, typing: false, queue: [], queueIndex: 0, advanceTimer: null };

document.addEventListener("DOMContentLoaded", () => {
  ensureModals();
  checkLlmStatus();
  setInterval(checkLlmStatus, 30000);
  const navLink = document.getElementById("nav-ai-report");
  if (navLink) navLink.addEventListener("click", openLatestWoTerminalReport);
});

async function checkLlmStatus() {
  try {
    const res = await fetch("/api/llm-status");
    const data = await res.json();
    window.llmConnected = !!(data && data.connected);
  } catch (error) {
    window.llmConnected = false;
  }
  const el = document.getElementById("llm-status");
  if (el) {
    el.className = `badge ${window.llmConnected ? "ok" : "error"}`;
    el.textContent = window.llmConnected ? "LLM: ready" : "LLM: disconnected";
  }
  document.body.classList.toggle("llm-disconnected", !window.llmConnected);
}

async function fetchWithTimeout(url, timeoutMs = 180000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

function ensureModals() {
  if (modalsReady) return;
  modalsReady = true;
  document.body.insertAdjacentHTML(
    "beforeend",
    `
    <div class="terminal-overlay" id="terminal-modal">
      <div class="terminal-window">
        <div class="terminal-titlebar" id="term-titlebar">
          <span class="term-title">⬛ AI QUALITY REPORT</span>
          <span class="term-sep">|</span>
          <span id="term-wo-label">WO: —</span>
          <span class="term-sep">|</span>
          <span id="term-alert-label" class="term-alert-normal">NORMAL</span>
          <span class="term-hint">[ Click or press any key to skip ]</span>
        </div>
        <div class="terminal-body" id="term-body">
          <div id="term-content"></div><span class="term-cursor" id="term-cursor"></span>
        </div>
        <div class="terminal-footer">
          <span id="term-status">● IDLE</span>
          <span id="term-countdown"></span>
        </div>
      </div>
    </div>
    <div class="ai-modal-overlay" id="ai-modal">
      <div class="ai-modal-content">
        <div class="ai-modal-header">
          <div class="ai-modal-title">✨ AI Report
            <span id="ai-modal-wo" style="color:var(--accent);font-size:13px;font-weight:normal;font-family:monospace;margin-left:8px;"></span>
            <span id="ai-modal-alert" class="badge" style="width:auto;display:inline-block;margin-left:8px;"></span>
            <span id="ai-modal-lang" style="margin-left:6px;font-size:11px;color:var(--muted);"></span>
          </div>
          <button class="ai-modal-close" id="ai-modal-close-btn">&times;</button>
        </div>
        <div class="ai-modal-body" id="ai-modal-body">
          <div id="ai-loading" style="display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 0;gap:12px;">
            <div style="width:30px;height:30px;border:3px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite;"></div>
            <div style="color:var(--muted);font-size:13px;">Generating AI report...</div>
          </div>
          <div id="ai-result" class="markdown-body" style="display:none;"></div>
        </div>
      </div>
    </div>
  `
  );
  document.getElementById("term-titlebar").addEventListener("click", handleTerminalSkip);
  document.getElementById("term-body").addEventListener("click", handleTerminalSkip);
  document.getElementById("terminal-modal").addEventListener("click", (event) => {
    if (event.target.id === "terminal-modal") closeTerminalModal();
  });
  document.addEventListener("keydown", () => {
    const modal = document.getElementById("terminal-modal");
    if (modal && modal.classList.contains("open")) handleTerminalSkip();
  });
  document.getElementById("ai-modal-close-btn").addEventListener("click", closeAiModal);
  document.getElementById("ai-modal").addEventListener("click", (event) => {
    if (event.target.id === "ai-modal") closeAiModal();
  });
}

async function openLatestWoTerminalReport() {
  if (!window.llmConnected) return;
  ensureModals();
  const modal = document.getElementById("terminal-modal");
  document.getElementById("term-wo-label").textContent = "WO: —";
  document.getElementById("term-alert-label").textContent = "NORMAL";
  document.getElementById("term-alert-label").className = "term-alert-normal";
  document.getElementById("term-content").innerHTML = "";
  document.getElementById("term-status").textContent = "● CONNECTING...";
  modal.classList.add("open");
  try {
    const orders = await getJson("/api/work-order-summary");
    const latest = orders[0];
    if (!latest) {
      document.getElementById("term-content").textContent = "No work order data available.";
      document.getElementById("term-status").textContent = "● NO DATA";
      return;
    }
    document.getElementById("term-wo-label").textContent = `WO: ${latest.work_order}`;
    const [resEn, resZh] = await Promise.all([
      fetchWithTimeout(`/api/workorders/${encodeURIComponent(latest.work_order)}/ai-summary?lang=en&mode=carousel`),
      fetchWithTimeout(`/api/workorders/${encodeURIComponent(latest.work_order)}/ai-summary?lang=zh&mode=carousel`),
    ]);
    const dataEn = await resEn.json();
    const dataZh = await resZh.json();
    if (!resEn.ok || !dataEn.summary) throw new Error(dataEn.detail || "AI summary failed");
    termState.queue = [{ text: dataEn.summary }, { text: resZh.ok && dataZh.summary ? dataZh.summary : dataEn.summary }];
    termState.queueIndex = 0;
    startTerminalTypewriter(termState.queue[0].text);
  } catch (error) {
    document.getElementById("term-status").textContent = "● ERROR";
    document.getElementById("term-content").textContent = `Failed to generate report: ${error.message || error}`;
  }
}

function startTerminalTypewriter(text) {
  clearInterval(termState.timer);
  clearTimeout(termState.advanceTimer);
  termState.text = text;
  termState.charIndex = 0;
  termState.typing = true;
  renderTermContent("");
  document.getElementById("term-status").textContent = "● GENERATING...";
  termState.timer = setInterval(() => {
    termState.charIndex += 3;
    renderTermContent(text.slice(0, termState.charIndex));
    if (termState.charIndex >= text.length) {
      clearInterval(termState.timer);
      termState.typing = false;
      document.getElementById("term-status").textContent = "● DONE";
      scheduleAdvance();
    }
  }, 16);
}

function completeTermTyping() {
  if (!termState.typing) return;
  clearInterval(termState.timer);
  termState.typing = false;
  renderTermContent(termState.text);
  document.getElementById("term-status").textContent = "● DONE";
  scheduleAdvance();
}

function scheduleAdvance() {
  clearTimeout(termState.advanceTimer);
  termState.advanceTimer = setTimeout(advanceTerminal, 6000);
}

function advanceTerminal() {
  termState.queueIndex += 1;
  if (termState.queueIndex < termState.queue.length) {
    startTerminalTypewriter(termState.queue[termState.queueIndex].text);
  } else {
    closeTerminalModal();
  }
}

function handleTerminalSkip() {
  if (termState.typing) {
    completeTermTyping();
    return;
  }
  clearTimeout(termState.advanceTimer);
  advanceTerminal();
}

function closeTerminalModal() {
  const modal = document.getElementById("terminal-modal");
  if (modal) modal.classList.remove("open");
  clearInterval(termState.timer);
  clearTimeout(termState.advanceTimer);
  termState.typing = false;
}

function renderTermContent(text) {
  const escaped = escapeHtml(text);
  const withTags = escaped
    .replace(/&lt;num&gt;([\s\S]*?)&lt;\/num&gt;/g, '<span class="term-hl-num">$1</span>')
    .replace(/&lt;ok&gt;([\s\S]*?)&lt;\/ok&gt;/g, '<span class="term-hl-ok">$1</span>')
    .replace(/&lt;err&gt;([\s\S]*?)&lt;\/err&gt;/g, '<span class="term-hl-err">$1</span>')
    .replace(/&lt;warn&gt;([\s\S]*?)&lt;\/warn&gt;/g, '<span class="term-hl-warn">$1</span>');
  document.getElementById("term-content").innerHTML = withTags;
}

async function openAiSummary(wo, lang, yieldPct) {
  ensureModals();
  const modal = document.getElementById("ai-modal");
  document.getElementById("ai-modal-wo").textContent = wo;
  document.getElementById("ai-modal-lang").textContent = String(lang).toUpperCase();
  setAlertBadge(document.getElementById("ai-modal-alert"), yieldPct);
  document.getElementById("ai-loading").style.display = "flex";
  document.getElementById("ai-result").style.display = "none";
  modal.classList.add("open");
  try {
    const data = await getJson(`/api/workorders/${encodeURIComponent(wo)}/ai-summary?lang=${encodeURIComponent(lang)}&mode=normal`);
    document.getElementById("ai-result").innerHTML = renderMarkdownLite(data.summary || "");
  } catch (error) {
    document.getElementById("ai-result").innerHTML = `<p>Failed to load AI report: ${escapeHtml(error.message || String(error))}</p>`;
  } finally {
    document.getElementById("ai-loading").style.display = "none";
    document.getElementById("ai-result").style.display = "block";
  }
}
window.openAiSummary = openAiSummary;

function closeAiModal() {
  const modal = document.getElementById("ai-modal");
  if (modal) modal.classList.remove("open");
}
window.closeAiModal = closeAiModal;

function setAlertBadge(el, yieldPct) {
  if (!el) return;
  const pct = Number(yieldPct) || 0;
  let cls = "ok";
  let text = "NORMAL";
  if (pct < 98.5) {
    cls = "error";
    text = "ALARM";
  } else if (pct < 99.2) {
    cls = "pending";
    text = "WARNING";
  }
  el.className = `badge ${cls}`;
  el.textContent = text;
  el.style.width = "auto";
  el.style.display = "inline-block";
}

function renderMarkdownLite(text) {
  const lines = escapeHtml(text).split("\n");
  let html = "";
  let listType = null;
  const closeList = () => {
    if (listType) {
      html += `</${listType}>`;
      listType = null;
    }
  };
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      closeList();
      return;
    }
    const inline = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    if (/^### /.test(inline)) {
      closeList();
      html += `<h4>${inline.slice(4)}</h4>`;
      return;
    }
    if (/^## /.test(inline)) {
      closeList();
      html += `<h3>${inline.slice(3)}</h3>`;
      return;
    }
    if (/^# /.test(inline)) {
      closeList();
      html += `<h2>${inline.slice(2)}</h2>`;
      return;
    }
    const ulMatch = /^[-*]\s+(.*)$/.exec(inline);
    if (ulMatch) {
      if (listType !== "ul") {
        closeList();
        html += "<ul>";
        listType = "ul";
      }
      html += `<li>${ulMatch[1]}</li>`;
      return;
    }
    const olMatch = /^\d+\.\s+(.*)$/.exec(inline);
    if (olMatch) {
      if (listType !== "ol") {
        closeList();
        html += "<ol>";
        listType = "ol";
      }
      html += `<li>${olMatch[1]}</li>`;
      return;
    }
    closeList();
    html += `<p>${inline}</p>`;
  });
  closeList();
  return html;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
