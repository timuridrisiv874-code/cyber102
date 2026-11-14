// Real inspection UI + Python assessment/SQLite; only vision evidence is a fixture.
// Requires Python with project dependencies and Playwright Chromium/WebKit.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');

async function startBackend() {
  const child = spawn(process.env.CYBER102_TEST_PYTHON || 'python3',
    [path.join(__dirname, 'gold_server.py')], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-8000); });
  const lines = readline.createInterface({ input: child.stdout });
  let timer;
  try {
    const fixture = await Promise.race([
      once(lines, 'line').then(([line]) => JSON.parse(line)),
      once(child, 'exit').then(([code]) => { throw new Error(`Fixture backend exited (${code}): ${stderr}`); }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`Fixture backend timed out: ${stderr}`)), 15000); }),
    ]);
    return { child, fixture, stderr: () => stderr };
  } catch (error) {
    child.kill('SIGTERM');
    throw error;
  } finally { clearTimeout(timer); lines.close(); }
}

async function postResult(page, button, pathname) {
  let response;
  try {
    [response] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === pathname && response.request().method() === 'POST'),
      page.locator(button).click({ timeout: 5000 }),
    ]);
  } catch (error) {
    await page.screenshot({ path: process.env.GOLD_SMOKE_FAILURE_SCREENSHOT || path.join(os.tmpdir(), 'cyber102-gold-failure.png') });
    const state = await page.evaluate(() => ({
      message: document.getElementById('inspection-message').textContent,
      invalid: [...document.getElementById('inspection-form').querySelectorAll(':invalid')].map(node => ({ id: node.id, value: node.value, message: node.validationMessage })),
      disabled: document.getElementById('inspection-preview').disabled,
      trace: window.goldTrace,
      viewport: { width: innerWidth, visualWidth: visualViewport.width, scale: visualViewport.scale, scroll: scrollY, docWidth: document.documentElement.scrollWidth },
      rects: ['inspection-form', 'inspection-fields', 'inspection-manual', 'inspection-preview', 'inspection-source'].map(id => ({ id, rect: document.getElementById(id).getBoundingClientRect().toJSON() })),
      buttonCenter: (() => { const rect = document.getElementById('inspection-preview').getBoundingClientRect(); return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)?.outerHTML; })(),
    }));
    throw new Error(`${error.message}\nInspection UI: ${JSON.stringify(state)}`, { cause: error });
  }
  const data = await response.json();
  assert.equal(response.ok(), true, JSON.stringify(data));
  return data;
}

async function invalidateCheck(page, label) {
  assert.equal(await page.locator('#inspection-diagnosis').isVisible(), false, `${label}: old diagnosis must disappear`);
  assert.equal(await page.locator('#inspection-save').isDisabled(), true, `${label}: old preview cannot be saved`);
}

async function activate(page, fixture) {
  // Exercise the public inspection handoff with the evidence pre-saved by the fixture server.
  await page.evaluate(analysis => {
    window.CyberInspection.update(null, false);
    document.body.dataset.inspectionLayout = 'review';
    window.CyberInspection.update(analysis, false);
  }, fixture.analysis);
  await page.locator('#inspection-preview:not([disabled])').waitFor();
}

