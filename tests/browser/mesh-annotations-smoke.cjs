const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

function triangle() {
  const buffer = Buffer.alloc(3 * 15 + 12);
  const positions = [-.8, -.8, 0, .8, -.8, 0, 0, .8, 0];
  positions.forEach((value, index) => buffer.writeFloatLE(value, index * 4));
  [0, 1, 2].forEach((value, index) => buffer.writeUInt32LE(value, 36 + index * 4));
  buffer.fill(190, 48);
  return buffer;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH,
    headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let uploadHeaders;
    await page.route('http://mesh.test/', route => route.fulfill({
      contentType: 'text/html',
      body: `<style>
        .mesh-stage {position:relative;width:800px;height:600px;background:#172529}
        canvas,.mesh-markers {position:absolute;inset:0;width:100%;height:100%}
        .mesh-marker {position:absolute;pointer-events:auto}
        .mesh-markers {pointer-events:none}
      </style>
      <div id="inspection-modes"><button data-mode="mesh">3D</button></div>
      <input id="media-file"><span id="media-file-label"></span>
      <div id="inspection-mesh" hidden>
        <div class="mesh-stage"><canvas hidden></canvas><div class="mesh-markers" hidden></div>
          <div class="mesh-start"><input id="mesh-video-file" type="file"></div></div>
        <div class="mesh-toolbar" hidden>
          <button data-action="right">Повернуть</button><button id="mesh-mark-mode" aria-pressed="false">Отметить повреждение</button>
          <button id="mesh-replace">Другое видео</button></div>
        <p class="mesh-status"></p><section class="mesh-findings" hidden>
          <ol class="mesh-findings-list"></ol><video class="mesh-source-video"></video>
        </section></div>`,
    }));
    await page.route('http://mesh.test/api/mesh-jobs**', route => {
      const url = route.request().url();
      if (route.request().method() === 'POST') {
        uploadHeaders = route.request().headers();
        return route.fulfill({ status: 202, contentType: 'application/json',
          body: JSON.stringify({ id: 'a'.repeat(40), state: 'queued' }) });
      }
      if (url.endsWith('/mesh')) return route.fulfill({ contentType: 'application/octet-stream', body: triangle() });
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        id: 'a'.repeat(40), state: 'ready', vertices: 3, triangles: 1, indexBytes: 4,
        framesUsed: 26, processingSeconds: 112,
      }) });
    });
    await page.goto('http://mesh.test/');
    await page.addScriptTag({ path: path.resolve(__dirname, '../../web/inspection-mesh.js') });
    await page.evaluate(() => window.CyberInspectionMesh.create({
      media: { version: 0 }, openModel() {}, openMedia() {},
    }));
    await page.locator('[data-mode=mesh]').click();
    await page.locator('#mesh-video-file').setInputFiles({ name: 'test.mov', mimeType: 'video/quicktime', buffer: Buffer.from('video') });
    await page.locator('.mesh-status').getByText(/26 кадров/).waitFor();
    assert.equal(uploadHeaders['x-cyber102-mesh-profile'], 'fast');
    await page.locator('#mesh-mark-mode').click();
    await page.locator('canvas').click({ position: { x: 470, y: 300 } });
    await page.locator('.mesh-findings-list input').fill('Коррозия бака');
    await page.locator('.mesh-findings-list button').first().click();
    assert.match(await page.locator('.mesh-findings-list li').innerText(), /Коррозия бака/);
    const before = await page.locator('.mesh-marker').evaluate(el => el.style.left);
    await page.locator('[data-action=right]').click();
    assert.notEqual(await page.locator('.mesh-marker').evaluate(el => el.style.left), before);
    await page.locator('.mesh-findings-list button').click();
    assert.equal(await page.locator('.mesh-marker').count(), 0);
    assert.deepEqual(errors, []);
    console.log('3D marking smoke passed');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
