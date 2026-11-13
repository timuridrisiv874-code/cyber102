(() => {
if (window.CyberWebBoot?.blocked) return;
const byId = (id) => document.getElementById(id);

const viewfinder = byId('viewfinder');
const example = byId('example');
const cameraIdle = byId('camera-idle');
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
const offlineMode = window.CyberOffline?.enabled === true;
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
let inputGeneration = 0;
let frameMetadata = null;
let mediaProgress = '';
let clipReview = null;
let demoActive = false;
const media = window.CyberMedia.create({
  input: byId('media-file'), image: byId('uploaded-image'), video: byId('uploaded-video'),
  beforeChange: resetInput, changed: render, message: showMessage,
});
clipReview = offlineMode ? null : window.CyberVideoReview?.create({
  media,
  beforeStart: () => {
    inputGeneration += 1;
    stopVideoRun();
    analysisController?.abort();
    checking = false;
    cameraMode.value = 'single';
    frameSource = null; frameMetadata = null; result = null; mediaProgress = '';
    viewfinder.append(snapshot, detection);
    showMessage();
    render();
  },
  onBusyChange: render,
});

function resetInput() {
  clipReview?.reset();
  demoActive = false;
  inputGeneration += 1;
  stopCamera();
  checking = false;
  frameSource = null; frameMetadata = null; result = null; videoStats = null; mediaProgress = '';
  cameraMode.value = 'single';
  viewfinder.append(snapshot, detection);
}

function captureMetadata() {
  return media.kind ? media.metadata() : { captureSource: 'camera' };
}

function sourceLabel(metadata) {
  if (metadata?.captureSource === 'uploaded_video') return `Кадр из видео · ${window.CyberMedia.time(metadata.videoTimeSeconds || 0)}`;
  return metadata?.captureSource === 'uploaded_photo' ? 'Загруженное фото' : 'Кадр с камеры';
}

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
  if (offlineMode) cameraMode.value = 'single';
  const clipBusy = Boolean(clipReview?.busy);
  const videoOverview = !offlineMode && media.kind === 'uploaded_video';
  if (!offlineMode) {
    const reviewPanel = byId('video-review-panel');
    const destination = videoOverview ? byId('result') : byId('video-review-home');
    if (reviewPanel && destination && reviewPanel.parentElement !== destination) destination.append(reviewPanel);
    byId('result').dataset.videoReview = String(videoOverview);
    byId('result-title').textContent = videoOverview && !result ? 'Обзор видео' : 'Результат осмотра';
    byId('video-review-title').hidden = videoOverview && !result;
    reviewPanel?.setAttribute('aria-labelledby', videoOverview && !result ? 'result-title' : 'video-review-title');
  }
  const videoMode = cameraMode.value === 'video';
  const frozen = frameSource !== null;
  const live = Boolean(stream && (videoMode || !frozen));
  const uploadedPreview = Boolean(media.kind && (videoMode || !frozen));
  const cameraReady = video.readyState >= 2 && video.videoWidth > 0;
  const frameReady = media.kind ? media.ready : stream ? cameraReady :
    Boolean(demoActive && example?.complete && example.currentSrc === example.src && example.naturalWidth > 0);
  if (example) example.hidden = !demoActive || live || frozen || Boolean(media.kind);
  if (cameraIdle) cameraIdle.hidden = demoActive || live || Boolean(media.kind) || (frozen && !videoMode);
  byId('uploaded-image').hidden = !uploadedPreview || media.kind !== 'uploaded_photo';
  byId('uploaded-video').hidden = !uploadedPreview || media.kind !== 'uploaded_video';
  byId('uploaded-video').controls = !videoRun && !clipBusy;
  byId('media-clear').hidden = !media.kind && !media.loading;
  byId('media-progress').hidden = !media.kind || !mediaProgress;
  byId('media-progress').textContent = mediaProgress;
  video.hidden = !live;
  snapshot.hidden = !frozen;
  const visibleSource = uploadedPreview ? media.element : live ? video : snapshot;
  const sourceWidth = visibleSource.videoWidth || visibleSource.naturalWidth || visibleSource.width;
  const sourceHeight = visibleSource.videoHeight || visibleSource.naturalHeight || visibleSource.height;
  viewfinder.dataset.portrait = String(sourceHeight > sourceWidth);
  checkedViewfinder.dataset.portrait = String(frozen && snapshot.height > snapshot.width);
  byId('checked-frame').hidden = !videoMode || !frozen;
  byId('guide').hidden = (!live && !demoActive) || (!videoMode && frozen) || Boolean(media.kind);
  byId('source').dataset.live = String(live);
  byId('source').hidden = !(media.kind || live || demoActive || frameSource);
  byId('source-text').textContent = media.kind ? sourceLabel(media.metadata()) : live ? 'Камера' : frameSource === 'camera' ? 'Кадр с камеры' : demoActive ? 'Тестовый пример' : 'Выберите источник';
  if (byId('mode')) byId('mode').textContent = offlineMode ? 'На телефоне · без сервера' :
    endpoint && (live || frameSource === 'camera' || media.kind) ? 'Модель на сервере' : demoActive ? 'Тестовый пример' : 'Готово к осмотру';
  if (demoScenario) {
    byId('demo-options').hidden = !example || Boolean(stream || openingCamera || frameSource === 'camera' || media.kind);
    demoScenario.disabled = checking;
  }
  byId('camera-options').hidden = (!stream && !media.kind) || !endpoint;
  cameraMode.hidden = byId('camera-mode-label').hidden = media.kind === 'uploaded_photo';
  cameraMode.disabled = clipBusy || media.loading || openingCamera || checking || Boolean(videoRun) || Boolean(analysisController);
  byId('frame-interval-label').hidden = !videoMode;
  intervalSelect.hidden = !videoMode;
  intervalSelect.disabled = Boolean(videoRun);
  profileSelect.disabled = clipBusy || openingCamera || checking || Boolean(videoRun) || Boolean(analysisController);
  byId('video-help').hidden = !videoMode;
  byId('video-help').textContent = media.kind === 'uploaded_video' ?
    'Проверка с текущей позиции, максимум 20 кадров за запуск. Рамка относится к кадру с указанным таймкодом; промежутки между кадрами не анализируются.' :
    'Проверяем отдельные кадры с камеры. Рамка относится к последнему проверенному кадру.';
  videoToggle.hidden = !videoMode;
  videoToggle.disabled = clipBusy || (!videoRun && ((!stream && media.kind !== 'uploaded_video') || !frameReady || openingCamera || Boolean(analysisController)));
  byId('video-toggle-label').textContent = videoRun ? 'Остановить проверку' : 'Начать проверку';
  if (videoMetrics) {
    videoMetrics.hidden = !videoMode || !videoStats;
    if (videoStats) {
      const { count, latency, rate } = videoStats;
      videoMetrics.textContent = count ?
        `Проверено кадров: ${count} · Задержка: ${latencyFormat.format(latency)} с${count > 1 ? ` · Темп: ${rateFormat.format(rate)} кадра/с` : ''}` : 'Проверено кадров: 0';
    }
  }
  if (byId('video-summary')) renderVideoSummary();
  checkButton.hidden = videoMode || (!frameReady && !checking && !result);
  cameraButton.classList.toggle('primary', !frameReady && !result);
  cameraButton.classList.toggle('secondary', frameReady || Boolean(result));
  document.body.dataset.hasResult = String(Boolean(result));
  checkButton.disabled = clipBusy || openingCamera || checking || media.loading || Boolean(analysisController) || (!frozen && !frameReady);
  cameraButton.disabled = false;
  if (byId('analysis-cancel')) byId('analysis-cancel').hidden = !checking || frameSource === 'example';
  byId('check-label').textContent = checking ? 'Проверяем…' :
    result ? (['camera', 'uploaded_video'].includes(frameSource) ? 'Новый кадр' : 'Проверить ещё раз') : 'Проверить кадр';
  byId('camera-label').textContent = openingCamera ? 'Отменить запуск' :
    stream ? 'Выключить камеру' : 'Включить камеру';
  byId('capture-provenance').hidden = !result || !frameMetadata;
  byId('capture-provenance').textContent = frameMetadata ? sourceLabel(frameMetadata) : '';

  const detected = result && (result.status === 'defect' || result.status === 'clear');
  document.body.dataset.analysisState = checking || Boolean(videoRun) || clipBusy ? 'working' : 'idle';
  byId('result').dataset.resultReady = String(Boolean(detected || (offlineMode && result?.status === 'offline_observation')));
  detection.hidden = !detected;
  byId('details').hidden = !detected;
  byId('empty').hidden = Boolean(detected) || (videoOverview && !result);

  if (detected) {
    detection.dataset.status = result.status;
    detection.setAttribute('aria-label', `Открыть карточку узла: ${result.component}`);
    byId('detection-label').textContent = 'Узел';
    byId('finding').dataset.status = result.status;
    byId('finding').textContent = result.finding;
    byId('equipment').textContent = result.equipment.name;
    byId('equipment-id').textContent = result.equipment.id;
    byId('vision-observation').hidden = !result.observation;
    const languageNote = /[\u3400-\u9fff\u3040-\u30ff]/u.test(result.observation || '') ? 'В описании есть языковые ошибки; сверяйте его с кадром. ' : '';
    byId('vision-observation').textContent = result.observation ? `${languageNote}${result.observation}` : '';
    if (byId('analysis-model')) byId('analysis-model').textContent = `${result.model || 'Демонстрационный ответ'}${result.observationKind === 'template' ? ' · описание по классу' : ''}`;
    byId('component').textContent = result.component;
    if (byId('service-life')) byId('service-life').textContent = result.serviceLife;
    if (byId('next-service')) byId('next-service').textContent = result.nextService;
    byId('recommendation').textContent = result.recommendation;
    positionDetection();
  } else {
    const [title, description] = unavailableResults[result?.status] ?? [
      'Ожидает кадр',
      'Добавьте фото или видео для проверки.',
    ];
    byId('empty-title').textContent = checking ? 'Проверяем кадр…' : title;
    byId('empty-text').textContent = checking ? '' : description;
  }
  if (!offlineMode) window.CyberCamera.renderTiming(result);
  const settings = byId('analysis-settings');
  if (settings) settings.hidden = offlineMode || (byId('camera-options').hidden &&
    byId('analysis-timing').hidden && (!videoMetrics || videoMetrics.hidden));
  window.CyberInspection.update(result, clipBusy || checking || Boolean(videoRun));
  clipReview?.update();
  if (offlineMode) window.CyberOffline.render(result, checking);
}

function stopCamera() {
  clipReview?.cancel();
  inputGeneration += 1;
  checking = false;
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
  if (error.name === 'NotFoundError') return 'Камера не найдена. Можно выбрать фото или видео.';
  if (error.name === 'NotReadableError') return 'Камера недоступна. Закройте другие приложения, которые её используют, и повторите попытку.';
  return 'Не удалось открыть камеру. Можно выбрать фото или видео.';
}

async function toggleCamera() {
  showMessage();

  if (stream || openingCamera) {
    stopCamera();
    render();
    return;
  }

  resetInput(); media.clear();

  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    showMessage('Для доступа к камере откройте страницу по HTTPS. Можно выбрать фото или видео.', true);
    render(); return;
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
  const width = source.videoWidth || source.naturalWidth;
  const height = source.videoHeight || source.naturalHeight;
  if (!width || !height || (source.tagName === 'VIDEO' && source.readyState < 2)) {
    throw new Error('Кадр недоступен. Дождитесь загрузки изображения.');
  }
  const scale = Math.min(1, (offlineMode ? 768 : 1280) / Math.max(width, height));
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
}

async function analyzeFrame(useServer, frame, metadata = { captureSource: 'camera' }) {
  const controller = new AbortController();
  const started = performance.now();
  analysisController = controller;
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, offlineMode ? 180000 : useServer ? 13 * 60 * 1000 : 12000);
  try {
    if (offlineMode) {
      const image = await new Promise((resolve) => frame.toBlob(resolve, 'image/jpeg', 0.85));
      if (!image) throw new Error('Не удалось подготовить кадр.');
      const data = await window.CyberOffline.analyze(image, {
        signal: controller.signal, onProgress: showMessage, metadata,
      });
      data.clientDurationSeconds = (performance.now() - started) / 1000;
      return readResult(data);
    }
    const options = { signal: controller.signal, cache: 'no-store' };
    if (useServer) {
      const image = await new Promise((resolve) => frame.toBlob(resolve, 'image/jpeg', 0.85));
      if (!image) throw new Error('Не удалось подготовить кадр.');
      const body = new FormData();
      body.append('file', image, 'frame.jpg');
      body.append('profile', profileSelect.value);
      body.append('captureSource', metadata.captureSource);
      if (metadata.captureSource === 'uploaded_video') body.append('videoTimeSeconds', String(metadata.videoTimeSeconds));
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
    if (timedOut) throw new Error(offlineMode ? 'Модель на телефоне не завершила анализ вовремя. Попробуйте другой кадр.' : 'Сервер не ответил вовремя. Повторите проверку.');
    throw error;
  } finally {
    clearTimeout(timeout);
    if (analysisController === controller) analysisController = null;
  }
}

function readResult(data) {
  if (offlineMode && data?.status === 'offline_observation' && data.source === 'on_device' &&
      typeof data.model === 'string' && typeof data.observation === 'string' &&
      data.observation.trim() && data.observation.length <= 4000 && !data.box) return data;
  if (data && Object.prototype.hasOwnProperty.call(unavailableResults, data.status)) return data;
  if (!data || !['defect', 'clear'].includes(data.status)) throw new Error('Invalid result status');

  const { x, y, width, height } = data.box ?? {};
  const text = [data.equipment?.id, data.equipment?.name, data.component, data.finding,
    data.recommendation];
  if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0 ||
      x + width > 1.000001 || y + height > 1.000001 || text.some((value) => typeof value !== 'string')) {
    throw new Error('Invalid inspection result');
  }
  return data;
}

