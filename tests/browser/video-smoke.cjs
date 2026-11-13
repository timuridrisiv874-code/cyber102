const assert = require('node:assert/strict');
const { EventEmitter, once } = require('node:events');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium, devices, webkit } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const webRoot = path.resolve(__dirname, '../../web');
const screenshotDir = process.env.VIDEO_SMOKE_SCREENSHOTS;
const contentTypes = {
  '.css': 'text/css',
  '.html': 'text/html',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript',
  '.json': 'application/json',
};
const model = 'InternVL3 (тестовый ответ)';
const localObservation = {
  ...JSON.parse(fs.readFileSync(path.join(webRoot, 'mock-analysis.json'), 'utf8')),
  source: 'local_vision',
  model,
  observation: 'Проверка контура',
  durationSeconds: 0.3,
  serviceLife: 'Нужны паспорт и история узла',
  nextService: 'По регламенту оборудования',
};

function createMockServer() {
  const events = new EventEmitter();
  const frames = [];
  const server = http.createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (pathname === '/analyze') {
      try {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const frame = {
          method: request.method,
          contentType: request.headers['content-type'],
          body: Buffer.concat(chunks),
          async waitUntilClosed() {
            if (response.closed || response.destroyed) return;
            await once(response, 'close', { signal: AbortSignal.timeout(5000) });
          },
          reply(status, result) {
            response.writeHead(status, { 'Content-Type': 'application/json' });
            response.end(JSON.stringify(result));
          },
        };
        frames.push(frame);
        events.emit('frame', frame);
      } catch (error) {
        if (!response.destroyed) response.destroy(error);
      }
      return;
    }

    const asset = path.resolve(webRoot, `.${pathname}`);
    const contentType = contentTypes[path.extname(asset)];
    if (!asset.startsWith(webRoot + path.sep) || !contentType || !fs.existsSync(asset)) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { 'Content-Type': contentType });
    fs.createReadStream(asset).pipe(response);
  });
  return {
    server,
    frames,
    async nextFrame() {
      const [frame] = await once(events, 'frame', { signal: AbortSignal.timeout(10_000) });
      assert.equal(frame.method, 'POST');
      assert.match(frame.contentType, /^multipart\/form-data; boundary=/);
      assert.match(frame.body.toString('latin1'), /name="file"; filename="frame.jpg"/);
      assert.notEqual(frame.body.indexOf(Buffer.from([0xff, 0xd8, 0xff])), -1, 'Expected a JPEG upload');
      return frame;
    },
  };
}

async function installTestCamera(page) {
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        async getUserMedia() {
          clearInterval(window.testCameraTimer);
          const canvas = document.createElement('canvas');
          canvas.width = 640;
          canvas.height = 360;
          const context = canvas.getContext('2d');
          let frame = 0;
          const paint = () => {
            context.fillStyle = frame % 2 ? '#5190e6' : '#efa342';
            context.fillRect(0, 0, 640, 360);
            context.fillStyle = '#ffffff';
            context.font = '40px sans-serif';
            context.fillText(String(frame++), 10, 50);
          };
          paint();
          window.testCameraTimer = setInterval(paint, 50);
          window.testCamera = canvas.captureStream(20);
          return window.testCamera;
        },
      },
    });
  });
}

