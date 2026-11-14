// Real browser -> web API -> real CPU visual model. Also checks stale response handling.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium, webkit, devices} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.CYBER102_VISUAL_ORIGIN || 'http://127.0.0.1:18012';
const root = path.resolve(__dirname, '../..');
async function run(type, device) {
  const browser = await type.launch({headless:true});
  const page = await browser.newPage(device);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(origin + '/index.html?serial=21031004-2');
    await page.locator('#media-file').setInputFiles(path.join(root, 'web/equipment-photos/knz-km.jpg'));
    await page.locator('.similar-equipment-card').first().waitFor();
    assert.match(await page.locator('.similar-equipment-card h4').first().textContent(), /серии КМ/);
    assert.equal(await page.locator('#active-equipment').inputValue(), '21031004-2');
    assert.ok(await page.locator('.similar-equipment-card a').count() > 0);
    assert.match(await page.locator('#similar-equipment-status').textContent(), /сверить с табличкой/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator('#similar-equipment').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(root, 'var/visual-search-' + type.name() + '.png'), fullPage:true});
    // A late previous-image result must not replace the current photo's matches.
    let pending;
    await page.route('**/api/equipment/similar', async route => {
      if (!pending) { pending = route; return; }
      await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify({matches:[]})});
    });
    await page.locator('#media-file').setInputFiles(path.join(root, 'web/equipment-photos/wanshsin-wxr.jpg'));
    await page.waitForFunction(() => document.querySelector('#similar-equipment').getAttribute('aria-busy') === 'true');
    await page.locator('#media-file').setInputFiles(path.join(root, 'web/equipment-photos/eldin-ba-bra.jpg'));
    await page.waitForFunction(() => document.querySelector('#similar-equipment-status').textContent.includes('не найдено'));
    if (pending) await pending.fulfill({status:200, contentType:'application/json', body:JSON.stringify({matches:[{
      title:'Старый ответ', imageUrl:'/equipment-photos/wanshsin-wxr.jpg', references:[]}]})}).catch(() => {});
    assert.equal(await page.locator('.similar-equipment-card').count(), 0);
    assert.equal(await page.locator('#active-equipment').inputValue(), '21031004-2');
    await page.unroute('**/api/equipment/similar');
    await page.route('**/api/equipment/similar', route => route.fulfill({status:503,
      contentType:'application/json', body:JSON.stringify({error:'Поиск временно недоступен'})}));
    await page.locator('#media-file').setInputFiles(path.join(root, 'web/equipment-photos/knz-d.jpg'));
    await page.locator('#similar-equipment-retry').waitFor({state:'visible'});
    assert.match(await page.locator('#similar-equipment-status').textContent(), /недоступен/);
    await page.unroute('**/api/equipment/similar');
    await page.locator('#similar-equipment-retry').click();
    await page.locator('.similar-equipment-card').first().waitFor();
    assert.match(await page.locator('.similar-equipment-card h4').first().textContent(), /серии Д/);
    assert.deepEqual(errors, []);
    console.log(type.name() + ': visual search, references, serial preservation, stale response, retry: OK');
  } finally { await browser.close(); }
}
(async () => {
  const engines = (process.env.VISUAL_SEARCH_ENGINES || 'chromium,webkit').split(',');
  if (engines.includes('chromium')) await run(chromium, devices['Pixel 7']);
  if (engines.includes('webkit')) await run(webkit, devices['iPhone 13']);
})().catch(error => {console.error(error);process.exitCode = 1;});
