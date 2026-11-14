// Rendering test only: local fixtures, no production writes or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const evidenceId = '11111111-1111-4111-8111-111111111111';
const base = {serial: 'P-102', capturedAt: '2026-11-13T16:00:00Z', note: '', telemetry: {}, vision: {}, findings: []};
const records = [
  {...base, id: 'camera-demo', scenarioId: 'coupling_guard', note: 'Осмотр с демонстрационными показаниями',
    provenance: {source: 'camera_inspection', telemetry: 'demo_scenario'},
    evidence: {analysisId: evidenceId, imageUrl: 'javascript:alert(1)',
      result: {model: 'LocalModel', observation: '<img id="unsafe" src=x onerror=alert(1)>'}}},
  {...base, id: 'camera-none', scenarioId: null, vision: {guard_issue: true, seal_leak: null},
    provenance: {source: 'camera_inspection', telemetry: 'none'},
    evidence: {analysisId: evidenceId, result: {model: 'LocalModel'}},
    findings: [{severity: 'critical', title: 'Нарушение защиты муфты'}]},
  {...base, id: 'old-demo', capturedAt: '2026-11-10T16:00:00Z', scenarioId: 'coupling_guard', note: 'Старая запись', provenance: {}, evidence: {}},
];
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const json = data => {res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify(data));};
  if (pathname === '/api/scenarios') return json({passport: {serial: 'P-102', model: 'Demo pump', serviceLife: '—', nextService: '—'},
    qrAvailable: false, scenarios: [{id: 'coupling_guard', label: 'Кожух муфты'}]});
  if (pathname === '/api/history') return json({records});
  const files = {'/checkpoint.html': 'text/html', '/checkpoint.js': 'application/javascript', '/bootstrap.js': 'application/javascript',
    '/styles.css': 'text/css', '/checkpoint.css': 'text/css', '/workspace.css': 'text/css', '/shell.js': 'application/javascript'};
  if (files[pathname] && fs.existsSync(path.join(root, 'web', pathname))) {
    res.writeHead(200, {'Content-Type': files[pathname]}); res.end(fs.readFileSync(path.join(root, 'web', pathname))); return;
  }
  res.writeHead(404); res.end();
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    for (const [engine, options, name] of [
      [chromium, process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {}, 'chromium'],
      [webkit, {}, 'webkit'],
    ]) {
      const browser = await engine.launch(options);
      try {
        const page = await browser.newPage(devices['iPhone 13']);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`http://127.0.0.1:${server.address().port}/checkpoint.html?serial=P-102`);
        await page.getByText('Старая запись', {exact: true}).waitFor();
        const entries = page.locator('#history-records .history-entry');
        assert.equal(await entries.count(), 3);
        const days = page.locator('#history-records > .history-day-group');
        assert.equal(await days.count(), 2);
        assert.equal(await days.first().locator('.history-entry').count(), 2);
        assert.equal(await days.last().locator('.history-entry').count(), 1);
        assert.equal(await page.locator('.journal-tools').getAttribute('open'), null);
        const demoCamera = await entries.nth(0).textContent();
        assert.match(demoCamera, /Осмотр с камеры/);
        assert.match(demoCamera, /Пример показаний · Кожух муфты/);
        await entries.nth(0).getByText('Данные осмотра', {exact:true}).click();
        assert.match(await entries.nth(0).locator('details').innerText(), /LocalModel/);
        assert.match(await entries.nth(0).locator('details').innerText(), /Оборудование выбрано инженером/);
        await entries.nth(0).getByText('Данные осмотра', {exact:true}).click();
        assert.match(demoCamera, /Признаки инженером не оценены/);
        assert.match(demoCamera, /Неоценённые признаки остаются неизвестными/);
        assert.equal(await entries.nth(0).locator('a').getAttribute('href'), `/api/analyses/${evidenceId}/image`);
        assert.equal(await page.locator('#unsafe').count(), 0);
        const noSensors = await entries.nth(1).textContent();
        assert.match(noSensors, /Показаний датчиков нет/);
        assert.match(noSensors, /Проблема с защитным кожухом: Да/);
        assert.doesNotMatch(noSensors, /Влага в зоне уплотнения: Нет/);
        assert.match(await entries.nth(2).textContent(), /Пример показаний/);
        assert.equal(await entries.nth(2).locator('strong').textContent(), 'Кожух муфты');
        assert.deepEqual(errors, []);
        console.log(`${name}: PASS (camera+demo, no telemetry, preserved legacy history, unknown flags, safe evidence link)`);
      } finally {await browser.close();}
    }
  } finally {await new Promise(resolve => server.close(resolve));}
})().catch(error => {console.error(error); process.exitCode = 1;});
