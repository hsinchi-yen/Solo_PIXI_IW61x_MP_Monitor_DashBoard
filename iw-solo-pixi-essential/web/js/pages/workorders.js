// Work orders: one row per work order with the three yields, test runs and retests,
// colour-coded by the shared thresholds.
import { apiGet } from '../core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, retryLevel, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, debounce, tableState } from '../core/ui.js';

const COLS = 14;
let rows = [];
let seq = 0;
let appRef;

const yieldCell = v => `<td class="num"><span class="badge ${yieldLevel(v)}">${fmtPct(v)}</span></td>`;

function render() {
    const q = $('#woSearch').value.trim().toLowerCase();
    const shown = q ? rows.filter(r => (r.work_order || '').toLowerCase().includes(q)) : rows;
    $('#woCount').textContent = t('wo.count', { n: fmtInt(shown.length) });
    const tbody = $('#woRows');
    if (!shown.length) { tableState(tbody, COLS, 'empty'); return; }
    tbody.innerHTML = shown.map(r => {
        const y = r.yield.latest;
        return `<tr>
        <td class="nowrap"><strong>${esc(r.work_order)}</strong></td>
        <td class="nowrap">${esc(fmtDateTime(r.last_test))}</td>
        <td class="num">${fmtInt(r.units)}</td>
        <td class="num good-text">${fmtInt(y.pass)}</td>
        <td class="num bad-text">${fmtInt(y.fail)}</td>
        <td class="num warn-text">${fmtInt(y.stop)}</td>
        ${yieldCell(y.yield_pct)}${yieldCell(r.yield.first_pass.yield_pct)}${yieldCell(r.yield.any_pass.yield_pct)}
        <td class="num">${fmtInt(r.attempts)}</td>
        <td class="num">${r.unknown_mac ? `<span class="warn-text">${fmtInt(r.unknown_mac)}</span>` : '0'}</td>
        <td class="num"><span class="badge ${retryLevel(r.retry_rate)}">${fmtPct(r.retry_rate)}</span></td>
        <td class="num">${fmtNum(r.avg_duration_sec, 1)}</td>
        <td><button class="btn small" type="button" data-report="${esc(r.work_order)}">✨ ${esc(t('wo.report'))}</button></td>
    </tr>`;
    }).join('');
}

async function load(app) {
    const mine = ++seq;
    const tbody = $('#woRows');
    if (!rows.length) tableState(tbody, COLS, 'loading');
    try {
        const data = await apiGet(`/api/work-order-summary${queryString(app.params())}`);
        if (mine !== seq) return;
        rows = data;
        render();
    } catch (err) {
        if (mine !== seq) return;
        rows = [];
        tableState(tbody, COLS, 'error', t('common.loadFailedMsg', { error: err.message }), () => load(app));
    }
}

function init(app) {
    appRef = app;
    $('#woSearch').addEventListener('input', debounce(render, 200));
    $('#woRows').addEventListener('click', e => {
        const btn = e.target.closest('[data-report]');
        if (btn) appRef.openReport(btn.dataset.report);
    });
}

export default { id: 'workorders', scoped: true, init, load };
