const byId = (id) => document.getElementById(id);

const viewfinder = byId('viewfinder');
const example = byId('example');
const video = byId('video');
const snapshot = byId('snapshot');
const detection = byId('detection');
const checkButton = byId('check');
const cameraButton = byId('camera');
const demoScenario = byId('demo-scenario');
const endpoint = document.querySelector('main').dataset.analysisEndpoint.trim();

const unavailableResults = {
  no_marker: ['Оборудование не распознано', 'Метка не видна. Наведите камеру на метку и сделайте новый кадр.'],
  unusable: ['Не удалось оценить узел', 'Снимите узел крупнее при хорошем освещении и повторите проверку.'],
};

let stream = null;
let frameSource = null;
let openingCamera = false;
let checking = false;
let result = null;
let cameraRequest = 0;

function showMessage(text = '', error = false) {
  const message = byId('message');
  message.textContent = text;
  message.dataset.error = String(error);
}

function positionDetection() {
  if (!result?.box || detection.hidden) return;

  // The still image is letterboxed by object-fit: contain.
  const { width, height } = viewfinder.getBoundingClientRect();
  const scale = Math.min(width / snapshot.width, height / snapshot.height);
  const frameWidth = snapshot.width * scale;
  const frameHeight = snapshot.height * scale;
  const box = result.box;

  detection.style.left = `${(width - frameWidth) / 2 + box.x * frameWidth}px`;
  detection.style.top = `${(height - frameHeight) / 2 + box.y * frameHeight}px`;
  detection.style.width = `${box.width * frameWidth}px`;
  detection.style.height = `${box.height * frameHeight}px`;
}

function render() {
  const frozen = frameSource !== null;
  const live = Boolean(stream && !frozen);
  const frameReady = live ? video.readyState >= 2 && video.videoWidth > 0 :
    example.complete && example.currentSrc === example.src && example.naturalWidth > 0;
  example.hidden = live || frozen;
  video.hidden = !live;
  snapshot.hidden = !frozen;
  viewfinder.dataset.portrait = String(frozen ? snapshot.height > snapshot.width :
    live && video.videoHeight > video.videoWidth);
  byId('guide').hidden = frozen;
  byId('source').dataset.live = String(live);
  byId('source-text').textContent = live ? 'Камера' : frameSource === 'camera' ? 'Кадр с камеры' : 'Пример кадра';
  byId('mode').textContent = endpoint && (live || frameSource === 'camera') ? 'Серверный анализ' : 'Тестовый режим';
  byId('demo-options').hidden = Boolean(stream || openingCamera || frameSource === 'camera');
  demoScenario.disabled = checking;

  checkButton.disabled = openingCamera || checking || (!frozen && !frameReady);
  cameraButton.disabled = checking && !stream;
  byId('check-label').textContent = checking ? 'Проверяем…' :
    result ? (frameSource === 'camera' ? 'Новый кадр' : 'Проверить ещё раз') : 'Проверить кадр';
  byId('camera-label').textContent = openingCamera ? 'Отменить запуск' :
    stream ? 'Выключить камеру' : 'Включить камеру';

  const detected = result && (result.status === 'defect' || result.status === 'clear');
  detection.hidden = !detected;
  byId('details').hidden = !detected;
  byId('empty').hidden = Boolean(detected);

  if (detected) {
    detection.dataset.status = result.status;
    detection.setAttribute('aria-label', `Открыть карточку узла: ${result.component}`);
    byId('detection-label').textContent = 'Узел';
    byId('finding').dataset.status = result.status;
    byId('finding').textContent = result.finding;
    byId('equipment').textContent = result.equipment.name;
    byId('equipment-id').textContent = result.equipment.id;
    byId('component').textContent = result.component;
    byId('service-life').textContent = result.serviceLife;
    byId('next-service').textContent = result.nextService;
    byId('recommendation').textContent = result.recommendation;
    positionDetection();
  } else {
    const [title, description] = unavailableResults[result?.status] ?? [
      'Ожидает проверки',
      'После проверки здесь появятся данные узла и рекомендация по обслуживанию.',
    ];
    byId('empty-title').textContent = checking ? 'Проверяем кадр…' : title;
    byId('empty-text').textContent = checking ? '' : description;
  }
}

function stopCamera() {
  cameraRequest += 1;
  openingCamera = false;
  const previousStream = stream;
  stream = null;
  video.srcObject = null;
  previousStream?.getTracks().forEach((track) => track.stop());
}

function cameraError(error) {
  if (error.name === 'NotAllowedError') return 'Разрешите доступ к камере в настройках сайта или приложения.';
  if (error.name === 'NotFoundError') return 'Камера не найдена. Можно проверить пример кадра.';
  if (error.name === 'NotReadableError') return 'Камера недоступна. Закройте другие приложения, которые её используют, и повторите попытку.';
  return 'Не удалось открыть камеру. Можно проверить пример кадра.';
}

