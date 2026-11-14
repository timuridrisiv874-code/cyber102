const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const http = require('node:http');
const path = require('node:path');
const { once } = require('node:events');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const localResult = {
  ...JSON.parse(fs.readFileSync(path.join(root, 'web/mock-analysis.json'), 'utf8')),
  model: 'cyber102-detector-test', source: 'local_vision', observation: 'Проверка контракта модели',
  analysisId: 'fixture-analysis-1', durationSeconds: 0.5, profile: 'fast', observationKind: 'template', metrics: { generateMs: 400 },
};
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg', '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png', '.ico': 'image/x-icon' };
function makeServer() {
  const state = { analyses: [], previews: [], saves: [], next: 'busy-once', saveAttempts: 0, android: false };
  const reply = (response, status, data, extra = {}) => {
    response.writeHead(status, { 'Content-Type': 'application/json', ...extra });
    response.end(JSON.stringify(data));
  };
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://localhost');
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (url.pathname === '/analyze') {
      state.analyses.push(body.toString('latin1'));
      if (state.next === 'busy-once') {
        state.next = 'success';
        return reply(response, 503, { error: 'GPU busy', code: 'vision_busy' }, { 'Retry-After': '0.02' });
      }
      if (state.next === 'busy-limit') return reply(response, 503, { error: 'GPU busy', code: 'vision_busy' }, { 'Retry-After': '0.02' });
      if (state.next === 'busy') return reply(response, 503, { error: 'GPU busy', code: 'vision_busy' }, { 'Retry-After': '2' });
      if (state.next === 'error') return reply(response, 503, { error: 'Модель недоступна' });
      return reply(response, 200, { ...localResult, analysisId: `fixture-analysis-${state.analyses.length}` });
    }
    if (url.pathname === '/api/scenarios') return reply(response, 200, {
      passport: { serial: url.searchParams.get('serial'), model: 'Насос P-102' },
      scenarios: [{ id: 'healthy', label: 'Нормальные показания' }],
    });
    if (url.pathname === '/api/inspections/preview') {
      state.previews.push(JSON.parse(body));
      return reply(response, 200, {
        findings: [{ id: 'coupling_guard', title: 'Нарушение защиты муфты', diagnosis: 'Подтверждено инженером', recommendation: 'Проверить защитный кожух', evidence: ['guard_issue=true'] }],
        provenance: { telemetry: JSON.parse(body).telemetrySource.kind },
      });
    }
    if (url.pathname === '/api/inspections') {
      state.saves.push(JSON.parse(body));
      if (state.saveAttempts++ === 0) return reply(response, 503, { error: 'Ответ не подтверждён' });
      return reply(response, 200, { record: { id: 'saved-inspection-1' }, created: false });
    }
    let assetRoot = path.join(root, 'web');
    let assetPath = url.pathname;
    if (state.android && (url.pathname === '/index.html' || url.pathname.startsWith('/__mobile_inspection/'))) {
      assetRoot = path.join(root, 'android/app/src/main/assets/inspection');
      assetPath = url.pathname.replace('/__mobile_inspection/', '/');
    }
    const asset = path.resolve(assetRoot, `.${assetPath}`);
    if (!asset.startsWith(assetRoot + path.sep) || !mime[path.extname(asset)] || !fs.existsSync(asset)) return reply(response, 404, {});
    response.writeHead(200, { 'Content-Type': mime[path.extname(asset)] });
    response.end(fs.readFileSync(asset));
  });
  return { state, server };
}
async function camera(page) {
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'mediaDevices', { value: { async getUserMedia() {
      const canvas = document.createElement('canvas');
      canvas.width = 640; canvas.height = 360;
      const ctx = canvas.getContext('2d');
      const paint = () => { ctx.fillStyle = '#557766'; ctx.fillRect(0, 0, 640, 360); };
      paint();
      window.cameraTimer = setInterval(paint, 50);
      window.cameraStream = canvas.captureStream(20);
      return window.cameraStream;
    } } });
  });
}
async function run(browser, engine, state, server, android) {
  Object.assign(state, { analyses: [], previews: [], saves: [], next: 'busy-once', saveAttempts: 0, android });
  const page = await browser.newPage(devices['iPhone 13']);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.setDefaultTimeout(10000);
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html${android ? '' : '?example=1'}`);
    if (android) {
      await page.locator('.capture-settings').filter({ has: page.locator('#camera-options') }).locator('summary').click();
    } else {
      assert.equal(await page.locator('#analysis-settings').isVisible(), false, 'Analysis settings stay hidden before a source is ready');
      assert.equal(await page.locator('#check').isVisible(), false, 'No analysis action before selecting a source');
      await page.locator('#demo-options > summary').click();
      await page.locator('#show-example').click();
      await page.locator('#check').click();
      await page.locator('#details').waitFor();
      assert.equal(await page.locator('#inspection-workflow').isVisible(), false, 'Fixture cannot become a real inspection');
    }
    await camera(page);
    await page.locator('#camera').click();
    await page.locator('#video').waitFor();
    if (android) await page.locator('#analysis-profile').selectOption('fast');
    await page.locator('#check').click();
    await page.locator('#inspection-preview:not([disabled])').waitFor();
    assert.equal(state.analyses.length, 2, 'Typed GPU busy gets one retry');
    const frameHash = body => createHash('sha256').update(Buffer.from(body.slice(body.indexOf('ÿØ'), body.lastIndexOf('ÿÙ') + 2), 'latin1')).digest('hex');
    assert.equal(frameHash(state.analyses[0]), frameHash(state.analyses[1]), 'Retry must keep the identical JPEG');
    assert.ok(state.analyses[0].includes('name="profile"\r\n\r\n' + (android ? 'fast' : 'standard')));
    if (android) {
      await page.locator('#analysis-timing').getByText('Ответ на телефоне:', { exact: false }).waitFor();
      await page.locator('#analysis-timing').getByText('Сервер:', { exact: false }).waitFor();
      await page.locator('#vision-observation').getByText('cyber102-detector-test', { exact: false }).waitFor();
      await page.locator('#vision-observation').getByText('описание по классу', { exact: false }).waitFor();
    } else {
      await page.locator('#vision-observation').getByText('Проверка контракта модели', {exact:true}).waitFor();
      await page.locator('.result-provenance > summary').click();
      assert.ok(!(await page.locator('.result-provenance').innerText()).includes(localResult.model));
      assert.equal(await page.getByText(/Ответ на телефоне|подробный режим/i).count(), 0);
      assert.ok((await page.locator('#equipment-id').innerText()).trim());
      await page.locator('.result-provenance > summary').click();
    }
    assert.equal(await page.locator('#inspection-foundation_issue').inputValue(), '', 'Corrosion does not auto-confirm a foundation defect');
    assert.equal(await page.locator('#inspection-seal_leak').inputValue(), '');
    assert.equal(await page.locator('#inspection-telemetry').inputValue(), '', 'No demo telemetry is assumed');
    assert.equal(await page.locator('#inspection-save').isDisabled(), true);
    await page.locator('#inspection-telemetry').selectOption('healthy');
    if (!android) {
      await page.locator('#detection').tap();
      await page.locator('#inspection-closeup').waitFor();
      await page.locator('#closeup-file').setInputFiles(path.join(root, 'web/demo-pump-clean.jpg'));
      await page.locator('#closeup-image').waitFor();
      await page.locator('#closeup-label').fill('Выходной фланец');
      await page.locator('#closeup-defect').selectOption('corrosion');
    }
    await page.locator('#inspection-confirmations').locator('..').evaluate((node) => node.open = true);
    await page.locator('#inspection-guard_issue').selectOption('true');
    await page.locator('#inspection-preview').click();
    await page.locator('#inspection-save:not([disabled])').waitFor();
    const payload = state.previews[0];
    assert.equal(payload.confirmedVision.guard_issue, true);
    assert.equal('closeup' in payload, false, 'Diagnosis preview does not upload the close-up');
    assert.equal(payload.confirmedVision.seal_leak, null);
    assert.deepEqual(payload.telemetrySource, { kind: 'demo_scenario', scenarioId: 'healthy' });
    await page.locator('#inspection-diagnosis-source').getByText(android ? 'демонстрационная телеметрия' : 'Показания из примера', { exact: false }).waitFor();
    const layout = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth,
      overflowing: [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right + scrollX > innerWidth + 1 || node.scrollWidth > node.clientWidth + 1)
        .slice(0, 15).map(node => ({ tag: node.tagName, id: node.id, class: node.className, right: node.getBoundingClientRect().right, scroll: node.scrollWidth, client: node.clientWidth })) }));
    if (layout.content > layout.width) await page.screenshot({ path: '/private/tmp/cyber102-inspection-design-overflow.png', fullPage: true });
    assert.equal(layout.content <= layout.width, true, `Functional form must fit mobile width: ${JSON.stringify(layout)}`);
    await page.locator('#inspection-save').click();
    await page.getByRole('button', { name: 'Повторить сохранение' }).waitFor();
    await page.locator('#inspection-save').click();
    await page.getByRole('button', { name: 'Осмотр сохранён' }).waitFor();
    assert.equal(state.saves.length, 2);
    assert.deepEqual(state.saves[0], state.saves[1], 'Save retry keeps exact payload/requestId');
    if (!android) {
      assert.equal(state.saves[0].closeup.componentLabel, 'Выходной фланец');
      assert.equal(state.saves[0].closeup.defectType, 'corrosion');
      assert.equal(Buffer.from(state.saves[0].closeup.image, 'base64').subarray(0, 2).toString('hex'), 'ffd8');
      const point = state.saves[0].closeup.point;
      assert.ok(point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1);
    }
    assert.equal(await page.locator('#inspection-history').getAttribute('href'), '/checkpoint.html?serial=P-102' + (android ? '' : '&record=saved-inspection-1'));
    await page.getByRole('button', { name: 'Новый кадр' }).click();
    assert.equal(await page.locator('#inspection-workflow').isVisible(), false);
    state.next = 'busy';
    await page.locator('#check').click();
    await page.locator('#message').getByText('Модель занята.', { exact: false }).waitFor();
    const busyCount = state.analyses.length;
    await page.locator('#camera').click();
    await delay(2200);
    assert.equal(state.analyses.length, busyCount, 'Camera stop aborts busy retry timer');
    await page.locator('#camera').click();
    state.next = 'error';
    await page.locator('#check').click();
    await page.locator('#message').getByText('Модель недоступна', { exact: false }).waitFor();
    const errorCount = state.analyses.length;
    await delay(2100);
    assert.equal(state.analyses.length, errorCount, 'Generic 503 must not be retried');
    state.next = 'busy-limit';
    const beforeLimit = state.analyses.length;
    await page.locator('#check').click();
    await page.locator('#message').getByText('GPU busy', { exact: true }).waitFor();
    assert.equal(state.analyses.length - beforeLimit, 4, 'At most three retries after initial request');
    await delay(100);
    assert.equal(state.analyses.length - beforeLimit, 4, 'Retry budget cannot restart');
    assert.deepEqual(errors, []);
    console.log(`${engine} ${android ? 'Android bundle' : 'web'}: PASS — profile, retries/abort, evidence, unknown flags, demo provenance, idempotent save`);
  } finally { await page.close(); }
}
(async () => {
  const { state, server } = makeServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    for (const [engine, name, options] of [
      [chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}],
      [webkit, 'WebKit', {}],
    ]) {
      if (process.env.BROWSER_ENGINE && process.env.BROWSER_ENGINE !== name) continue;
      const browser = await engine.launch(options);
      try { for (const android of [false, true]) await run(browser, name, state, server, android); }
      finally { await browser.close(); }
    }
  } finally { server.close(); server.closeAllConnections(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
