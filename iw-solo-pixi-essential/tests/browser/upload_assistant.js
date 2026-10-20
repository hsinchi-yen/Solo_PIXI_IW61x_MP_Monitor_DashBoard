// AI upload assistant end to end: chat → upload → undo → upload again with the form.
// WARNING: deletes work order 5101-260715003 from the target database first, then uploads it
// again. Run it only against a local stack, never against production.
// Needs a work-order zip (each station's Log_all.txt), the uploader op.test / uploader123, the
// administrator pixi / pixipass and AI enabled. Run: node tests/browser/upload_assistant.js <zip> <screenshot dir>
const { chromium } = require('playwright-core');
const BASE = process.env.BASE || 'http://localhost:8004';
const ZIP = process.argv[2];
const OUT = process.argv[3];
const check = (name, ok, detail = '') => console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);

async function api(path, opts = {}) {
    const res = await fetch(BASE + path, opts);
    return res.json();
}

(async () => {
    const adm = await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                               body: JSON.stringify({ username: 'pixi', password: 'pixipass' }) });
    const del = await api('/api/tweak/delete-scope?work_order=5101-260715003&confirm=DELETE',
                          { method: 'DELETE', headers: { 'X-Tweak-Token': adm.token } });
    check('local WO deleted before the test', del.deleted === 180, JSON.stringify(del));

    const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', d => d.accept());

    await page.goto(`${BASE}/#logupload`, { waitUntil: 'networkidle' });
    await page.fill('#luUser', 'op.test');
    await page.fill('#luPass', 'uploader123');
    await page.click('#btnLuLogin');
    await page.waitForSelector('#luPanel:not([hidden])');
    await page.setInputFiles('#luFile', ZIP);
    await page.waitForSelector('#luStepConfirm:not([hidden])', { timeout: 120000 });
    const facts = await page.textContent('#luFactGrid');
    check('analyze shows the facts', facts.includes('180') && facts.includes('120'), facts.replace(/\s+/g, ' ').slice(0, 160));
    const mode = await page.textContent('#luModeLabel');
    check('AI chat mode is used when the AI is online', /AI|chat|對話/i.test(mode), mode);
    const opening = await page.textContent('#luChatLog');
    check('opening line built from facts', opening.includes('5101-260715003') && opening.includes('180'), opening.slice(0, 160));

    const send = async text => {
        await page.fill('#luChatInput', text);
        await page.click('#btnLuChatSend');
        await page.waitForFunction(() => !document.querySelector('#luChatLog .typing') && !document.getElementById('luChatInput').disabled,
                                   null, { timeout: 180000 });
    };
    await send('Yes, the work order is 5101-260715003');
    check('chat extracted the work order', (await page.inputValue('#luWo')) === '5101-260715003', await page.inputValue('#luWo'));
    await send('planned 200 units');
    check('chat extracted the planned quantity', (await page.inputValue('#luQty')) === '200', await page.inputValue('#luQty'));
    const issues = await page.textContent('#luIssues');
    check('rules shown (R5 plan vs units, R7 target)', issues.includes('200') && issues.includes('120'), issues.replace(/\s+/g, ' ').slice(0, 200));
    await page.screenshot({ path: `${OUT}/upload-chat.png`, fullPage: true });
    check('Upload button enabled by the rules', !(await page.isDisabled('#btnLuUpload')));

    await page.click('#btnLuUpload');
    await page.waitForFunction(() => /complete|完成|done/i.test(document.getElementById('luProgressText').textContent)
                                     || document.getElementById('luProgressFill').className === 'done', null, { timeout: 180000 });
    const result = await page.textContent('#luResultGrid');
    check('upload wrote 180 new runs', result.replace(/\s+/g, ' ').includes('180'), result.replace(/\s+/g, ' '));
    await page.screenshot({ path: `${OUT}/upload-done.png`, fullPage: true });

    let s = await api('/api/summary?work_order=5101-260715003');
    check('numbers match the snapshot after the upload', s.units === 120 && s.yield.latest.yield_pct === 99.17 && s.attempts.total === 180,
          `${s.units} ${s.yield.latest.yield_pct} ${s.attempts.total}`);
    const targets = await api('/api/alignment-targets');
    check('planned quantity saved as the alignment target', targets['5101-260715003'] === 200, JSON.stringify(targets));

    // Undo
    await page.click('#btnLuRevert');
    await page.waitForFunction(() => /revert|撤銷|undone/i.test(document.getElementById('luProgressText').textContent), null, { timeout: 60000 });
    s = await api('/api/summary?work_order=5101-260715003');
    check('undo removed exactly the uploaded runs', s.attempts.total === 0, `attempts ${s.attempts.total}`);
    const t2 = await api('/api/alignment-targets');
    check('undo restored the previous alignment target', t2['5101-260715003'] === 120, JSON.stringify(t2));

    // Again, with the form
    await page.click('#btnLuNext');
    await page.setInputFiles('#luFile', ZIP);
    await page.waitForSelector('#luStepConfirm:not([hidden])', { timeout: 120000 });
    await page.click('#btnLuMode');
    await page.fill('#luWo', '5101-260715003');
    await page.fill('#luQty', '120');
    await page.waitForFunction(() => !document.getElementById('btnLuUpload').disabled, null, { timeout: 15000 });
    await page.click('#btnLuUpload');
    await page.waitForFunction(() => document.getElementById('luProgressFill').className === 'done', null, { timeout: 180000 });
    s = await api('/api/summary?work_order=5101-260715003');
    check('form upload restored the work order', s.units === 120 && s.attempts.total === 180, `${s.units} ${s.attempts.total}`);
    const hist = await page.textContent('#luHistoryRows');
    check('history lists the batches', (hist.match(/5101-260715003/g) || []).length >= 2);
    check('no page errors', !errors.length, errors.join(' | '));
    await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
