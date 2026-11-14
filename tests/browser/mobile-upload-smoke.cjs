// Exercise the native file input before deferred scripts finish and after returning to the page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { once } = require('node:events');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const web = path.resolve(__dirname, '../../web');
const photo = path.join(web, 'demo-pump.jpg');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg' };

async function tapAndChoose(page, file) {
  const choice = page.waitForEvent('filechooser');
  await page.locator('.media-upload-control').tap();
  await (await choice).setFiles(file);
}

async function previewReady(page, kind) {
  await page.waitForFunction(type => {
    const el = document.getElementById('uploaded-' + type);
    const decoded = type === 'video' ? el.readyState >= 2 && el.videoWidth > 0 : el.complete && el.naturalWidth > 0;
    return !el.hidden && decoded && !document.getElementById('check').disabled;
  }, kind, { timeout: 8000 });
}

async function exercise(browser, device, origin, clip, kind) {
  const context = await browser.newContext({ ...device, serviceWorkers: 'block' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let allowBoot;
  const gate = new Promise(resolve => { allowBoot = resolve; });
  await page.route('**/app.js*', async route => { await gate; await route.continue(); });
  try {
    await page.goto(origin + '/index.html', { waitUntil: 'commit' });
    const file = kind === 'image' ? photo : clip;
    await tapAndChoose(page, file);
    assert.equal(await page.locator('#media-file').evaluate(el => el.files.length), 1);
    allowBoot();
    await page.waitForLoadState('domcontentloaded');
    await previewReady(page, kind);
    // The page may visit the back/forward cache while the user switches tasks.
    await page.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    await previewReady(page, kind);
    await tapAndChoose(page, kind === 'image' ? clip : photo);
    await previewReady(page, kind === 'image' ? 'video' : 'image');
    await tapAndChoose(page, file);
    await previewReady(page, kind);
    // A chooser dismissed without selecting a replacement must keep the preview.
    await page.locator('#media-file').dispatchEvent('cancel');
    await previewReady(page, kind);
    await tapAndChoose(page, file);
    await previewReady(page, kind);
    await page.locator('#media-clear').click();
    await tapAndChoose(page, { name: path.basename(file), mimeType: 'application/octet-stream', buffer: fs.readFileSync(file) });
    await previewReady(page, kind);
    assert.deepEqual(errors, []);
  } finally { allowBoot(); await context.close(); }
}

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'web-picker-'));
  const clip = path.join(temp, 'test.mp4');
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname.startsWith('/api/')) {
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end('{}'); return;
    }
    const asset = path.resolve(web, '.' + pathname);
    if (!asset.startsWith(web + path.sep) || !mime[path.extname(asset)] || !fs.existsSync(asset)) {
      res.writeHead(404); res.end(); return;
    }
    res.writeHead(200, { 'Content-Type': mime[path.extname(asset)] }); res.end(fs.readFileSync(asset));
  });
  try {
    execFileSync('ffmpeg', ['-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',clip]);
    let origin = process.env.MOBILE_UPLOAD_TEST_ORIGIN;
    if (!origin) {
      server.listen(0, '127.0.0.1'); await once(server, 'listening');
      origin = 'http://127.0.0.1:' + server.address().port;
    }
    for (const [name, engine, device] of [['Chrome',chromium,devices['Pixel 7']], ['WebKit',webkit,devices['iPhone 13']]]) {
      const browser = await engine.launch(name === 'Chrome' && process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
      try {
        for (const kind of ['image','video']) {
          await exercise(browser, device, origin, clip, kind);
          console.log(`${name} ${kind} PASS: tap, early choice, page restore, replacement, cancel and same-file choice`);
        }
      } finally { await browser.close(); }
    }
  } finally {
    if (server.listening) { server.closeAllConnections(); server.close(); }
    fs.rmSync(temp, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
