// Shared Android/web layout and animation checks. Demo responses are labelled fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { chromium, webkit } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const screenshots = process.env.DESIGN_SMOKE_SCREENSHOTS;
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg', '.json': 'application/json' };
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost');
  if (url.pathname === '/api/capabilities') {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ vision: { profiles: ['standard', 'fast'], videoReview: false } }));
    return;
  }
  const android = url.pathname === '/android.html' || url.pathname.startsWith('/__mobile_inspection/');
  const base = path.join(root, android ? 'android/app/src/main/assets/inspection' : 'web');
  const relative = url.pathname === '/android.html' ? '/index.html' : url.pathname.replace('/__mobile_inspection/', '/');
  const target = path.resolve(base, '.' + relative);
  if (!target.startsWith(base + path.sep) || !mime[path.extname(target)] || !fs.existsSync(target)) {
    response.writeHead(404); response.end(); return;
  }
  response.writeHead(200, { 'Content-Type': mime[path.extname(target)] });
  response.end(fs.readFileSync(target));
});

async function run(browser, engine, android, width, reducedMotion) {
  const page = await browser.newPage({ viewport: { width, height: width > 740 ? 950 : 844 }, reducedMotion });
  const errors = [], failed = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { if (!response.ok()) failed.push([response.status(), new URL(response.url()).pathname]); });
  page.on('request', request => requests.push(new URL(request.url())));
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(`${origin}/${android ? 'android.html' : 'index.html'}`);
    await page.waitForFunction(() => typeof window.CyberInspection?.update === 'function');
    await page.locator('#camera-idle').waitFor();
    await page.waitForTimeout(450); // Bounded entry animation must settle; idle has no animation loop.
    assert.equal(await page.locator('#check').isDisabled(), true, 'No analysis before a source is chosen');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    assert.equal(await page.locator('.app-nav a').count(), android ? 5 : 4, 'Shared navigation keeps equipment and photos in one web section');
    if (!android) {
      assert.equal(await page.locator('.app-nav a[href*="materials.html"]').count(), 0);
      assert.equal(await page.locator('.app-nav a[href*="documents.html"]').count(), 1);
    }
    assert.equal(await page.evaluate(() => document.getAnimations().filter(animation => animation.playState === 'running').length), 0,
      'Idle interface must not continuously animate');
    if (reducedMotion === 'reduce') {
      assert.equal(await page.locator('.capture').evaluate(node => getComputedStyle(node).animationName), 'none');
      assert.equal(await page.locator('html').evaluate(node => getComputedStyle(node).scrollBehavior), 'auto');
    } else {
      assert.equal(await page.locator('.capture').evaluate(node => getComputedStyle(node).animationIterationCount), '1');
    }
    if (screenshots && !reducedMotion && engine === 'Chromium') {
      fs.mkdirSync(screenshots, { recursive: true });
      await page.evaluate(() => { document.activeElement?.blur(); scrollTo({ top: 0, behavior: 'instant' }); });
      await page.screenshot({ path: path.join(screenshots, `${android ? 'android' : 'web'}-${width}-start.png`), fullPage: true });
    }
    await page.locator('#demo-options > summary').click();
    await page.locator('#show-example').click();
    await page.locator('#check:not([disabled])').waitFor();
    await page.locator('#check').click();
    await page.locator('#details').waitFor();
    assert.equal(await page.locator('#inspection-workflow').isVisible(), false, 'Fixtures cannot become saved real inspections');
    assert.match(await page.locator('#mode').textContent(), /Тестовый пример/);
    assert.equal(await page.locator('#result').getAttribute('data-result-ready'), 'true');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => document.getAnimations().filter(animation => animation.playState === 'running').length), 0);
    if (screenshots && !reducedMotion && engine === 'Chromium') {
      await page.evaluate(() => { document.activeElement?.blur(); scrollTo({ top: 0, behavior: 'instant' }); });
      await page.screenshot({ path: path.join(screenshots, `${android ? 'android' : 'web'}-${width}-result.png`), fullPage: true });
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(failed, []);
    assert.equal(requests.every(url => url.origin === origin), true, 'No third-party fonts/scripts/services');
    assert.equal(requests.filter(url => url.pathname === '/api/capabilities').length, 1);
    console.log(`${engine} ${android ? 'Android' : 'web'} ${width}px ${reducedMotion || 'motion'}: PASS — layout, assets, animations, explicit demo, no external requests`);
  } finally { await page.close(); }
}

(async () => {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    for (const [engine, name, options] of [[chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}], [webkit, 'WebKit', {}]]) {
      const browser = await engine.launch(options);
      try {
        for (const android of [false, true]) {
          for (const width of [320, 390, 1440]) await run(browser, name, android, width);
          await run(browser, name, android, 390, 'reduce');
        }
      } finally { await browser.close(); }
    }
  } finally { server.close(); server.closeAllConnections(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
