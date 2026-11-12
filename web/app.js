const byId = (id) => document.getElementById(id);

const viewfinder = byId('viewfinder');
const example = byId('example');
const video = byId('video');
const snapshot = byId('snapshot');
const detection = byId('detection');
const checkButton = byId('check');
const cameraButton = byId('camera');
const endpoint = document.querySelector('main').dataset.analysisEndpoint.trim();

const unavailableResults = {
  no_marker: ['Оборудование не распознано', 'Метка не видна. Наведите камеру на метку и сделайте новый кадр.'],
  unusable: ['Не удалось оценить узел', 'Снимите узел крупнее при хорошем освещении и повторите проверку.'],
};

let stream = null;
let frozen = false;
let pending = '';
let result = null;
let cameraRequest = 0;

function showMessage(text = '', error = false) {
  const message = byId('message');
  message.textContent = text;
  message.dataset.error = String(error);
  message.hidden = !text;
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
  const live = Boolean(stream && !frozen);
  const frameReady = live ? video.readyState >= 2 && video.videoWidth > 0 : example.naturalWidth > 0;
  example.hidden = live || frozen;
  video.hidden = !live;
  snapshot.hidden = !frozen;
  byId('guide').hidden = frozen;
  byId('source').dataset.live = String(live);
  byId('source-text').textContent = live ? 'Камера' : frozen && stream ? 'Кадр с камеры' : 'Пример кадра';
  byId('mode').textContent = endpoint && stream ? 'Серверный анализ' : 'Тестовый режим';

  checkButton.disabled = Boolean(pending) || (!frozen && !frameReady);
  cameraButton.disabled = Boolean(pending);
  byId('check-label').textContent = pending === 'analysis' ? 'Проверяем…' :
    frozen && result ? (stream ? 'Новый кадр' : 'Проверить ещё раз') : 'Проверить кадр';
  byId('camera-label').textContent = pending === 'camera' ? 'Открываем…' :
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
    byId('empty-title').textContent = pending === 'analysis' ? 'Проверяем кадр…' : title;
    byId('empty-text').textContent = pending === 'analysis' ? '' : description;
  }
}

function stopCamera() {
  cameraRequest += 1;
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
  if (pending) return;
  showMessage();

  if (stream) {
    stopCamera();
    frozen = false;
    result = null;
    render();
    return;
  }

  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    showMessage('Для доступа к камере откройте страницу по HTTPS. Можно проверить пример кадра.', true);
    return;
  }

  const request = ++cameraRequest;
  pending = 'camera';
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
    frozen = false;
    result = null;
    video.srcObject = stream;
    opened.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (stream !== opened) return;
      stopCamera();
      frozen = false;
      result = null;
      if (pending === 'camera') pending = '';
      showMessage('Камера отключилась. Включите её снова или проверьте пример.', true);
      render();
    });
    render();
    await video.play();
  } catch (error) {
    if (request !== cameraRequest) return;
    stopCamera();
    pending = '';
    showMessage(cameraError(error), true);
    render();
  } finally {
    if (request === cameraRequest) {
      pending = '';
      render();
    }
  }
}

function freezeFrame() {
  const source = stream ? video : example;
  const width = stream ? video.videoWidth : example.naturalWidth;
  const height = stream ? video.videoHeight : example.naturalHeight;
  if (!width || !height) throw new Error('Frame unavailable');

  const scale = Math.min(1, 1280 / Math.max(width, height));
  snapshot.width = Math.max(1, Math.round(width * scale));
  snapshot.height = Math.max(1, Math.round(height * scale));
  snapshot.getContext('2d').drawImage(source, 0, 0, snapshot.width, snapshot.height);
  frozen = true;
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
    const response = await fetch(useServer ? endpoint : './mock-analysis.json', options);
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
  if (pending) return;
  showMessage();

  if (frozen && result && stream) {
    result = null;
    frozen = false;
    render();
    video.play().catch(() => {
      stopCamera();
      showMessage('Не удалось возобновить камеру. Включите её снова.', true);
      render();
    });
    return;
  }

  result = null;
  pending = 'analysis';
  try {
    if (!frozen) freezeFrame();
    render();
    const request = cameraRequest;
    const analyzed = await analyzeFrame(Boolean(endpoint && stream));
    if (request !== cameraRequest) return;
    result = analyzed;
    showMessage('Проверка завершена.');
  } catch (error) {
    showMessage(error.name === 'AbortError' ? 'Сервер не ответил вовремя. Повторите проверку.' :
      'Не удалось проверить кадр. Повторите попытку.', true);
  } finally {
    pending = '';
    render();
  }
}

cameraButton.addEventListener('click', toggleCamera);
checkButton.addEventListener('click', checkFrame);
example.addEventListener('load', render);
example.addEventListener('error', () => showMessage('Пример кадра недоступен. Попробуйте включить камеру.', true));
video.addEventListener('loadeddata', render);
video.addEventListener('playing', render);
detection.addEventListener('click', () => {
  byId('result-title').focus({ preventScroll: true });
  byId('result').scrollIntoView({ block: 'nearest' });
});
window.addEventListener('pagehide', () => {
  if (!stream && pending !== 'camera') return;
  stopCamera();
  frozen = false;
  result = null;
  if (pending === 'camera') pending = '';
  render();
});
if ('ResizeObserver' in window) {
  new ResizeObserver(positionDetection).observe(viewfinder);
} else {
  window.addEventListener('resize', positionDetection);
}
render();
