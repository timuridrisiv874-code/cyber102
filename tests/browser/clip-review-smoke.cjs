// Decode a real MP4; the server comparison is an explicit contract fixture, not a model accuracy test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium, webkit, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cyber-clip-review-'));
const clip = path.join(temp, 'private-original-name.mp4');
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=10',
  '-vf', 'drawbox=x=1456:y=872:w=192:h=144:color=red:t=fill,drawbox=x=1552:y=872:w=96:h=144:color=blue:t=fill,drawgrid=w=5:h=3:t=1:c=white@0.6',
  '-t', '5', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', clip]);
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpg': 'image/jpeg', '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png', '.ico': 'image/x-icon' };
const state = { android: false, posts: [], photoPosts: [], mode: 'success', pending: [], available: true };
const reply = (res, code, data) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/capabilities') return reply(res, 200, { vision: { profiles: ['standard', 'fast'], videoReview: state.available } });
  if (url.pathname === '/analyze') {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    state.photoPosts.push(Buffer.concat(chunks).toString('latin1'));
    const response = { ...JSON.parse(fs.readFileSync(path.join(root,'web/mock-analysis.json'))),
      source: 'local_vision', model: 'clip-contract-test', profile: 'standard' };
    state.pending.push(() => reply(res, 200, response));
    return;
  }
  if (url.pathname === '/api/video-review') {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString(); const body = JSON.parse(raw); state.posts.push({ body, raw });
    const result = { model: 'clip-contract-test', status: 'defect', summary: 'Тест: последовательность из восьми кадров.',
      observations: body.frames.map((frame, index) => {
        const status = [0,1,4,5,7].includes(index) ? 'defect' : index === 3 ? 'unusable' : 'clear';
        return {frameIndex:index, timestampSeconds:frame.timestampSeconds, status,
          component:status === 'unusable' ? 'none' : 'pump',
          condition:{defect:'corrosion',unusable:'uncertain',clear:'no_visible_defect'}[status],
          observation: `Тестовое наблюдение ${index + 1}`, box: status === 'unusable' ? [0,0,0,0] : [100+index,100,500,500]};
      }) };
    if (state.mode === 'mismatch') result.observations[0].timestampSeconds += 1;
    if (state.mode === 'one' || state.mode === 'clear') for (const row of result.observations) {
      if (state.mode === 'one' && row.frameIndex === 0) continue;
      Object.assign(row, {status: 'clear', component: 'pump', condition: 'no_visible_defect'});
    }
    if (state.mode === 'separated-details') {
      for (const [index, component] of [[0,'pump'],[1,'motor'],[4,'valve'],[5,'other'],[7,'pipe']]) {
        result.observations[index].component = component;
      }
      result.observations[7].condition = 'wet_patch';
      result.defectDetails = body.frames.map((frame, index) => ({
        frameIndex: index, timestampSeconds: frame.timestampSeconds, status: 'checked',
        boxes: [1, 2, 7].includes(index) ? [{kind: 'crack', box: [240, 200, 720, 510]}] : [],
      }));
    }
    if (state.mode === 'empty') result.observations = [];
    if (state.mode === 'unavailable') return reply(res, 503, {code:'vision_unavailable', error:'Сервис анализа временно недоступен. Повторите разбор этого видео позже.'});
    if (state.mode === 'hold') { state.pending.push(() => reply(res, 200, result)); return; }
    return reply(res, 200, result);
  }
  const android = state.android && (url.pathname === '/index.html' || url.pathname.startsWith('/__mobile_inspection/'));
  const base = path.join(root, android ? 'android/app/src/main/assets/inspection' : 'web');
  const target = path.resolve(base, '.' + url.pathname.replace('/__mobile_inspection/', '/'));
  if (!target.startsWith(base + path.sep) || !mime[path.extname(target)] || !fs.existsSync(target)) return reply(res, 404, {});
  res.writeHead(200, { 'Content-Type': mime[path.extname(target)] }); res.end(fs.readFileSync(target));
});
async function until(condition) { for (let i = 0; i < 200; i++) { if (condition()) return; await delay(25); } throw new Error('Condition timed out'); }

