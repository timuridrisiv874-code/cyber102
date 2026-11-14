// Real browser decoding and real multipart requests; no camera hardware or GPU required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { createHash } = require('node:crypto');
const { once } = require('node:events');
const { execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const fixture = { ...JSON.parse(fs.readFileSync(path.join(root, 'web/mock-analysis.json'))),
  model: 'cyber102-detector-test', source: 'local_vision', analysisId: 'media-test', observationKind: 'template' };
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg', '.json': 'application/json' };
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cyber-media-'));
const photo = path.join(temp, 'private-original-name.png');
const clip = path.join(temp, 'private-video.mp4');
const longClip = path.join(temp, 'long.mp4');
const boundedClip = path.join(temp, 'bounded.mp4');
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=1600x900', '-frames:v', '1', '-threads', '1', photo]);
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10', '-t', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', clip]);
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=1', '-t', '121', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', longClip]);
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=1', '-t', '45', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', boundedClip]);
const state = { android: false, posts: [], mocks: 0, active: 0, maxActive: 0, hold: false, pending: [], profiles: ['standard', 'fast'] };
const reply = (res, code, data) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/analyze') {
    const bytes = [];
    for await (const chunk of req) bytes.push(chunk);
    const form = await new Request('http://localhost/analyze', { method: 'POST', headers: req.headers, body: Buffer.concat(bytes) }).formData();
    const file = form.get('file');
    const image = Buffer.from(await file.arrayBuffer());
    const post = { source: form.get('captureSource'), time: form.get('videoTimeSeconds'), profile: form.get('profile'), name: file.name, type: file.type, image };
    state.posts.push(post); state.active += 1; state.maxActive = Math.max(state.active, state.maxActive);
    const done = () => { state.active -= 1; reply(res, 200, { ...fixture, analysisId: `media-${state.posts.length}` }); };
    if (state.hold) state.pending.push(done); else setTimeout(done, 30);
    return;
  }
  if (url.pathname === '/api/capabilities') return reply(res, 200, { vision: { enabled: true, backend: 'detector', profiles: state.profiles } });
  if (url.pathname === '/api/scenarios') return reply(res, 200, { passport: { serial: 'P-102', model: 'Насос' }, scenarios: [] });
  if (url.pathname.startsWith('/api/')) return reply(res, 200, {});
  if (url.pathname.includes('mock-analysis')) state.mocks += 1;
  const androidAsset = state.android && (url.pathname === '/index.html' || url.pathname.startsWith('/__mobile_inspection/'));
  const base = path.join(root, androidAsset ? 'android/app/src/main/assets/inspection' : 'web');
  const asset = path.resolve(base, '.' + url.pathname.replace('/__mobile_inspection/', '/'));
  if (!asset.startsWith(base + path.sep) || !mime[path.extname(asset)] || !fs.existsSync(asset)) return reply(res, 404, {});
  res.writeHead(200, { 'Content-Type': mime[path.extname(asset)] }); res.end(fs.readFileSync(asset));
});
async function waitUntil(fn) { for (let i = 0; i < 200; i++) { if (fn()) return; await delay(25); } throw new Error('Condition timed out'); }
async function run(browser, engine, android) {
  Object.assign(state, { android, posts: [], mocks: 0, active: 0, maxActive: 0, hold: false, pending: [], profiles: ['standard', 'fast'] });
  const page = await browser.newPage({ ...devices['iPhone 13'], defaultBrowserType: undefined });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.mediaUrls = { created: [], revoked: [] };
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = value => { const url = create(value); mediaUrls.created.push(url); return url; };
    URL.revokeObjectURL = url => { mediaUrls.revoked.push(url); revoke(url); };
  });
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
    if (android) {
      await page.locator('.capture-settings').filter({ has: page.locator('#camera-options') }).locator('summary').click();
    } else {
      assert.equal(await page.locator('#analysis-settings').isVisible(), false, 'Analysis settings stay hidden before a source is ready');
    }
    await page.locator('#media-file').setInputFiles(photo);
    await page.locator('#uploaded-image').waitFor();
    if (android) await page.locator('#analysis-profile').selectOption('fast');
    else assert.equal(await page.locator('#analysis-settings').isVisible(), false);
    await page.locator('#check').click();
    await page.locator('#details').waitFor();
    assert.equal(state.posts.length, 1);
    assert.equal(state.posts[0].source, 'uploaded_photo'); assert.equal(state.posts[0].time, null);
    assert.equal(state.posts[0].profile, android ? 'fast' : 'standard'); assert.equal(state.posts[0].name, 'frame.jpg');
    assert.equal(state.posts[0].type, 'image/jpeg'); assert.equal(state.posts[0].image.subarray(0, 2).toString('hex'), 'ffd8');
    assert.deepEqual(await page.locator('#snapshot').evaluate(el => [el.width, el.height]), [1280, 720]);
    if (!android) await page.locator('.result-provenance > summary').click();
    assert.match(await page.locator('#capture-provenance').innerText(), /Загруженное фото/);
    if (!android) await page.locator('.result-provenance > summary').click();
    if (!android) assert.equal(await page.locator('#analysis-settings').isVisible(), false);
    assert.equal(state.mocks, 0, 'An uploaded image must never use a mock result');

    await page.locator('#media-file').setInputFiles(clip);
    await page.locator('#uploaded-video').waitFor();
    await page.locator('#uploaded-video').evaluate(async el => { await new Promise(resolve => { el.addEventListener('seeked', resolve, { once: true }); el.currentTime = 1; }); });
    if (!android) {
      assert.equal(await page.locator('#video-review-run').isVisible(), true);
      assert.equal(await page.locator('#check').isVisible(), false);
      await page.locator('#analysis-settings > summary').click();
    }
    await page.locator('#check').click();
    await page.locator('#details').waitFor();
    assert.equal(state.posts.at(-1).source, 'uploaded_video'); assert.equal(Number(state.posts.at(-1).time), 1);
    if (!android) await page.locator('.result-provenance > summary').click();
    assert.match(await page.locator('#capture-provenance').innerText(), /0:01\.0/);
    if (!android) await page.locator('.result-provenance > summary').click();
    await page.locator('#camera-mode').selectOption('video');
    await page.locator('#frame-interval').selectOption('2000');
    await page.locator('#uploaded-video').evaluate(async (el, position) => { await new Promise(resolve => { el.addEventListener('seeked', resolve, { once: true }); el.currentTime = position; }); }, android ? 0 : 4.7);
    const firstVideo = state.posts.length;
    await page.locator('#video-toggle').click();
    await page.locator('#message').getByText('Проверка выбранных кадров завершена.', { exact: false }).waitFor();
    assert.deepEqual(state.posts.slice(firstVideo).map(post => Number(post.time)), [0, 2, 4]);
    assert.equal(state.maxActive, 1, 'Video requests must be sequential');
    assert.equal(new Set(state.posts.slice(firstVideo).map(post => createHash('sha256').update(post.image).digest('hex'))).size, 3, 'Seeking must produce distinct actual frames, not repeated snapshots');
    assert.match(await page.locator('#media-progress').textContent(), /3 из 3/);
    assert.equal(state.mocks, 0);

    // Replacing an input during a request aborts that run; its late response must not replace the new file.
    await page.locator('#media-file').setInputFiles(photo);
    await page.locator('#uploaded-image').waitFor();
    state.hold = true;
    await page.locator('#check').click(); await waitUntil(() => state.pending.length === 1);
    await page.locator('#media-file').setInputFiles(clip);
    await page.locator('#uploaded-video').waitFor();
    state.pending.shift()(); state.hold = false; await delay(100);
    assert.equal(await page.locator('#details').isVisible(), false);
    assert.equal(await page.locator('#snapshot').isVisible(), false);
    if (!android) await page.locator('#analysis-settings > summary').click();
    await page.locator('#camera-mode').selectOption('video');
    state.hold = true; const beforeStop = state.posts.length;
    await page.locator('#video-toggle').click(); await waitUntil(() => state.pending.length === 1);
    await page.locator('#video-toggle').click();
    state.pending.shift()(); state.hold = false; await delay(150);
    assert.equal(state.posts.length, beforeStop + 1, 'Stop must cancel subsequent sampled frames');

    await page.locator('#media-file').setInputFiles(boundedClip);
    await page.locator('#uploaded-video').waitFor();
    if (!android) {
      await page.locator('#analysis-settings > summary').click();
      assert.equal(await page.locator('#camera-mode').inputValue(), 'single');
      assert.equal(await page.locator('#frame-interval').inputValue(), '5000');
    }
    await page.locator('#camera-mode').selectOption('video');
    await page.locator('#frame-interval').selectOption('2000');
    const beforeBounded = state.posts.length;
    await page.locator('#video-toggle').click();
    await page.locator('#message').getByText('Проверка выбранных кадров завершена.', { exact: false }).waitFor();
    assert.equal(state.posts.length - beforeBounded, 20, 'Uploaded video is bounded to 20 frames per run');
    assert.equal(Number(state.posts.at(-1).time), 38);
    await page.locator('#media-file').setInputFiles(longClip);
    await page.locator('#message').getByText('до 2 минут', { exact: false }).waitFor();
    await page.locator('#media-file').setInputFiles({ name: 'file.txt', mimeType: 'text/plain', buffer: Buffer.from('invalid') });
    await page.locator('#message').getByText('Выберите фото или видео.', { exact: false }).waitFor();
    await page.locator('#media-file').setInputFiles({ name: 'large.png', mimeType: 'image/png', buffer: Buffer.alloc(20 * 1024 * 1024 + 1) });
    await page.locator('#message').getByText('превышает 20 МБ', { exact: false }).waitFor();
    await page.locator('#media-file').setInputFiles(photo);
    await page.locator('#uploaded-image').waitFor();
    await page.evaluate(() => { Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => { throw new DOMException('No camera for test', 'NotFoundError'); } } }); });
    await page.locator('#camera').click();
    await page.locator('#message').getByText('Камера не найдена.', { exact: false }).waitFor();
    await page.locator('#media-file').setInputFiles(photo);
    await page.locator('#uploaded-image').waitFor();
    await page.locator('#media-clear').click();
    await page.locator('#media-file').setInputFiles(photo);
    await page.locator('#uploaded-image').waitFor();
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
    const urls = await page.evaluate(() => mediaUrls);
    assert.deepEqual([...urls.revoked].sort(), [...urls.created].sort(), 'All replaced/cleared object URLs must be revoked');
    state.profiles = ['standard'];
    await page.reload();
    if (android) {
      await page.waitForFunction(() => document.querySelector('#analysis-profile option[value="fast"]').disabled);
      assert.equal(await page.locator('#analysis-profile').inputValue(), 'standard');
      assert.equal(await page.locator('#analysis-profile option[value="fast"]').evaluate(el => el.hidden), true);
    } else assert.equal(await page.locator('#analysis-profile').count(), 0);
    assert.deepEqual(errors, []);
    console.log(`${engine} ${android ? 'Android bundle' : 'web'} PASS: real photo/video decoding, JPEG dimensions, real API/provenance, frame sampling, stop/replace, limits, URL cleanup`);
  } catch (error) { console.error({ engine, android, settings: await page.locator('#analysis-settings').evaluate(el => ({open: el.open, hidden: el.hidden})).catch(() => null), message: await page.locator('#message').textContent(), errors }); throw error; } finally { state.pending.splice(0).forEach(done => done()); await page.close(); }
}
(async () => {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    for (const [engine, name, options] of [[chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}], [webkit, 'WebKit', {}]]) {
      if (process.env.BROWSER_ENGINE && process.env.BROWSER_ENGINE !== name) continue;
      const browser = await engine.launch(options);
      try { for (const android of (process.env.WEB_ONLY ? [false] : [false, true])) await run(browser, name, android); } finally { await browser.close(); }
    }
  } finally { server.close(); server.closeAllConnections(); fs.rmSync(temp, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
