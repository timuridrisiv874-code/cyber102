const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium, webkit, devices} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.CYBER102_SMOKE_URL || 'http://127.0.0.1:8130';
const screenshotDir = process.env.WORKSPACE_SCREENSHOTS;
const credentials = process.env.CYBER102_ACCESS_FILE ? JSON.parse(fs.readFileSync(process.env.CYBER102_ACCESS_FILE)) : null;
const hold = ms => new Promise(resolve => setTimeout(resolve, ms));

async function loadAfterPageRestore(page, pathname, endpoints, ready) {
  const gates = endpoints.map(endpoint => {
    let release;
    const wait = new Promise(resolve => { release = resolve; });
    return { endpoint, wait, release, attempts: 0 };
  });
  for (const gate of gates) {
    await page.route(`**${gate.endpoint}`, async route => {
      const attempt = ++gate.attempts;
      if (attempt === 1) await gate.wait;
      try {
        await route.fallback();
      } catch (error) {
        if (attempt !== 1) throw error;
      }
    });
  }
  try {
    const matches = endpoint => request => new URL(request.url()).pathname === endpoint;
    await Promise.all([
      ...endpoints.map(endpoint => page.waitForRequest(matches(endpoint))),
      page.goto(new URL(pathname, origin).href),
    ]);
    const failed = endpoints.map(endpoint => page.waitForEvent('requestfailed', matches(endpoint)));
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    await Promise.all(failed);
    const retried = endpoints.map(endpoint => page.waitForRequest(matches(endpoint)));
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await Promise.all(retried);
    await ready.waitFor();
    for (const gate of gates) assert.equal(gate.attempts, 2, `Expected one restore request for ${gate.endpoint}`);
  } finally {
    for (const gate of gates) gate.release();
    await Promise.all(endpoints.map(endpoint => page.unroute(`**${endpoint}`)));
  }
}

