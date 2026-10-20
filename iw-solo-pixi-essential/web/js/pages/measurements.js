// Measurements (Q6, Q8): Spec check / BT / Wi-Fi / Other tabs over GET /api/measurements.
// Spec check lists every IW611 test item by how close it is to its limits (Cpk, margin);
// clicking a row draws that item's histogram with its limit lines.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { cpkLevel, esc, fmtDateTime, fmtInt, fmtNum, fmtShortDate } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, $$, debounce, tableState } from '../core/ui.js';

const KEY = ['technology', 'direction', 'standard', 'band', 'frequency_mhz', 'bandwidth', 'rate', 'antenna', 'metric_name'];
let tab = 'spec';
let data = null;
let dataKey = '';
let items = [];
let selected = null;
let seq = 0;
let itemSeq = 0;
let appRef;

const tile = (label, value, cls = '', sub = '') =>
    `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value ${cls}">${esc(value)}</div>${sub ? `<div class="tile-sub">${esc(sub)}</div>` : ''}</div>`;

const STATUS_CLASS = { fail: 'bad', bad: 'bad', warn: 'warn', ok: 'good' };

export function condition(i) {
    return [i.band, i.standard, i.bandwidth, i.rate, i.frequency_mhz ? `${i.frequency_mhz} MHz` : '']
        .filter(Boolean).join(' ');
}

function limitsText(lo, hi, unit) {
    const u = unit ? ` ${unit}` : '';
    if (lo != null && hi != null) return `${fmtNum(lo, 2)} ~ ${fmtNum(hi, 2)}${u}`;
    if (hi != null) return `≤ ${fmtNum(hi, 2)}${u}`;
    if (lo != null) return `≥ ${fmtNum(lo, 2)}${u}`;
    return '—';
}

function statText(m, digits = 2) {
    if (!m) return '—';
    return fmtNum(m.mean, digits);
}

/* ─── Histogram with limit lines ─── */
function limitLines(hist) {
    return {
        id: 'limitLines',
        afterDatasetsDraw(chart) {
            const x = chart.scales.x;
            const { top, bottom } = chart.chartArea;
            const start = parseFloat(hist.labels[0]);
            const ctx = chart.ctx;
            for (const [value, color] of [[hist.limit_low, charts.cssVar('--bad')], [hist.limit_high, charts.cssVar('--bad')]]) {
                if (value == null) continue;
                const f = (value - start) / hist.bin - 0.5;
                if (f < -0.5 || f > hist.labels.length - 0.5) continue;
                const lo = Math.floor(f), hi = Math.ceil(f);
                const pLo = x.getPixelForValue(Math.max(lo, 0)), pHi = x.getPixelForValue(Math.min(hi, hist.labels.length - 1));
                const px = lo === hi ? pLo : pLo + (pHi - pLo) * (f - lo);
                ctx.save();
                ctx.strokeStyle = color;
                ctx.setLineDash([6, 4]);
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(px, top);
                ctx.lineTo(px, bottom);
                ctx.stroke();
                ctx.restore();
            }
        },
    };
}

function histChart(id, hist, color, noteId, title) {
    if (noteId) {
        const notes = [];
        if (hist?.n) notes.push(t('ms.histN', { n: fmtInt(hist.n), min: fmtNum(hist.min, 3), max: fmtNum(hist.max, 3) }));
        if (hist?.limit_low != null || hist?.limit_high != null) notes.push(t('ms.histLimits', { limits: limitsText(hist.limit_low, hist.limit_high, hist.unit) }));
        if (hist?.clipped) notes.push(t('ms.clipped', { n: fmtInt(hist.clipped) }));
        $(noteId).textContent = notes.join(' · ');
    }
    if (!hist || !hist.n) { charts.chartMessage(id, t('common.empty')); return; }
    charts.render(id, c => ({
        type: 'bar',
        data: { labels: hist.labels, datasets: [{ label: title || t('ms.count'), data: hist.counts, backgroundColor: c[color], borderRadius: 3 }] },
        options: { plugins: { legend: { display: false } },
                   scales: { x: { title: { display: !!hist.unit, text: hist.unit || '' } }, y: { beginAtZero: true, ticks: { precision: 0 } } } },
        plugins: [limitLines(hist)],
    }));
}

async function loadHistogram(item, canvas = 'chMsHist', noteId = '#msHistNote', color = 'c1') {
    const params = { ...appRef.params() };
    for (const k of KEY) if (item[k] !== '' && item[k] != null && !(k === 'frequency_mhz' && !item[k])) params[k] = item[k];
    try {
        const hist = await apiGet(`/api/measurements/histogram${queryString(params)}`);
        histChart(canvas, hist, color, noteId, item.metric_name);
    } catch (err) {
        charts.chartMessage(canvas, t('common.loadFailedMsg', { error: err.message }), 'error');
    }
}