async function run(browser, name, options, mock) {
  const page = await browser.newPage(options);
  page.setDefaultTimeout(10_000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const firstRequest = mock.frames.length;
  const frameCount = () => mock.frames.length - firstRequest;
  const cameraOn = page.getByRole('button', { name: 'Включить камеру' });
  const check = page.getByRole('button', { name: 'Проверить кадр' });
  const start = page.getByRole('button', { name: 'Начать проверку' });
  const stop = page.getByRole('button', { name: 'Остановить проверку' });
  const video = page.locator('#video');
  const checkedFrame = page.locator('#checked-viewfinder');
  const snapshot = checkedFrame.locator('#snapshot');
  const marker = checkedFrame.locator('#detection');
  const metrics = page.locator('#video-metrics');
  const checkCount = count => metrics.getByText(`Проверено кадров: ${count}`, { exact: false }).waitFor();
  const startFrame = async button => {
    const [frame] = await Promise.all([mock.nextFrame(), button.click()]);
    return frame;
  };

  try {
    await page.goto(`http://127.0.0.1:${mock.server.address().port}/index.html`);
    await page.locator('.capture-settings').filter({ has: page.locator('#camera-options') }).locator('summary').click();
    await page.locator('#demo-options > summary').click();
    await page.locator('#show-example').click();
    await installTestCamera(page);
    await check.click();
    await page.locator('#viewfinder #detection').waitFor();
    assert.equal(frameCount(), 0, 'Demo example must use its local JSON fixture');
    assert.equal(await page.locator('#mode').textContent(), 'Тестовый пример');

    await cameraOn.click();
    await video.waitFor();
    await page.locator('#camera-mode').selectOption('video');
    const interval = page.getByLabel('Интервал');
    assert.equal(await interval.inputValue(), '5000');
    await interval.selectOption('2000');

    const first = await startFrame(start);
    await delay(2250);
    assert.equal(frameCount(), 1, 'Slow response must prevent a second in-flight request');
    assert.equal(await snapshot.isVisible(), false);

    const next = mock.nextFrame();
    first.reply(200, localObservation);
    await checkCount(1);
    await marker.waitFor();
    assert.equal(await video.isVisible(), true);
    assert.equal(await page.locator('#viewfinder #detection').count(), 0);
    await page.locator('#vision-observation').getByText('Проверка контура', {exact:true}).waitFor();
    await page.locator('.result-provenance > summary').click();
    assert.equal(await page.locator('#analysis-model').innerText(), model);
    await page.locator('.result-provenance > summary').click();
    const firstImage = await snapshot.evaluate(canvas => canvas.toDataURL());
    const second = await next;
    assert.equal(await snapshot.evaluate(canvas => canvas.toDataURL()), firstImage, 'Pending frame must not replace the last result');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (screenshotDir) {
      await page.screenshot({ path: path.join(screenshotDir, `video-${name.toLowerCase()}.png`), fullPage: true });
    }

    const thirdPending = mock.nextFrame();
    second.reply(200, { status: 'unusable', source: 'local_vision', model });
    await checkCount(2);
    assert.match(await metrics.innerText(), /Темп: [\d.,]+ кадра\/с/);
    await page.getByText('Не удалось оценить узел').waitFor();
    assert.equal(await marker.isVisible(), false);
    assert.notEqual(await snapshot.evaluate(canvas => canvas.toDataURL()), firstImage);
    const third = await thirdPending;
    await stop.click();
    await page.getByText('Видеопроверка остановлена.').waitFor();
    await third.waitUntilClosed();
    await delay(2250);
    assert.equal(frameCount(), 3, 'Stop must abort the current request and prevent subsequent captures');
    await checkCount(2);
    assert.equal(await video.isVisible(), true);

    const fourth = await startFrame(start);
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    await fourth.waitUntilClosed();
    await cameraOn.waitFor();
    assert.equal(await checkedFrame.isVisible(), false);
    assert.equal(await page.evaluate(() => window.testCamera.getTracks()[0].readyState), 'ended');

    await cameraOn.click();
    await video.waitFor();
    const single = await startFrame(check);
    single.reply(200, localObservation);
    await page.locator('#viewfinder #detection').waitFor();
    assert.equal(await video.isVisible(), false);
    await page.getByRole('button', { name: 'Новый кадр' }).click();
    await video.waitFor();
    await page.locator('#camera-mode').selectOption('video');
    const unavailable = await startFrame(start);
    unavailable.reply(503, { error: 'Локальная модель занята.' });
    await page.getByText('Локальная модель занята.').waitFor();
    assert.equal(await start.isEnabled(), true);
    assert.equal(await checkedFrame.isVisible(), false);
    assert.equal(frameCount(), 6);
    assert.deepEqual(errors, []);
    console.log(`${name}: PASS — ${frameCount()} JPEG uploads to the simulated API`);
  } finally {
    await page.close();
  }
}

async function main() {
  if (screenshotDir) fs.mkdirSync(screenshotDir, { recursive: true });
  const mock = createMockServer();
  mock.server.listen(0, '127.0.0.1');
  await once(mock.server, 'listening');
  try {
    const chromePath = process.env.CHROME_PATH;
    const engines = [
      [chromium, 'Chromium', {}, chromePath ? { executablePath: chromePath } : {}],
      [webkit, 'WebKit', devices['iPhone 13'], {}],
    ];
    for (const [engine, name, context, launch] of engines) {
      const browser = await engine.launch(launch);
      try {
        await run(browser, name, context, mock);
      } finally {
        await browser.close();
      }
    }
  } finally {
    mock.server.close();
    mock.server.closeAllConnections();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