async function run(browser, options, name) {
  const context = await browser.newContext({...options, ...(credentials ? {httpCredentials:{username:credentials.username,password:credentials.password,origin}} : {})});
  if (credentials) {
    const authorization = 'Basic ' + Buffer.from(credentials.username + ':' + credentials.password).toString('base64');
    await context.route(new URL(origin).origin + '/**', async route => {
      try { await route.continue({headers:{...route.request().headers(),authorization}}); }
      catch (error) {
        // A held page route can reach this handler after the lifecycle check aborts it.
        if (!/Route is already handled|Target page, context or browser has been closed/.test(error.message)) throw error;
      }
    });
  }
  const page = await context.newPage();
  page.setDefaultTimeout(12_000);
  const errors = [];
  const snapshots = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if(request.url().includes('/api/telemetry/snapshot?')) snapshots.push(request.url());});
  try {
    await loadAfterPageRestore(page, '/documents.html', ['/api/customer/documents', '/api/customer/equipment-passports'],
      page.locator('#equipment-grid .workspace-card').first());
    await page.getByText('22 PDF', {exact:true}).waitFor();
    assert.equal(await page.locator('.document-item').count() >= 22, true);
    const equipmentSource = page.locator('#equipment-grid').locator('..').getByText('О данных', {exact:true});
    await equipmentSource.click();
    await page.getByText(/Примеры измерений не привязаны к этим агрегатам/).waitFor();
    assert.ok((await page.locator('#passport-limitations').textContent()).trim());
    await equipmentSource.click();
    const kind = page.getByLabel('Тип оборудования');
    const equipment = page.locator('#equipment-grid .workspace-card');
    assert.equal(await kind.inputValue(), 'pump');
    assert.equal(await equipment.count(), 4);
    await kind.selectOption('all');
    assert.equal(await equipment.count(), 16);
    await kind.selectOption('motor');
    assert.ok(await equipment.count() > 0 && await equipment.count() < 16);
    await kind.selectOption('pump');
    assert.equal(await equipment.count(), 4);
    const passport = equipment.filter({hasText:'21031004-1'});
    assert.equal(await passport.count(), 1);
    assert.match(await passport.textContent(), /AMS PCP60S12/);
    await passport.locator('summary').click();
    const link = passport.locator('a[href^="/api/customer/documents/"]').first();
    assert.match(await link.getAttribute('href'), /^\/api\/customer\/documents\/[a-z0-9-]+\/pdf#page=\d+$/);
    const pdf = await context.request.get(new URL(await link.getAttribute('href'), origin).href,
      {headers:{range:'bytes=0-63'}});
    assert.equal(pdf.status(), 206);
    assert.equal(pdf.headers()['content-type'], 'application/pdf');
    assert.equal((await pdf.body()).subarray(0,5).toString(), '%PDF-');
    const passportActions = passport.locator('.equipment-card-actions');
    const originalViewport = page.viewportSize();
    for (const width of [320, 390, 1280]) {
      await page.setViewportSize({width, height:900});
      const [inspect, search] = await passportActions.evaluate(actions => [...actions.children].map(action => action.getBoundingClientRect().toJSON()));
      assert.ok(inspect.right <= search.left || inspect.bottom <= search.top, 'Card actions must not overlap');
      assert.ok(search.right <= width, 'Search button must fit on the screen');
    }
    if (originalViewport) await page.setViewportSize(originalViewport);
    await page.getByLabel('Поиск по названию и тексту').fill('21031004');
    assert.equal(await page.locator('#document-list .document-item').count() > 0, true);
    await page.getByLabel('Поиск по названию и тексту').fill('not-found-cyber102');
    assert.equal(await page.locator('#document-list .document-item').count(), 0);
    await page.getByLabel('Поиск по названию и тексту').fill('');
    assert.equal(await page.locator('#document-list .document-item').count(), 22);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (screenshotDir) await page.screenshot({path:path.join(screenshotDir,`documents-${name}.png`)});
    const unavailable = route => route.fulfill({status:503, body:'upstream unavailable'});
    await page.route('**/api/customer/documents', unavailable);
    await page.goto(new URL('/documents.html', origin).href);
    await page.getByText('Библиотека временно недоступна.').waitFor({timeout:40000});
    await page.locator('#equipment-grid .workspace-card').first().waitFor();
    await page.locator('#equipment-grid .workspace-card summary').first().click();
    assert.ok(await page.locator('#equipment-grid a[href^="/api/customer/documents/"]').first().isVisible());
    await page.unroute('**/api/customer/documents', unavailable);
    await page.getByRole('button', {name:'Повторить загрузку'}).click();
    await page.getByText('22 PDF', {exact:true}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Повторить загрузку'}).isVisible(),false);

    await loadAfterPageRestore(page, '/telemetry.html', ['/api/telemetry/catalog'],
      page.getByText('Запись 1 из 500', {exact:true}));
    assert.equal(await page.locator('#telemetry-channels .workspace-card').count(),6);
    await page.locator('#source-title').click();
    await page.getByText(/Часовой пояс записей не указан/).waitFor();
    await page.getByText('Происхождение текущей строки', {exact:true}).click();
    await page.locator('#replay-provenance').waitFor();
    assert.equal(JSON.parse(await page.locator('#replay-provenance').textContent()).source, 'customer_replay');
    await page.locator('#source-title').click();
    assert.equal(await page.getByText('Порог не задан', {exact:true}).count(),1);
    await page.getByRole('button',{name:'Следующая запись'}).click();
    await page.getByText('Запись 2 из 500', {exact:true}).waitFor();
    async function seek(value) {await page.locator('#replay-position').evaluate((el,v) => {el.value=String(v);el.dispatchEvent(new Event('input',{bubbles:true}));}, value);}
    await seek(402);
    await page.getByText('Запись 403 из 500',{exact:true}).waitFor();
    await page.getByText(/Исходная метка и оценка по порогам расходятся/).waitFor();
    if (screenshotDir) await page.screenshot({path:path.join(screenshotDir,`telemetry-${name}.png`)});
    await seek(499);
    await page.getByText('Запись 500 из 500',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Следующая запись'}).isDisabled(),true);
    await page.getByLabel('Интервал').selectOption('1000');
    await page.getByRole('button',{name:'Воспроизвести',exact:true}).click();
    await page.getByText('Запись 1 из 500',{exact:true}).waitFor();
    await page.getByText('Запись 2 из 500',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Пауза',exact:true}).click();
    const paused = snapshots.length;
    await hold(1250);
    assert.equal(snapshots.length,paused);
    await page.route('**/api/telemetry/snapshot?index=400', async route => {await hold(700);try {await route.fallback();}catch {}});
    await seek(400); await seek(401);
    await page.getByText('Запись 402 из 500',{exact:true}).waitFor();
    await hold(850);
    assert.equal(await page.locator('#replay-position').inputValue(),'401');
    const pendingSeek = request => new URL(request.url()).searchParams.get('index') === '400';
    await Promise.all([page.waitForRequest(pendingSeek), seek(400)]);
    const canceledSeek = page.waitForEvent('requestfailed', pendingSeek);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    await canceledSeek;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    assert.equal(await page.locator('#replay-position').inputValue(), '401');
    await page.getByText('Запись 402 из 500 · пауза', { exact: true }).waitFor();
    await page.route('**/api/telemetry/snapshot?index=3', route => route.fulfill({status:503,json:{error:'Тест: источник недоступен'}}));
    await seek(3);
    await page.getByText('Тест: источник недоступен',{exact:true}).waitFor();
    assert.equal(await page.locator('#telemetry-channels .workspace-card').count(),0);
    await page.getByRole('button',{name:'В начало'}).click();
    await page.getByText('Запись 1 из 500',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Воспроизвести',exact:true}).click();
    await page.getByText('Запись 2 из 500',{exact:true}).waitFor();
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    const stopped = snapshots.length;
    await hold(1250);
    assert.equal(snapshots.length,stopped);
    assert.equal(await page.getByRole('button',{name:'Воспроизвести',exact:true}).isEnabled(),true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log(`${name}: PASS (22 PDFs, passports, search, playback, seek, pause, abort, restore, errors)`);
  } finally {await context.close();}
}
(async () => {
  if(screenshotDir) fs.mkdirSync(screenshotDir,{recursive:true});
  for(const [engine,launch,options,name] of [
    [chromium,process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {},{viewport:{width:1280,height:900}},'chromium'],
    [webkit,{},devices['iPhone 13'],'webkit'],
  ]) {
    const browser=await engine.launch(launch);
    try {await run(browser,options,name);} finally {await browser.close();}
  }
})().catch(error => {console.error(error);process.exitCode=1;});
