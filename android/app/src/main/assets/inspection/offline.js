/* Android-only on-device inference. This module never sends images to a server. */
(() => {
  'use strict';
  const enabled = location.origin === 'https://appassets.androidplatform.net' &&
    location.pathname.startsWith('/offline/') && typeof window.CyberSmolNative?.analyze === 'function';
  const pending = new Map();
  let modelState = { loaded: false, busy: false, state: 'idle' };
  let controls, message, progress, loadButton, cancelButton, importButton, resultCard;
  let frameCancelButton;
  let modelDescription;
  let historyPanel, renderedResult = null;
  const HISTORY_KEY = 'cyber102.offline.inspections.v1';
  const MODELS = new Set(['SmolVLM-256M-Instruct', 'SmolVLM-500M-Instruct', 'SmolVLM2-2.2B-Instruct', 'VisionPsy-Nano-460M']);
  const modelName = model => MODELS.has(model) ? model : 'VisionPsy-Nano-460M';
  const displayBytes = bytes => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2).replace('.', ',')} ГБ` : `${Math.ceil(bytes / 1e6)} МБ`;
  const findingLabels = new Map([
    ['irregular split or hole', 'Неровная трещина или отверстие'],
    ['electrical wires', 'Видна электропроводка'],
    ['damaged electrical contacts', 'Повреждение электрических контактов'],
    ['contact damaged', 'Повреждение контакта'],
    ['electrical wires or contacts damaged', 'Повреждение проводки или контактов'],
    ['heavy surface rust', 'Сильная поверхностная коррозия'],
    ['liquid dripping', 'Видны капли жидкости'],
    ['liquids dripping', 'Видны капли жидкости'],
    ['normal', 'Явный дефект по кадру не различим'],
    ['normal or unclear', 'Явный дефект по кадру не различим'],
    ['unclear', 'Кадр не даёт однозначного наблюдения'],
    ['indistinct', 'Кадр не даёт однозначного наблюдения'],
  ]);
  let counter = 0;
  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = value;
    if (className) element.className = className;
    return element;
  };
  const aborted = () => new DOMException('Анализ отменён', 'AbortError');

  function readHistory() {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw && raw.length > 150000) throw new Error('Локальная история повреждена.');
    const records = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(records)) throw new Error('Локальная история повреждена.');
    return records.filter(record => record?.source === 'on_device' && typeof record.id === 'string' &&
      typeof record.observation === 'string' && record.observation.length <= 4000 &&
      typeof record.note === 'string' && record.note.length <= 1000 &&
      typeof record.createdAt === 'string' && Number.isFinite(Date.parse(record.createdAt))).slice(0, 10);
  }

  function renderHistory() {
    if (!historyPanel) return;
    historyPanel.replaceChildren();
    try {
      const records = readHistory();
      historyPanel.append(text('summary', `Осмотры на телефоне · ${records.length}`),
        text('p', 'Последние 10 текстовых осмотров сохранены только на этом устройстве. Фото и видео в эту историю не записываются; синхронизации с сервером нет.', 'video-help'));
      const list = text('ol');
      for (const record of records) {
        const item = text('li');
        item.style.overflowWrap = 'anywhere';
        item.append(text('strong', new Date(record.createdAt).toLocaleString('ru-RU')),
          text('p', record.observation), text('p', record.note || 'Заметка не добавлена', 'equipment-id'),
          text('p', `${modelName(record.model)} · анализ на устройстве · исходный ответ модели`, 'video-help'));
        list.append(item);
      }
      historyPanel.append(list);
    } catch (_) {
      historyPanel.append(text('summary', 'История на телефоне недоступна'), text('p', 'Браузер не разрешил прочитать локальное хранилище. Результат анализа остаётся на экране.', 'video-help'));
    }
  }

  function updateStatus(state) {
    modelState = { ...modelState, ...state };
    if (!controls) return;
    const size = Number.isSafeInteger(modelState.modelBytes) && modelState.modelBytes > 0 ?
      `Размер двух файлов модели: ${displayBytes(modelState.modelBytes)}. ` : '';
    modelDescription.textContent = `${modelName(modelState.model)} работает без подключения к сети. ${size}При первом запуске файлы проверяются и копируются в память приложения. Проверка выполняется по одному кадру.`;
    message.textContent = modelState.message || 'Подготовьте модель для офлайн-анализа.';
    message.dataset.error = String(modelState.state === 'error');
    progress.hidden = !modelState.busy;
    progress.value = Number.isFinite(modelState.progress) ? modelState.progress : 0;
    loadButton.disabled = Boolean(modelState.busy || modelState.loaded);
    loadButton.textContent = modelState.loaded ? 'Модель готова' : 'Подготовить модель';
    cancelButton.hidden = !modelState.busy;
    frameCancelButton.hidden = modelState.state !== 'analyzing' || !modelState.busy;
    importButton.disabled = Boolean(modelState.busy);
    controls.setAttribute('aria-busy', String(Boolean(modelState.busy)));
  }

  function onNativeEvent(event) {
    if (!enabled || !event || typeof event !== 'object') return;
    if (event.type === 'status') { updateStatus(event); return; }
    const job = pending.get(event.requestId);
    if (!job) return;
    if (event.type === 'result') {
      const raw = event.result;
      if (!MODELS.has(event.model) || !raw || raw.cancelled || typeof raw.text !== 'string' || !raw.text.trim() || raw.text.length > 4000) {
        job.reject(new Error('Модель не вернула пригодное наблюдение. Повторите кадр.'));
        return;
      }
      const timings = {};
      for (const key of ['promptMs', 'generateMs', 'totalMs', 'generatedTokens', 'inputTokens']) {
        if (Number.isFinite(raw[key]) && raw[key] >= 0) timings[key] = raw[key];
      }
      job.resolve({ status: 'offline_observation', source: 'on_device', model: event.model,
        modelVersion: typeof event.modelVersion === 'string' ? event.modelVersion : '',
        observation: raw.text.trim(), preliminary: true, observationLanguage: 'en',
        durationSeconds: Number.isFinite(raw.totalMs) ? raw.totalMs / 1000 : undefined,
        metrics: timings, media: job.metadata });
    } else if (event.type === 'error') {
      job.reject(event.code === 'cancelled' ? aborted() : new Error(event.message || 'Офлайн-анализ не выполнен.'));
    }
  }

  async function analyze(blob, { signal, onProgress, metadata = { captureSource: 'camera' } } = {}) {
    if (!enabled) throw new Error('Офлайн-модель доступна только в Android-приложении.');
    if (signal?.aborted) throw aborted();
    updateStatus(JSON.parse(window.CyberSmolNative.state()));
    if (!modelState.loaded) throw new Error('Сначала нажмите «Подготовить модель» в блоке офлайн-анализа.');
    if (modelState.busy || pending.size) throw new Error('Офлайн-модель занята. Дождитесь завершения или отмените анализ.');
    if (!(blob instanceof Blob) || blob.type !== 'image/jpeg' || !blob.size || blob.size > 1536 * 1024) throw new Error('Для офлайн-анализа нужен JPEG-кадр до 1,5 МБ.');
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      const stop = () => { reader.abort(); reject(aborted()); };
      signal?.addEventListener('abort', stop, { once: true });
      reader.onload = () => { signal?.removeEventListener('abort', stop); resolve(String(reader.result).split(',')[1]); };
      reader.onerror = () => { signal?.removeEventListener('abort', stop); reject(new Error('Не удалось подготовить кадр.')); };
      reader.readAsDataURL(blob);
    });
    if (signal?.aborted) throw aborted();
    const requestId = `frame_${Date.now()}_${++counter}`;
    onProgress?.('Анализ на телефоне. Кадр не отправляется в сеть…');
    return new Promise((resolve, reject) => {
      let timer;
      const finish = (callback, value) => {
        clearTimeout(timer); signal?.removeEventListener('abort', cancel); pending.delete(requestId); callback(value);
      };
      const cancel = () => {
        window.CyberSmolNative.cancel();
        finish(reject, aborted());
      };
      pending.set(requestId, {
        metadata: { ...metadata, declaredBy: 'client' },
        resolve: value => finish(resolve, value), reject: error => finish(reject, error),
      });
      signal?.addEventListener('abort', cancel, { once: true });
      timer = setTimeout(() => {
        window.CyberSmolNative.cancel();
        finish(reject, new Error('Офлайн-анализ не завершился за 3 минуты. Попробуйте более простой кадр.'));
      }, 180000);
      try { window.CyberSmolNative.analyze(requestId, encoded); }
      catch (error) { finish(reject, error); }
    });
  }

  function render(result, checking = false) {
    if (!enabled || !resultCard) return;
    const valid = result?.status === 'offline_observation' && result?.source === 'on_device';
    resultCard.hidden = !valid;
    const empty = document.getElementById('empty');
    if (empty && valid) empty.hidden = true;
    for (const id of ['details', 'inspection-workflow', 'detection', 'video-review-panel', 'demo-options']) {
      const element = document.getElementById(id); if (element) element.hidden = true;
    }
    const mode = document.getElementById('mode');
    if (mode) mode.textContent = `На телефоне · ${modelName(result?.model || modelState.model)}`;
    if (!valid) {
      renderedResult = null;
      if (!checking) {
        const emptyText = document.getElementById('empty-text');
        if (emptyText) emptyText.textContent = 'Проверьте кадр: VisionPsy даст предварительное описание видимого объекта прямо на телефоне.';
      }
      return;
    }
    resultCard.setAttribute('aria-busy', String(checking));
    if (renderedResult === result) return;
    renderedResult = result;
    const capture = { camera: 'Кадр камеры', uploaded_photo: 'Загруженное фото', uploaded_video: 'Кадр загруженного видео' }[result.media?.captureSource] || 'Кадр на устройстве';
    const position = result.media?.captureSource === 'uploaded_video' && Number.isFinite(result.media.videoTimeSeconds) ? ` · ${result.media.videoTimeSeconds.toFixed(1)} с` : '';
    const duration = Number.isFinite(result.durationSeconds) ? ` · Анализ ${result.durationSeconds.toFixed(1)} с` : '';
    const labelText = findingLabels.get(result.observation.toLowerCase().replace(/[.!]+$/, '').trim());
    resultCard.replaceChildren(text('span', 'Офлайн · предварительное наблюдение', 'finding'),
      text('h3', `${modelName(result.model)} на этом телефоне`),
      text('p', capture + position + duration, 'video-metrics'),
      checkedFrame());
    if (labelText) resultCard.append(text('p', labelText, 'offline-caption'));
    resultCard.append(text('p', 'Исходный ответ модели на английском', 'equipment-id'),
      text('p', result.observation, 'offline-observation'),
      text('p', 'Сверьте описание с кадром. Оценка скрытых неисправностей, ресурса и безопасности эксплуатации требует отдельной проверки.', 'equipment-id'));
    const label = text('label', 'Заметка к осмотру на телефоне'); label.htmlFor = 'offline-note';
    const note = text('textarea'); note.id = label.htmlFor; note.rows = 3; note.maxLength = 1000;
    note.style.width = '100%'; note.style.font = 'inherit';
    const save = text('button', 'Сохранить на телефоне', 'button secondary'); save.type = 'button'; save.id = 'offline-save';
    const saved = text('p', '', 'message'); saved.id = 'offline-saved'; saved.setAttribute('role', 'status');
    save.addEventListener('click', () => {
      try {
        const record = { id: `local_${Date.now()}_${++counter}`, source: 'on_device', createdAt: new Date().toISOString(),
          model: result.model, modelVersion: result.modelVersion, observation: result.observation,
          note: note.value.slice(0,1000), media: result.media, durationSeconds: result.durationSeconds };
        localStorage.setItem(HISTORY_KEY, JSON.stringify([record, ...readHistory()].slice(0, 10)));
        save.disabled = note.disabled = true;
        saved.textContent = 'Сохранено на телефоне. На сервер ничего не отправлено.';
        renderHistory();
      } catch (_) { saved.textContent = 'Не удалось сохранить на телефоне. Локальное хранилище недоступно.'; }
    });
    resultCard.append(label, note, save, saved);
    resultCard.querySelector('.offline-observation').style.overflowWrap = 'anywhere';
  }

  function checkedFrame() {
    const source = document.getElementById('snapshot');
    const preview = document.createElement('canvas');
    preview.className = 'offline-checked-frame';
    preview.setAttribute('role', 'img');
    preview.setAttribute('aria-label', 'Кадр, по которому получено наблюдение');
    preview.width = source.width;
    preview.height = source.height;
    preview.getContext('2d').drawImage(source, 0, 0);
    return preview;
  }

  function setup() {
    if (!enabled) return;
    controls = text('section', undefined, 'panel'); controls.id = 'offline-controls';
    modelDescription = text('p', 'Определяем модель из комплекта приложения…', 'video-help'); modelDescription.id = 'offline-model-description';
    controls.append(text('h2', 'Модель на телефоне'), modelDescription);
    controls.style.padding = '16px'; controls.style.marginBottom = '16px';
    const actions = text('div', undefined, 'actions');
    loadButton = text('button', 'Подготовить модель', 'button primary'); loadButton.type = 'button'; loadButton.id = 'offline-load';
    cancelButton = text('button', 'Остановить', 'button secondary'); cancelButton.type = 'button'; cancelButton.id = 'offline-cancel';
    importButton = text('button', 'Импортировать модель', 'button secondary'); importButton.type = 'button'; importButton.id = 'offline-import';
    loadButton.addEventListener('click', () => window.CyberSmolNative.load());
    const cancel = () => {
      window.CyberSmolNative.cancel();
      for (const job of [...pending.values()]) job.reject(aborted());
    };
    cancelButton.addEventListener('click', cancel);
    frameCancelButton = text('button', 'Остановить анализ', 'button secondary');
    frameCancelButton.type = 'button'; frameCancelButton.id = 'offline-frame-cancel'; frameCancelButton.hidden = true;
    frameCancelButton.addEventListener('click', cancel);
    document.getElementById('check')?.after(frameCancelButton);
    importButton.addEventListener('click', () => window.CyberSmolNative.importModel());
    actions.append(loadButton, cancelButton, importButton);
    progress = text('progress'); progress.max = 100; progress.hidden = true; progress.setAttribute('aria-label', 'Подготовка модели');
    message = text('p', '', 'message'); message.id = 'offline-status'; message.setAttribute('role', 'status');
    controls.append(actions, progress, message);
    historyPanel = text('details'); historyPanel.id = 'offline-history'; controls.append(historyPanel); renderHistory();
    const workspace = document.querySelector('.workspace'); workspace?.before(controls);
    resultCard = text('section', undefined, 'details'); resultCard.id = 'offline-result'; resultCard.hidden = true;
    document.getElementById('result')?.append(resultCard);
    for (const selector of ['.active-equipment-bar', '#inspection-journal-link', '#demo-options', '#video-review-panel', '#camera-mode', '#camera-mode-label', '#analysis-profile', 'label[for="analysis-profile"]', '#profile-help']) {
      const node = document.querySelector(selector); if (node) node.hidden = true;
    }
    const serverSettings = document.getElementById('camera-options')?.closest('.capture-settings');
    if (serverSettings) serverSettings.hidden = true;
    for (const link of document.querySelectorAll('.app-nav a')) {
      if (!link.getAttribute('href')?.startsWith('index.html')) link.hidden = true;
    }
    for (const link of document.querySelectorAll('a[href]')) {
      if (link.getAttribute('href')?.startsWith('index.html')) link.href = '/offline/index.html';
    }
    const intro = document.querySelector('.intro p');
    if (intro) intro.textContent = 'Камера и фото на устройстве · без отправки кадров на сервер';
    const mediaHelp = document.getElementById('media-help');
    if (mediaHelp) mediaHelp.textContent = 'Фото до 20 МБ · видео до 150 МБ и 2 минут. Выбранный кадр анализируется прямо на телефоне.';
    updateStatus(JSON.parse(window.CyberSmolNative.state()));
    render(null);
    if (!modelState.loaded && !modelState.busy) window.CyberSmolNative.load();
  }

  window.CyberOffline = { enabled, active: enabled, analyze, render, onNativeEvent };
  if (enabled) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup, { once: true }); else setup();
    window.addEventListener('pagehide', () => {
      window.CyberSmolNative.cancel();
      for (const job of [...pending.values()]) job.reject(aborted());
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) updateStatus(JSON.parse(window.CyberSmolNative.state()));
    });
  }
})();