async function checkFrame() {
  if (clipReview?.busy || openingCamera || checking || videoRun || analysisController || media.loading) return;
  showMessage();
  if (result && frameSource === 'uploaded_video') {
    result = null; frameSource = null; frameMetadata = null; render(); return;
  }
  if (result && frameSource === 'camera') {
    if (!stream) { await toggleCamera(); return; }
    const resumedStream = stream;
    result = null; frameSource = null; frameMetadata = null;
    render();
    video.play().catch(() => {
      if (stream !== resumedStream) return;
      stopCamera(); showMessage('Не удалось возобновить камеру. Включите её снова.', true); render();
    });
    return;
  }
  if (!stream && !media.kind && !demoActive && !frameSource) return;
  const generation = inputGeneration;
  result = null; checking = true;
  try {
    if (!frameSource) {
      media.pause();
      drawFrame(snapshot, media.kind ? media.element : stream ? video : example);
      frameSource = media.kind || (stream ? 'camera' : 'example');
      frameMetadata = frameSource === 'example' ? null : captureMetadata();
    }
    const useServer = frameSource !== 'example';
    if (useServer && !endpoint && !offlineMode) throw new Error('Не задан адрес сервера анализа.');
    if (useServer) showMessage(offlineMode ? 'SmolVLM анализирует кадр на телефоне…' : 'Модель анализирует кадр. Задержка появится после ответа.');
    render();
    const nextResult = await analyzeFrame(useServer, snapshot, frameMetadata);
    if (generation !== inputGeneration) return;
    result = nextResult;
    showMessage('Проверка завершена.');
  } catch (error) {
    if (generation === inputGeneration && error.name !== 'AbortError') showMessage(error.message || 'Не удалось проверить кадр. Повторите попытку.', true);
  } finally {
    if (generation === inputGeneration) checking = false;
    render();
  }
}