/* ─── Spec check ─── */
function renderSpecTiles(status) {
    $('#msSpecTiles').innerHTML = [
        tile(t('ms.st.fail'), fmtInt(status.fail), status.fail ? 'bad-text' : 'good-text', t('ms.st.failSub')),
        tile(t('ms.st.bad'), fmtInt(status.bad), status.bad ? 'bad-text' : '', t('ms.st.badSub')),
        tile(t('ms.st.warn'), fmtInt(status.warn), status.warn ? 'warn-text' : '', t('ms.st.warnSub')),
        tile(t('ms.st.ok'), fmtInt(status.ok), 'good-text', t('ms.st.okSub')),
    ].join('');
}

function renderItems() {
    const tbody = $('#msItemRows');
    $('#msCount').textContent = t('ms.itemCount', { n: fmtInt(items.length) });
    if (!items.length) { tableState(tbody, 8, 'empty'); return; }
    tbody.innerHTML = items.map((i, idx) => `<tr data-idx="${idx}" class="${selected === idx ? 'selected' : ''}" tabindex="0">
        <td><span class="badge ${STATUS_CLASS[i.status]}">${esc(t(`ms.st.${i.status}`))}</span></td>
        <td><strong>${esc(i.metric_name)}</strong><div class="muted small-text">${esc(i.technology)} ${esc(i.direction)}</div></td>
        <td class="small-text nowrap" title="${esc(`${condition(i)} ${i.antenna || ''}`)}">${esc(condition(i))}</td>
        <td class="num">${fmtNum(i.mean, 3)}</td>
        <td class="num nowrap">${esc(limitsText(i.limit_low, i.limit_high, i.unit))}</td>
        <td class="num"><span class="badge ${cpkLevel(i.cpk)}">${i.cpk == null ? '—' : fmtNum(i.cpk, 2)}</span></td>
        <td class="num ${i.margin != null && i.margin < 0 ? 'bad-text' : ''}">${fmtNum(i.margin, 3)}${i.margin_pct != null ? `<div class="muted small-text">${fmtNum(i.margin_pct, 1)}%</div>` : ''}</td>
        <td class="num ${i.n_fail ? 'bad-text' : ''}">${fmtInt(i.n_fail)}<div class="muted small-text">/ ${fmtInt(i.n)}</div></td>
    </tr>`).join('');
}

async function loadItems() {
    const mine = ++itemSeq;
    const params = { ...appRef.params(), limit: 500 };
    const tech = $('#msTech').value;
    const search = $('#msSearch').value.trim();
    if (tech) params.technology = tech;
    if (search) params.search = search;
    if (!items.length) tableState($('#msItemRows'), 8, 'loading');
    try {
        const res = await apiGet(`/api/measurements/items${queryString(params)}`);
        if (mine !== itemSeq) return;
        items = res.items;
        renderSpecTiles(res.status);
        selected = items.length ? 0 : null;
        renderItems();
        if (items.length) selectItem(0);
        else { charts.chartMessage('chMsHist', t('common.empty')); $('#msHistNote').textContent = ''; }
    } catch (err) {
        if (mine !== itemSeq) return;
        tableState($('#msItemRows'), 8, 'error', t('common.loadFailedMsg', { error: err.message }), loadItems);
    }
}

function selectItem(idx) {
    selected = idx;
    $$('#msItemRows tr').forEach(tr => tr.classList.toggle('selected', Number(tr.dataset.idx) === idx));
    const i = items[idx];
    $('#msHistTitle').textContent = `${i.metric_name} — ${condition(i)}`;
    loadHistogram(i);
}

/* ─── BT ─── */
const cellStat = (m, digits = 2, unitless = false) => {
    if (!m) return '<td class="num muted">—</td>';
    const worst = m.limit_high != null ? m.max : m.limit_low != null ? m.min : null;
    return `<td class="num ${m.fails ? 'bad-text' : ''}" title="${esc(`min ${fmtNum(m.min, digits)} · max ${fmtNum(m.max, digits)} · ${limitsText(m.limit_low, m.limit_high, '')}`)}">${fmtNum(m.mean, digits)}${!unitless && worst != null ? `<div class="muted small-text">${t('ms.worst')} ${fmtNum(worst, digits)}</div>` : ''}</td>`;
};

