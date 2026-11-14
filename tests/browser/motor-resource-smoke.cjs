// Real manufacturer catalog, responsive website and temporary API. Photo inference is a fixture.
const assert = require('node:assert/strict');
const { once } = require('node:events');
const path = require('node:path');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { startBackend } = require('./gold-smoke.cjs');

async function check(page, url) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const panel = page.locator('#motor-resource');
  const hours = page.locator('#motor-resource-reading');
  const remaining = page.locator('#motor-resource-remaining');
  const remainingText = async () => (await remaining.textContent()).replace(/[\u00a0\u202f]/gu, ' ');
  const active = page.locator('#active-equipment');
  await page.goto(url + '/documents.html');
  await page.locator('#equipment-kind').selectOption('motor');
  await page.getByRole('link', {name:'Рассчитать ресурс двигателя 230201068'}).click();
  await hours.waitFor();
  assert.equal(await page.locator('#inspection-component').inputValue(), 'motor');
  assert.equal(await page.locator('#inspection-workflow').isVisible(), false);
  assert.match(await panel.locator('a').getAttribute('href'), /drive-1-passports\/pdf#page=2$/);
  await hours.fill('12000');
  assert.match(await remainingText(), /48 000 ч/);
  await hours.fill('0');
  assert.match(await remainingText(), /60 000 ч/);
  await active.fill('230201067');
  await active.blur();
  await page.locator('#motor-resource-title').getByText(/230201067/).waitFor();
  assert.equal(await hours.inputValue(), '');
  assert.match(await panel.locator('a').getAttribute('href'), /drive-3-passports\/pdf#page=4$/);
  assert.equal(await remainingText(), '');

  await active.fill('21031004-1');
  await active.blur();
  await panel.waitFor({state:'hidden'});
  await page.locator('#media-file').setInputFiles(path.resolve(__dirname, '../../web/demo-pump.jpg'));
  await page.locator('#check').click();
  await page.locator('#inspection-preview:not([disabled])').waitFor();
  await page.locator('#inspection-telemetry').selectOption('manual');
  await page.locator('#inspection-telemetry-hours').fill('9000');
  const component = page.locator('#inspection-component');
  await component.selectOption('motor');
  await panel.waitFor();
  assert.equal(await hours.isVisible(), false, 'No motor assumed from a pump serial');
  const confirm = page.locator('#motor-resource-serial');
  await confirm.fill('230201070');
  await hours.waitFor();
  assert.equal(await remainingText(), '', 'Aggregate running hours cannot supply a motor reading');
  await hours.fill('60010');
  assert.match(await remainingText(), /ресурс по наработке достигнут/);
  await confirm.fill('wrong');
  assert.equal(await hours.isVisible(), false);
  await confirm.fill('230201070');
  assert.equal(await hours.inputValue(), '');
  await component.selectOption('pump');
  await panel.waitFor({state:'hidden'});
  await component.selectOption('motor');
  assert.equal(await confirm.inputValue(), '');
  assert.equal(await hours.isVisible(), false);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
}

(async () => {
  const backend = await startBackend('--inspection-work');
  try {
    for (const [engine, device] of [[chromium, 'Pixel 7'], [webkit, 'iPhone 13']]) {
      const browser = await engine.launch(engine === chromium && process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {});
      try {
        const page = await browser.newPage({...devices[device], locale:'ru-RU'});
        await check(page, backend.fixture.url);
        console.log(device + ' PASS — linked motor passport, explicit motor counter and selection reset');
      } finally { await browser.close(); }
    }
  } finally {
    const closed = once(backend.child, 'exit'); backend.child.kill('SIGTERM'); await closed;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