function changeCameraMode() {
  if (offlineMode) cameraMode.value = 'single';
  const target = cameraMode.value === 'video' ? checkedViewfinder : viewfinder;
  target.append(snapshot, detection);
  frameSource = null;
  result = null;
  videoStats = null;
  showMessage();
  render();
}

function recordVideoResult(frame) {
  videoStats.total += 1;
  if (frame.status === 'clear') {
    videoStats.clear += 1;
  } else if (frame.status === 'defect') {
    videoStats.defects += 1;
    const label = frame.component + ' — ' + frame.finding;
    const entry = videoStats.findings.get(label);
    if (entry) {
      entry.count += 1;
    } else {
      videoStats.findings.set(label, { count: 1, recommendation: frame.recommendation });
    }
  } else {
    videoStats.unusable += 1;
  }
}

function renderVideoSummary() {
  byId('video-summary').hidden = !videoStats || cameraMode.value !== 'video';
  if (!videoStats || cameraMode.value !== 'video') return;
  const state = videoRun ? 'Проверка идёт' : videoStats.error ? 'Проверка прервана. ' + videoStats.error :
    videoStats.total ? 'За текущий сеанс' : 'Сервер ещё не вернул ни одного результата';
  const stateNode = byId('summary-state');
  if (stateNode.textContent !== state) stateNode.textContent = state;
  byId('summary-total').textContent = videoStats.total;
  byId('summary-defects').textContent = videoStats.defects;
  byId('summary-clear').textContent = videoStats.clear;
  byId('summary-unusable').textContent = videoStats.unusable;
  const list = byId('summary-findings');
  list.hidden = !videoStats.findings.size;
  const entries = [];
  for (const [label, detail] of videoStats.findings) {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = label + ' · ' + detail.count;
    const recommendation = document.createElement('p');
    recommendation.textContent = detail.recommendation;
    item.append(title, recommendation);
    entries.push(item);
  }
  list.replaceChildren(...entries);
}


