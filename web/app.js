const byId = (id) => document.getElementById(id);

const viewfinder = byId('viewfinder');
const example = byId('example');
const video = byId('video');
const snapshot = byId('snapshot');
const detection = byId('detection');
const checkButton = byId('check');
const cameraButton = byId('camera');
const demoScenario = byId('demo-scenario');
const cameraMode = byId('camera-mode');
const intervalSelect = byId('frame-interval');
const profileSelect = byId('analysis-profile');
const videoToggle = byId('video-toggle');
const checkedViewfinder = byId('checked-viewfinder');
const videoMetrics = byId('video-metrics');
const endpoint = document.querySelector('main').dataset.analysisEndpoint.trim();
const latencyFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const rateFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

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
let analysisController = null;
let videoRun = null;
let videoStats = null;

function showMessage(text = '', error = false) {
  const message = byId('message');
  message.textContent = text;
  message.dataset.error = String(error);
}

function positionDetection() {
  if (!result?.box || detection.hidden) return;

  // The still image is letterboxed by object-fit: contain.
  const { width, height } = snapshot.parentElement.getBoundingClientRect();
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
  const videoMode = cameraMode.value === 'video';
  const frozen = frameSource !== null;
  const live = Boolean(stream && (videoMode || !frozen));
  const cameraReady = video.readyState >= 2 && video.videoWidth > 0;
  const frameReady = live ? cameraReady :
    example.complete && example.currentSrc === example.src && example.naturalWidth > 0;
  example.hidden = live || frozen;
  video.hidden = !live;
  snapshot.hidden = !frozen;
  viewfinder.dataset.portrait = String(live ? video.videoHeight > video.videoWidth : frozen && snapshot.height > snapshot.width);
  checkedViewfinder.dataset.portrait = String(frozen && snapshot.height > snapshot.width);
  byId('checked-frame').hidden = !videoMode || !frozen;
  byId('guide').hidden = !videoMode && frozen;
  byId('source').dataset.live = String(live);
  byId('source-text').textContent = live ? 'Камера' : frameSource === 'camera' ? 'Кадр с камеры' : 'Пример кадра';
  byId('mode').textContent = endpoint && (live || frameSource === 'camera') ? 'Локальная модель' : 'Тестовый режим';
  byId('demo-options').hidden = Boolean(stream || openingCamera || frameSource === 'camera');
  demoScenario.disabled = checking;
  byId('camera-options').hidden = !stream || !endpoint;
  cameraMode.disabled = openingCamera || checking || Boolean(videoRun) || Boolean(analysisController);
  byId('frame-interval-label').hidden = !videoMode;
  intervalSelect.hidden = !videoMode;
  intervalSelect.disabled = Boolean(videoRun);
  profileSelect.disabled = openingCamera || checking || Boolean(videoRun) || Boolean(analysisController);
  byId('video-help').hidden = !videoMode;
  videoToggle.hidden = !videoMode;
  videoToggle.disabled = !videoRun && (!stream || !cameraReady || openingCamera || Boolean(analysisController));
  byId('video-toggle-label').textContent = videoRun ? 'Остановить проверку' : 'Начать проверку';
  videoMetrics.hidden = !videoMode || !videoStats;
  if (videoStats) {
    const { count, latency, rate } = videoStats;
    videoMetrics.textContent = count ?
      `Проверено кадров: ${count} · Задержка: ${latencyFormat.format(latency)} с${count > 1 ? ` · Темп: ${rateFormat.format(rate)} кадра/с` : ''}` :
      'Проверено кадров: 0';
  }

  checkButton.hidden = videoMode;
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
    byId('vision-observation').hidden = !result.observation;
    byId('vision-observation').textContent = result.observation ? `Предварительный осмотр · ${result.model}${result.observationKind === 'template' ? ' · описание по классу' : ''}: ${result.observation}` : '';
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
  window.CyberCamera.renderTiming(result);
  window.CyberInspection.update(result, checking || Boolean(videoRun));
}

