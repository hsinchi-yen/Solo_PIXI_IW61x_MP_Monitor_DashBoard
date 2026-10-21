// Dashboard: the PASS / FAIL / STOP doughnut, the latest-result yield with first-pass / any-pass
// below it (Q1), units and test runs kept apart, and four charts laid out as on pixi-dashboard 3.2
// (yield trend, hourly output, units and FAIL / STOP per month), from GET /api/overview.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { fmtDate, fmtInt, fmtPct, fmtShortDate, fmtTime, retryLevel, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $ } from '../core/ui.js';

let seq = 0;

function setNumber(id, text, level) {
    const el = $(id);
    el.textContent = text;
    el.classList.toggle('long', text.length > 5);
    el.classList.remove('v-good', 'v-warn', 'v-bad');
    if (level) el.classList.add(`v-${level}`);
}

function renderNumbers(s, aligned) {
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
}

// Small doughnut left of the yield: units by latest result. The legend shows percentages (the
// counts are in the PASS / FAIL / STOP boxes), the tooltip both.
function renderSplit(latest) {
    const names = ['PASS', 'FAIL', 'STOP'];
    const values = [latest.pass, latest.fail, latest.stop];
    if (!values.some(Boolean)) { charts.chartMessage('chOvSplit', t('common.empty')); return; }
    const total = values.reduce((a, b) => a + b, 0);
    const pct = i => (values[i] * 100 / total).toFixed(2);
    charts.render('chOvSplit', c => ({
        type: 'doughnut',
        data: {
            labels: names.map((label, i) => t('ov.splitShort', { label, pct: pct(i) })),
            datasets: [{ data: values, backgroundColor: [c.pass, c.fail, c.stop], borderWidth: 0 }],
        },
        options: {
            cutout: '62%',
            plugins: {
                legend: { position: 'right', maxWidth: 160, labels: { boxWidth: 10, padding: 8 } },
                tooltip: { callbacks: { label: ctx => t('ov.splitLabel', { label: names[ctx.dataIndex], n: fmtInt(values[ctx.dataIndex]), pct: pct(ctx.dataIndex) }) } },
            },
        },
    }));
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

const hourLabel = h => `${String(h).padStart(2, '0')}:00`;

// Test runs per work hour on one day of one work order; the PASS part is the units produced.
function renderHourly(h) {
    const pts = h.points || [];
    const passed = pts.reduce((a, p) => a + p.pass, 0);
    if (!pts.some(p => p.pass + p.fail + p.stop)) {
        $('#ovHourlyNote').textContent = '';
        charts.chartMessage('chOvHourly', t('common.empty'));
        return;
    }
    $('#ovHourlyNote').textContent =
        t('ov.hourlyNote', { date: fmtDate(h.date), from: hourLabel(h.start), to: hourLabel(h.end), wo: h.work_order, pass: fmtInt(passed) })
        + (h.outside ? t('ov.hourlyOutside', { n: fmtInt(h.outside) }) : '');
    charts.render('chOvHourly', c => ({
        type: 'bar',
        data: {
            labels: pts.map(p => hourLabel(p.hour)),
            datasets: [
                { label: 'PASS', data: pts.map(p => p.pass), backgroundColor: c.pass, stack: 'r', borderRadius: 2 },
                { label: 'FAIL', data: pts.map(p => p.fail), backgroundColor: c.fail, stack: 'r', borderRadius: 2 },
                { label: 'STOP', data: pts.map(p => p.stop), backgroundColor: c.stop, stack: 'r', borderRadius: 2 },
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: { x: { stacked: true },
                      y: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: t('ov.runsAxis') } } },
        },
    }));
}

// The whole year across all work orders: units by the month of their last test and their latest result.
function renderMonthly(monthly) {
    const note = monthly.year ? t('ov.yearAll', { year: monthly.year }) : '';
    $('#ovMonthlyNote').textContent = note;
    $('#ovMonthlyFailNote').textContent = note;
    const months = monthly.months || [];
    if (!months.some(m => m.units)) {
        charts.chartMessage('chOvMonthly', t('common.empty'));
        charts.chartMessage('chOvMonthlyFail', t('common.empty'));
        return;
    }
    const labels = months.map(m => t(`ov.m${m.month}`));
    charts.render('chOvMonthly', c => ({
        type: 'bar',
        data: { labels, datasets: [
            { label: 'PASS', data: months.map(m => m.pass), backgroundColor: c.pass, stack: 'u', borderRadius: 3 },
            { label: 'FAIL + STOP', data: months.map(m => m.fail + m.stop), backgroundColor: c.fail, stack: 'u', borderRadius: 3 },
        ] },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 },
                                                         title: { display: true, text: t('ov.unitsAxis') } } } },
    }));
    charts.render('chOvMonthlyFail', c => ({
        type: 'bar',
        data: { labels, datasets: [
            { label: 'FAIL', data: months.map(m => m.fail), backgroundColor: c.fail, borderRadius: 3 },
            { label: 'STOP', data: months.map(m => m.stop), backgroundColor: c.stop, borderRadius: 3 },
        ] },
        options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: t('ov.unitsAxis') } } } },
    }));
}

async function load(app) {
    const mine = ++seq;
    try {
        const data = await apiGet(`/api/overview${queryString(app.params())}`);
        if (mine !== seq) return;
        renderNumbers(data.summary, data.aligned);
        if (!data.summary.units && !data.summary.attempts.total) {
            ['chOvSplit', 'chOvTrend', 'chOvHourly'].forEach(id => charts.chartMessage(id, t('common.empty')));
            $('#ovTrendNote').textContent = '';
            $('#ovHourlyNote').textContent = '';
        } else {
            renderSplit(data.summary.yield.latest);
            renderTrend(data.trend);
            renderHourly(data.hourly);
        }
        renderMonthly(data.monthly);
    } catch (err) {
        if (mine !== seq) return;
        ['#ovYield', '#ovUnits', '#ovPass', '#ovFail', '#ovStop', '#ovRetry'].forEach(id => { $(id).textContent = '—'; });
        $('#ovViews').textContent = '';
        $('#ovAligned').textContent = err.message;
        ['#ovTrendNote', '#ovHourlyNote', '#ovMonthlyNote', '#ovMonthlyFailNote'].forEach(id => { $(id).textContent = ''; });
        ['chOvTrend', 'chOvSplit', 'chOvHourly', 'chOvMonthly', 'chOvMonthlyFail'].forEach((id, i) =>
            charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error', i ? null : () => load(app)));
    }
}

export default { id: 'overview', scoped: true, load };