async function run(browser, engine, fixture, android) {
  const page = await browser.newPage({ ...devices['iPhone 13'], defaultBrowserType: undefined });
  const errors = [];
  let inferenceRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (new URL(request.url()).pathname === '/analyze') inferenceRequests += 1; });
  page.setDefaultTimeout(10000);
  try {
    await page.addInitScript(() => {
      window.goldTrace = [];
      for (const type of ['click', 'input', 'change']) document.addEventListener(type, event => {
        if (event.target.id?.startsWith('inspection-')) window.goldTrace.push(`${type}:${event.target.id}`);
      }, true);
    });
    await page.goto(`${fixture.url}/${android ? 'android.html' : 'index.html?serial=P-102'}`);
    await page.waitForFunction(() => typeof window.CyberInspection?.update === 'function');
    await activate(page, fixture);
    await page.locator('#inspection-telemetry').selectOption('manual');
    await page.locator('#inspection-telemetry-vibration').selectOption('rising');
    await page.locator('#inspection-telemetry-temperature').selectOption('rising');
    assert.equal(await page.locator('#inspection-telemetry-current').inputValue(), '');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true,
      'Manual fields must not expand the mobile viewport or obstruct action buttons');
    const incomplete = await postResult(page, '#inspection-preview', '/api/inspections/preview');
    assert.deepEqual(incomplete.findings, [], 'Missing motor current must not become normal current');
    assert.equal(incomplete.guidance.rules.find(rule => rule.id === 'bearing_lubrication').status, 'insufficient_data');
    assert.ok(incomplete.guidance.nextChecks.some(check => check.field === 'telemetry.current'));
    const nextCurrent = page.locator('#inspection-guidance ol li').filter({ has: page.getByText('Ток двигателя', { exact: true }) });
    await nextCurrent.getByRole('button', { name: 'Внести результат проверки' }).click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'inspection-telemetry-current', 'Next-check button focuses the exact input');
    // A real operator waits for the active field to reach the screen before choosing a value.
    await page.waitForFunction(async () => {
      const field = document.getElementById('inspection-telemetry-current');
      const start = field.getBoundingClientRect();
      if (start.top < 0 || start.bottom > innerHeight) return false;
      for (let frame = 0; frame < 4; frame += 1) {
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (Math.abs(field.getBoundingClientRect().top - start.top) > 0.5) return false;
      }
      return true;
    });
    await invalidateCheck(page, 'Next check');
    await page.locator('#inspection-telemetry-current').selectOption('normal');
    const complete = await postResult(page, '#inspection-preview', '/api/inspections/preview');
    assert.deepEqual(complete.findings.map(finding => finding.id), ['bearing_lubrication']);
    assert.equal(complete.guidance.rules.find(rule => rule.id === 'bearing_lubrication').status, 'triggered');
    await page.locator('#inspection-save:not([disabled])').waitFor();
    await page.locator('#inspection-guidance [data-rule-id="bearing_lubrication"]').locator('..').evaluate(node => { node.open = true; });
    assert.match(await page.locator('#inspection-guidance [data-rule-id="bearing_lubrication"]').textContent(), /Условия выполнены/);
    assert.match(await page.locator('#inspection-diagnosis-source').textContent(), /Кадр загруженного видео/);

    // Editing any observation invalidates the checked preview before saving.
    await page.locator('#inspection-note').fill(`${engine} ${android ? 'Android' : 'web'} functional check`);
    await invalidateCheck(page, 'Edited note');
    await postResult(page, '#inspection-preview', '/api/inspections/preview');
    const saved = await postResult(page, '#inspection-save', '/api/inspections');
    assert.equal(saved.created, true);
    assert.equal(saved.record.provenance.telemetry, 'manual');
    assert.equal(saved.record.provenance.captureSource, 'uploaded_video');
    assert.equal(saved.record.provenance.telemetryEquipmentSerial, 'P-102');
    assert.equal(saved.record.provenance.guidance.rules.find(rule => rule.id === 'bearing_lubrication').status, 'triggered');
    assert.deepEqual(saved.record.evidence.result.media, fixture.analysis.media);
    assert.equal(saved.record.evidence.analysisId, fixture.analysis.analysisId);
    const storedResponse = await page.request.get(`${fixture.url}/api/inspections/${saved.record.id}`);
    assert.equal(storedResponse.status(), 200);
    assert.deepEqual((await storedResponse.json()).record, saved.record, 'History preserves server-owned guidance and media evidence');
    const imageResponse = await page.request.get(`${fixture.url}${saved.record.evidence.imageUrl}`);
    assert.equal(imageResponse.status(), 200);
    assert.equal((await imageResponse.body()).subarray(0, 2).toString('hex'), 'ffd8');

    await activate(page, fixture);
    await page.locator('#inspection-serial').fill('21031004-1');
    await page.locator('#inspection-serial').press('Tab');
    await page.locator('#inspection-component option[value="gearbox"]').waitFor({ state: 'attached' });
    await page.locator('#inspection-preview:not([disabled])').waitFor();
    await page.locator('#inspection-component').selectOption('gearbox');
    await page.locator('#inspection-telemetry').selectOption('manual');
    for (const [key, value] of [['vibration', 'rising'], ['temperature', 'rising'], ['current', 'normal']]) {
      await page.locator(`#inspection-telemetry-${key}`).selectOption(value);
    }
    await page.locator('#inspection-confirmations').locator('..').evaluate(node => { node.open = true; });
    await page.locator('#inspection-guard_issue').selectOption('true');
    const pump = await postResult(page, '#inspection-preview', '/api/inspections/preview');
    assert.deepEqual(pump.findings, [], 'AMS must not inherit demo diagnoses, including the guard rule');
    assert.equal(pump.guidance.applicable, false);
    assert.equal(pump.manufacturer.applicability, 'reference_only');
    const oil = pump.manufacturer.recommendations.find(item => item.id === 'gearbox_oil');
    assert.ok(oil);
    assert.equal(oil.target.serial, 'SAL023020084_001002');
    assert.equal(oil.target.groupNumber, 1);
    assert.match(await page.locator('#inspection-references').textContent(), /SAL023020084_001002/);
    assert.match(await page.locator('#inspection-references blockquote').textContent(), /3,7 л/);
    const pdf = page.locator('#inspection-references a[href="/api/customer/documents/lubricants/pdf#page=1"]');
    assert.equal(await pdf.count(), 1);
    assert.match(await pdf.textContent(), /стр\. 1/);
    assert.match(await page.locator('#inspection-guidance').textContent(), /не валидированы/);

    await page.locator('#inspection-component').selectOption('bearing');
    await invalidateCheck(page, 'Changed component');
    assert.equal(await page.locator('#inspection-references').textContent(), '');
    const bearing = await postResult(page, '#inspection-preview', '/api/inspections/preview');
    assert.deepEqual(bearing.manufacturer.recommendations, []);
    assert.doesNotMatch(await page.locator('#inspection-references').textContent(), /3,7 л|VG220/);
    assert.equal(await page.locator('#inspection-references a').count(), 0);
    assert.equal(inferenceRequests, 0, 'Functional test must never invoke a model');
    assert.deepEqual(errors, []);
    console.log(`${engine} ${android ? 'Android assets' : 'web'}: PASS — next check/focus, manual fusion, immutable history, source metadata, AMS applicability, scoped PDF citation, stale preview`);
  } catch (error) {
    if (errors.length) error.message += `\nBrowser errors: ${errors.join('; ')}`;
    throw error;
  } finally { await page.close(); }
}

(async () => {
  const backend = await startBackend();
  try {
    for (const [engine, name, options] of [
      [chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}],
      [webkit, 'WebKit', {}],
    ]) {
      const browser = await engine.launch(options);
      try { for (const android of [false, true]) await run(browser, name, backend.fixture, android); }
      finally { await browser.close(); }
    }
  } finally {
    backend.child.kill('SIGTERM');
    await Promise.race([once(backend.child, 'exit'), delay(3000).then(() => backend.child.kill('SIGKILL'))]);
    const stderr = backend.stderr();
    if (stderr) console.error(stderr);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
