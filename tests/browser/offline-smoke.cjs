// Shared camera/file UI in the exact offline origin; native inference is mocked.
// Native model/runtime tests are separate. No live model/server requests here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const origin = 'https://appassets.androidplatform.net';
const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.jpg': 'image/jpeg', '.json': 'application/json' };

async function run(browser, name, android) {
  const model = android ? 'LFM3-VL-2B' : name === 'WebKit' ? 'SmolVLM-500M-Instruct' : 'SmolVLM-256M-Instruct';
  const modelBytes = android ? 1478623840 : name === 'WebKit' ? 546000000 : 278824384;
  const page = await browser.newPage({ ...devices['iPhone 13'], defaultBrowserType: undefined });
  page.setDefaultTimeout(15000);
  const errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.protocol === 'blob:' || url.protocol === 'data:') return route.continue();
    if (url.origin !== origin || !url.pathname.startsWith('/offline/') && !url.pathname.startsWith('/__mobile_inspection/')) {
      unexpected.push(url.pathname); return route.abort();
    }
    const name = url.pathname.replace(/^\/(?:offline|__mobile_inspection)\//, '');
    const base = path.join(root, android ? 'android/app/src/main/assets/inspection' : 'web');
    const asset = path.resolve(base, name);
    if (!asset.startsWith(base + path.sep) || !fs.existsSync(asset) || !mime[path.extname(asset)]) return route.fulfill({ status: 404, body: '' });
    await route.fulfill({ contentType: mime[path.extname(asset)], body: fs.readFileSync(asset),
      headers: { 'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'" } });
  });
  await page.addInitScript(({ model, modelBytes, android }) => {
    window.mockSmol = { loaded: false, busy: false, calls: [], cancelled: 0, delay: 50 };
    const snapshot = () => ({ type: 'status', loaded: mockSmol.loaded, busy: mockSmol.busy,
      state: mockSmol.loaded ? 'ready' : 'idle', available: true, message: 'Native mock state', progress: 100, model, modelBytes });
    window.CyberSmolNative = {
      state: () => JSON.stringify(snapshot()),
      load: () => { mockSmol.loaded = true; setTimeout(() => CyberOffline.onNativeEvent(snapshot()), 0); },
      importModel: () => {},
      cancel: () => { mockSmol.cancelled++; mockSmol.busy = false; setTimeout(() => CyberOffline.onNativeEvent(snapshot()), 0); },
      analyze: (requestId, image) => {
        mockSmol.calls.push({ requestId, image }); mockSmol.busy = true;
        CyberOffline.onNativeEvent({ ...snapshot(), state: 'analyzing' });
        setTimeout(() => {
          mockSmol.busy = false;
          CyberOffline.onNativeEvent({ type: 'result', requestId, model, modelVersion: 'fixture-revision',
            result: { text: android
              ? (mockSmol.answer || 'The metal pipe has a long open crack near the joint.')
              : '<script>alert("unsafe")</script> A blue machine with a visible surface.',
              russian: android ? (mockSmol.ru === undefined ? 'На металлической трубе возле соединения видна длинная открытая трещина.' : mockSmol.ru) : undefined,
              translatorModel: android ? 'fixture-enru' : undefined, translationMs: 810, translationLoadMs: 240,
              truncated: mockSmol.truncated === true, totalMs: 200, promptMs: 150, generateMs: 50,
              generatedTokens: 12, inputTokens: 89, cancelled: false } });
          CyberOffline.onNativeEvent(snapshot());
        }, mockSmol.delay);
      },
    };
  }, { model, modelBytes, android });
  try {
    await page.goto(origin + '/offline/index.html');
    await page.locator('#offline-load:disabled').waitFor();
    if (name === 'Chromium' && android) {
      fs.mkdirSync('/private/tmp/cyber102-design-preview', { recursive: true });
      await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
      await page.screenshot({ path: '/private/tmp/cyber102-design-preview/android-offline-start.png', fullPage: true });
    }
    assert.equal(await page.evaluate(() => CyberOffline.enabled), true);
    assert.ok((await page.locator('#offline-model-description').textContent()).includes(model));
    if (android) assert.match(await page.locator('#offline-model-description').textContent(), /1,48 ГБ/);
    assert.equal(await page.locator('.active-equipment-bar').isVisible(), false);
    assert.equal(await page.locator('#demo-options').isVisible(), false);
    await page.locator('#media-file').setInputFiles(path.join(root, 'web/demo-pump.jpg'));
    await page.locator('#check:not([disabled])').waitFor();
    await page.locator('#check').click();
    await page.locator('#offline-result:not([hidden])').waitFor();
    if (android) {
      assert.match(await page.locator('#offline-result .offline-observation').textContent(), /длинная открытая трещина/);
      assert.equal(await page.locator('#offline-result .offline-observation').getAttribute('lang'), 'ru');
      assert.match(await page.locator('#offline-result .video-metrics').textContent(), /Перевод 0,81 с/);
      const original = page.locator('#offline-result .offline-original-text');
      assert.equal(await original.isVisible(), false);
      await page.locator('#offline-result .offline-original > summary').click();
      assert.equal(await original.isVisible(), true);
      assert.equal(await original.textContent(), 'The metal pipe has a long open crack near the joint.');
    } else {
      assert.match(await page.locator('#offline-result').textContent(), /Исходный ответ модели на английском/);
      assert.match(await page.locator('#offline-result').textContent(), /<script>alert/);
    }
    assert.ok((await page.locator('#offline-result h3').textContent()).includes(model));
    assert.equal(await page.locator('#offline-result script').count(), 0);
    if (android) assert.equal(await page.locator('#offline-result .offline-checked-frame').isVisible(), true);
    assert.equal(await page.locator('#detection').isVisible(), false);
    assert.equal(await page.locator('#inspection-workflow').isVisible(), false);
    assert.equal(await page.locator('#details').isVisible(), false);
    await page.locator('#offline-note').fill('Подтверждение инженера: требуется осмотр поверхности.');
    await page.evaluate(() => render());
    assert.match(await page.locator('#offline-note').inputValue(), /Подтверждение инженера/);
    await page.evaluate(() => {
      localStorage.setItem('cyber102.offline.inspections.v1', JSON.stringify(Array.from({ length: 10 }, (_, index) => ({
        id: 'old_' + index, source: 'on_device', observation: 'Previous observation', note: '', createdAt: new Date().toISOString(),
      }))));
    });
    await page.locator('#offline-save').click();
    const history = await page.evaluate(() => JSON.parse(localStorage.getItem('cyber102.offline.inspections.v1')));
    assert.equal(history.length, 10);
    assert.equal(history[0].source, 'on_device');
    assert.match(history[0].note, /Подтверждение инженера/);
    assert.equal(history[0].modelVersion, 'fixture-revision');
    if (android) {
      assert.equal(history[0].observation, 'На металлической трубе возле соединения видна длинная открытая трещина.');
      assert.equal(history[0].originalEnglish, 'The metal pipe has a long open crack near the joint.');
      assert.equal(history[0].observationLanguage, 'ru');
      assert.equal(history[0].translatorModel, 'fixture-enru');
      assert.equal(history[0].translationMs, 810);
    }
    assert.equal(history[0].model, model);
    assert.ok((await page.locator('#offline-history').textContent()).includes(model));
    assert.equal(history[9].id, 'old_8');
    assert.equal(history.some(record => record.image || record.base64), false);
    assert.equal(await page.locator('#offline-save').isDisabled(), true);
    if (name === 'Chromium' && android) {
      await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
      await page.screenshot({ path: '/private/tmp/cyber102-design-preview/android-offline-result.png', fullPage: true });
    }
    const calls = await page.evaluate(() => mockSmol.calls);
    assert.equal(calls.length, 1);
    assert.equal(Buffer.from(calls[0].image, 'base64').subarray(0,2).toString('hex'), 'ffd8');
    assert.ok(Buffer.from(calls[0].image, 'base64').length < 1536 * 1024);
    assert.match(await page.locator('#mode').textContent(), /На телефоне/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    await page.evaluate(() => { mockSmol.delay = 2000; });
    await page.locator('#check').click();
    await page.waitForFunction(() => mockSmol.calls.length === 2);
    await page.locator(android ? '#offline-frame-cancel' : '#offline-cancel').click();
    await page.waitForFunction(() => mockSmol.cancelled > 0);
    await page.locator('#check:not([disabled])').waitFor();
    assert.equal(await page.locator('#offline-result').isVisible(), false);
    // Late native output after cancellation cannot replace the current result.
    await page.waitForTimeout(2100);
    assert.equal(await page.locator('#offline-result').isVisible(), false);
    if (android) {
      for (const testCase of [
        { answer: 'No crack is visible.', ru: 'Трещина не видна.' },
        { answer: 'No equipment is visible.', ru: '' },
        { answer: 'The surface has', ru: 'На поверхности есть', truncated: true },
      ]) {
        await page.evaluate(fixture => Object.assign(mockSmol, fixture, { delay: 30 }), testCase);
        await page.locator('#check:not([disabled])').click();
        await page.locator('#offline-result:not([hidden])').waitFor();
        assert.equal(await page.locator('#offline-result .offline-observation').textContent(), testCase.ru || testCase.answer);
        assert.equal(await page.locator('#offline-result .offline-original').count(), testCase.ru ? 1 : 0);
        if (testCase.ru) assert.equal(await page.locator('#offline-result .offline-original-text').textContent(), testCase.answer);
        assert.equal(await page.locator('#offline-result .offline-warning').count(), testCase.truncated || !testCase.ru ? 1 : 0);
      }
    }
    assert.deepEqual(unexpected, []);
    assert.deepEqual(errors, []);
    console.log(`${name} ${android ? 'Android assets' : 'web'}: PASS — offline origin, no HTTP/API, real JPEG, native result, no false boxes, bounded local history, cancellation/stale result, safe text, narrow viewport`);
  } catch (error) {
    error.message += '\nOffline UI: ' + JSON.stringify(await page.evaluate(() => ({
      message: document.getElementById('message')?.textContent,
      model: document.getElementById('offline-status')?.textContent,
      imageWidth: document.getElementById('uploaded-image')?.naturalWidth,
      imageSource: document.getElementById('uploaded-image')?.src,
      checkHidden: document.getElementById('check')?.hidden,
      checkDisabled: document.getElementById('check')?.disabled,
    }))) + '\nErrors: ' + JSON.stringify(errors) + '\nNetwork: ' + JSON.stringify(unexpected);
    throw error;
  } finally { await page.close(); }
}

(async () => {
  for (const [engine, name, options] of [[chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}], [webkit, 'WebKit', {}]]) {
    if (process.env.OFFLINE_TEST_ENGINE && process.env.OFFLINE_TEST_ENGINE !== name) continue;
    const browser = await engine.launch(options);
    try { for (const android of process.env.OFFLINE_TEST_ANDROID_ONLY === '1' ? [true] : [false, true]) await run(browser, name, android); }
    finally { await browser.close(); }
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
