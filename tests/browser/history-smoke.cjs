// Rendering test only: local fixtures, no production writes or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const evidenceId = '11111111-1111-4111-8111-111111111111';
const missingId = '22222222-2222-4222-8222-222222222222';
const syntheticNote = 'Автопроверка релиза 0.6: синтетическое фото, ручные демонстрационные значения.';
const engineerNote = 'После автопроверки осмотреть крепление со стороны муфты.';
const base = {serial: 'P-102', capturedAt: '2026-11-13T16:00:00Z', note: '', telemetry: {}, vision: {}, findings: []};
const records = [
  {...base, id: 'camera-demo', scenarioId: 'coupling_guard',
    provenance: {source: 'camera_inspection', telemetry: 'demo_scenario'},
    evidence: {analysisId: evidenceId, imageUrl: 'javascript:alert(1)',
      result: {model: 'LocalModel', box: {x: 0.125, y: 0.25, width: 0.5, height: 0.25},
        observation: '<img id="unsafe" src=x onerror=alert(1)>'}}},
  {...base, id: 'camera-none', scenarioId: null, note: engineerNote,
    vision: {guard_issue: true, terminal_damage: false, seal_leak: null},
    provenance: {source: 'camera_inspection', captureSource: 'uploaded_video', telemetry: 'none'},
    evidence: {analysisId: missingId, result: {model: 'LocalModel', media: {videoTimeSeconds: 4.39}}},
    findings: [{severity: 'critical', title: 'Нарушение защиты муфты'}]},
  {...base, id: 'release-check', note: syntheticNote, telemetry: {hours: 150},
    provenance: {source: 'camera_inspection', captureSource: 'uploaded_photo', telemetry: 'manual'},
    evidence: {analysisId: evidenceId, result: {model: 'OpenGVLab/InternVL3-2B-hf', component: 'Корпус насоса',
      finding: 'Возможные признаки коррозии', observation: 'Корпус насоса: возможные признаки коррозии.'}}},
  {...base, id: 'old-demo', capturedAt: '2026-11-10T16:00:00Z', scenarioId: 'coupling_guard', note: 'Старая запись', provenance: {}, evidence: {}},
];
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const json = data => {res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify(data));};
  if (pathname === '/api/scenarios') return json({passport: {serial: 'P-102', model: 'Demo pump', serviceLife: '—', nextService: '—'},
    qrAvailable: false, scenarios: [{id: 'coupling_guard', label: 'Кожух муфты'}]});
  if (pathname === '/api/history') return json({records});
  if (pathname === `/api/analyses/${evidenceId}/image`) {
    res.writeHead(200, {'Content-Type': 'image/jpeg'});
    res.end(fs.readFileSync(path.join(root, 'web/demo-pump.jpg')));
    return;
  }
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
        const journalURL = `http://127.0.0.1:${server.address().port}/checkpoint.html?serial=P-102`;
        await page.goto(journalURL);
        await page.getByText('Старая запись', {exact: true}).waitFor();
        const entries = page.locator('#history-records .history-entry');
        assert.equal(await entries.count(), 4);
        const days = page.locator('#history-records > .history-day-group');
        assert.equal(await days.count(), 2);
        assert.equal(await days.first().locator('.history-entry').count(), 3);
        assert.equal(await days.last().locator('.history-entry').count(), 1);
        assert.equal(await page.locator('.journal-tools').getAttribute('open'), null);
        const demoCamera = await entries.nth(0).textContent();
        assert.match(demoCamera, /Пример показаний · Кожух муфты/);
        assert.equal(await page.locator('#unsafe').count(), 0);
        assert.ok(demoCamera.includes('<img id="unsafe" src=x onerror=alert(1)>'), 'Keep the original observation as plain text');
        const allHistory = await page.locator('#history-records').textContent();
        for (const removed of [syntheticNote, 'Показания введены вручную', 'Оборудование выбрано инженером',
          'Признаки инженером не оценены', 'OpenGVLab/InternVL3-2B-hf', 'LocalModel']) {
          assert.ok(!allHistory.includes(removed), `Journal still renders: ${removed}`);
        }
        const videoEntry = entries.nth(1);
        assert.ok((await videoEntry.textContent()).includes(engineerNote));
        await videoEntry.getByText('Данные осмотра', {exact:true}).click();
        const details = await videoEntry.locator('details').innerText();
        assert.match(details, /Проблема с защитным кожухом.*Да/);
        assert.match(details, /Следы повреждения клемм.*Нет/);
        assert.match(details, /4[,.]39/);
        assert.doesNotMatch(details, /Влага в зоне уплотнения.*Нет/);
        const manualEntry = entries.nth(2);
        const manualText = await manualEntry.textContent();
        assert.equal((manualText.match(/возможные признаки коррозии/gi) || []).length, 1);
        assert.match(manualText, /Корпус насоса/i);
        await manualEntry.getByText('Данные осмотра', {exact:true}).click();
        assert.match(await manualEntry.locator('details').innerText(), /Наработка.*150/);
        assert.match(await entries.last().textContent(), /Пример показаний/);
        assert.equal(await entries.last().locator('strong').textContent(), 'Кожух муфты');

        const open = entries.first().getByText('Открыть кадр', {exact:true});
        await open.click();
        const dialog = page.getByRole('dialog', {name: /.+/});
        await dialog.waitFor();
        await page.waitForFunction(url => Array.from(document.querySelectorAll('dialog img,[role="dialog"] img'))
          .some(image => image.naturalWidth > 0 && new URL(image.currentSrc).pathname === url),
          `/api/analyses/${evidenceId}/image`);
        assert.equal(page.url(), journalURL);
        assert.equal(page.context().pages().length, 1, 'Viewing a frame must keep the current journal tab');
        const toggle = dialog.getByRole('button', {name: 'Рамка узла'});
        const marker = dialog.locator('.journal-frame-marker');
        assert.equal(await toggle.getAttribute('aria-pressed'), 'false');
        assert.equal(await marker.isVisible(), false);
        await toggle.click();
        await marker.waitFor();
        assert.equal(await toggle.getAttribute('aria-pressed'), 'true');
        const pictureBox = await dialog.locator('img').boundingBox();
        const markerBox = await marker.boundingBox();
        for (const [actual, expected] of [[markerBox.x, pictureBox.x + pictureBox.width / 8],
          [markerBox.y, pictureBox.y + pictureBox.height / 4], [markerBox.width, pictureBox.width / 2],
          [markerBox.height, pictureBox.height / 4]]) {
          assert.ok(Math.abs(actual - expected) < 2, 'Marker must align with the displayed photo on a narrow viewport');
        }
        await page.keyboard.press('Escape');
        await dialog.waitFor({state: 'hidden'});
        assert.equal(await open.evaluate(button => document.activeElement === button), true);

        const broken = videoEntry.getByText('Открыть кадр', {exact:true});
        await broken.click();
        await dialog.getByRole('status').getByText('Не удалось загрузить кадр.', {exact:true}).waitFor();
        await dialog.getByRole('button', {name: /закрыть/i}).click();
        await dialog.waitFor({state: 'hidden'});
        assert.equal(await page.getByRole('status').filter({hasText:'Не удалось загрузить кадр.'}).count(), 0);
        assert.equal(await broken.evaluate(button => document.activeElement === button), true);
        await manualEntry.getByText('Открыть кадр', {exact:true}).click();
        await dialog.locator('img').waitFor({state: 'visible'});
        assert.equal(await toggle.isVisible(), false, 'A frame without saved geometry must have no overlay control');
        assert.equal((await dialog.innerText()).match(/не удалось|недоступ|не загруз/i), null);
        assert.deepEqual(errors, []);
        console.log(`${name}: PASS (concise history, preserved notes and evidence, inline photo, focus, image failure and recovery)`);
      } finally {await browser.close();}
    }
  } finally {await new Promise(resolve => server.close(resolve));}
})().catch(error => {console.error(error); process.exitCode = 1;});