async function checkRegion(page, timestamp) {
  const original = {x:1452,y:870,width:204,height:150};
  const canvasRegion = {x:968,y:580,width:136,height:100};
  const before = state.photoPosts.length;
  const expected = await page.locator('#uploaded-video').evaluate(async (video, area) => {
    if (video.videoWidth !== 1920 || video.videoHeight !== 1080 || !video.paused) throw new Error('Expected paused 1920×1080 video');
    const output = document.createElement('canvas'); output.width = area.width; output.height = area.height;
    const context = output.getContext('2d');
    const jpeg = async () => {
      const blob = await new Promise(resolve => output.toBlob(resolve, 'image/jpeg', 0.85));
      return new Promise(resolve => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.readAsDataURL(blob);
      });
    };
    context.drawImage(video, area.x, area.y, area.width, area.height, 0, 0, area.width, area.height);
    const direct = await jpeg();
    const preview = document.createElement('canvas'); preview.width = 1280; preview.height = 720;
    preview.getContext('2d').drawImage(video, 0, 0, 1280, 720);
    context.drawImage(preview, area.x*2/3, area.y*2/3, area.width*2/3, area.height*2/3, 0, 0, area.width, area.height);
    return {direct, enlargedPreview:await jpeg()};
  }, original);
  assert.notEqual(expected.direct, expected.enlargedPreview, 'Fine detail must distinguish original pixels from an enlarged preview');
  await page.locator('#choose-region').click();
  const dialog = page.getByRole('dialog', {name:'Выбрать область'});
  await dialog.locator('summary').press('Enter');
  for (const [name, key] of [['Слева','x'],['Сверху','y'],['Ширина','width'],['Высота','height']]) {
    await dialog.getByLabel(name, {exact:true}).fill(String(canvasRegion[key]));
  }
  await dialog.getByRole('button', {name:'Проверить область'}).press('Enter');
  await until(() => state.photoPosts.length === before + 1 && state.pending.length === 1);
  const request = state.photoPosts.at(-1);
  const contentType = 'multipart/form-data; boundary=' + request.slice(2, request.indexOf('\r\n'));
  const form = await new Response(Buffer.from(request,'latin1'), {headers:{'Content-Type':contentType}}).formData();
  assert.equal(form.get('profile'),'standard');
  assert.equal(form.get('captureSource'),'uploaded_video');
  assert.ok(Math.abs(Number(form.get('videoTimeSeconds')) - timestamp) <= .001);
  const file = form.get('file'); assert.equal(file.type,'image/jpeg');
  const bytes = Buffer.from(await file.arrayBuffer());
  assert.equal(bytes.toString('base64') === expected.direct,true,'Sent JPEG must contain the exact region sampled from the original paused frame');
  assert.equal(bytes.toString('base64') === expected.enlargedPreview,false);
  const dimensions = await page.evaluate(async data => {
    const image = new Image(); image.src='data:image/jpeg;base64,'+data; await image.decode();
    return [image.naturalWidth,image.naturalHeight];
  },bytes.toString('base64'));
  assert.deepEqual(dimensions,[204,150]);
  state.pending.shift()();
  await page.locator('#details').waitFor();
  assert.match(await page.locator('#capture-provenance').textContent(), /Фрагмент видео/);
  assert.deepEqual(await page.locator('#snapshot').evaluate(c=>[c.width,c.height]),dimensions);
  await page.getByRole('button',{name:'Весь кадр',exact:true}).click();
  await page.locator('#uploaded-video').waitFor();
  assert.ok(Math.abs(await page.locator('#uploaded-video').evaluate(v=>v.currentTime)-timestamp)<.001);
  assert.equal(await page.locator('#details').isVisible(),false);
  assert.equal(await page.locator('.video-review-frame').count(),1);
  assert.equal(state.posts.length,1);
  await page.locator('#choose-region').click();
  await dialog.getByRole('button',{name:'Отмена'}).press('Enter');
  assert.equal(state.photoPosts.length,before+1);
}