function stopCamera() {
  cameraRequest += 1;
  openingCamera = false;
  stopVideoRun();
  analysisController?.abort();
  if (cameraMode.value === 'video') {
    cameraMode.value = 'single';
    videoStats = null;
    viewfinder.append(snapshot, detection);
    frameSource = null;
    result = null;
  } else if (checking && frameSource === 'camera') {
    frameSource = null;
  }
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

function drawFrame(canvas, source) {
  const fromCamera = source === video;
  const width = fromCamera ? video.videoWidth : example.naturalWidth;
  const height = fromCamera ? video.videoHeight : example.naturalHeight;
  if (!width || !height || (fromCamera && video.readyState < 2)) {
    throw new Error('Кадр недоступен. Дождитесь изображения с камеры.');
  }

  const scale = Math.min(1, 1280 / Math.max(width, height));
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
}

async function analyzeFrame(useServer, frame) {
  const controller = new AbortController();
  const started = performance.now();
  analysisController = controller;
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, useServer ? 120000 : 12000);
  try {
    const options = { signal: controller.signal, cache: 'no-store' };
    if (useServer) {
      const image = await new Promise((resolve) => frame.toBlob(resolve, 'image/jpeg', 0.85));
      if (!image) throw new Error('Не удалось подготовить кадр.');
      const body = new FormData();
      body.append('file', image, 'frame.jpg');
      body.append('profile', profileSelect.value);
      Object.assign(options, { method: 'POST', body });
    }
    const mockPath = frameSource === 'example' ? demoScenario.selectedOptions[0].dataset.response : 'mock-analysis.json';
    const response = useServer ?
      await window.CyberCamera.fetchAnalysis(endpoint, options, showMessage) :
      await fetch(`./${mockPath}`, options);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || `Ошибка анализа: ${response.status}`);
    }
    const data = await response.json();
    if (useServer) {
      window.CyberCamera.validateModel(data);
      data.clientDurationSeconds = (performance.now() - started) / 1000;
    }
    return readResult(data);
  } catch (error) {
    if (timedOut) throw new Error('Сервер не ответил вовремя. Повторите проверку.');
    throw error;
  } finally {
    clearTimeout(timeout);
    analysisController = null;
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
  if (openingCamera || checking || videoRun || analysisController) return;
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
    if (!frameSource) {
      drawFrame(snapshot, stream ? video : example);
      frameSource = stream ? 'camera' : 'example';
    }
    if (endpoint && frameSource === 'camera') showMessage('Модель анализирует кадр. Задержка появится после ответа.');
    render();
    result = await analyzeFrame(Boolean(endpoint && frameSource === 'camera'), snapshot);
    showMessage('Проверка завершена.');
  } catch (error) {
    if (error.name !== 'AbortError') showMessage(error.message || 'Не удалось проверить кадр. Повторите попытку.', true);
  } finally {
    checking = false;
    render();
  }
}

function changeCameraMode() {
  const target = cameraMode.value === 'video' ? checkedViewfinder : viewfinder;
  target.append(snapshot, detection);
  frameSource = null;
  result = null;
  videoStats = null;
  showMessage();
  render();
}

function stopVideoRun() {
  if (!videoRun) return;
  clearTimeout(videoRun.timer);
  videoRun = null;
  analysisController?.abort();
}

async function checkVideoFrame(run) {
  const started = performance.now();
  try {
    drawFrame(run.frame, video);
    const nextResult = await analyzeFrame(true, run.frame);
    if (videoRun !== run) return;

    snapshot.width = run.frame.width;
    snapshot.height = run.frame.height;
    snapshot.getContext('2d').drawImage(run.frame, 0, 0);
    frameSource = 'camera';
    result = nextResult;
    const completed = performance.now();
    const count = videoStats.count + 1;
    if (count === 1) run.firstCompleted = completed;
    videoStats = {
      count,
      latency: (completed - started) / 1000,
      rate: count > 1 ? (count - 1) * 1000 / (completed - run.firstCompleted) : null,
    };
    if (count === 1) showMessage('Видеопроверка идёт.');
    run.timer = setTimeout(() => checkVideoFrame(run), Math.max(0, run.interval - (completed - started)));
  } catch (error) {
    if (videoRun !== run) return;
    stopVideoRun();
    showMessage(error.message || 'Не удалось проверить кадр. Запустите проверку ещё раз.', true);
  } finally {
    render();
  }
}

function toggleVideoRun() {
  if (videoRun) {
    stopVideoRun();
    showMessage('Видеопроверка остановлена.');
    render();
    return;
  }
  if (!stream || openingCamera || checking || analysisController || document.hidden) return;
  frameSource = null;
  result = null;
  videoStats = { count: 0 };
  videoRun = {
    frame: document.createElement('canvas'),
    firstCompleted: null,
    interval: Number(intervalSelect.value),
    timer: null,
  };
  showMessage('Модель анализирует первый кадр.');
  render();
  checkVideoFrame(videoRun);
}

cameraButton.addEventListener('click', toggleCamera);
checkButton.addEventListener('click', checkFrame);
cameraMode.addEventListener('change', changeCameraMode);
videoToggle.addEventListener('click', toggleVideoRun);
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
  if (!stream && !openingCamera && !analysisController) return;
  stopCamera();
  showMessage();
  render();
}

window.addEventListener('pagehide', releaseCamera);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) releaseCamera();
});
if ('ResizeObserver' in window) {
  const observer = new ResizeObserver(positionDetection);
  observer.observe(viewfinder);
  observer.observe(checkedViewfinder);
} else {
  window.addEventListener('resize', positionDetection);
}
updateDemoExample();
