// Read real QR pixels through the inspection camera in both mobile engines.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { startBackend } = require('./gold-smoke.cjs');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const python = process.env.CYBER102_TEST_PYTHON || 'python3';

function label(text) {
  const program = [
    'import qrcode,sys',
    'from qrcode.image.pure import PyPNGImage',
    'qrcode.make(sys.argv[1], image_factory=PyPNGImage, box_size=8).save(sys.stdout.buffer)',
  ].join('\n');
  return 'data:image/png;base64,' + execFileSync(python, ['-c', program, text]).toString('base64');
}

async function exercise(browser, device, origin, knownLabel) {
  const page = await browser.newPage({ ...device });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(origin + '/index.html', {waitUntil: 'domcontentloaded'});
    const button = page.getByRole('button', {name:'Сканировать QR оборудования'});
    assert.equal(await button.isVisible(), false);
    await page.evaluate((src) => {
      const canvas = document.createElement('canvas');
      canvas.width = 640; canvas.height = 480;
      const context = canvas.getContext('2d');
      const image = new Image();
      window.paintQr = async value => { image.src = value; await image.decode(); };
      const paint = () => {
        context.fillStyle = '#b8b8b8';
        context.fillRect(0, 0, 640, 480);
        if (image.complete && image.naturalWidth) context.drawImage(image, 120, 40, 400, 400);
      };
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { async getUserMedia() {
        await window.paintQr(src);
        paint();
        const stream = canvas.captureStream(12);
        const timer = setInterval(paint, 80);
        window.addEventListener('pagehide', () => clearInterval(timer), {once:true});
        return stream;
      } } });
    }, knownLabel);
    await page.locator('#camera').tap();
    await button.tap();
    await page.waitForFunction(() => document.querySelector('#inspection-serial').value === 'P-102' &&
      document.querySelector('#qr-scan-status').textContent.includes('Выбрано оборудование'), null, {timeout:24000});
    assert.equal(await page.locator('#active-equipment').inputValue(), 'P-102');
    assert.equal(await button.getAttribute('aria-pressed'), 'false');
    assert.equal(new URL(page.url()).pathname, '/index.html');
    // A second, unregistered tag must not replace an already selected pump.
    await page.evaluate(value => window.paintQr(value), label('UNKNOWN-QR-742'));
    await page.waitForTimeout(350);
    await button.tap();
    await page.waitForFunction(() => document.querySelector('#qr-scan-status').textContent.includes('отсутствует'));
    assert.equal(await page.locator('#active-equipment').inputValue(), 'P-102');
    // Exercise the optional photo-calibration path using the same QR artwork.
    const geometry = await page.evaluate(async value => {
      const photo = new Image();
      photo.src = value;
      await photo.decode();
      window.chosenMeasure = window.CyberPhotoMeasure.choose(photo);
      return {width:photo.naturalWidth,height:photo.naturalHeight};
    }, knownLabel);
    const surface = page.locator('#measure-surface');
    await page.locator('#measure-qr').tap();
    await page.waitForFunction(() => !document.querySelector('#measure-photo-dialog [data-undo]').disabled);
    assert.ok(geometry.width >= 100 && geometry.width === geometry.height);
    await page.locator('#measure-reference-mm').fill('40');
    await surface.scrollIntoViewIfNeeded();
    const box = await surface.boundingBox();
    assert.ok(box.width <= device.viewport.width && Math.abs(box.width - box.height) < 2);
    for (const dx of [0.2,0.7]) await page.touchscreen.tap(box.x + dx * box.width,box.y + 0.6 * box.height);
    await page.locator('#measure-apply').tap();
    const measured = await page.evaluate(() => window.chosenMeasure);
    assert.equal(measured.target.length, 2);
    assert.equal(measured.reference.length, 2);
    assert.equal(measured.referenceMm, 40);
    const [a,b] = measured.reference;
    assert.ok(Math.hypot(a.x-b.x,a.y-b.y) > 0.65);
    await page.locator('#camera').tap();
    assert.equal(await button.isVisible(), false);
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
}

async function main() {
  let origin = process.env.QR_TEST_ORIGIN;
  let child;
  try {
    if (!origin) {
      const backend = await startBackend();
      child = backend.child;
      origin = backend.fixture.url;
    }
    const code = label(origin + '/checkpoint.html?serial=P-102');
    for (const [engine, profile] of [[chromium,'Pixel 7'],[webkit,'iPhone 13']]) {
      const browser = await engine.launch(engine === chromium && process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {});
      try { await exercise(browser, devices[profile], origin, code); }
      finally { await browser.close(); }
      console.log(profile + ': camera QR, equipment lookup and calibrated photo passed');
    }
  } finally { child?.kill('SIGTERM'); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
