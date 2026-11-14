(() => {
if (window.CyberWebBoot?.blocked) return;
// The inspection page can retry a missing script without binding controls twice.
if (!window.CyberMedia || !window.CyberInspection ||
    (!window.CyberOffline?.enabled && (!window.CyberVideoReview || !window.CyberAnalysisJobs || !window.CyberInspectionPhoto))) return;
if (window.CyberCaptureStarted) return;
window.CyberCaptureStarted = true;
const byId = (id) => document.getElementById(id);

const viewfinder = byId('viewfinder');
const example = byId('example');
const cameraIdle = byId('camera-idle');
const video = byId('video');
const snapshot = byId('snapshot');
const detection = byId('detection');
const liquidNames = { liquid_flow: 'Струя или капли', wet_surface: 'Мокрый участок' };
let liquidMarkers = [];
const checkButton = byId('check');
const cameraButton = byId('camera');
const demoScenario = byId('demo-scenario');
const examplesRequested = new URLSearchParams(location.search).get('example') === '1';
const cameraMode = byId('camera-mode');
const intervalSelect = byId('frame-interval');
const videoToggle = byId('video-toggle');
const checkedViewfinder = byId('checked-viewfinder');
const videoMetrics = byId('video-metrics');
const endpoint = document.querySelector('main').dataset.analysisEndpoint.trim();
const offlineMode = window.CyberOffline?.enabled === true;

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
let regionController = null;
let mediaProgress = '';
let clipReview = null;
let demoActive = false;
let defaultMediaVersion = null, defaultMediaKind = null;
const media = window.CyberMedia.create({
  input: byId('media-file'), image: byId('uploaded-image'), video: byId('uploaded-video'),
  beforeChange: resetInput, changed: mediaChanged, message: showMessage,
});
const sourceModes = offlineMode ? null : window.CyberInspectionMesh?.create({
  media,
  openModel: () => {
    media.pause();
    if (stream || openingCamera) stopCamera();
    render();
  },
  openMedia: render,
});
clipReview = offlineMode ? null : window.CyberVideoReview?.create({
  media,
  beforeStart: () => {
    inputGeneration += 1;
    stopVideoRun();
    analysisController?.abort();
    checking = false;
    frameSource = null; frameMetadata = null; result = null; videoStats = null; mediaProgress = '';
    viewfinder.append(snapshot, detection);
    showMessage();
    render();
  },
  onBusyChange: render,
  isInspecting: () => checking || Boolean(analysisController || videoRun || regionController),
  onUseOverview: (representative, image) => {
    window.CyberCamera.validateModel(representative);
    const inspected = readResult(representative);
    media.pause();
    cameraMode.value = 'single';
    viewfinder.append(snapshot, detection);
    drawFrame(snapshot, image);
    frameSource = 'uploaded_video'; frameMetadata = inspected.media; result = inspected;
    showMessage(); render();
    byId('inspection-title').focus();
  },
  onSelectFrame: () => {
    if (!frameSource || media.kind !== 'uploaded_video') return;
    result = null; frameSource = null; frameMetadata = null;
    viewfinder.append(snapshot, detection);
    showMessage(); render();
  },
  onInspectFrame: async () => {
    if (media.kind !== 'uploaded_video' || !media.ready || clipReview?.busy || checking || analysisController || videoRun) return;
    cameraMode.value = 'single';
    viewfinder.append(snapshot, detection);
    result = null; frameSource = null; frameMetadata = null;
    await checkFrame();
  },
});

function mediaChanged() {
  // Apply defaults once per source, retaining a manually selected mode on seek
  // and decode events from the same file.
  if (media.version !== defaultMediaVersion || media.kind !== defaultMediaKind) {
    defaultMediaVersion = media.version;
    defaultMediaKind = media.kind;
    cameraMode.value = !offlineMode && media.kind === 'uploaded_video' ? 'video' : 'single';
    intervalSelect.value = '10000';
  }
  render();
}

function resetInput() {
  regionController?.abort();
  clipReview?.reset();
  demoActive = false;
  inputGeneration += 1;
  stopCamera();
  checking = false;
  frameSource = null; frameMetadata = null; result = null; videoStats = null; mediaProgress = '';
  cameraMode.value = 'single';
  byId('analysis-settings').open = false;
  intervalSelect.value = '10000';
  viewfinder.append(snapshot, detection);
}

function captureMetadata() {
  return media.kind ? media.metadata() : { captureSource: 'camera' };
}

function sourceLabel(metadata) {
  if (metadata?.regionLabel) return metadata.regionLabel;
  if (media.catalogPhoto && metadata?.captureSource === 'uploaded_photo') return 'Фото из подборки';
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
  function place(marker, box) {
    marker.style.left = `${(width - frameWidth) / 2 + box.x * frameWidth}px`;
    marker.style.top = `${(height - frameHeight) / 2 + box.y * frameHeight}px`;
    marker.style.width = `${box.width * frameWidth}px`;
    marker.style.height = `${box.height * frameHeight}px`;
  }
  place(detection, result.box);
  for (const [index, marker] of liquidMarkers.entries()) {
    const [left, top, right, bottom] = result.liquidBoxes[index + 1].box;
    place(marker, { x: left / 1000, y: top / 1000, width: (right - left) / 1000, height: (bottom - top) / 1000 });
  }
}

function render() {
  const knowledge = byId('photo-knowledge');
  if (knowledge) {
    const list = knowledge.querySelector('ul');
    list.replaceChildren();
    for (const item of result?.equipmentContext?.references || []) {
      const url = item.source?.url;
      const local = typeof url === 'string' && /^\/api\/customer\/documents\/[a-z0-9-]+\/pdf#page=\d+$/.test(url);
      const external = item.applicability === 'technical_reference' && /^https:\/\/[a-z0-9.-]+\//i.test(url);
      if (!local && !external) continue;
      const li = document.createElement('li'), link = document.createElement('a');
      link.href = url; link.target = '_blank'; link.rel = 'noopener';
      link.textContent = item.title + (local ? ' · стр. ' + item.source.page : ' · ' + item.source.publisher);
      li.append(link); list.append(li);
    }
    knowledge.hidden = !list.childElementCount;
    const context = result?.equipmentContext;
    knowledge.querySelector('summary').textContent = context ? 'Источники контекста · ' + context.serial : 'Источники контекста';
  }
  if (offlineMode) cameraMode.value = 'single';
  const clipBusy = Boolean(clipReview?.busy);
  sourceModes?.sync();
  const videoOverview = !offlineMode && media.kind === 'uploaded_video';
  const controls = byId(videoOverview ? 'video-extra-actions' : 'capture-actions');
  if (controls && checkButton.parentElement !== controls) {
    controls.append(checkButton, videoToggle, byId('analysis-cancel'), byId('choose-region'));
    checkButton.classList.toggle('primary', !videoOverview);
    checkButton.classList.toggle('secondary', videoOverview);
    videoToggle.classList.toggle('primary', !videoOverview);
    videoToggle.classList.toggle('secondary', videoOverview);
  }
  if (!offlineMode) {
    const reviewPanel = byId('video-review-panel');
    const destination = videoOverview ? byId('result') : byId('video-review-home');
    if (reviewPanel && destination && reviewPanel.parentElement !== destination) destination.append(reviewPanel);
    byId('result').dataset.videoReview = String(videoOverview);
    const resultTitle = byId('result-title');
    if (videoOverview && !result) resultTitle.textContent = 'Обзор видео';
    else if (videoOverview && Number.isFinite(frameMetadata?.videoTimeSeconds))
      resultTitle.textContent = `${result?.videoReview ? 'Опорный кадр' : 'Кадр'} · ${window.CyberMedia.time(frameMetadata.videoTimeSeconds)}`;
    else resultTitle.textContent = 'Результат осмотра';
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
  const sourceBadge = byId('source');
  if (sourceBadge) {
    sourceBadge.dataset.live = String(live);
    sourceBadge.hidden = !(media.kind || live || demoActive || frameSource);
    byId('source-text').textContent = frozen && frameMetadata ? sourceLabel(frameMetadata) : media.kind ? sourceLabel(media.metadata()) : live ? 'Камера' : demoActive ? 'Сохранённый пример' : '';
  }
  if (byId('mode')) byId('mode').textContent = offlineMode ? 'На телефоне · без сервера' :
    endpoint && (live || frameSource === 'camera' || media.kind) ? 'Готово к проверке' : demoActive ? 'Тестовый пример' : 'Готово к осмотру';
  if (demoScenario) {
    byId('demo-options').hidden = !examplesRequested || !example || Boolean(stream || openingCamera || frameSource === 'camera' || media.kind);
    demoScenario.disabled = checking;
  }
  const hasVideoSource = Boolean(stream || media.kind === 'uploaded_video');
  byId('analysis-settings').hidden = offlineMode || !endpoint || !hasVideoSource;
  byId('camera-options').hidden = !hasVideoSource;
  cameraMode.disabled = clipBusy || media.loading || openingCamera || checking || Boolean(videoRun || analysisController || regionController);
  byId('frame-interval-label').hidden = !videoMode;
  intervalSelect.hidden = !videoMode;
  intervalSelect.disabled = cameraMode.disabled;
  byId('video-help').hidden = true;
  byId('video-help').textContent = '';
  videoToggle.hidden = !videoMode;
  videoToggle.disabled = clipBusy || (!videoRun && ((!stream && media.kind !== 'uploaded_video') || !frameReady || openingCamera || Boolean(analysisController)));
  byId('video-toggle-label').textContent = videoRun ? 'Остановить проверку' : videoOverview ? 'Проверить по интервалу' : 'Начать проверку';
  if (videoMetrics) {
    videoMetrics.hidden = true;
    if (videoStats) videoMetrics.textContent = `Проверено кадров: ${videoStats.count}`;
  }
  if (byId('video-summary')) renderVideoSummary();
  checkButton.hidden = videoMode || (!frameReady && !checking && !result);
  const regionButton = byId('choose-region');
  if (regionButton) {
    regionButton.hidden = offlineMode || !window.CyberRegionReview || videoMode || !media.ready;
    regionButton.disabled = checking || clipBusy || Boolean(videoRun || regionController || analysisController);
  }
  cameraButton.classList.toggle('primary', !frameReady && !result);
  cameraButton.classList.toggle('secondary', frameReady || Boolean(result));
  document.body.dataset.hasResult = String(Boolean(result));
  document.body.dataset.inspectionLayout = videoOverview || result || checking || videoRun ? 'review' : 'capture';
  checkButton.disabled = clipBusy || openingCamera || checking || media.loading || Boolean(analysisController) || (!frozen && !frameReady);
  cameraButton.disabled = false;
  if (byId('analysis-cancel')) byId('analysis-cancel').hidden = !checking || frameSource === 'example';
  byId('check-label').textContent = checking ? 'Проверяем…' :
    frameMetadata?.regionLabel ? 'Весь кадр' : result ? (['camera', 'uploaded_video'].includes(frameSource) ? 'Новый кадр' : 'Проверить ещё раз') : videoOverview ? 'Проверить текущий кадр' : 'Проверить кадр';
  byId('camera-label').textContent = openingCamera ? 'Отменить запуск' :
    stream ? 'Выключить камеру' : 'Камера';
  byId('capture-provenance').hidden = !result || !frameMetadata;
  byId('capture-provenance').textContent = frameMetadata ? sourceLabel(frameMetadata) : '';

  const detected = result && (result.status === 'defect' || result.status === 'clear');
  document.body.dataset.analysisState = checking || Boolean(videoRun) || clipBusy ? 'working' : 'idle';
  byId('result').dataset.resultReady = String(Boolean(detected || (offlineMode && result?.status === 'offline_observation')));
  detection.hidden = !detected;
  for (const marker of liquidMarkers) marker.remove();
  liquidMarkers = [];
  byId('details').hidden = !detected;
  byId('empty').hidden = Boolean(detected) || (videoOverview && !result);

  if (detected) {
    detection.dataset.status = result.status;
    const [primaryLiquid, ...otherLiquid] = result.liquidBoxes ?? [];
    detection.setAttribute('aria-label', `Открыть узел: ${primaryLiquid ? liquidNames[primaryLiquid.kind] : result.component}`);
    byId('detection-label').textContent = primaryLiquid ? liquidNames[primaryLiquid.kind] : result.component;
    for (const item of otherLiquid) {
      const marker = document.createElement('button');
      marker.type = 'button';
      marker.className = detection.className;
      marker.setAttribute('aria-label', `Открыть участок: ${liquidNames[item.kind]}`);
      marker.addEventListener('click', event => {
        const [left, top, right, bottom] = item.box;
        selectInspectionTarget(event, {x:left / 1000, y:top / 1000, width:(right - left) / 1000, height:(bottom - top) / 1000}, liquidNames[item.kind], item.box.join(','));
      });
      const label = document.createElement('span');
      label.textContent = liquidNames[item.kind];
      marker.append(label);
      detection.after(marker);
      liquidMarkers.push(marker);
    }
    byId('finding').dataset.status = result.status;
    byId('finding').textContent = result.finding;
    const genericName = result.source === 'local_vision' && result.equipment.name === 'Оборудование на снимке';
    byId('equipment').textContent = genericName ? result.component : result.equipment.name;
    byId('component').parentElement.hidden = genericName;
    byId('equipment-id').textContent = result.equipment.id;
    const observation = result.observation || '';
    const cyrillicCount = (observation.match(/[а-яё]/gi) || []).length;
    const foreignCount = (observation.match(/[a-z\u3400-\u9fff\u3040-\u30ff]/gi) || []).length;
    const originalOnly = foreignCount > 4 && foreignCount > cyrillicCount;
    byId('vision-observation').hidden = !observation || originalOnly;
    byId('vision-observation').textContent = originalOnly ? '' : observation;
    const original = byId('original-observation');
    if (original) {
      original.hidden = !originalOnly;
      original.textContent = originalOnly ? `Исходное описание — ${observation}` : '';
    }
    byId('component').textContent = result.component;
    if (byId('service-life')) byId('service-life').textContent = result.serviceLife;
    if (byId('next-service')) byId('next-service').textContent = result.nextService;
    byId('recommendation').textContent = result.recommendation;
    positionDetection();
  } else {
    const [title, description] = result?.profile === 'liquid'
      ? ['Видимой жидкости не найдено', 'Для проверки внутренних утечек нужны осмотр узла и показания датчиков.']
      : unavailableResults[result?.status] ?? [
      'Ожидает кадр',
      'Добавьте фото или видео для проверки.',
    ];
    byId('empty-title').textContent = checking ? 'Проверяем кадр…' : title;
    byId('empty-text').textContent = checking ? '' : description;
  }
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
      return readResult(data);
    }
    const options = { signal: controller.signal, cache: 'no-store' };
    if (useServer) {
      const image = await new Promise((resolve) => frame.toBlob(resolve, 'image/jpeg', 0.85));
      if (!image) throw new Error('Не удалось подготовить кадр.');
      const body = new FormData();
      body.append('file', image, 'frame.jpg');
      body.append('profile', 'standard');
      const serial = byId('active-equipment')?.value.trim();
      if (serial) body.append('equipmentSerial', serial);
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
    if (useServer) window.CyberCamera.validateModel(data);
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
  if (data?.profile === 'liquid') {
    const boxes = data.liquidBoxes;
    if (!Array.isArray(boxes) || boxes.length > 4 || (boxes.length ? data.status !== 'defect' : data.status !== 'unusable')
        || boxes.some((item) => !item || !Object.hasOwn(liquidNames, item.kind) || !Array.isArray(item.box)
          || item.box.length !== 4 || item.box.some((value) => !Number.isInteger(value) || value < 0 || value > 1000)
          || item.box[0] >= item.box[2] || item.box[1] >= item.box[3])) {
      throw new Error('Сервер вернул некорректные рамки жидкости.');
    }
  }
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
  if (frameMetadata?.regionLabel || (result && frameSource === 'uploaded_video')) {
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
    if (useServer) showMessage(offlineMode ? 'SmolVLM анализирует кадр на телефоне…' : 'Проверяем кадр…');
    render();
    const nextResult = await analyzeFrame(useServer, snapshot, frameMetadata);
    if (generation !== inputGeneration) return;
    result = nextResult;
    showMessage();
  } catch (error) {
    if (generation === inputGeneration && error.name !== 'AbortError') showMessage(error.message || 'Не удалось проверить кадр. Повторите попытку.', true);
  } finally {
    if (generation === inputGeneration) checking = false;
    render();
  }
}

async function checkSelectedRegion() {
  if (!media.ready || videoRun || checking || clipReview?.busy || regionController || analysisController) return;
  const generation = inputGeneration;
  const selection = new AbortController();
  regionController = selection;
  const original = media.element;
  const isVideo = media.kind === 'uploaded_video';
  const fullFrame = document.createElement('canvas');
  try {
    media.pause();
    if (isVideo && frameMetadata?.videoTimeSeconds != null) {
      await media.seek(frameMetadata.videoTimeSeconds, selection.signal);
    }
    if (selection.signal.aborted || generation !== inputGeneration) return;
    const metadata = captureMetadata();
    // The dialog uses a bounded preview. The analysis always samples original pixels.
    drawFrame(fullFrame, original);
    const area = await window.CyberRegionReview.choose(fullFrame, { signal: selection.signal });
    if (!area || generation !== inputGeneration || selection.signal.aborted) return;
    const width = original.videoWidth || original.naturalWidth;
    const height = original.videoHeight || original.naturalHeight;
    const left = Math.round(area.x * width / fullFrame.width);
    const top = Math.round(area.y * height / fullFrame.height);
    const right = Math.round((area.x + area.width) * width / fullFrame.width);
    const bottom = Math.round((area.y + area.height) * height / fullFrame.height);
    const w = right - left, h = bottom - top;
    if (w < 1 || h < 1 || left < 0 || top < 0 || right > width || bottom > height) throw new Error('Выберите область внутри кадра.');
    const cropped = document.createElement('canvas');
    const scale = Math.min(1, 1280 / Math.max(w, h));
    cropped.width = Math.max(1, Math.round(w * scale));
    cropped.height = Math.max(1, Math.round(h * scale));
    cropped.getContext('2d').drawImage(original, left, top, w, h, 0, 0, cropped.width, cropped.height);
    checking = true;
    render(); showMessage('Проверяем выбранную область…');
    const answer = await analyzeFrame(true, cropped, metadata);
    if (generation !== inputGeneration || selection.signal.aborted) return;
    snapshot.width = cropped.width; snapshot.height = cropped.height;
    snapshot.getContext('2d').drawImage(cropped, 0, 0);
    frameSource = media.kind;
    frameMetadata = { ...metadata, regionLabel: isVideo ? `Фрагмент видео · ${window.CyberMedia.time(metadata.videoTimeSeconds)}` : 'Фрагмент фотографии' };
    result = { ...answer, regionNote: `Область осмотра: x=${left}, y=${top}, ${w}×${h} пикс. Исходный кадр ${width}×${height}.` };
    showMessage();
  } catch (error) {
    if (generation === inputGeneration && error.name !== 'AbortError') showMessage(error.message || 'Не удалось проверить область.', true);
  } finally {
    if (regionController === selection) regionController = null;
    if (generation === inputGeneration) checking = false;
    render();
  }
}

function changeCameraMode() {
  if (offlineMode) cameraMode.value = 'single';
  const target = cameraMode.value === 'video' ? checkedViewfinder : viewfinder;
  target.append(snapshot, detection);
  frameSource = null;
  frameMetadata = null;
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
    recordVideoResult(nextResult);
    if (run.times) {
      run.index += 1;
      mediaProgress = '';
      if (run.index >= run.times.length) {
        stopVideoRun();
        showMessage();
        return;
      }
    }
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
    for (let t = 0; t < media.element.duration && times.length < 20; t += interval / 1000) times.push(t);
  }
  videoRun = { frame: document.createElement('canvas'), interval,
    timer: null, controller: new AbortController(), times, index: 0 };
  showMessage('Проверяем видео…'); render(); checkVideoFrame(videoRun);
}

cameraButton.addEventListener('click', toggleCamera);
byId('choose-region')?.addEventListener('click', checkSelectedRegion);
const catalogButton = byId('open-example-photo');
catalogButton?.addEventListener('click', async () => {
  if (await media.openCatalogPhoto(catalogButton.dataset.photoUrl)) {
    checkButton.focus({ preventScroll: true });
  }
});
const catalogPreview = byId('example-photo-preview');
function hideUnavailablePhoto() {
  if (catalogPreview && !catalogPreview.naturalWidth) {
    if (catalogButton) catalogButton.hidden = true;
    cameraIdle.dataset.unavailable = 'true';
  }
}
catalogPreview?.addEventListener('error', hideUnavailablePhoto);
if (catalogPreview?.complete) hideUnavailablePhoto();
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
function selectInspectionTarget(event, box, label, anchor = 'primary') {
  if (!box) return;
  const marker = event.currentTarget.getBoundingClientRect();
  // The overlay is already positioned in image coordinates, excluding letterboxing.
  const fraction = (pixel, start, span) => event.detail && span ? Math.max(0, Math.min(1, (pixel - start) / span)) : 0.5;
  const point = {x: Math.min(1, box.x + box.width * fraction(event.clientX, marker.left, marker.width)),
    y: Math.min(1, box.y + box.height * fraction(event.clientY, marker.top, marker.height))};
  if (window.CyberInspection.openCloseup?.(point, label, anchor)) return;
  byId('result-title').focus({ preventScroll: true });
  byId('result').scrollIntoView({ block: 'nearest' });
}
detection.addEventListener('click', event => {
  selectInspectionTarget(event, result?.box, byId('detection-label').textContent);
});
function releaseCamera() {
  regionController?.abort();
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

window.addEventListener('pagehide', event => {
  releaseCamera();
  if (event.persisted) return;
  resetInput(); media.clear(); render();
});
window.addEventListener('pageshow', event => { if (event.persisted) render(); });
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

window.CyberCaptureReady = true;

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
    render();
  } catch (_) {
    // Discovery is optional: camera and file selection never wait for this request.
  } finally { clearTimeout(timeout); }
}
loadCapabilities();
})();
