// Real public photo analysis: visual retrieval -> reference excerpts -> VLM -> used-source UI.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium, webkit, devices} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const origin = process.env.CYBER102_VISUAL_ORIGIN || 'http://127.0.0.1:18012';
async function exercise(type, device) {
  const browser = await type.launch({headless:true});
  const page = await browser.newPage(device);
  page.setDefaultTimeout(120000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(origin + '/index.html');
    await page.locator('#media-file').setInputFiles(path.join(root, 'web/equipment-photos/knz-km.jpg'));
    await page.locator('#check').click();
    await page.waitForFunction(() => document.querySelector('#photo-knowledge summary').textContent ===
      'Модель использовала справки по фото');
    const sources = page.locator('#photo-knowledge');
    await sources.locator('summary').click();
    assert.ok(await sources.locator('a').count() >= 1);
    assert.ok(await sources.locator('a[href*="RE-tipa-KM.pdf#page=17"]').count() === 1);
    assert.match(await sources.textContent(), /найдено по фото/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await sources.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(root, 'var/visual-rag-' + type.name() + '.png'),fullPage:true});
    assert.deepEqual(errors, []);
    console.log(type.name() + ': real photo analysis returned visually retrieved sources: OK');
  } finally {await browser.close();}
}
(async () => {
  const engines = (process.env.VISUAL_SEARCH_ENGINES || 'chromium,webkit').split(',');
  if (engines.includes('chromium')) await exercise(chromium, devices['Pixel 7']);
  if (engines.includes('webkit')) await exercise(webkit, devices['iPhone 13']);
})().catch(error => {console.error(error);process.exitCode = 1;});