function renderBt(bt) {
    const tx = $('#msBtTxRows');
    if (!bt.tx.length) tableState(tx, 8, 'empty');
    else tx.innerHTML = bt.tx.map(r => `<tr>
        <td><strong>${esc(r.standard)}</strong></td><td class="num">${esc(r.frequency_mhz || '—')}</td>
        ${cellStat(r.power)}${cellStat(r.edr_evm, 3)}${cellStat(r.edr_evm_pk, 3)}${cellStat(r.freq_drift)}${cellStat(r.delta_f1, 1)}
        <td class="num ${r.fails ? 'bad-text' : ''}">${fmtInt(r.fails)}</td></tr>`).join('');
    const rx = $('#msBtRxRows');
    if (!bt.rx.length) tableState(rx, 6, 'empty');
    else rx.innerHTML = bt.rx.map(r => `<tr>
        <td><strong>${esc(r.standard)}</strong></td><td class="num">${esc(r.frequency_mhz || '—')}</td>
        ${cellStat(r.ber, 4)}${cellStat(r.per, 2)}${cellStat(r.rssi, 1, true)}
        <td class="num ${r.fails ? 'bad-text' : ''}">${fmtInt(r.fails)}</td></tr>`).join('');
    loadHistogram({ technology: 'BT', standard: 'BDR', direction: 'TX', metric_name: 'POWER_AVERAGE_DBM' }, 'chMsBdr', '#msBdrNote', 'c1');
    loadHistogram({ technology: 'BT', standard: 'EDR', direction: 'TX', metric_name: 'EDR_EVM_AV' }, 'chMsEdr', '#msEdrNote', 'c2');
}

/* ─── Wi-Fi ─── */
function renderWifi(w) {
    const tx = $('#msWifiTxRows');
    if (!w.tx.length) tableState(tx, 10, 'empty');
    else tx.innerHTML = w.tx.map(r => `<tr>
        <td>${esc(r.band)}</td><td><strong>${esc(r.standard)}</strong></td><td>${esc(r.bandwidth)}</td><td>${esc(r.rate)}</td>
        <td class="num">${statText(r.evm)}</td>
        <td class="num ${r.evm?.fails ? 'bad-text' : ''}">${r.evm ? `${fmtNum(r.evm.max, 2)} <span class="muted small-text">/ ${fmtNum(r.evm.limit_high, 0)}</span>` : '—'}</td>
        ${cellStat(r.power)}${cellStat(r.freq_error, 2, true)}${cellStat(r.lo_leakage, 1)}
        <td class="num ${r.fails ? 'bad-text' : ''}">${fmtInt(r.fails)}</td></tr>`).join('');
    const rows = w.tx.filter(r => r.evm);
    if (!rows.length) charts.chartMessage('chMsEvm', t('common.empty'));
    else charts.render('chMsEvm', c => ({
        type: 'bar',
        data: {
            labels: rows.map(r => `${r.band} ${r.standard} ${r.bandwidth} ${r.rate}`),
            datasets: [
                { label: t('ms.evmMean'), data: rows.map(r => r.evm.mean), backgroundColor: charts.alpha(c.c1, 0.85), borderRadius: 3 },
                { label: t('ms.evmWorst'), data: rows.map(r => r.evm.max), backgroundColor: charts.alpha(c.c2, 0.85), borderRadius: 3 },
                { label: t('ms.evmLimit'), data: rows.map(r => r.evm.limit_high), backgroundColor: charts.alpha(c.bad, 0.35), borderRadius: 3 },
            ],
        },
        options: { indexAxis: 'y', scales: { x: { reverse: true, title: { display: true, text: 'dB' } }, y: { ticks: { autoSkip: false, font: { size: 11 } } } } },
    }));
    const rx = $('#msWifiRxRows');
    if (!w.rx.length) tableState(rx, 7, 'empty');
    else rx.innerHTML = w.rx.map(r => `<tr>
        <td>${esc(r.band)}</td><td><strong>${esc(r.standard)}</strong></td><td>${esc(r.rate)}</td>
        <td class="num">${fmtNum(r.per.mean, 2)}</td><td class="num ${r.per.fails ? 'bad-text' : ''}">${fmtNum(r.per.max, 2)}</td>
        <td class="num">${fmtNum(r.per.limit_high, 1)}</td>
        <td class="num ${r.per.fails ? 'bad-text' : ''}">${fmtInt(r.per.fails)}<span class="muted small-text"> / ${fmtInt(r.per.n)}</span></td></tr>`).join('');
}

