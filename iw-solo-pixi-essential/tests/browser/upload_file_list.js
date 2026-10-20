// Log Upload file list: add files one at a time, remove one (✕), remove a wrong file type, remove all.
// Analyses only — nothing is uploaded; each removed analysis is discarded on the server.
// Needs: npm i playwright-core, a Chrome binary, the administrator pixi / pixipass (or ADMIN_USER /
// ADMIN_PASS) and the sample station logs under rawlogs/. Run: BASE=http://localhost:8004 node tests/browser/upload_file_list.js
const path = require('path');
const fs = require('fs');
const os = require('os');
const { chromium } = require('playwright-core');

const BASE = process.env.BASE || 'http://localhost:8004';
const LOGS = path.resolve(__dirname, '../../../rawlogs/TN329_IW611_LOG/TN329_IW611_LOG');
const results = [];
const check = (name, ok, detail = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  — ' + detail : ''}`); };

(async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'iw-filelist-'));
    const station = s => { const dir = path.join(tmp, s); fs.mkdirSync(dir); const f = path.join(dir, 'Log_all.txt'); fs.copyFileSync(path.join(LOGS, s, 'Log_all.txt'), f); return f; };
    const s30 = station('30'), s40 = station('40');
    const wrong = path.join(tmp, 'notes.pdf'); fs.writeFileSync(wrong, 'not a log');

    const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const discards = [];
    page.on('request', r => { if (/\/api\/log-upload\/[0-9a-f]+\/discard$/.test(r.url())) discards.push(r.url()); });

    await page.goto(`${BASE}/?lang=en#logupload`, { waitUntil: 'networkidle' });
    await page.fill('#luUser', process.env.ADMIN_USER || 'pixi');
    await page.fill('#luPass', process.env.ADMIN_PASS || 'pixipass');
    await page.click('#btnLuLogin');
    await page.waitForSelector('#luPanel:not([hidden])');
    check('no file list before choosing files', await page.isHidden('#luFiles'));

    const analysed = async n => {
        await page.waitForFunction(want => {
            const grid = document.getElementById('luFactGrid');
            return !document.getElementById('luStepConfirm').hidden && grid && grid.textContent.includes(String(want));
        }, n, { timeout: 120000 });
    };
    const names = () => page.$$eval('#luFileList .lu-file-name', els => els.map(e => e.textContent));

    await page.setInputFiles('#luFile', s30);
    await analysed(78);
    check('one file listed and analysed (station 30: 78 runs)', (await names()).length === 1);

    await page.setInputFiles('#luFile', s40);
    await analysed(180);
    check('a second file is added, not replaced (both stations: 180 runs)', (await names()).length === 2, (await names()).join(', '));
    check('the earlier analysis was discarded on the server', discards.length === 1, `${discards.length} discard calls`);

    await page.click('#luFileList li:first-child [data-remove-file]');
    await analysed(102);
    check('✕ removes one file and re-analyses the rest (station 40: 102 runs)', (await names()).length === 1);

    await page.setInputFiles('#luFile', wrong);
    await page.waitForFunction(() => document.getElementById('luAnalyzeStatus').classList.contains('error'));
    check('a wrong file type is listed with an error', (await names()).includes('notes.pdf'));
    await page.click('#luFileList li:last-child [data-remove-file]');
    await analysed(102);
    check('removing the wrong file brings the analysis back', !(await names()).includes('notes.pdf'));

    await page.click('#btnLang');
    await page.waitForTimeout(300);
    check('buttons follow the language (全部移除)', (await page.textContent('#btnLuClearFiles')).includes('全部移除'));
    await page.click('#btnLang');

    await page.click('#btnLuClearFiles');
    await page.waitForTimeout(500);
    check('Remove all clears the list and the steps', await page.isHidden('#luFiles') && await page.isHidden('#luStepConfirm')
          && /No file chosen/.test(await page.textContent('#luFileInfo')));
    check('every analysis was discarded (4 analyses → 4 discards)', discards.length === 4, `${discards.length} discard calls`);
    const login = await (await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: process.env.ADMIN_USER || 'pixi', password: process.env.ADMIN_PASS || 'pixipass' }) })).json();
    const H = { 'X-Tweak-Token': login.token };
    const history = (await (await fetch(`${BASE}/api/log-upload/history`, { headers: H })).json()).items.map(i => i.status);
    await fetch(`${BASE}/api/auth/logout`, { method: 'POST', headers: H });
    check('discarded analyses are not in the upload history', !history.includes('discarded'), history.join(','));
    check('no page errors', !errors.length, errors.join(' | '));

    await page.click('#btnLogout').catch(() => {});
    await browser.close();
    fs.rmSync(tmp, { recursive: true, force: true });
    const failed = results.filter(ok => !ok).length;
    console.log(`\n${results.length - failed}/${results.length} checks passed`);
    process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
