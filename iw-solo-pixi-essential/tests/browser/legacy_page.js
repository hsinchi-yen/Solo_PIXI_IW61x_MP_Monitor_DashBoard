// The pre-2.0 dashboard at /legacy.html (kept for comparison, Q23) against a running stack.
// Needs: npm i playwright-core, a Chrome binary, an uploader account op.test / uploader123
// and the default administrator pixi / pixipass. Run: node tests/browser/legacy_page.js
const { chromium } = require('playwright-core');
const path = require('path');

const BASE = process.env.BASE || 'http://localhost:8004';
const LOG = process.env.IW_LOG || path.resolve(__dirname, '../../../rawlogs/5101-260715003/20260721_142350_001F7B5806CA_001F7B5806CB_PASS.txt');
const PAGES = ['overview', 'workorders', 'fails', 'bt', 'wifi', 'advanced', 'failanalysis', 'dbtweak', 'dataalign', 'upload'];
const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true });
  // Never call the real LLM from tests.
  await context.route('**/api/legacy/workorders/*/ai-summary*', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ summary: '1. General Information\nWO <num>5101-260715003</num>\n<script>window.__xss=1</script>' }),
  }));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });

  // 1. Every page loads without errors
  for (const p of PAGES) {
    errors.length = 0;
    await page.goto(`${BASE}/legacy.html?page=${p}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(400);
    const relevant = errors.filter(e => !e.includes('401'));
    check(`page ${p} loads without JS errors`, relevant.length === 0, relevant.join(' | '));
  }

  // 2. API badge and last refresh are live
  await page.goto(`${BASE}/legacy.html?page=overview`, { waitUntil: 'networkidle' });
  check('API badge shows online', (await page.textContent('#api-status')).includes('online'));
  check('last refresh is written', (await page.textContent('#last-refresh')).startsWith('Data updated'));

  // 3. U1: the AI link no longer blanks the main area; U2: report stays open, IW611 label, Esc closes
  await page.evaluate(() => { window.llmConnected = true; });
  await page.click('#nav-ai-report');
  await page.waitForSelector('#terminal-modal.show');
  const visiblePages = await page.$$eval('.page.active', els => els.length);
  check('U1 AI link keeps the current page visible', visiblePages === 1, `active pages: ${visiblePages}`);
  check('U2 product label is IW611', (await page.textContent('#term-product-label')).includes('IW611'));
  await page.waitForFunction(() => document.getElementById('term-status').textContent.includes('COMPLETE'), null, { timeout: 15000 });
  await page.click('#term-body');
  await page.waitForTimeout(300);
  check('U2 clicking inside the report does not close it', await page.$('#terminal-modal.show') !== null);
  await page.waitForTimeout(31000);
  check('U2 report does not auto-close after 30 s', await page.$('#terminal-modal.show') !== null);
  await page.click('#term-countdown button');           // English done → 中文 ▶
  await page.waitForTimeout(800);
  check('U2 "中文 ▶" continues to the Chinese report', await page.$('#terminal-modal.show') !== null);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(800);
  check('U2 one Esc closes the report', await page.$('#terminal-modal.show') === null);

  // 4. Markdown AI modal is sanitized
  await page.goto(`${BASE}/legacy.html?page=workorders`, { waitUntil: 'networkidle' });
  await page.evaluate(() => openAiSummary('5101-260715003', 'en'));
  await page.waitForSelector('#ai-result', { state: 'visible' });
  const html = await page.innerHTML('#ai-result');
  check('S4 AI markdown has no <script>', !html.includes('<script'), html.slice(0, 120));
  await page.evaluate(() => closeAiModal());

  // 5. API failure shows a message with Retry
  await page.route('**/api/legacy/summary*', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Server error"}' }));
  await page.goto(`${BASE}/legacy.html?page=overview`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  check('U3 failed load shows an error with Retry', await page.isVisible('#load-error button'));
  await page.unroute('**/api/legacy/summary*');
  await page.click('#load-error button');
  await page.waitForTimeout(1500);
  check('U3 Retry clears the error', !(await page.isVisible('#load-error')));

  // 6. Upload asks for a sign-in; uploader account works
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${BASE}/legacy.html?page=upload`, { waitUntil: 'networkidle' });
  await page.setInputFiles('#iwUploadFiles', LOG);
  await page.fill('#iwUploadWorkOrder', '5101-260715003');
  await page.click('#btnIwUpload');
  await page.waitForSelector('#login-modal.show');
  check('S2 upload asks for sign-in', true);
  await page.fill('#loginModalUser', 'op.test');
  await page.fill('#loginModalPass', 'uploader123');
  await page.click('#btnLoginModalOk');
  await page.waitForFunction(() => document.getElementById('iwUploadStatus').textContent.startsWith('Complete'), null, { timeout: 30000 });
  check('upload completes as uploader', (await page.textContent('#iwUploadStatus')).includes('1 duplicate'));

  // 7. Raw-log download with the uploader session (header, no token in URL)
  await page.goto(`${BASE}/legacy.html?page=fails`, { waitUntil: 'networkidle' });
  const requests = [];
  page.on('request', r => { if (r.url().includes('raw-log')) requests.push(r.url()); });
  const recordId = await page.evaluate(async () => (await (await fetch('/api/fails?limit=1')).json())[0].id);
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.evaluate(id => downloadFailLog(id), recordId),
  ]);
  check('raw log downloads as a .txt file', download.suggestedFilename().endsWith('.txt'), download.suggestedFilename());
  check('S3 no token in the download URL', requests.every(u => !u.includes('token=')) && requests.every(u => u.includes('/api/legacy/')), requests.join(' '));

  // 8. Alignment save needs the administrator; uploader is refused, admin succeeds
  await page.goto(`${BASE}/legacy.html?page=dataalign`, { waitUntil: 'networkidle' });
  await page.click('#btnAlignSaveAll');
  await page.waitForSelector('#login-modal.show');
  check('S2 uploader session is not enough for alignment targets', true);
  await page.fill('#loginModalUser', 'pixi');
  await page.fill('#loginModalPass', 'pixipass');
  await page.click('#btnLoginModalOk');
  await page.waitForFunction(() => document.getElementById('alignSaveStatus').textContent.includes('Saved to DB'), null, { timeout: 10000 });
  check('alignment targets save as administrator', true);

  // 9. DB Tweak reuses the administrator session
  await page.goto(`${BASE}/legacy.html?page=dbtweak`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  check('DB Tweak opens with the existing admin session', await page.isVisible('#tweakPanel'));
  check('DB Tweak lists records', (await page.textContent('#tweakResultSummary')).includes('180'));

  await browser.close();
  const failed = results.filter(r => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
