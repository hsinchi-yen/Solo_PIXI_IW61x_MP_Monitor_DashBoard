// Fail analysis: failing steps (runs, units, units that passed on a retest), the failure
// categories, and the step / message detail table.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { esc, fmtInt } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, tableState } from '../core/ui.js';

let seq = 0;

async function load(app) {
    const mine = ++seq;
    const tbody = $('#faRows');
    try {
        const data = await apiGet(`/api/fail-analysis${queryString(app.params())}`);
        if (mine !== seq) return;
        const top = data.top_steps;
        if (!top.length) charts.chartMessage('chFaSteps', t('ov.noFails'));
        else charts.render('chFaSteps', c => ({
            type: 'bar',
            data: {
                labels: top.map(s => s.step),
                datasets: [
                    { label: t('ov.stepRuns'), data: top.map(s => s.attempts), backgroundColor: c.fail, borderRadius: 3 },
                    { label: t('ov.stepUnits'), data: top.map(s => s.units), backgroundColor: c.stop, borderRadius: 3 },
                    { label: t('ov.stepRecovered'), data: top.map(s => s.recovered_units), backgroundColor: c.pass, borderRadius: 3 },
                ],
            },
            options: { indexAxis: 'y', scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { ticks: { autoSkip: false } } } },
        }));
        const cats = data.categories;
        if (!cats.length) charts.chartMessage('chFaCategories', t('ov.noFails'));
        else charts.render('chFaCategories', c => ({
            type: 'bar',
            data: {
                labels: cats.map(r => r.category),
                datasets: [{ label: t('ov.stepRuns'), data: cats.map(r => r.count),
                             backgroundColor: cats.map(r => (r.category === 'STOP' ? c.stop : c.fail)), borderRadius: 4 }],
            },
            options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } },
        }));
        if (!data.steps.length) tableState(tbody, 5, 'empty');
        else tbody.innerHTML = data.steps.map(r => `<tr>
            <td>${esc(r.fail_step_num != null ? `${r.fail_step_num}. ${r.step}` : r.step)}</td>
            <td><span class="badge ${r.result === 'FAIL' ? 'fail' : 'stop'}">${esc(r.result)}</span></td>
            <td>${esc(r.category || '—')}</td>
            <td class="truncate" title="${esc(r.message || '')}">${esc(r.message || '—')}</td>
            <td class="num">${fmtInt(r.count)}</td>
        </tr>`).join('');
    } catch (err) {
        if (mine !== seq) return;
        const msg = t('common.loadFailedMsg', { error: err.message });
        charts.chartMessage('chFaSteps', msg, 'error');
        charts.chartMessage('chFaCategories', msg, 'error');
        tableState(tbody, 5, 'error', msg, () => load(app));
    }
}

export default { id: 'failanalysis', scoped: true, load };