/* ─── Other ─── */
function renderOther(o, retries) {
    const all = o.duration.reduce((acc, r) => ({ n: acc.n + r.count }), { n: 0 });
    const pass = o.duration.find(r => r.result === 'PASS');
    $('#msOtherTiles').innerHTML = [
        tile(t('ms.durPass'), pass ? `${fmtNum(pass.p50, 1)} s` : '—', '', pass ? t('ms.durPassSub', { p90: fmtNum(pass.p90, 1) }) : ''),
        tile(t('ms.timedRuns'), fmtInt(all.n)),
        tile(t('ms.retryUnits'), fmtInt(o.retry.units), o.retry.units ? 'warn-text' : ''),
        tile(t('ms.retryHigh'), fmtInt(o.retry.high), o.retry.high ? 'bad-text' : ''),
    ].join('');
    histChart('chMsDuration', o.duration_hist, 'c3', null, t('ms.runs'));
    histChart('chMsXtal', o.xtal_reg, 'c4', '#msXtalNote', 'XTAL_REG');
    const ppm = o.freq_error_trend || [];
    if (!ppm.length) charts.chartMessage('chMsPpm', t('common.empty'));
    else charts.render('chMsPpm', c => ({
        type: 'line',
        data: { labels: ppm.map(p => fmtShortDate(p.day)), datasets: [
            { label: t('ms.ppmAvg'), data: ppm.map(p => p.avg), borderColor: c.c1, backgroundColor: c.c1, tension: 0.2 },
            { label: 'min', data: ppm.map(p => p.min), borderColor: charts.alpha(c.c2, 0.6), borderDash: [4, 4], pointRadius: 0 },
            { label: 'max', data: ppm.map(p => p.max), borderColor: charts.alpha(c.c2, 0.6), borderDash: [4, 4], pointRadius: 0 },
        ] },
        options: { scales: { y: { title: { display: true, text: 'ppm' } } } },
    }));
    const tbody = $('#msRetryRows');
    if (!retries.length) tableState(tbody, 10, 'empty');
    else tbody.innerHTML = retries.map(r => `<tr>
        <td class="nowrap">${esc(r.work_order)}</td><td><code>${esc(r.mac1)}</code></td>
        <td class="num">${fmtInt(r.attempts)}</td><td class="num good-text">${fmtInt(r.pass_count)}</td>
        <td class="num bad-text">${fmtInt(r.fail_count)}</td><td class="num warn-text">${fmtInt(r.stop_count)}</td>
        <td><span class="badge ${String(r.first_result).toLowerCase()}">${esc(r.first_result)}</span></td>
        <td><span class="badge ${String(r.latest_result).toLowerCase()}">${esc(r.latest_result)}</span></td>
        <td><span class="badge ${r.retry_risk === 'high' ? 'bad' : r.retry_risk === 'medium' ? 'warn' : 'good'}">${esc(t(`ms.risk.${r.retry_risk}`))}</span></td>
        <td class="nowrap">${esc(fmtDateTime(r.last_attempt))}</td></tr>`).join('');
}

/* ─── Tabs and loading ─── */
function showTab(name) {
    tab = name;
    $$('[data-ms-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.msTab === name)));
    $$('[data-ms-view]').forEach(v => { v.hidden = v.dataset.msView !== name; });
    renderTab();
}

function renderTab() {
    if (tab === 'spec') { loadItems(); return; }
    if (!data) return;
    if (tab === 'bt') renderBt(data.bt);
    else if (tab === 'wifi') renderWifi(data.wifi);
    else renderOther(data.other, data.top_retries);
}

async function load(app) {
    const key = app.scopeKey();
    const mine = ++seq;
    if (key !== dataKey) { data = null; items = []; }
    try {
        const res = await apiGet(`/api/measurements${queryString(app.params())}`);
        if (mine !== seq) return;
        data = res;
        dataKey = key;
        renderTab();
    } catch (err) {
        if (mine !== seq) return;
        const msg = t('common.loadFailedMsg', { error: err.message });
        ['chMsBdr', 'chMsEdr', 'chMsEvm', 'chMsDuration', 'chMsXtal', 'chMsPpm', 'chMsHist'].forEach(id => charts.chartMessage(id, msg, 'error'));
        tableState($('#msItemRows'), 8, 'error', msg, () => load(app));
    }
}

function init(app) {
    appRef = app;
    $$('[data-ms-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.msTab)));
    $('#msTech').addEventListener('change', loadItems);
    $('#msSearch').addEventListener('input', debounce(loadItems, 300));
    const pick = e => {
        const tr = e.target.closest('tr[data-idx]');
        if (tr) selectItem(Number(tr.dataset.idx));
    };
    $('#msItemRows').addEventListener('click', pick);
    $('#msItemRows').addEventListener('keydown', e => { if (e.key === 'Enter') pick(e); });
}

export default { id: 'measurements', scoped: true, init, load };
