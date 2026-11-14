// A real MP4, queue, JPEG evidence and SQLite journal. Only the VLM response is a fixture.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const { once } = require('node:events');
const os = require('node:os');
const path = require('node:path');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { startBackend } = require('./gold-smoke.cjs');

async function check(browser, device, url, clip) {
  const page = await browser.newPage({ ...devices[device], locale: 'ru-RU' });
  const errors = [], batches = [], photoRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const route = new URL(request.url()).pathname;
    if (route === '/api/video-review') batches.push(request.postDataJSON());
    if (route === '/analyze') photoRequests.push(request.url());
  });
  await page.goto(url + '/index.html?serial=P-102');
  const file = page.locator('#media-file');
  await file.setInputFiles(clip);
  await page.locator('#video-review-run').click();
  const use = page.getByRole('button', {name: 'Оформить видеоосмотр'});
  await use.click();
  await page.locator('#inspection-preview').click();
  const [saved] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === '/api/inspections'),
    page.locator('#inspection-save').click(),
  ]);
  assert.equal(saved.ok(), true, await saved.text());
  const {record} = await saved.json();
  assert.equal(batches.length, 1);
  assert.equal(photoRequests.length, 0, 'Recording the overview must not rerun photo inference');
  assert.equal(record.evidence.result.media.frameIndex, 2);
  const firstFinding = batches[0].frames[2];
  const image = await page.request.get(url + record.evidence.imageUrl);
  const normalized = execFileSync(process.env.CYBER102_TEST_PYTHON || 'python3', ['-c',
    'import sys; from server.video_review import normalize_frame; print(normalize_frame(sys.stdin.read())[0])'],
    {cwd: path.resolve(__dirname, '../..'), input: firstFinding.image, encoding: 'utf8'}).trim();
  assert.equal((await image.body()).equals(Buffer.from(normalized, 'base64')), true);
  await page.locator('#inspection-history').click();
  await page.waitForURL(address => address.pathname === '/checkpoint.html');
  await page.reload();
  const entry = page.locator('.history-entry[data-record-id="' + record.id + '"]');
  await entry.getByText('Видеоосмотр', {exact: true}).waitFor();
  await entry.locator('.journal-video summary').click();
  await entry.getByText('Проверить фланец на двух кадрах.', {exact: true}).waitFor();
  const times = await entry.locator('.journal-video').textContent();
  assert.match(times, /Проверено кадров: 8/);
  assert.match(times, /0:01\.25, 0:02\.75/);
  await entry.getByRole('button', {name: 'Опорный кадр видео'}).click();
  const dialog = page.locator('#journal-frame[open]');
  await dialog.locator('img').waitFor();
  assert.equal(new URL(await dialog.locator('img').evaluate(img => img.src)).pathname, record.evidence.imageUrl);
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cyber-video-history-'));
  const clip = path.join(dir, 'sample.mp4');
  let backend;
  try {
    execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
      'testsrc2=size=320x240:rate=10', '-t', '4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', clip]);
    backend = await startBackend('--video-history');
    for (const [engine, device] of [[chromium, 'Pixel 7'], [webkit, 'iPhone 13']]) {
      const browser = await engine.launch(engine === chromium && process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {});
      try {
        await check(browser, device, backend.fixture.url, clip);
        console.log(device + ' PASS — one video inference, exact representative JPEG and persisted timecodes');
      } finally { await browser.close(); }
    }
  } finally {
    if (backend) {
      const stopped = once(backend.child, 'exit');
      backend.child.kill('SIGTERM');
      await stopped;
    }
    fs.rmSync(dir, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