async function toggleCamera() {
  if (checking && !stream) return;
  showMessage();

  if (stream || openingCamera) {
    stopCamera();
    render();
    return;
  }

  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    showMessage('Для доступа к камере откройте страницу по HTTPS. Можно проверить пример кадра.', true);
    return;
  }

  const request = ++cameraRequest;
  openingCamera = true;
  showMessage('Разрешите доступ к камере в запросе браузера.');
  render();

  try {
    const opened = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
    });
    if (request !== cameraRequest) {
      opened.getTracks().forEach((track) => track.stop());
      return;
    }

    stream = opened;
    frameSource = null;
    result = null;
    showMessage();
    video.srcObject = stream;
    opened.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (stream !== opened) return;
      stopCamera();
      showMessage('Камера отключилась. Включите её снова для нового кадра.', true);
      render();
    });
    render();
    await video.play();
  } catch (error) {
    if (request !== cameraRequest) return;
    stopCamera();
    showMessage(cameraError(error), true);
    render();
  } finally {
    if (request === cameraRequest) {
      openingCamera = false;
      render();
    }
  }
}

function freezeFrame() {
  const source = stream ? video : example;
  const width = stream ? video.videoWidth : example.naturalWidth;
  const height = stream ? video.videoHeight : example.naturalHeight;
  if (!width || !height || (stream && video.readyState < 2)) throw new Error('Frame unavailable');

  const scale = Math.min(1, 1280 / Math.max(width, height));
  snapshot.width = Math.max(1, Math.round(width * scale));
  snapshot.height = Math.max(1, Math.round(height * scale));
  snapshot.getContext('2d').drawImage(source, 0, 0, snapshot.width, snapshot.height);
  frameSource = stream ? 'camera' : 'example';
}

async function analyzeFrame(useServer) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const options = { signal: controller.signal, cache: 'no-store' };
    if (useServer) {
      const image = await new Promise((resolve) => snapshot.toBlob(resolve, 'image/jpeg', 0.85));
      if (!image) throw new Error('JPEG unavailable');
      const body = new FormData();
      body.append('file', image, 'frame.jpg');
      Object.assign(options, { method: 'POST', body });
    }
    const mockPath = frameSource === 'example' ? demoScenario.selectedOptions[0].dataset.response : 'mock-analysis.json';
    const response = await fetch(useServer ? endpoint : `./${mockPath}`, options);
    if (!response.ok) throw new Error(`Analysis returned ${response.status}`);
    return readResult(await response.json());
  } finally {
    clearTimeout(timeout);
  }
}

function readResult(data) {
  if (data && Object.prototype.hasOwnProperty.call(unavailableResults, data.status)) return data;
  if (!data || !['defect', 'clear'].includes(data.status)) throw new Error('Invalid result status');

  const { x, y, width, height } = data.box ?? {};
  const text = [data.equipment?.id, data.equipment?.name, data.component, data.finding,
    data.serviceLife, data.nextService, data.recommendation];
  if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0 ||
      x + width > 1.000001 || y + height > 1.000001 || text.some((value) => typeof value !== 'string')) {
    throw new Error('Invalid inspection result');
  }
  return data;
}

async function checkFrame() {
  if (openingCamera || checking) return;
  showMessage();

  if (result && frameSource === 'camera') {
    if (!stream) {
      await toggleCamera();
      return;
    }
    const resumedStream = stream;
    result = null;
    frameSource = null;
    render();
    video.play().catch(() => {
      if (stream !== resumedStream) return;
      stopCamera();
      showMessage('Не удалось возобновить камеру. Включите её снова.', true);
      render();
    });
    return;
  }

  result = null;
  checking = true;
  try {
    if (!frameSource) freezeFrame();
    render();
    result = await analyzeFrame(Boolean(endpoint && frameSource === 'camera'));
    showMessage('Проверка завершена.');
  } catch (error) {
    showMessage(error.name === 'AbortError' ? 'Сервер не ответил вовремя. Повторите проверку.' :
      'Не удалось проверить кадр. Повторите попытку.', true);
  } finally {
    checking = false;
    render();
  }
}

cameraButton.addEventListener('click', toggleCamera);
checkButton.addEventListener('click', checkFrame);
function updateDemoExample() {
  const { image } = demoScenario.selectedOptions[0].dataset;
  frameSource = null;
  result = null;
  showMessage();
  if (example.getAttribute('src') !== image) example.src = image;
  render();
}
demoScenario.addEventListener('change', updateDemoExample);
example.addEventListener('load', render);
example.addEventListener('error', () => showMessage('Пример кадра недоступен. Попробуйте включить камеру.', true));
video.addEventListener('loadeddata', render);
video.addEventListener('canplay', render);
video.addEventListener('playing', render);
video.addEventListener('resize', render);
detection.addEventListener('click', () => {
  byId('result-title').focus({ preventScroll: true });
  byId('result').scrollIntoView({ block: 'nearest' });
});
function releaseCamera() {
  if (!stream && !openingCamera) return;
  stopCamera();
  showMessage();
  render();
}

window.addEventListener('pagehide', releaseCamera);
document.addEventListener('visibilitychange', () => {
  if (document.hidden && stream) releaseCamera();
});
if ('ResizeObserver' in window) {
  new ResizeObserver(positionDetection).observe(viewfinder);
} else {
  window.addEventListener('resize', positionDetection);
}
updateDemoExample();