function stopVideoRun() {
  if (!videoRun) return;
  clearTimeout(videoRun.timer);
  videoRun.controller.abort();
  media.pause();
  videoRun = null;
  analysisController?.abort();
}

async function checkVideoFrame(run) {
  const started = performance.now();
  try {
    if (run.times) {
      await media.seek(run.times[run.index], run.controller.signal);
      if (videoRun !== run) return;
      mediaProgress = `Кадр ${run.index + 1} из ${run.times.length} · ${window.CyberMedia.time(media.element.currentTime)}`;
      render();
    }
    drawFrame(run.frame, run.times ? media.element : video);
    const metadata = captureMetadata();
    const nextResult = await analyzeFrame(true, run.frame, metadata);
    if (videoRun !== run) return;
    snapshot.width = run.frame.width;
    snapshot.height = run.frame.height;
    snapshot.getContext('2d').drawImage(run.frame, 0, 0);
    frameSource = metadata.captureSource;
    frameMetadata = metadata;
    result = nextResult;
    const completed = performance.now();
    videoStats.count += 1;
    if (videoStats.count === 1) run.firstCompleted = completed;
    videoStats.latency = (completed - started) / 1000;
    videoStats.rate = videoStats.count > 1 ? (videoStats.count - 1) * 1000 / (completed - run.firstCompleted) : null;
    recordVideoResult(nextResult);
    if (run.times) {
      run.index += 1;
      mediaProgress = `Проверено ${run.index} из ${run.times.length} кадров · последний ${window.CyberMedia.time(metadata.videoTimeSeconds)}`;
      if (run.index >= run.times.length) {
        stopVideoRun();
        showMessage('Проверка выбранных кадров завершена. Промежутки между кадрами не анализировались.');
        return;
      }
    } else if (videoStats.count === 1) showMessage('Видеопроверка идёт.');
    run.timer = setTimeout(() => checkVideoFrame(run), run.times ? 0 : Math.max(0, run.interval - (completed - started)));
  } catch (error) {
    if (videoRun !== run) return;
    stopVideoRun();
    videoStats.error = error.message || 'Не удалось проверить кадр. Запустите проверку ещё раз.';
    showMessage(videoStats.error, true);
  } finally { render(); }
}

