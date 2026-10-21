// New dashboard (web/index.html) against a running stack (default http://localhost:8003).
// Needs: npm i playwright-core, a Chrome binary, data for work order 5101-260715003 and the
// default administrator pixi / pixipass. Run: node tests/browser/dashboard.js
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://localhost:8003';
const PAGES = ['overview', 'workorders', 'fails', 'measurements', 'failanalysis', 'dbtweak', 'dataalign', 'logupload', 'accounts'];
const results = [];
const check = (name, ok, detail = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };

(async () => {
    const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const errors = [];
    page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
    page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
    page.on('response', r => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`); });
    page.on('dialog', d => d.accept());

    for (const lang of ['en', 'zh']) {
        for (const p of PAGES) {
            errors.length = 0;
            await page.goto(`${BASE}/?lang=${lang}#${p}`, { waitUntil: 'networkidle' });
            await page.waitForTimeout(400);
            check(`${lang} page ${p} loads without errors`, errors.length === 0, errors.join(' | '));
        }
    }

    // Dashboard: latest work order by default (Q3), latest-result yield first (Q1)
    await page.goto(`${BASE}/?lang=en#overview`, { waitUntil: 'networkidle' });
    check('starts on the latest work order', (await page.textContent('#pageScope')).includes('5101-260715003'));
    check('headline is the latest-result yield', (await page.textContent('#ovYield')).startsWith('99.17'));
    const views = await page.textContent('#ovViews');
    check('first-pass and any-pass shown beside it', views.includes('72.50%') && views.includes('99.17%'), views);
    check('units and runs kept apart', (await page.textContent('#ovUnitsSub')).includes('180 test runs'));
    check('four charts drawn', await page.$$eval('.ov-charts canvas', cs => cs.length === 4 && cs.every(c => c.width > 0)) &&
          (await page.$$('.ov-charts .state-msg')).length === 0);

    // pixi-dashboard 3.2 layout: doughnut left of the yield, seven boxes in one row at 1920, Hourly Output 07:00–19:00
    const boxes = await page.$$eval('.ov-numbers > div', els => els.map(e => [e.className, Math.round(e.getBoundingClientRect().top)]));
    check('doughnut is the first box, left of the yield', boxes[0][0] === 'ov-split' && boxes[1][0] === 'ov-hero', boxes.map(b => b[0]).join(','));
    check('seven boxes in one row at 1920 px', boxes.length === 7 && boxes.every(b => b[1] === boxes[0][1]), JSON.stringify(boxes));
    check('Out of Spec is no longer on the dashboard', !(await page.textContent('#page-overview')).includes('Out of Spec'));
    const wrapped = await page.$$eval('.ov-num .value', els => els.filter(e => e.scrollWidth > e.clientWidth + 1).length);
    check('big numbers fit their boxes', wrapped === 0, `${wrapped} overflow`);
    check('doughnut drawn', await page.$eval('#chOvSplit', c => c.width > 0) && !(await page.$('.ov-split .state-msg')));
    const hourNote = await page.textContent('#ovHourlyNote');
    check('Hourly Output names the day, 07:00–19:00 and the work order', /07:00–19:00 · 5101-260715003 · \d+ passed/.test(hourNote), hourNote);
    check('two monthly charts cover all work orders', (await page.textContent('#ovMonthlyFailNote')).includes('all work orders'));
    const fit = await page.evaluate(() => [document.scrollingElement.scrollHeight, window.innerHeight]);
    check('dashboard fits 1920×1080 without scrolling', fit[0] <= fit[1] + 2, fit.join(' > '));
    for (const [w, h] of [[1440, 900], [390, 844]]) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(500);
        const over = await page.evaluate(() => document.scrollingElement.scrollWidth > window.innerWidth + 1);
        check(`no sideways scrolling at ${w} px`, !over);
    }
    await page.setViewportSize({ width: 1920, height: 1080 });

    // Measurements: spec check, row → histogram
    await page.goto(`${BASE}/?lang=en#measurements`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#msItemRows tr[data-idx]');
    check('spec check lists the out-of-spec items first', (await page.textContent('#msItemRows tr[data-idx="0"]')).includes('Out of spec'));
    await page.click('#msItemRows tr[data-idx="1"]');
    await page.waitForFunction(() => document.getElementById('msHistNote').textContent.includes('limits'));
    check('clicking an item draws its histogram with limits', (await page.textContent('#msHistTitle')).includes('PER'));
    for (const tab of ['bt', 'wifi', 'other']) {
        await page.click(`[data-ms-tab="${tab}"]`);
        await page.waitForTimeout(500);
    }
    check('BT RX shows BER at 2403 / 2478 MHz', /2403[\s\S]*2478/.test(await page.textContent('#msBtRxRows')));

    // Fail list result filter
    await page.goto(`${BASE}/?lang=en#fails`, { waitUntil: 'networkidle' });
    await page.click('#failResultSeg [data-result="STOP"]');
    await page.waitForFunction(() => document.getElementById('failCount').textContent.includes('46'));
    check('fail list STOP filter', true);

    // Theme and language switch keep the page
    await page.goto(`${BASE}/?lang=en#overview`, { waitUntil: 'networkidle' });
    await page.click('#btnTheme');
    check('light theme', await page.evaluate(() => document.documentElement.dataset.theme === 'light'));
    await page.click('#btnTheme');
    await page.click('#btnLang');
    await page.waitForTimeout(500);
    check('switch to 中文', (await page.textContent('#pageTitle')) === '儀表板');
    await page.click('#btnLang');

    // AI report window (stored report or "AI off" message), Esc closes it
    await page.goto(`${BASE}/?lang=en#workorders`, { waitUntil: 'networkidle' });
    await page.click('[data-report]');
    await page.waitForSelector('#reportModal:not([hidden])');
    await page.waitForFunction(() => document.querySelector('#rpFacts .rp-fact') || document.querySelector('#rpBanner .notice'), null, { timeout: 30000 });
    check('report window shows the facts', (await page.textContent('#rpFacts')).includes('120'));
    await page.keyboard.press('Escape');
    check('Esc closes the report', await page.$('#reportModal[hidden]') !== null);

    // DB Tweak and accounts need the administrator
    await page.goto(`${BASE}/?lang=en#dbtweak`, { waitUntil: 'networkidle' });
    await page.fill('#twUser', 'pixi');
    await page.fill('#twPass', 'pixipass');
    await page.click('#btnTwLogin');
    await page.waitForSelector('#twPanel:not([hidden])');
    await page.waitForSelector('#twRows tr td.num');
    check('DB Tweak lists records after sign-in', (await page.$$('#twRows tr')).length > 10);
    check('accounts menu appears for the administrator', !(await page.isHidden('#navAccounts')));
    await page.goto(`${BASE}/?lang=en#accounts`, { waitUntil: 'networkidle' });
    check('accounts page opens', !(await page.isHidden('#acctPanel')));
    await page.click('#btnLogout');

    // Uploaders get 中文 while signed in (pixi-dashboard Q53–Q55); the browser's own choice comes back after sign-out.
    // Uses the account lang.check on the target (created, then disabled), so run it only against a local stack.
    const api = async (path, opts = {}) => (await fetch(BASE + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) } })).json();
    const adm = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: 'pixi', password: 'pixipass' }) });
    const H = { 'X-Tweak-Token': adm.token };
    const pw = 'lang-check-1';
    await api('/api/admin/users', { method: 'POST', headers: H, body: JSON.stringify({ username: 'lang.check', display_name: 'Lang Check', password: pw }) });
    await api('/api/admin/users/lang.check', { method: 'PATCH', headers: H, body: JSON.stringify({ enabled: true }) });
    await api('/api/admin/users/lang.check/reset-password', { method: 'POST', headers: H, body: JSON.stringify({ password: pw }) });
    await page.goto(`${BASE}/?lang=en#logupload`, { waitUntil: 'networkidle' });
    check('upload menu item reads 上傳 Log in English too', (await page.textContent('[data-page="logupload"]')).includes('上傳 Log'));
    await page.fill('#luUser', 'lang.check');
    await page.fill('#luPass', pw);
    await page.click('#btnLuLogin');
    await page.waitForSelector('#luPanel:not([hidden])');
    await page.waitForTimeout(400);
    check('an uploader signing in gets 中文', await page.evaluate(() => document.documentElement.lang) === 'zh-Hant-TW'
          && (await page.textContent('[data-page="overview"]')).includes('儀表板'));
    await page.click('#btnLogout');
    await page.waitForTimeout(400);
    check('signing out brings English back', await page.evaluate(() => document.documentElement.lang) === 'en'
          && (await page.textContent('[data-page="overview"]')).includes('Dashboard'));
    await api('/api/admin/users/lang.check', { method: 'PATCH', headers: H, body: JSON.stringify({ enabled: false }) });
    await api('/api/auth/logout', { method: 'POST', headers: H });

    await browser.close();
    const failed = results.filter(ok => !ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks passed`);
    process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
