const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium, webkit, devices} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.CYBER102_SMOKE_URL || 'http://127.0.0.1:8130';
const screenshotDir = process.env.MATERIALS_SMOKE_SCREENSHOTS;
const hold = ms => new Promise(resolve => setTimeout(resolve, ms));
const mock = {
  status:'defect', source:'local_vision', model:'InternVL3', component:'Насос', finding:'Предварительный признак',
  observation:'Возможная коррозия', recommendation:'Осмотреть на месте', box:{x:0.25,y:0.2,width:0.5,height:0.5},
};
async function run(browser, options, name) {
  const page = await browser.newPage(options);
  page.setDefaultTimeout(10_000);
  const errors = [];
  const uploads = [];
  let diagnose;
  let delayDiagnosis = false;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/diagnose')) diagnose = request.postDataJSON();
  });
  await page.route('**/analyze', async route => {
    const attempt = uploads.push(route.request());
    const pending = attempt === 2 || attempt === 4;
    if (pending) await hold(700);
    const result = attempt === 3 ? {status:503,json:{error:'Модель занята'}} : {status:200,json:mock};
    try {
      await route.fulfill(result);
    } catch (error) {
      if (!pending) throw error;
    }
  });
  await page.route('**/api/diagnose', async route => {
    if (delayDiagnosis) await hold(700);
    try {
      await route.continue();
    } catch (error) {
      if (!delayDiagnosis) throw error;
    }
  });
  try {
    await page.goto(new URL('/materials.html', origin).href);
    await page.getByText(/Уникальных фото: 53/).waitFor();
    await page.getByText(/500 строк/).waitFor();
    await page.getByText(/Достаточность входов \(0 из 12\)/).waitFor();
    assert.equal(await page.locator('.photo-option').count(), 12);
    assert.equal(await page.locator('.sensor-chart').count(), 2);
    assert.equal(await page.locator('.sensor-table tbody tr').count(), 40);
    assert.equal(await page.locator('.sensor-table:visible').count(), 0);
    const readings = page.getByText('Показать 20 строк', { exact: true }).first();
    await readings.click();
    assert.equal(await page.locator('.sensor-table:visible tbody tr').count(), 20);
    await readings.click();
    assert.equal(await page.locator('.sensor-table:visible').count(), 0);
    assert.equal(await page.locator('#rule-fields details[open]').count(), 0);
    assert.equal(await page.getByText(/порог тревоги не указан/).count(), 2);
    await page.getByRole('button',{name:'Показать ещё'}).click();
    assert.equal(await page.locator('.photo-option').count(), 24);
    await page.getByLabel('Категория').selectOption('valve');
    assert.equal(await page.locator('.photo-option').count(), 10);
    const photo = page.locator('.photo-option').first();
    await photo.click();
    const analyze = page.getByRole('button',{name:'Проверить фото'});
    await analyze.click();
    await page.getByText('Предварительная оценка фотографии.').waitFor();
    assert.match(uploads[0].headers()['content-type'], /^multipart\/form-data; boundary=/);
    assert.match(uploads[0].postDataBuffer().toString('latin1'), /filename="frame.jpg"/);
    const box = await page.locator('#photo-box').boundingBox();
    const image = await page.locator('#selected-image').boundingBox();
    assert.ok(Math.abs(box.x - (image.x + image.width * 0.25)) < 1);
    assert.ok(Math.abs(box.width - image.width * 0.5) < 1);
    assert.ok(Math.abs(box.y - (image.y + image.height * 0.2)) < 1);
    if (screenshotDir) {
      const imagePath = suffix => path.join(screenshotDir, `materials-${name.toLowerCase()}${suffix}.png`);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: imagePath('') });
      await page.locator('#measurements').screenshot({ path: imagePath('-sensors') });
      await page.locator('#manual').screenshot({ path: imagePath('-manual') });
    }
    await Promise.all([page.waitForRequest('**/analyze'), analyze.click()]);
    await page.locator('.photo-option').nth(1).click();
    await hold(800);
    assert.equal(await page.locator('#photo-box').isVisible(), false);
    await analyze.click();
    await page.getByText('Модель занята').waitFor();
    assert.equal(await page.locator('#photo-result').isVisible(), false);
    const manual = page.getByRole('button',{name:'Проверить связки'});
    await manual.click();
    await page.getByText('Укажите хотя бы одно подтверждённое значение.').waitFor();
    assert.equal(diagnose, undefined);
    await page.getByText('Подтверждённые наблюдения', { exact: true }).click();
    await page.locator('#vision-guard_issue').selectOption('true');
    await page.getByText(/Достаточность входов \(1 из 12\)/).waitFor();
    await manual.click();
    await page.getByText('Сработало правил: 1.').waitFor();
    assert.deepEqual(diagnose,{serial:'P-102',telemetry:{},vision:{guard_issue:true}});
    await page.locator('#vision-guard_issue').selectOption('false');
    await manual.click();
    await page.getByText('По введённым данным ни одна связка не сработала.').waitFor();
    assert.deepEqual(diagnose,{serial:'P-102',telemetry:{},vision:{guard_issue:false}});
    await page.locator('#vision-guard_issue').selectOption('true');
    delayDiagnosis = true;
    const photoAborted = page.waitForEvent('requestfailed', request => request.url().endsWith('/analyze'));
    const diagnoseAborted = page.waitForEvent('requestfailed', request => request.url().endsWith('/api/diagnose'));
    await Promise.all([page.waitForRequest('**/analyze'), analyze.click()]);
    await Promise.all([page.waitForRequest('**/api/diagnose'), manual.click()]);
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    await Promise.all([photoAborted, diagnoseAborted]);
    assert.equal(await analyze.isEnabled(), true);
    assert.equal(await manual.isEnabled(), true);
    assert.equal(await page.locator('#photo-result').isVisible(), false);
    assert.equal(await page.locator('#manual-findings').textContent(), '');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log(name + ': PASS');
  } finally { await page.close(); }
}
(async () => {
  if (screenshotDir) fs.mkdirSync(screenshotDir, {recursive:true});
  for (const [engine, launch, options, name] of [
    [chromium, process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {}, {viewport:{width:1280,height:900}}, 'Chromium'],
    [webkit, {}, devices['iPhone 13'], 'WebKit'],
  ]) {
    const browser = await engine.launch(launch);
    try { await run(browser,options,name); } finally { await browser.close(); }
  }
})().catch(error => {console.error(error);process.exitCode=1;});