function toggleVideoRun() {
  if (offlineMode) return;
  if (clipReview?.busy) return;
  if (videoRun) {
    stopVideoRun(); showMessage('Видеопроверка остановлена.'); render(); return;
  }
  if ((!stream && media.kind !== 'uploaded_video') || openingCamera || checking || analysisController || document.hidden || media.loading) return;
  frameSource = null; frameMetadata = null; result = null;
  videoStats = { count: 0, total: 0, defects: 0, clear: 0, unusable: 0, findings: new Map(), error: null };
  const interval = Number(intervalSelect.value);
  let times = null;
  if (media.kind === 'uploaded_video') {
    media.pause(); times = [];
    // Start at the user's playhead. A new run is needed for any remaining clip.
    for (let t = Math.min(media.element.currentTime, media.element.duration - 0.001); t < media.element.duration && times.length < 20; t += interval / 1000) times.push(t);
  }
  videoRun = { frame: document.createElement('canvas'), firstCompleted: null, interval,
    timer: null, controller: new AbortController(), times, index: 0 };
  showMessage('Модель анализирует первый кадр.'); render(); checkVideoFrame(videoRun);
}

cameraButton.addEventListener('click', toggleCamera);
checkButton.addEventListener('click', checkFrame);
byId('analysis-cancel')?.addEventListener('click', () => {
  if (!checking) return;
  inputGeneration += 1;
  analysisController?.abort(); analysisController = null; checking = false;
  showMessage('Анализ отменён.'); render();
});
cameraMode.addEventListener('change', changeCameraMode);
videoToggle.addEventListener('click', toggleVideoRun);
function updateDemoExample() {
  if (offlineMode) return;
  if (!example || !demoScenario) return;
  resetInput();
  media.clear();
  demoActive = true;
  const { image } = demoScenario.selectedOptions[0].dataset;
  frameSource = null;
  result = null;
  showMessage();
  if (example.getAttribute('src') !== image) example.src = image;
  render();
}
demoScenario?.addEventListener('change', updateDemoExample);
byId('show-example')?.addEventListener('click', updateDemoExample);
example?.addEventListener('load', render);
example?.addEventListener('error', () => showMessage('Пример кадра недоступен. Попробуйте включить камеру.', true));
video.addEventListener('loadeddata', render);
video.addEventListener('canplay', render);
video.addEventListener('playing', render);
video.addEventListener('resize', render);
detection.addEventListener('click', () => {
  byId('result-title').focus({ preventScroll: true });
  byId('result').scrollIntoView({ block: 'nearest' });
});
function releaseCamera() {
  clipReview?.cancel();
  media.pause();
  if (!stream && !openingCamera && !analysisController && !videoRun) return;
  inputGeneration += 1;
  checking = false;
  if (media.kind) {
    stopVideoRun(); analysisController?.abort();
  } else stopCamera();
  showMessage(); render();
}

window.addEventListener('pagehide', () => { releaseCamera(); resetInput(); media.clear(); render(); });
byId('media-clear').addEventListener('click', () => { resetInput(); media.clear(); showMessage(); render(); });
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
render();


async function loadCapabilities() {
  if (offlineMode) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch('/api/capabilities', { signal: controller.signal, cache: 'no-store', redirect: 'error' });
    if (!response.ok) return;
    const capabilities = await response.json();
    window.CyberAnalysisJobs?.setCapabilities(capabilities);
    if (typeof capabilities?.vision?.videoReview === 'boolean') {
      clipReview?.setAvailable(capabilities.vision.videoReview);
    }
    const profiles = capabilities?.vision?.profiles;
    // Ignore missing/old capability responses; the standard profile remains the default.
    if (!Array.isArray(profiles) || !profiles.includes('standard')) return;
    for (const option of profileSelect.options) {
      option.disabled = !profiles.includes(option.value);
      option.hidden = option.disabled;
    }
    if (!profiles.includes(profileSelect.value)) profileSelect.value = 'standard';
    render();
  } catch (_) {
    // Discovery is optional: camera and file selection never wait for this request.
  } finally { clearTimeout(timeout); }
}
loadCapabilities();
})();
