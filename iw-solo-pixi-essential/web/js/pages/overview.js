// Dashboard: the latest-result yield first, first-pass / any-pass beside it (Q1), units and
// test runs kept apart, and four charts (Q2), from GET /api/overview.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { fmtInt, fmtPct, fmtShortDate, fmtTime, retryLevel, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $ } from '../core/ui.js';

let seq = 0;

function setNumber(id, text, level) {
    const el = $(id);
    el.textContent = text;
    el.classList.remove('v-good', 'v-warn', 'v-bad');
    if (level) el.classList.add(`v-${level}`);
}

function renderNumbers(s, aligned, spec) {
    const latest = s.yield.latest;
    const hero = $('#ovYield');
    hero.innerHTML = '';
    hero.classList.remove('v-good', 'v-warn', 'v-bad');
    if (s.units) {
        hero.append(Number(latest.yield_pct).toFixed(2));
        const small = document.createElement('small');
        small.textContent = '%';
        hero.append(small);
        hero.classList.add(`v-${yieldLevel(latest.yield_pct)}`);
    } else {
        hero.textContent = '—';
    }

    const views = $('#ovViews');
    views.innerHTML = '';
    for (const view of ['first_pass', 'any_pass']) {
        const span = document.createElement('span');
        const b = document.createElement('b');
        b.textContent = s.units ? fmtPct(s.yield[view].yield_pct) : '—';
        if (s.units) b.className = `v-${yieldLevel(s.yield[view].yield_pct)}`;
        span.append(t(`ov.view.${view}`), ' ', b);
        views.appendChild(span);
    }

    const al = $('#ovAligned');
    al.innerHTML = '';
    if (aligned.has_target && aligned.yield_pct != null) {
        const b = document.createElement('b');
        b.textContent = fmtPct(aligned.yield_pct);
        b.className = `v-${yieldLevel(aligned.yield_pct)}`;
        al.append(t('ov.aligned'), ' ', b);
        if (aligned.gap > 0) {
            const gap = document.createElement('span');
            gap.className = 'gap';
            gap.textContent = t('ov.alignedGap', { gap: fmtInt(aligned.gap) });
            al.append(gap);
        }
    } else {
        al.textContent = t('ov.alignedNoTarget');
    }

    const a = s.attempts;
    setNumber('#ovUnits', fmtInt(s.units));
    $('#ovUnitsSub').textContent = a.total ? t('ov.unitsSub', { attempts: fmtInt(a.total), unknown: fmtInt(a.unknown_mac) }) : '';
    setNumber('#ovPass', fmtInt(latest.pass));
    setNumber('#ovFail', fmtInt(latest.fail));
    setNumber('#ovStop', fmtInt(latest.stop));
    $('#ovPassSub').textContent = a.total ? t('ov.runs', { n: fmtInt(a.pass) }) : '';
    $('#ovFailSub').textContent = a.total ? t('ov.runs', { n: fmtInt(a.fail) }) : '';
    $('#ovStopSub').textContent = a.total ? t('ov.runs', { n: fmtInt(a.stop) }) : '';
    setNumber('#ovRetry', s.units ? fmtPct(s.retry_rate) : '—', s.units ? retryLevel(s.retry_rate) : '');
    $('#ovRetrySub').textContent = s.units ? t('ov.retrySub', { units: fmtInt(s.retry_units), recovered: fmtInt(s.recovered_units) }) : '';

    const st = spec.status;
    const outOfSpec = st.fail || 0;
    const weak = (st.bad || 0) + (st.warn || 0);
    setNumber('#ovSpec', fmtInt(outOfSpec), outOfSpec ? 'bad' : weak ? 'warn' : 'good');
    $('#ovSpecSub').textContent = t('ov.specSub', { weak: fmtInt(weak), ok: fmtInt(st.ok || 0) });
}

function renderTrend(trend) {
    const pts = trend.points || [];
    $('#ovTrendNote').textContent = pts.length ? (trend.unit === 'hour' ? t('ov.trendHour') : t('ov.trendDay')) : '';
    if (!pts.length) { charts.chartMessage('chOvTrend', t('common.empty')); return; }
    const labels = pts.map(p => (trend.unit === 'hour' ? fmtTime(p.t) : fmtShortDate(p.t)));
    const lows = pts.flatMap(p => [p.latest, p.first_pass, p.any_pass]).filter(v => v != null);
    const min = Math.max(0, Math.floor((Math.min(...lows, 90) - 5) / 10) * 10);
    charts.render('chOvTrend', c => ({
        data: {
            labels,
            datasets: [
                { type: 'line', label: t('ov.view.latest'), data: pts.map(p => p.latest), yAxisID: 'y',
                  borderColor: c.good, backgroundColor: c.good, tension: 0.25, pointRadius: 3, spanGaps: true, order: 0 },
                { type: 'line', label: t('ov.view.first_pass'), data: pts.map(p => p.first_pass), yAxisID: 'y',
                  borderColor: c.c2, backgroundColor: c.c2, borderDash: [6, 4], tension: 0.25, pointRadius: 2, spanGaps: true, order: 0 },
                { type: 'line', label: t('ov.view.any_pass'), data: pts.map(p => p.any_pass), yAxisID: 'y',
                  borderColor: c.c3, backgroundColor: c.c3, borderDash: [2, 3], tension: 0.25, pointRadius: 2, spanGaps: true, order: 0 },
                { type: 'bar', label: t('ov.unitsAxis'), data: pts.map(p => p.units), yAxisID: 'y1',
                  backgroundColor: charts.alpha(c.c1, 0.45), borderRadius: 4, order: 1 },
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: { position: 'left', min, max: 100, title: { display: true, text: t('ov.yieldAxis') } },
                y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, ticks: { precision: 0 },
                      title: { display: true, text: t('ov.unitsAxis') } },
            },
        },
    }));
}