async function run(browser, engine, android) {
  Object.assign(state, { android, posts: [], photoPosts: [], mode: 'success', pending: [], available: true });
  const page = await browser.newPage(devices['iPhone 13']);
  const errors = [], violations = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.exposeFunction('captureCspViolation', value => violations.push(value));
  await page.addInitScript(() => document.addEventListener('securitypolicyviolation', event => window.captureCspViolation(event.blockedURI)));
  page.setDefaultTimeout(15000);
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
    await page.locator('#media-file').setInputFiles(clip);
    await page.waitForFunction(() => document.querySelector('#uploaded-video').readyState >= 2);
    if (!android) {
      const primary = page.locator('#capture-actions button.primary:visible');
      assert.equal(await primary.count(), 1);
      assert.equal(await primary.getAttribute('id'), 'video-review-run');
      assert.equal(await primary.innerText(), 'Проверить всё видео');
      assert.equal(await page.locator('#check').isVisible(), false);
      assert.equal(state.posts.length + state.photoPosts.length, 0);
      await page.locator('#uploaded-video').evaluate(async video => {
        await new Promise(resolve => {
          video.addEventListener('seeked', resolve, {once: true}); video.currentTime = 4.8;
        });
      });
    }
    await page.locator('#video-review-run').click();
    await page.locator('#video-review-results').waitFor();
    assert.equal(state.posts.length, 1, 'Eight frames are compared in one bounded request');
    const post = state.posts[0];
    assert.deepEqual(Object.keys(post.body).sort(), ['durationSeconds', 'frames']);
    assert.equal(post.body.frames.length, 8);
    assert.equal(post.body.durationSeconds, 5);
    assert.equal(post.raw.includes('private-original-name'), false, 'Original filename is not uploaded');
    const hashes = [];
    post.body.frames.forEach((frame, index) => {
      assert.deepEqual(Object.keys(frame).sort(), ['image', 'timestampSeconds']);
      assert.equal(frame.timestampSeconds, 5 * (index + 0.5) / 8);
      const bytes = Buffer.from(frame.image, 'base64');
      assert.equal(bytes.subarray(0, 2).toString('hex'), 'ffd8');
      hashes.push(createHash('sha256').update(bytes).digest('hex'));
    });
    assert.equal(new Set(hashes).size, 8, 'Sampling decodes eight distinct actual frames');
    assert.equal(await page.locator('.video-review-frame').count(), android ? 8 : 1);
    await page.waitForFunction(() => [...document.querySelectorAll('.video-review-seek img')].every(image => image.complete && image.naturalWidth > 0));
    assert.deepEqual(violations, [], 'Bundled Android CSP must allow generated local thumbnails');
    const resultText = await page.locator('#video-review-results').textContent();
    if (android) assert.match(resultText, /не сохранён в историю/);
    else assert.doesNotMatch(resultText, /не сохранён в историю/);
    assert.equal(await page.locator('#inspection-workflow').isVisible(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (android) {
      await page.locator('.video-review-seek').nth(3).click();
      await page.waitForFunction(time => Math.abs(document.getElementById('uploaded-video').currentTime - time) < .01, 5 * 3.5 / 8);
    } else {
      const gallery = page.locator('.video-review-gallery');
      const slide = gallery.locator('.video-review-frame'), next = gallery.locator('.video-review-next');
      const previous = gallery.locator('.video-review-prev'), position = gallery.locator('.video-review-position');
      const image = slide.locator('img');
      await position.getByText('1 из 3', {exact:true}).waitFor();
      assert.equal(await previous.isDisabled(), true);
      await slide.getByText('Тестовое наблюдение 1', {exact:true}).waitFor();
      const thumbnails = [await image.getAttribute('src')];
      const swipe = (x, y, trailingClick = false) => slide.locator('img').evaluate((img, move) => {
        const options = {bubbles:true, pointerType:'touch', pointerId:17, isPrimary:true, button:0};
        img.dispatchEvent(new PointerEvent('pointerdown', {...options, clientX:160, clientY:100}));
        img.dispatchEvent(new PointerEvent('pointerup', {...options, clientX:160 + move.x, clientY:100 + move.y}));
        if (move.trailingClick) document.querySelector('.video-review-slide img').dispatchEvent(
          new MouseEvent('click', {bubbles:true, cancelable:true, detail:1}));
      }, {x, y, trailingClick});
      const playhead = await page.locator('#uploaded-video').evaluate(video => video.currentTime);
      await swipe(-10, 85);
      assert.equal(await position.innerText(), '1 из 3', 'Vertical scrolling keeps the selected finding');
      await swipe(-95, 6, true);
      await position.getByText('2 из 3', {exact:true}).waitFor();
      assert.equal(await page.locator('#uploaded-video').evaluate(video => video.currentTime), playhead, 'Swiping should not seek the player');
      await swipe(90, -7);
      await position.getByText('1 из 3', {exact:true}).waitFor();
      assert.equal(await slide.locator('.video-review-time-link').count(), 2);
      await slide.locator('.video-review-time-link').last().press('Enter');
      await slide.getByText('Тестовое наблюдение 2', {exact:true}).waitFor();
      await next.click();
      await slide.getByText('Тестовое наблюдение 5', {exact:true}).waitFor();
      thumbnails.push(await image.getAttribute('src'));
      await slide.locator('.video-review-seek').press('ArrowRight');
      await slide.getByText('Тестовое наблюдение 8', {exact:true}).waitFor();
      await position.getByText('3 из 3', {exact:true}).waitFor();
      assert.equal(await next.isDisabled(), true);
      thumbnails.push(await image.getAttribute('src'));
      assert.equal(new Set(thumbnails).size, 3, 'Each slide keeps the image of its own episode');
      const last = slide.locator('.video-review-seek');
      await last.focus(); await last.press('Enter');
      await page.waitForFunction(time => Math.abs(document.querySelector('#uploaded-video').currentTime-time)<.001, post.body.frames[7].timestampSeconds);
      await slide.getByText('Тестовое наблюдение 8', {exact:true}).waitFor();
      assert.equal(await image.getAttribute('src'), thumbnails[2]);
      assert.equal(state.photoPosts.length,0);
      await page.locator('#analysis-settings > summary').click();
      await checkRegion(page, post.body.frames[7].timestampSeconds);
      await slide.getByRole('button',{name:'Уточнить кадр'}).press('Enter');
      await until(() => state.photoPosts.length===2 && state.pending.length===1);
      assert.match(state.photoPosts.at(-1), /name="profile"\r?\n\r?\nstandard/);
      const sentTime = /name="videoTimeSeconds"\r?\n\r?\n([\d.]+)/.exec(state.photoPosts.at(-1));
      assert.ok(sentTime && Math.abs(Number(sentTime[1])-post.body.frames[7].timestampSeconds)<=.001);
      assert.equal(await last.isDisabled(), true);
      assert.equal(await page.locator('#video-review-run').isDisabled(), true);
      await last.evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));
      assert.equal(state.photoPosts.length,2);
      state.pending.shift()();
      await page.locator('#details').waitFor();
      assert.equal(await page.locator('#video-review-results').isVisible(), true, 'Refining a frame keeps the overview and its evidence available');
      assert.equal(await slide.count(), 1);
      await previous.click();
      await position.getByText('2 из 3', {exact:true}).waitFor();
      await previous.click();
      await position.getByText('1 из 3', {exact:true}).waitFor();
      await slide.locator('.video-review-time-link').first().press('Enter');
      await page.locator('#uploaded-video').waitFor();
      await slide.getByText('Тестовое наблюдение 1', {exact:true}).waitFor();
      assert.equal(await page.locator('#details').isVisible(), false);
      const residual=page.locator('.video-review-evidence');
      await residual.locator('summary').press('Enter');
      await residual.getByRole('button',{name:/Не удалось оценить/}).press('Enter');
      await residual.locator('.video-review-inspect').waitFor();
      assert.equal(state.photoPosts.length,2);
      state.mode='separated-details';
      await page.locator('#video-review-run').click();
      await gallery.waitFor();
      const headline = await page.locator('.video-review-summary').innerText();
      assert.match(headline, /При уточнении отмечены признаки трещины\./);
      assert.doesNotMatch(headline, /проводки/);
      const pages = [], total = Number((await position.innerText()).split(' из ')[1]);
      assert.ok(total >= 5 && total <= 7);
      for (let n = 1; n <= total; n++) {
        await position.getByText(n + ' из ' + total, {exact:true}).waitFor();
        assert.equal(await gallery.locator('img').count(), 1);
        pages.push(await slide.innerText());
        if (n < total) await next.click();
      }
      assert.match(pages.join(' '), /трещин/i);
      assert.match(pages.join(' '), /Трубопровод или фланец/);
      assert.match(pages.join(' '), /видимые следы жидкости/i);
      await slide.locator('.video-review-seek').click();
      await page.waitForFunction(time => Math.abs(document.querySelector('#uploaded-video').currentTime-time)<.001, post.body.frames[7].timestampSeconds);
      state.mode = 'one';
      await page.locator('#video-review-run').click();
      await slide.waitFor();
      assert.equal(await gallery.locator('.video-review-gallery-nav').count(), 0);
      state.mode = 'clear';
      await page.locator('#video-review-run').click();
      await page.locator('.video-review-summary').getByText(/явных внешних дефектов не замечено/i).waitFor();
      assert.equal(await gallery.count(), 0);
      state.mode='empty';
      await page.locator('#video-review-run').click();
      await page.locator('#video-review-status').getByText('Модель не вернула наблюдений',{exact:false}).waitFor();
      assert.equal(await page.locator('#video-review-results').isVisible(),false);
    }

    if (!android) {
      const selectedVideo = await page.locator('#uploaded-video').evaluate(el => el.currentSrc);
      state.mode = 'unavailable';
      await page.locator('#video-review-run').click();
      await page.locator('#video-review-status').getByText(/Сервис анализа временно недоступен/).waitFor();
      state.mode = 'success';
      await page.locator('#video-review-run').click();
      await page.locator('#video-review-results').waitFor();
      assert.ok(selectedVideo.startsWith('blob:'));
      assert.equal(await page.locator('#uploaded-video').evaluate(el => el.currentSrc), selectedVideo, 'Retry keeps the selected video');
    }
    state.mode = 'mismatch';
    await page.locator('#video-review-run').click();
    await page.locator('#video-review-status').getByText('Позиции наблюдений не совпали', { exact: false }).waitFor();
    assert.equal(await page.locator('#video-review-results').isVisible(), false, 'A timestamp mismatch must not show misleading frame results');

    state.mode = 'hold';
    await page.locator('#video-review-run').click(); await until(() => state.pending.length === 1);
    await page.locator('#video-review-cancel').click();
    state.pending.shift()(); await delay(100);
    assert.equal(await page.locator('#video-review-results').isVisible(), false, 'Late cancelled results are ignored');
    assert.match(await page.locator('#video-review-status').textContent(), /отменён/);
    if (!android) {
      await page.locator('#video-review-run').click(); await until(() => state.pending.length === 1);
      await page.locator('#media-file').setInputFiles(path.join(root,'web/demo-pump.jpg'));
      state.pending.shift()(); await delay(100);
      assert.equal(await page.locator('#video-review-panel').isVisible(),false);
      assert.equal(await page.locator('.video-review-frame').count(),0,'A late response must not attach timecodes to a different source');
      await page.locator('#choose-region').click();
      await page.getByRole('dialog', {name:'Выбрать область'}).waitFor();
      await page.locator('#media-file').setInputFiles(clip);
      await page.waitForFunction(() => !document.querySelector('dialog[open]') && document.querySelector('#uploaded-video').readyState >= 2);
      assert.equal(state.photoPosts.length,2,'Changing the source while selecting a region must cancel the dialog without uploading stale pixels');
    }

    state.available = false;
    await page.reload();
    await page.locator('#media-file').setInputFiles(clip);
    await page.waitForFunction(() => document.querySelector('#uploaded-video').readyState >= 2);
    assert.equal(await page.locator('#video-review-run').isDisabled(), true);
    if (!android) {
      await page.locator('#video-review-status').getByText(/Разбор видео недоступен/).waitFor();
      assert.equal(await page.locator('#check').isVisible(), false);
    }
    assert.equal(requests.includes('/api/inspections'), false);
    assert.deepEqual(errors, []);
    console.log(`${engine} ${android ? 'Android bundle' : 'web'} PASS: eight distinct JPEGs, one request, thumbnails/CSP, timecodes, seek, mismatch, cancellation, capabilities`);
  } finally { state.pending.splice(0).forEach(done => done()); await page.close(); }
}
(async () => {
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    for (const [engine, name, options] of [[chromium, 'Chromium', process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}], [webkit, 'WebKit', {}]]) {
      if (process.env.BROWSER_ENGINE && process.env.BROWSER_ENGINE !== name) continue;
      const browser = await engine.launch({ ...options, headless: process.env.HEADED !== '1' });
      try { for (const android of (process.env.WEB_ONLY ? [false] : [false, true])) await run(browser, name, android); } finally { await browser.close(); }
    }
  } finally { server.close(); server.closeAllConnections(); fs.rmSync(temp, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
