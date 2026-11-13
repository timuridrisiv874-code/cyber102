/* Shared functional inspection workflow for the browser and Android WebView. */
(() => {
  'use strict';
  const byId = (id) => document.getElementById(id);
  const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

  function abortableDelay(milliseconds, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
      const aborted = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      const timer = setTimeout(() => { signal?.removeEventListener('abort', aborted); resolve(); }, milliseconds);
      signal?.addEventListener('abort', aborted, { once: true });
    });
  }

  async function fetchAnalysis(endpoint, options, onBusy) {
    // Only a typed busy response can be retried. Keep the same captured frame;
    // never overlap requests or silently retry failed inference/network errors.
    for (let attempt = 0; ; attempt += 1) {
      const response = await fetch(endpoint, options);
      if (response.status !== 503 || attempt >= 3) return response;
      const error = await response.clone().json().catch(() => ({}));
      if (error.code !== 'vision_busy') return response;
      const seconds = Number(response.headers.get('Retry-After'));
      const wait = Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 5) * 1000 : 2000;
      onBusy?.(`Модель занята. Повторяем этот кадр: ${attempt + 1}/3…`);
      await abortableDelay(wait, options.signal);
    }
  }

  function validateModel(data) {
    if (data?.source !== 'local_vision' || typeof data.model !== 'string' ||
        !data.model.trim() || data.model.length > 200) {
      throw new Error('Сервер не подтвердил локальный анализ кадра. Проверьте настройки модели.');
    }
  }

  function renderTiming(result) {
    const node = byId('analysis-timing');
    if (!node) return;
    const timings = [];
    if (Number.isFinite(result?.clientDurationSeconds)) timings.push(`Ответ на телефоне: ${number.format(result.clientDurationSeconds)} с`);
    if (Number.isFinite(result?.durationSeconds)) timings.push(`Сервер: ${number.format(result.durationSeconds)} с`);
    if (Number.isFinite(result?.metrics?.generateMs)) timings.push(`Модель: ${number.format(result.metrics.generateMs / 1000)} с`);
    if (result?.profile === 'fast') timings.push('быстрый режим');
    if (result?.profile === 'standard') timings.push('подробный режим');
    node.textContent = timings.join(' · ');
    node.hidden = !timings.length;
  }

  window.CyberCamera = { fetchAnalysis, validateModel, renderTiming };
  const panel = byId('inspection-workflow');
  if (!panel) return;
  const form = byId('inspection-form');
  const fields = byId('inspection-fields');
  const serial = byId('inspection-serial');
  const telemetry = byId('inspection-telemetry');
  const gauge = byId('inspection-gauge');
  const note = byId('inspection-note');
  const previewButton = byId('inspection-preview');
  const saveButton = byId('inspection-save');
  const message = byId('inspection-message');
  const confirms = byId('inspection-confirmations');
  const visionLabels = {
    seal_leak: 'Жидкость именно в зоне уплотнения вала',
    foundation_issue: 'Коррозия рамы / отсутствие или ослабление анкеров',
    oil_issue: 'Мутное масло или уровень ниже MIN в смотровом стекле',
    terminal_damage: 'Окисление или следы перегрева клеммной коробки',
    guard_issue: 'Видимое отсутствие, повреждение или смещение кожуха муфты',
    flow_unstable: 'Видимая нестабильность потока',
  };
  for (const [key, title] of Object.entries(visionLabels)) {
    const row = document.createElement('div');
    row.className = 'demo-options';
    const label = document.createElement('label');
    label.htmlFor = `inspection-${key}`;
    label.textContent = title;
    const select = document.createElement('select');
    select.id = label.htmlFor;
    select.dataset.visionKey = key;
    select.append(new Option('Не оценено', ''), new Option('Да · подтверждено', 'true'), new Option('Нет · проверено', 'false'));
    row.append(label, select);
    confirms.append(row);
  }
  serial.value = new URLSearchParams(location.search).get('serial') || serial.value;
  let current = null;
  let cameraBusy = false;
  let pending = false;
  let serialReady = '';
  let serialLoad = 0;
  let revision = 0;
  let preview = null;
  let saved = false;
  let savePayload = null;
  let controller = null;

  function setMessage(text = '', error = false) {
    message.textContent = text;
    message.dataset.error = String(error);
  }

  function sync() {
    fields.disabled = pending || cameraBusy || Boolean(savePayload) || saved;
    const ready = Boolean(current && serialReady === serial.value.trim());
    previewButton.disabled = !ready || pending || cameraBusy || Boolean(savePayload) || saved;
    saveButton.disabled = !ready || pending || cameraBusy || saved || (!preview && !savePayload);
    byId('inspection-paused').hidden = !cameraBusy;
    const selected = telemetry.value;
    byId('inspection-source').textContent = selected ?
      'Демонстрационные показания: эмулятор, не реальные датчики этого кадра. Визуальные признаки сценария не используются.' :
      'Данные датчиков не подключены. Неизвестные параметры не считаются нормой.';
    byId('inspection-history').href = `/checkpoint.html?${new URLSearchParams({ serial: serial.value.trim() })}`;
  }

  function invalidate() {
    revision += 1;
    controller?.abort();
    controller = null;
    pending = false;
    preview = null;
    saved = false;
    savePayload = null;
    byId('inspection-diagnosis').hidden = true;
    byId('inspection-findings').replaceChildren();
    saveButton.textContent = 'Сохранить осмотр';
    setMessage();
    sync();
  }

  async function api(path, body, signal) {
    const response = await fetch(path, {
      cache: 'no-store', redirect: 'error', signal,
      ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
    });
    const data = await response.json().catch(() => { throw new Error('Сервер вернул некорректный ответ.'); });
    if (!response.ok) throw new Error(data.error || `Ошибка сервера: ${response.status}`);
    return data;
  }

  async function loadSerial() {
    const requested = serial.value.trim();
    const load = ++serialLoad;
    serialReady = '';
    telemetry.replaceChildren(new Option('Нет данных датчиков', ''));
    invalidate();
    if (!requested) return;
    setMessage('Проверяем серийный номер…');
    try {
      const data = await api(`/api/scenarios?${new URLSearchParams({ serial: requested })}`);
      if (load !== serialLoad || requested !== serial.value.trim()) return;
      if (data.passport?.serial !== requested || !Array.isArray(data.scenarios)) throw new Error('Сервер не подтвердил серийный номер.');
      serialReady = requested;
      for (const item of data.scenarios) {
        if (typeof item.id === 'string' && typeof item.label === 'string') telemetry.append(new Option(`Демо: ${item.label}`, item.id));
      }
      setMessage(`Оборудование: ${data.passport.model || requested}. Источник датчиков выберите отдельно.`);
    } catch (error) {
      if (load === serialLoad) setMessage(error.message, true);
    } finally {
      if (load === serialLoad) sync();
    }
  }

  function buildPayload() {
    if (!current || serialReady !== serial.value.trim()) throw new Error('Подтвердите серийный номер оборудования.');
    if (!form.reportValidity()) return null;
    const confirmedVision = {};
    for (const select of confirms.querySelectorAll('select')) {
      confirmedVision[select.dataset.visionKey] = select.value === '' ? null : select.value === 'true';
    }
    if (gauge.value.trim()) {
      const value = Number(gauge.value);
      if (!Number.isFinite(value) || value < 0) throw new Error('Показание манометра должно быть неотрицательным числом в МПа.');
      confirmedVision.gauge_pressure = value;
    }
    return {
      serial: serialReady, analysisId: current.analysisId, confirmedVision, note: note.value,
      telemetrySource: telemetry.value ? { kind: 'demo_scenario', scenarioId: telemetry.value } : { kind: 'none' },
    };
  }

  function renderFindings(data) {
    if (!Array.isArray(data.findings)) throw new Error('Сервер вернул неполную диагностику.');
    const list = byId('inspection-findings');
    list.replaceChildren();
    for (const finding of data.findings) {
      const item = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = finding.title || finding.id || 'Результат';
      const diagnosis = document.createElement('p');
      diagnosis.textContent = finding.diagnosis || '';
      const recommendation = document.createElement('p');
      recommendation.textContent = finding.recommendation || '';
      item.append(title, diagnosis, recommendation);
      if (Array.isArray(finding.evidence)) {
        const evidence = document.createElement('p');
        evidence.className = 'equipment-id';
        evidence.textContent = `Основание: ${finding.evidence.join('; ')}`;
        item.append(evidence);
      }
      list.append(item);
    }
    if (!data.findings.length) {
      const item = document.createElement('li');
      item.textContent = 'Правила не сработали по переданным данным. Это не подтверждение исправности; часть признаков может быть неизвестна.';
      list.append(item);
    }
    byId('inspection-diagnosis-source').textContent = data.provenance?.telemetry === 'demo_scenario' ?
      'Кадр камеры + подтверждение инженера + демонстрационная телеметрия.' :
      'Кадр камеры + подтверждение инженера. Телеметрии нет.';
    byId('inspection-diagnosis').hidden = false;
  }

  async function runPreview() {
    if (previewButton.disabled) return;
    const payload = buildPayload();
    if (!payload) return;
    const activeRevision = revision;
    pending = true;
    controller = new AbortController();
    sync();
    setMessage('Сопоставляем подтверждённые признаки и показания…');
    try {
      const data = await api('/api/inspections/preview', payload, controller.signal);
      if (activeRevision !== revision) return;
      renderFindings(data);
      preview = payload;
      setMessage('Проверьте выводы. После сохранения кадр, показания и подтверждения останутся в истории.');
    } catch (error) {
      if (activeRevision === revision && error.name !== 'AbortError') setMessage(error.message, true);
    } finally {
      if (activeRevision === revision) { pending = false; controller = null; sync(); }
    }
  }

  function requestId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
  }

  async function saveInspection(event) {
    event.preventDefault();
    if (saveButton.disabled) return;
    if (!savePayload) savePayload = { ...preview, requestId: requestId() };
    const payload = savePayload;
    const activeRevision = revision;
    pending = true;
    controller = new AbortController();
    sync();
    saveButton.textContent = 'Сохраняем…';
    setMessage();
    try {
      const data = await api('/api/inspections', payload, controller.signal);
      if (activeRevision !== revision) return;
      if (!data.record?.id) throw new Error('Сервер не подтвердил сохранение.');
      saved = true;
      saveButton.textContent = 'Осмотр сохранён';
      setMessage(`Осмотр сохранён в историю ${payload.serial}. Запись ${data.record.id}.`);
    } catch (error) {
      if (activeRevision !== revision || error.name === 'AbortError') return;
      saveButton.textContent = 'Повторить сохранение';
      setMessage(`Не удалось подтвердить сохранение. ${error.message} Повтор использует тот же номер запроса и не создаёт дубль.`, true);
    } finally {
      if (activeRevision === revision) { pending = false; controller = null; sync(); }
    }
  }

  window.CyberInspection = {
    update(result, busy) {
      cameraBusy = Boolean(busy);
      const valid = result?.source === 'local_vision' && ['defect', 'clear'].includes(result.status) &&
        typeof result.analysisId === 'string' && result.analysisId.length > 0 && result.analysisId.length <= 128;
      panel.hidden = !valid;
      if ((valid ? result.analysisId : null) !== (current?.analysisId || null)) {
        current = valid ? result : null;
        invalidate();
        for (const select of confirms.querySelectorAll('select')) select.value = '';
        gauge.value = note.value = '';
        if (valid) {
          byId('inspection-frame').textContent = `Привязан последний проверенный кадр. Анализ: ${result.analysisId}.`;
          if (!serialReady) loadSerial();
        }
      }
      sync();
    },
  };
  serial.addEventListener('input', () => { serialReady = ''; serialLoad += 1; invalidate(); });
  serial.addEventListener('change', loadSerial);
  telemetry.addEventListener('change', invalidate);
  confirms.addEventListener('change', invalidate);
  gauge.addEventListener('input', invalidate);
  note.addEventListener('input', invalidate);
  previewButton.addEventListener('click', () => runPreview().catch((error) => setMessage(error.message, true)));
  form.addEventListener('submit', (event) => saveInspection(event).catch((error) => setMessage(error.message, true)));
  window.addEventListener('pagehide', () => { controller?.abort(); });
})();
