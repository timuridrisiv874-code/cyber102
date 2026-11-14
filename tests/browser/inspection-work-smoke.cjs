// Mobile web with a real transient SQLite database. Only model inference is replaced.
// Set INSPECTION_SCREENSHOT_DIR to capture the measured close-up and completed journal entry.
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const { once } = require('node:events');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { startBackend } = require('./gold-smoke.cjs');
const root = path.resolve(__dirname, '../..');

async function pickPhoto(page, selector, filename) {
  const [picker] = await Promise.all([
    page.waitForEvent('filechooser'), page.locator(selector).tap(),
  ]);
  await picker.setFiles(filename);
}

async function postByTap(page, button, pathname) {
  const [response] = await Promise.all([
    page.waitForResponse(res => new URL(res.url()).pathname === pathname && res.request().method() === 'POST'),
    button.tap(),
  ]);
  const data = await response.json();
  assert.ok(response.ok(), JSON.stringify(data));
  return data;
}

async function snapshot(page, name) {
  if (!process.env.INSPECTION_SCREENSHOT_DIR) return;
  await fs.mkdir(process.env.INSPECTION_SCREENSHOT_DIR, {recursive: true});
  await page.screenshot({ path: path.join(process.env.INSPECTION_SCREENSHOT_DIR, name) });
}

async function fitsScreen(page) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'The phone should not require horizontal scrolling');
}

async function run(engine, device, options, fixture) {
  const browser = await engine.launch(options);
  const isIPhone = engine === webkit;
  const page = await browser.newPage({ ...devices[device], locale: 'ru-RU' });
  page.setDefaultTimeout(12000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(`${fixture.url}/index.html?serial=P-102`);
    await pickPhoto(page, '#media-file', path.join(root, 'web/demo-pump.jpg'));
    await page.locator('#uploaded-image').waitFor();
    await page.locator('#check').tap();
    await page.locator('#detection').tap();
    await page.locator('#inspection-closeup').waitFor();
    await page.locator('#closeup-label').fill('Выходной фланец');
    await page.locator('#closeup-defect').selectOption('corrosion');
    await pickPhoto(page, '#closeup-file', fixture.closeup);
    await page.locator('#closeup-measure').tap();
    const surface = page.locator('#measure-surface');
    await page.locator('#measure-reference-mm').fill('50');
    await page.locator('#measure-title').tap(); // dismiss the phone keyboard before marking points
    await surface.scrollIntoViewIfNeeded();
    const box = await surface.boundingBox();
    for (const [x, y] of [[.2, .3], [.6, .3], [.35, .53], [.55, .53]]) {
      await surface.tap({position: {x: Math.round(box.width * x), y: Math.round(box.height * y)}});
    }
    await page.locator('#measure-apply').tap();
    await page.locator('#measure-photo-dialog').waitFor({state:'detached'});
    assert.match(await page.locator('#closeup-length').innerText(), /мм/);
    await fitsScreen(page);
    await postByTap(page, page.locator('#inspection-preview'), '/api/inspections/preview');
    const { record, created } = await postByTap(page, page.locator('#inspection-save'), '/api/inspections');
    assert.equal(created, true);
    assert.equal(record.serial, 'P-102');
    assert.equal(record.provenance.captureSource, 'uploaded_photo');
    assert.equal(record.closeup.componentLabel, 'Выходной фланец');
    assert.equal(record.closeup.defectType, 'corrosion');
    assert.ok(Math.abs(record.closeup.measurement.lengthMm - 25) < 2,
      'A 50 mm reference twice the length of the marked defect must measure about 25 mm');
    assert.ok(record.closeup.point.x > .55 && record.closeup.point.y > .25,
      'The touched flange belongs in the right half of the overview image');
    await page.locator('#inspection-history').tap();
    await page.waitForURL(url => url.pathname === '/checkpoint.html' && url.searchParams.get('record') === record.id);
    const entry = page.locator(`.history-entry[data-record-id="${record.id}"]`);
    await entry.getByRole('button', {name:'Зафиксировать работу'}).tap();
    const work = page.locator('#journal-work');
    assert.match(await work.locator('#work-context').innerText(), /P-102 · Выходной фланец/);
    await work.locator('#work-action').selectOption('replaced');
    const workDate = await page.evaluate(() => {
      const now = new Date();
      return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-');
    });
    await work.locator('#work-date').fill(workDate);
    await pickPhoto(page, '#work-photo', path.join(root, 'web/demo-pump-clean.jpg'));
    await work.locator('#work-preview').waitFor();
    const workPath = '/api/history/' + record.id + '/work';
    const updated = await postByTap(page, work.locator('#work-submit'), workPath);
    assert.equal(updated.created, true);
    assert.equal(updated.record.id, record.id);
    assert.equal(updated.record.work.length, 1);
    const [event] = updated.record.work;
    assert.equal(event.action, 'replaced');
    assert.equal(event.date, workDate);
    await work.waitFor({state:'hidden'});

    await page.reload();
    await entry.getByText('Узел заменён', {exact:true}).waitFor();
    assert.equal(await entry.locator('.journal-work-event time').getAttribute('datetime'), workDate);
    await fitsScreen(page);
    if (!isIPhone) {
      await entry.evaluate(node => node.scrollIntoView({block:'center'}));
      await snapshot(page, 'inspection-work-android.png');
    }
    const dialog = page.locator('#journal-frame[open]');
    const digests = new Set();
    for (const [role, url] of [
      ['Общий вид', record.evidence.imageUrl],
      ['Крупный план', record.closeup.imageUrl],
      ['Фото после', event.imageUrl],
    ]) {
      await entry.getByRole('button', {name:role, exact:true}).tap();
      const photo = dialog.locator('img');
      await photo.waitFor();
      assert.ok(await photo.evaluate(img => img.complete && img.naturalWidth > 0));
      assert.equal(new URL(await photo.evaluate(img => img.src)).pathname, url);
      assert.match(await dialog.locator('#journal-frame-context').innerText(), /Выходной фланец/);
      const bytes = await page.request.get(fixture.url + url);
      assert.equal(bytes.status(), 200);
      assert.equal(bytes.headers()['content-type'], 'image/jpeg');
      digests.add(createHash('sha256').update(await bytes.body()).digest('hex'));
      if (role === 'Крупный план') {
        assert.equal(await dialog.locator('svg line').count(), 2);
        assert.match(await dialog.locator('#journal-reference-length').innerText(), /50 мм/);
        assert.match(await dialog.locator('#journal-target-length').innerText(), /мм/);
        if (isIPhone) await snapshot(page, 'inspection-work-iphone.png');
      }
      await dialog.getByRole('button', {name:'Закрыть'}).tap();
    }
    assert.equal(digests.size, 3, 'Overview, close-up and after image must be three distinct stored images');
    assert.equal(page.context().pages().length, 1);
    assert.deepEqual(errors, []);
    console.log(`${device}: PASS — uploaded overview, touched flange, separate close-up + measured defect, saved inspection, dated replacement, three distinct photos after reload`);
  } finally {
    await browser.close();
  }
}

(async () => {
  const backend = await startBackend('--inspection-work');
  try {
    await run(chromium, 'Pixel 7', process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {}, backend.fixture);
    await run(webkit, 'iPhone 13', {}, backend.fixture);
  } finally {
    const stopped = once(backend.child, 'exit');
    backend.child.kill('SIGTERM');
    await stopped;
    if (backend.stderr()) console.error(backend.stderr());
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
