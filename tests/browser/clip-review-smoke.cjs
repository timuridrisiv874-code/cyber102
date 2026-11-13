// Decode a real MP4; the server comparison is an explicit contract fixture, not a model accuracy test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cyber-clip-review-'));
const clip = path.join(temp, 'private-original-name.mp4');
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10', '-t', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', clip]);
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg', '.json': 'application/json' };
const state = { android: false, posts: [], mode: 'success', pending: [], available: true };
const reply = (res, code, data) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/capabilities') return reply(res, 200, { vision: { profiles: ['standard', 'fast'], videoReview: state.available } });
  if (url.pathname === '/api/video-review') {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString(); const body = JSON.parse(raw); state.posts.push({ body, raw });
    const result = { model: 'clip-contract-test', status: 'defect', summary: 'Тест: последовательность из восьми кадров.',
      observations: body.frames.map((frame, index) => ({ frameIndex: index, timestampSeconds: frame.timestampSeconds,
        status: index === 3 ? 'defect' : 'clear', component: 'pump', condition: index === 3 ? 'corrosion' : 'no_visible_defect',
        observation: `Тестовое наблюдение ${index + 1}`, box: [100, 100, 500, 500] })) };
    if (state.mode === 'mismatch') result.observations[0].timestampSeconds += 1;
    if (state.mode === 'hold') { state.pending.push(() => reply(res, 200, result)); return; }
    return reply(res, 200, result);
  }
  const android = state.android && (url.pathname === '/index.html' || url.pathname.startsWith('/__mobile_inspection/'));
  const base = path.join(root, android ? 'android/app/src/main/assets/inspection' : 'web');
  const target = path.resolve(base, '.' + url.pathname.replace('/__mobile_inspection/', '/'));
  if (!target.startsWith(base + path.sep) || !mime[path.extname(target)] || !fs.existsSync(target)) return reply(res, 404, {});
  res.writeHead(200, { 'Content-Type': mime[path.extname(target)] }); res.end(fs.readFileSync(target));
});
async function until(condition) { for (let i = 0; i < 200; i++) { if (condition()) return; await delay(25); } throw new Error('Condition timed out'); }
async function run(browser, engine, android) {
  Object.assign(state, { android, posts: [], mode: 'success', pending: [], available: true });
  const page = await browser.newPage(devices['iPhone 13']);
  const errors = [], violations = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.exposeFunction('captureCspViolation', value => violations.push(value));
  await page.addInitScript(() => document.addEventListener('securitypolicyviolation', event => window.captureCspViolation(event.blockedURI)));
  page.setDefaultTimeout(15000);
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
    await page.locator('#media-file').setInputFiles(clip);
    await page.locator('#message').getByText('Видео открыто.', { exact: false }).waitFor();
    await page.locator('#video-review-run').click();
    await page.locator('#video-review-results').waitFor();
    assert.equal(state.posts.length, 1, 'Eight frames are compared in one bounded request');
    const post = state.posts[0];
    assert.deepEqual(Object.keys(post.body).sort(), ['durationSeconds', 'frames']);
    assert.equal(post.body.frames.length, 8);
    assert.equal(post.body.durationSeconds, 5);
    assert.equal(post.raw.includes('private-original-name'), false, 'Original filename is not uploaded');
    const hashes = [];
    post.body.frames.forEach((frame, index) => {
      assert.deepEqual(Object.keys(frame).sort(), ['image', 'timestampSeconds']);
      assert.equal(frame.timestampSeconds, 5 * (index + 0.5) / 8);
      const bytes = Buffer.from(frame.image, 'base64');
      assert.equal(bytes.subarray(0, 2).toString('hex'), 'ffd8');
      hashes.push(createHash('sha256').update(bytes).digest('hex'));
    });
    assert.equal(new Set(hashes).size, 8, 'Sampling decodes eight distinct actual frames');
    assert.equal(await page.locator('.video-review-frame').count(), 8);
    await page.waitForFunction(() => [...document.querySelectorAll('.video-review-seek img')].every(image => image.complete && image.naturalWidth > 0));
    assert.deepEqual(violations, [], 'Bundled Android CSP must allow generated local thumbnails');
    assert.match(await page.locator('#video-review-results').textContent(), /не сохранён в историю/);
    assert.equal(await page.locator('#inspection-workflow').isVisible(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('.video-review-seek').nth(3).click();
    await page.waitForFunction(time => Math.abs(document.getElementById('uploaded-video').currentTime - time) < .01, 5 * 3.5 / 8);
    assert.equal(requests.includes('/analyze'), false, 'Opening a thumbnail only seeks; it does not rerun inference');

    state.mode = 'mismatch';
    await page.locator('#video-review-run').click();
    await page.locator('#video-review-status').getByText('Позиции наблюдений не совпали', { exact: false }).waitFor();
    assert.equal(await page.locator('#video-review-results').isVisible(), false, 'A timestamp mismatch must not show misleading frame results');

    state.mode = 'hold';
    await page.locator('#video-review-run').click(); await until(() => state.pending.length === 1);
    await page.locator('#video-review-cancel').click();
    state.pending.shift()(); await delay(100);
    assert.equal(await page.locator('#video-review-results').isVisible(), false, 'Late cancelled results are ignored');
    assert.match(await page.locator('#video-review-status').textContent(), /отменён/);

    state.available = false;
    await page.reload();
    await page.locator('#media-file').setInputFiles(clip);
    await page.locator('#message').getByText('Видео открыто.', { exact: false }).waitFor();
    assert.equal(await page.locator('#video-review-run').isDisabled(), true);
    assert.equal(requests.includes('/api/inspections'), false);
    assert.deepEqual(errors, []);
    console.log(`${engine} ${android ? 'Android bundle' : 'web'} PASS: eight distinct JPEGs, one request, thumbnails/CSP, timecodes, seek, mismatch, cancellation, capabilities`);
  } finally { state.pending.splice(0).forEach(done => done()); await page.close(); }
}
(async () => {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    for (const [engine, name, options] of [[chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}], [webkit, 'WebKit', {}]]) {
      if (process.env.BROWSER_ENGINE && process.env.BROWSER_ENGINE !== name) continue;
      const browser = await engine.launch(options);
      try { for (const android of [false, true]) await run(browser, name, android); } finally { await browser.close(); }
    }
  } finally { server.close(); server.closeAllConnections(); fs.rmSync(temp, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