function renderHourly(hourly) {
    const pts = hourly.points || [];
    if (!pts.some(p => p.pass + p.fail + p.stop)) { charts.chartMessage('chOvHourly', t('common.empty')); $('#ovHourlyNote').textContent = ''; return; }
    $('#ovHourlyNote').textContent = t('ov.hourlyNote', { hours: hourly.hours, to: `${fmtShortDate(pts[pts.length - 1].t)} ${fmtTime(pts[pts.length - 1].t)}` });
    charts.render('chOvHourly', c => ({
        type: 'bar',
        data: {
            labels: pts.map(p => fmtTime(p.t)),
            datasets: [
                { label: 'PASS', data: pts.map(p => p.pass), backgroundColor: c.pass, stack: 'r', borderRadius: 2 },
                { label: 'FAIL', data: pts.map(p => p.fail), backgroundColor: c.fail, stack: 'r', borderRadius: 2 },
                { label: 'STOP', data: pts.map(p => p.stop), backgroundColor: c.stop, stack: 'r', borderRadius: 2 },
            ],
        },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 },
                                                         title: { display: true, text: t('ov.runsAxis') } } } },
    }));
}

function renderSteps(steps) {
    if (!steps.length) { charts.chartMessage('chOvSteps', t('ov.noFails')); return; }
    charts.render('chOvSteps', c => ({
        type: 'bar',
        data: {
            labels: steps.map(s => s.step),
            datasets: [
                { label: t('ov.stepRuns'), data: steps.map(s => s.attempts), backgroundColor: c.fail, borderRadius: 3 },
                { label: t('ov.stepUnits'), data: steps.map(s => s.units), backgroundColor: c.stop, borderRadius: 3 },
                { label: t('ov.stepRecovered'), data: steps.map(s => s.recovered_units), backgroundColor: c.pass, borderRadius: 3 },
            ],
        },
        options: { indexAxis: 'y', scales: { x: { beginAtZero: true, ticks: { precision: 0 } },
                                             y: { ticks: { autoSkip: false, font: { size: 12 } } } } },
    }));
}

function renderMonthly(monthly) {
    $('#ovMonthlyNote').textContent = monthly.year ? t('ov.yearNote', { year: monthly.year }) : '';
    const months = monthly.months || [];
    if (!months.some(m => m.units)) { charts.chartMessage('chOvMonthly', t('common.empty')); return; }
    charts.render('chOvMonthly', c => ({
        type: 'bar',
        data: { labels: months.map(m => t(`ov.m${m.month}`)), datasets: [
            { label: 'PASS', data: months.map(m => m.pass), backgroundColor: c.pass, stack: 'u', borderRadius: 3 },
            { label: 'FAIL', data: months.map(m => m.fail), backgroundColor: c.fail, stack: 'u', borderRadius: 3 },
            { label: 'STOP', data: months.map(m => m.stop), backgroundColor: c.stop, stack: 'u', borderRadius: 3 },
        ] },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 },
                                                         title: { display: true, text: t('ov.unitsAxis') } } } },
    }));
}

async function load(app) {
    const mine = ++seq;
    try {
        const data = await apiGet(`/api/overview${queryString(app.params())}`);
        if (mine !== seq) return;
        renderNumbers(data.summary, data.aligned, data.spec);
        if (!data.summary.units && !data.summary.attempts.total) {
            ['chOvTrend', 'chOvHourly', 'chOvSteps'].forEach(id => charts.chartMessage(id, t('common.empty')));
        } else {
            renderTrend(data.trend);
            renderHourly(data.hourly);
            renderSteps(data.top_fail_steps);
        }
        renderMonthly(data.monthly);
    } catch (err) {
        if (mine !== seq) return;
        ['#ovYield', '#ovUnits', '#ovPass', '#ovFail', '#ovStop', '#ovRetry', '#ovSpec'].forEach(id => { $(id).textContent = '—'; });
        $('#ovViews').textContent = '';
        $('#ovAligned').textContent = err.message;
        ['chOvTrend', 'chOvHourly', 'chOvSteps', 'chOvMonthly'].forEach(id =>
            charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
    }
}

export default { id: 'overview', scoped: true, load };
