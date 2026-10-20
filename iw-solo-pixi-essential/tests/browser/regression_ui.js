// Browser regression of the improve_plan.md UI fixes not covered by dashboard.js:
// U1 (AI link keeps the page), U2 (report window behaviour), U3 (API errors, retry, failed save),
// A4 (empty range), 1080p fit, AI-off state, and escaping of user-entered text (S4).
// Creates/updates the account xss.check on the target, so run it only against a local stack.
// Needs: npm i playwright-core, a Chrome binary, the administrator pixi / pixipass.
// Run: BASE=http://localhost:8003 node tests/browser/regression_ui.js
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://localhost:8003';
const results = [];
const check = (item, name, ok, detail = '') => {
    results.push(ok);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${item.padEnd(4)}  ${name}${!ok && detail ? '  — ' + detail : ''}`);
};

async function api(path, opts = {}) {
    const res = await fetch(BASE + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) } });
    return res.json();
}

(async () => {
    const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const errors = [];
    page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
    page.on('dialog', d => d.accept());
    await page.addInitScript(() => { window.__xss = 0; });

    // 1080p: the dashboard fits without a vertical scroll bar (stage 4 acceptance)
    await page.goto(`${BASE}/?lang=en#overview`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    const fit = await page.evaluate(() => [document.scrollingElement.scrollHeight, window.innerHeight]);
    check('A1', 'dashboard fits 1920×1080 without scrolling', fit[0] <= fit[1] + 2, fit.join(' > '));
    check('U3', 'API and AI status lights are updated (not "checking")',
          !/checking|檢查中/i.test(await page.textContent('#apiText')) && !/checking|檢查中/i.test(await page.textContent('#llmText')),
          `${await page.textContent('#apiText')} / ${await page.textContent('#llmText')}`);

    // U1: side-bar "AI report (latest WO)" opens the window and keeps the page
    await page.click('#navAiReport');
    await page.waitForSelector('#reportModal:not([hidden])');
    check('U1', 'AI report link opens the window, page stays visible',
          !(await page.isHidden('#page-overview')) && /%/.test(await page.textContent('#ovYield')));
    // U2: clicking inside does not close; text selectable; IW611 name; no auto close; backdrop closes
    await page.click('#rpTitle');
    check('U2', 'clicking inside the report keeps it open', await page.$('#reportModal:not([hidden])') !== null);
    const select = await page.evaluate(() => getComputedStyle(document.querySelector('#reportModal .modal-panel')).userSelect);
    check('U2', 'report text can be selected', select !== 'none', select);
    const modalText = await page.textContent('#reportModal');
    check('U2', 'no QCA product name in the report window', !/9377/.test(modalText));
    await page.waitForTimeout(31000);
    check('U2', 'report window does not close by itself (31 s)', await page.$('#reportModal:not([hidden])') !== null);
    await page.mouse.click(5, 540);
    check('U2', 'clicking outside closes it', await page.$('#reportModal[hidden]') !== null);

    // U3: API failure → message with Retry → works after Retry
    await page.route('**/api/overview*', r => r.abort());
    await page.reload({ waitUntil: 'networkidle' });             // same URL: goto() would not reload
    await page.waitForTimeout(500);
    const errBox = await page.$('#page-overview .state-msg.error button');
    check('U3', 'failed API call shows an error with Retry', errBox !== null);
    await page.unroute('**/api/overview*');
    if (errBox) {
        await errBox.click();
        await page.waitForFunction(() => /%/.test(document.getElementById('ovYield').textContent), null, { timeout: 15000 })
            .catch(() => {});
    }
    check('U3', 'Retry loads the numbers', /%/.test(await page.textContent('#ovYield')));

    // A4: a range without data says so (no error, no stale numbers)
    errors.length = 0;
    await page.click('#rangeSeg [data-range="custom"]');
    await page.fill('#dateFrom', '2020-01-01');
    await page.dispatchEvent('#dateFrom', 'change');
    await page.fill('#dateTo', '2020-01-02');
    await page.dispatchEvent('#dateTo', 'change');
    await page.waitForTimeout(1500);
    const empty = await page.$$eval('#page-overview .state-msg', els => els.filter(e => !e.classList.contains('error')).length);
    check('A4', 'empty range shows "no data" messages, no errors', empty > 0 && errors.length === 0,
          `${empty} messages; ${errors.join(' | ')}`);
    check('A4', 'empty range does not keep the old yield', !/99\.17/.test(await page.textContent('#ovYield')),
          await page.textContent('#ovYield'));
    await page.click('#rangeSeg [data-range="all"]');

    // AI off: report buttons disabled with a notice
    await page.route('**/api/llm-status', r => r.fulfill({ json: { enabled: false, connected: false, status: 'error', model: '', last_ok_at: null } }));
    await page.route('**/api/meta', async r => {
        const res = await r.fetch();
        const body = await res.json();
        body.llm = { ...body.llm, enabled: false, connected: false };
        await r.fulfill({ response: res, json: body });
    });
    await page.route('**/api/reports/*', async r => {
        const res = await r.fetch();
        const body = await res.json();
        body.llm = { ...(body.llm || {}), enabled: false, connected: false };
        await r.fulfill({ response: res, json: body });
    });
    await page.goto(`${BASE}/?lang=en#workorders`, { waitUntil: 'networkidle' });
    await page.click('[data-report]');
    await page.waitForSelector('#reportModal:not([hidden])');
    await page.waitForTimeout(800);
    check('C4', 'AI off: Regenerate and Ask are disabled',
          await page.isDisabled('#btnRpRegen') && await page.isDisabled('#btnRpAsk'));
    await page.keyboard.press('Escape');
    await page.unrouteAll({ behavior: 'ignoreErrors' });

    // S4: user-entered text is shown as text, never run (accounts page)
    const adm = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: 'pixi', password: 'pixipass' }) });
    const H = { 'X-Tweak-Token': adm.token };
    const payload = '<img src=x onerror="window.__xss=1">';
    await api('/api/admin/users', { method: 'POST', headers: H, body: JSON.stringify({ username: 'xss.check', display_name: payload, password: 'xss-check-1' }) });
    await api('/api/admin/users/xss.check', { method: 'PATCH', headers: H, body: JSON.stringify({ display_name: payload, department: payload }) });
    await page.goto(`${BASE}/?lang=en#dbtweak`, { waitUntil: 'networkidle' });
    await page.fill('#twUser', 'pixi');
    await page.fill('#twPass', 'pixipass');
    await page.click('#btnTwLogin');
    await page.waitForSelector('#twPanel:not([hidden])');
    await page.goto(`${BASE}/?lang=en#accounts`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);
    check('S4', 'HTML in a display name is shown as text and not executed',
          (await page.evaluate(() => window.__xss)) === 0 && (await page.textContent('#page-accounts')).includes('<img src=x'));

    // U3: a failed Alignment save is reported as a failure
    await page.goto(`${BASE}/?lang=en#dataalign`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#alRows .target-input');
    await page.route('**/api/alignment-targets', r => r.request().method() === 'POST'
        ? r.fulfill({ status: 500, json: { error: 'Server error', error_zh: '伺服器錯誤', code: 'error' } }) : r.continue());
    const input = await page.$('#alRows .target-input');
    await input.fill('123');
    await input.dispatchEvent('input');
    await page.click('#btnAlignSave');
    await page.waitForTimeout(800);
    const toastText = await page.evaluate(() => document.getElementById('toast')?.hidden === false ? document.getElementById('toast').textContent : '');
    const statusText = await page.textContent('#alStatus');
    check('U3', 'failed Alignment save shows the error, not "Saved"',
          !/saved/i.test(toastText) && /error|fail/i.test(statusText), `status="${statusText}" toast="${toastText}"`);
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await page.click('#btnLogout').catch(() => {});

    // Addresses of the pre-2.0 dashboard (bookmarks, links) open the matching page
    for (const [old, page_, tab] of [['/work-orders.html', 'workorders'], ['/bt-analysis.html', 'measurements', 'bt'],
                                     ['/?page=fails&lang=en', 'fails'], ['/?page=wifi', 'measurements', 'wifi']]) {
        await page.goto(`${BASE}${old}`, { waitUntil: 'networkidle' });
        await page.waitForTimeout(400);
        const shown = await page.evaluate(() => [...document.querySelectorAll('.page')].find(s => !s.hidden)?.id);
        const tabOk = !tab || (await page.getAttribute(`[data-ms-tab="${tab}"]`, 'aria-selected')) === 'true';
        check('E1', `old address ${old} opens ${page_}${tab ? ' / ' + tab : ''}`, shown === `page-${page_}` && tabOk, `${shown} ${page.url()}`);
    }

    check('--', 'no uncaught page errors', errors.length === 0, errors.join(' | '));
    await browser.close();
    const failed = results.filter(ok => !ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks passed`);
    process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
