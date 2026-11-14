/* Shared functional inspection workflow for the browser and Android WebView. */
(() => {
  'use strict';
  const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

  function citedField(record, key, units) {
    const field = record?.fields?.[key];
    const ref = field?.source;
    return Number.isFinite(field?.value) && field.value > 0 && units.includes(field.unit) &&
      typeof ref?.documentId === 'string' && /^[a-zA-Z0-9_-]+$/.test(ref.documentId) &&
      Number.isSafeInteger(ref.page) && ref.page > 0 ? field : null;
  }

  function motorThreshold(catalog, serial, reading) {
    const motor = catalog?.equipment?.find(item => item.kind === 'motor' && item.serial === serial);
    const life = citedField(motor, 'assigned_lifetime', ['лет', 'года', 'год']);
    const hours = citedField(motor, 'assigned_resource', ['ч']);
    if (!hours && !life) return null;
    const terms = [], sources = new Map();
    for (const [field, label] of [[life, 'Назначенный срок службы'], [hours, 'Назначенный ресурс']]) {
      if (!field) continue;
      terms.push(`${label} — ${number.format(field.value)} ${field.unit}`);
      sources.set(`/api/customer/documents/${field.source.documentId}/pdf#page=${field.source.page}`, `Паспорт двигателя · стр. ${field.source.page}`);
    }
    const valid = Number.isFinite(reading) && reading >= 0;
    const remaining = !valid || !hours ? '' : reading >= hours.value
      ? 'Назначенный ресурс по наработке достигнут.'
      : `До назначенного ресурса — ${number.format(hours.value - reading)} ч.`;
    return {terms: terms.join('. ') + '.', remaining, acceptsHours: Boolean(hours), sources};
  }

  function pumpThreshold(catalog, serial, source, reading) {
    const pump = catalog?.equipment?.find(item => item.kind === 'pump' && item.serial === serial);
    if (!pump) return null;
    const terms = [];
    const sources = new Map();
    let minimum;
    for (const [key, units] of [['design_lifetime', ['год', 'года', 'лет']], ['design_resource_before_writeoff', ['ч']]]) {
      const field = citedField(pump, key, units);
      if (!field) continue;
      const ref = field.source;
      const amount = `${number.format(field.value)} ${field.unit}`;
      if (key === 'design_lifetime') terms.push(`Срок службы — ${amount}`);
      else {
        minimum = field.value;
        terms.push(`Ресурс до списания — не менее ${amount}`);
      }
      sources.set(`/api/customer/documents/${ref.documentId}/pdf#page=${ref.page}`, `Паспорт · стр. ${ref.page}`);
    }
    if (!terms.length) return null;
    let hours = '';
    if (minimum !== undefined) {
      if (source !== 'manual' || !Number.isFinite(reading) || reading < 0) {
        hours = 'Для расчёта до паспортного минимума укажите суммарную наработку в ручных показаниях.';
      } else if (reading >= minimum) {
        hours = `Паспортный минимум достигнут при наработке ${number.format(minimum)} ч. Введено ${number.format(reading)} ч.`;
      } else {
        hours = `До паспортного минимума — ${number.format(minimum - reading)} ч. Введено ${number.format(reading)} ч.`;
      }
    }
    return { terms: terms.join('. ') + '.', hours, sources };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { pumpThreshold, motorThreshold };
  if (typeof window === 'undefined' || window.CyberWebBoot?.blocked) return;
  const byId = (id) => document.getElementById(id);

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
      const response = window.CyberAnalysisJobs
        ? await window.CyberAnalysisJobs.request(endpoint, options, onBusy) : await fetch(endpoint, options);
      // A queued failure belongs to one idempotent job and must not resubmit it.
      if (response.analysisQueued) return response;
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
    if (result?.profile === 'liquid') timings.push('поиск жидкости');
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
  const closeupPanel = byId('inspection-closeup');
  const closeupOpen = byId('closeup-open');
  const closeupLabel = byId('closeup-label');
  const closeupImage = byId('closeup-image');
  const closeupStatus = byId('closeup-status');
  const confirms = byId('inspection-confirmations');
  const manual = document.createElement('div');
  manual.id = 'inspection-manual';
  manual.hidden = true;
  telemetry.closest('.demo-options').after(manual);
  const componentRow = document.createElement('div');
  componentRow.className = 'demo-options';
  const componentLabel = document.createElement('label');
  componentLabel.htmlFor = 'inspection-component';
  componentLabel.textContent = 'Узел';
  const component = document.createElement('select');
  component.id = componentLabel.htmlFor;
  component.append(new Option('Насос', 'pump'));
  componentRow.append(componentLabel, component);
  serial.closest('p').after(componentRow);
  const serialChoices = document.createElement('datalist');
  serialChoices.id = 'inspection-equipment-options';
  serial.setAttribute('list', serialChoices.id);
  const activeEquipment = byId('active-equipment');
  (activeEquipment || serial).after(serialChoices);
  const navigator = document.createElement('section');
  navigator.id = 'inspection-guidance';
  const references = document.createElement('section');
  references.id = 'inspection-references';
  byId('inspection-findings').after(navigator, references);
  let manualFields = [];
  let catalogPromise = null;
  let passportsPromise = null;
  let passports = null;
  const resource = byId('inspection-resource');
  const motorPanel = byId('motor-resource');
  const motorSerial = byId('motor-resource-serial');
  const motorHours = byId('motor-resource-reading');
  let motorContext = '';
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
  const parameters = new URLSearchParams(location.search);
  serial.value = parameters.get('serial') || (parameters.get('example') === '1' ? 'P-102' : serial.value);
  let current = null;
  let cameraBusy = false;
  let pending = false;
  let serialReady = '';
  let serialLoad = 0;
  let revision = 0;
  let preview = null;
  let saved = '';
  let savePayload = null;
  let controller = null;
  let closeupPoint = null;
  let closeupJpeg = '';
  let closeupMeasurement = null;
  let closeupVersion = 0;
  let preparingCloseup = false;
  let closeupTarget = '';
  let closeupAnchor = '';

  function showCloseupStatus(text = '', error = false) {
    closeupStatus.textContent = text;
    closeupStatus.hidden = !text;
    closeupStatus.dataset.error = String(error);
  }

  function resetCloseup() {
    closeupVersion++;
    closeupPoint = null;
    closeupJpeg = '';
    closeupTarget = '';
    closeupAnchor = '';
    closeupMeasurement = null;
    preparingCloseup = false;
    closeupLabel.required = false;
    closeupLabel.value = '';
    byId('closeup-defect').value = '';
    delete closeupPanel.dataset.serial;
    closeupImage.removeAttribute('src');
    byId('closeup-preview').hidden = true;
    byId('closeup-remove').hidden = true;
    byId('closeup-measures').hidden = true;
    byId('closeup-length').textContent = '';
    closeupPanel.hidden = true;
    showCloseupStatus();
  }

  function setCloseupPoint(point) {
    closeupPoint = { x: Math.max(0, Math.min(1, point.x)), y: Math.max(0, Math.min(1, point.y)) };
    const mark = byId('closeup-mark');
    mark.style.left = (100 * closeupPoint.x) + '%';
    mark.style.top = (100 * closeupPoint.y) + '%';
  }

  function openCloseup(point, label, anchor = 'primary') {
    if (!current || current.regionNote || cameraBusy || pending || saved || savePayload) return false;
    const frame = byId('snapshot');
    const overview = byId('closeup-overview');
    if (!frame.width || !frame.height || !point) return false;
    if (!closeupPoint || anchor !== closeupAnchor) {
      resetCloseup();
      closeupAnchor = anchor;
      closeupTarget = label || current.component;
      closeupLabel.value = closeupTarget;
      const ratio = Math.min(1, 400 / Math.max(frame.width, frame.height));
      overview.width = Math.max(1, Math.round(frame.width * ratio));
      overview.height = Math.max(1, Math.round(frame.height * ratio));
      overview.parentElement.style.width = Math.min(overview.width, 210 * overview.width / overview.height) + 'px';
      overview.getContext('2d').drawImage(frame, 0, 0, overview.width, overview.height);
    }
    setCloseupPoint(point);
    closeupPanel.hidden = false;
    closeupPanel.dataset.serial = serialReady || serial.value.trim();
    sync();
    byId('closeup-title').focus({ preventScroll: true });
    closeupPanel.scrollIntoView({ block: 'nearest' });
    return true;
  }

  async function selectCloseup(event) {
    const input = event.target;
    const file = input.files?.[0];
    if (!file || !closeupPoint || pending || saved || savePayload) return;
    input.value = '';
    const version = ++closeupVersion;
    preparingCloseup = true;
    sync();
    showCloseupStatus('Подготавливаем снимок…');
    try {
      const jpeg = await window.CyberInspectionPhoto.toJpeg(file);
      if (version !== closeupVersion) return;
      closeupJpeg = jpeg;
      closeupMeasurement = null;
      byId('closeup-length').textContent = '';
      byId('closeup-measures').hidden = !window.CyberPhotoMeasure;
      closeupImage.src = 'data:image/jpeg;base64,' + jpeg;
      closeupLabel.required = true;
      byId('closeup-preview').hidden = false;
      byId('closeup-remove').hidden = false;
      showCloseupStatus();
    } catch (error) {
      if (version === closeupVersion) showCloseupStatus(error.message, true);
    } finally {
      if (version === closeupVersion) { preparingCloseup = false; sync(); }
    }
  }

  function setMessage(text = '', error = false) {
    message.textContent = text;
    message.dataset.error = String(error);
  }

  function syncMotorResource() {
    if (!motorPanel) return;
    const selectedSerial = serial.value.trim();
    const equipment = passports?.equipment.find(item => item.serial === selectedSerial);
    const direct = equipment?.kind === 'motor';
    const onPump = equipment?.kind === 'pump' && serialReady === selectedSerial && component.value === 'motor';
    const context = direct ? 'motor:' + selectedSerial : onPump ? 'pump:' + selectedSerial : '';
    if (context !== motorContext) {
      motorContext = context;
      motorSerial.value = '';
      motorHours.value = '';
    }
    motorPanel.hidden = !context;
    if (!context) return;
    const home = direct ? byId('motor-resource-home') : componentRow.parentElement;
    if (motorPanel.parentElement !== home) {
      if (direct) home.append(motorPanel); else componentRow.after(motorPanel);
    }
    const lookup = direct ? selectedSerial : motorSerial.value.trim();
    const data = motorThreshold(passports, lookup, motorHours.valueAsNumber);
    byId('motor-resource-title').textContent = direct || data ? `Электродвигатель · ${lookup}` : 'Ресурс электродвигателя';
    byId('motor-resource-confirm').hidden = direct;
    byId('motor-resource-values').hidden = !data;
    byId('motor-resource-missing').hidden = !lookup || Boolean(data);
    if (!data) return;
    byId('motor-resource-terms').textContent = data.terms;
    byId('motor-resource-hour-field').hidden = !data.acceptsHours;
    byId('motor-resource-remaining').textContent = data.remaining;
    const links = byId('motor-resource-sources');
    if (links.dataset.serial !== lookup) {
      links.replaceChildren();
      for (const [href, label] of data.sources) {
        const a = textNode('a', label);
        a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.className = 'text-link';
        if (links.childNodes.length) links.append(' · ');
        links.append(a);
      }
      links.dataset.serial = lookup;
    }
  }

  function sync() {
    fields.disabled = pending || cameraBusy || Boolean(savePayload) || Boolean(saved);
    const ready = Boolean(current && serialReady && serialReady === serial.value.trim());
    previewButton.disabled = !ready || pending || cameraBusy || Boolean(savePayload) || Boolean(saved) || preparingCloseup;
    saveButton.disabled = !ready || pending || cameraBusy || Boolean(saved) || preparingCloseup || (!preview && !savePayload);
    closeupOpen.hidden = !current || Boolean(current.regionNote);
    closeupOpen.disabled = fields.disabled;
    closeupOpen.textContent = closeupJpeg ? 'Открыть крупный план' : 'Добавить крупный план';
    closeupOpen.setAttribute('aria-expanded', String(!closeupPanel.hidden));
    byId('inspection-paused').hidden = !cameraBusy;
    const selected = telemetry.value;
    manual.hidden = selected !== 'manual';
    manual.querySelectorAll('input, select').forEach((input) => { input.disabled = manual.hidden; });
    byId('inspection-source').textContent = selected && selected !== 'manual' ?
      'Показания из примера, снятого отдельно от кадра.' : '';
    const historyLink = byId('inspection-history');
    historyLink.hidden = !ready;
    const query = new URLSearchParams({ serial: serialReady });
    if (saved) query.set('record', saved);
    historyLink.href = ready ? `/checkpoint.html?${query}` : '/checkpoint.html';
    historyLink.textContent = saved ? 'Открыть осмотр в журнале' : 'История осмотров';
    if (resource) {
      const data = ready && component.value !== 'motor' ? pumpThreshold(passports, serialReady, selected, byId('inspection-telemetry-hours')?.valueAsNumber) : null;
      resource.hidden = !data;
      byId('service-life-fallback').hidden = Boolean(data);
      if (data) {
        byId('inspection-resource-title').textContent = `Насосный агрегат · ${serialReady}`;
        byId('inspection-resource-terms').textContent = data.terms;
        byId('inspection-resource-limit').textContent = component.value === 'pump'
          ? 'Фактический остаточный ресурс неизвестен.'
          : 'Норматив относится ко всему агрегату. Фактический остаточный ресурс выбранного узла неизвестен.';
        const hours = byId('inspection-resource-hours');
        if (hours.textContent !== data.hours) hours.textContent = data.hours;
        hours.hidden = !data.hours;
        const links = byId('inspection-resource-sources');
        if (links.dataset.serial !== serialReady) {
          links.replaceChildren();
          for (const [href, label] of data.sources) {
            if (links.childNodes.length) links.append(' · ');
            const a = textNode('a', label);
            a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.className = 'text-link';
            links.append(a);
          }
          links.dataset.serial = serialReady;
        }
      }
    }
    syncMotorResource();
  }

  function invalidate() {
    revision += 1;
    controller?.abort();
    controller = null;
    pending = false;
    preview = null;
    saved = '';
    savePayload = null;
    byId('inspection-diagnosis').hidden = true;
    byId('inspection-findings').replaceChildren();
    navigator.replaceChildren();
    references.replaceChildren();
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
    if (closeupPoint && closeupPanel.dataset.serial && closeupPanel.dataset.serial !== requested) resetCloseup();
    const load = ++serialLoad;
    serialReady = '';
    telemetry.replaceChildren(new Option('Нет данных датчиков', ''), new Option('Ручные показания', 'manual'));
    component.replaceChildren(new Option('Насос', 'pump'));
    manual.querySelectorAll('input, select').forEach(input => { input.value = ''; });
    invalidate();
    if (!requested) return;
    setMessage('Проверяем серийный номер…');
    try {
      await loadPassports().catch(() => null);
      if (load !== serialLoad || requested !== serial.value.trim()) return;
      if (passports?.equipment.some(item => item.serial === requested && item.kind === 'motor')) {
        component.replaceChildren(new Option('Электродвигатель', 'motor'));
        setMessage('Для записи осмотра укажите номер насосного агрегата.');
        return;
      }
      const [data, referenceData] = await Promise.all([
        api(`/api/scenarios?${new URLSearchParams({ serial: requested })}`),
        api(`/api/recommendations?${new URLSearchParams({ serial: requested })}`).catch(() => null),
        loadCatalog().catch(() => null),
      ]);
      if (load !== serialLoad || requested !== serial.value.trim()) return;
      if (data.passport?.serial !== requested || !Array.isArray(data.scenarios)) throw new Error('Сервер не подтвердил серийный номер.');
      serialReady = requested;
      if (closeupPoint && !closeupPanel.dataset.serial) closeupPanel.dataset.serial = requested;
      telemetry.querySelector('option[value="manual"]').disabled = !manualFields.length;
      for (const item of data.scenarios) {
        if (typeof item.id === 'string' && typeof item.label === 'string') telemetry.append(new Option(`Пример · ${item.label}`, item.id));
      }
      if (referenceData?.availableComponents?.length) {
        component.replaceChildren(...referenceData.availableComponents.map((item) => new Option(item.label, item.key)));
      }
      setMessage(`Оборудование: ${data.passport.model || requested}`);
    } catch (error) {
      if (load === serialLoad) setMessage(error.message, true);
    } finally {
      if (load === serialLoad) sync();
    }
  }

  function loadPassports() {
    if (!passportsPromise) passportsPromise = api('/api/customer/equipment-passports').then(data => {
      if (!Array.isArray(data?.equipment)) throw new Error('Реестр паспортов недоступен.');
      passports = data;
    }).catch(error => { passportsPromise = null; throw error; });
    return passportsPromise;
  }

  function loadCatalog() {
    if (catalogPromise) return catalogPromise;
    catalogPromise = Promise.all([api('/api/customer/rules'), api('/api/equipment')]).then(([rules, equipment]) => {
      manualFields = rules.fields.telemetry;
      manual.replaceChildren();
      for (const field of manualFields) {
        const row = document.createElement('div');
        row.className = 'demo-options';
        const label = document.createElement('label');
        label.htmlFor = `inspection-telemetry-${field.key}`;
        label.textContent = `${field.label}${field.unit ? `, ${field.unit}` : ''}`;
        const input = document.createElement(field.type === 'number' ? 'input' : 'select');
        input.id = label.htmlFor;
        input.dataset.telemetryKey = field.key;
        if (field.type === 'number') {
          input.type = 'number'; input.min = '0'; input.step = 'any'; input.placeholder = 'Не измерено';
        } else {
          input.append(new Option('Не измерено', ''));
          const options = field.type === 'boolean' ? [{ value: 'true', label: 'Да' }, { value: 'false', label: 'Нет' }] : field.options;
          for (const option of options) input.append(new Option(option.label, option.value));
        }
        row.append(label, input);
        manual.append(row);
      }
      serialChoices.replaceChildren(...equipment.equipment.filter((item) => !item.kind || item.kind === 'pump' || item.kind === 'motor')
        .map((item) => new Option(`${item.serial} · ${item.model}`, item.serial)));
    }).catch((error) => { catalogPromise = null; throw error; });
    return catalogPromise;
  }

  function buildPayload() {
    if (!current || !serialReady || serialReady !== serial.value.trim()) throw new Error('Подтвердите серийный номер оборудования.');
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
      selectedComponent: component.value,
      telemetrySource: telemetry.value === 'manual' ? { kind: 'manual', values: Object.fromEntries(manualFields
        .filter((field) => byId(`inspection-telemetry-${field.key}`).value !== '')
        .map((field) => {
          const value = byId(`inspection-telemetry-${field.key}`).value;
          return [field.key, field.type === 'number' ? Number(value) : field.type === 'boolean' ? value === 'true' : value];
        })) } : telemetry.value ? { kind: 'demo_scenario', scenarioId: telemetry.value } : { kind: 'none' },
    };
  }

  function textNode(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }

  function renderGuidance(data) {
    navigator.replaceChildren(textNode('h3', 'Что проверить дальше'));
    if (!data || data.applicable === false) {
      navigator.append(textNode('p', data?.reason || 'Навигатор недоступен.'));
      return;
    }
    const statuses = { triggered: 'Условия выполнены', not_triggered: 'Условия не выполнены', insufficient_data: 'Не хватает данных' };
    navigator.append(textNode('p', `Проверено правил: ${data.summary.assessed}/${data.summary.total}. ` +
      `Сработало: ${data.summary.triggered}. Не хватает данных: ${data.summary.insufficientData}. Это не оценка исправности.`));
    const next = document.createElement('ol');
    for (const check of (data.nextChecks || []).slice(0, 5)) {
      const item = document.createElement('li');
      item.append(textNode('strong', check.label), textNode('p', check.instruction));
      const button = textNode('button', 'Внести результат проверки');
      button.type = 'button'; button.className = 'button secondary';
      button.addEventListener('click', () => {
        const [source, key] = check.field.split('.');
        if (source === 'telemetry') telemetry.value = 'manual';
        const target = source === 'telemetry' ? byId(`inspection-telemetry-${key}`) :
          key === 'gauge_pressure' ? gauge : byId(`inspection-${key}`);
        invalidate();
        const parentDetails = target?.closest('details');
        if (parentDetails) parentDetails.open = true;
        target?.focus({ preventScroll: true });
        target?.scrollIntoView({ block: 'center', behavior: 'instant' });
      });
      item.append(button); next.append(item);
    }
    navigator.append(next);
    const details = document.createElement('details');
    details.append(textNode('summary', 'Основания по всем 12 правилам'));
    for (const rule of data.rules || []) {
      const block = document.createElement('div');
      block.dataset.ruleId = rule.id;
      block.append(textNode('h4', `${rule.title} · ${statuses[rule.status] || rule.status}`));
      const checks = document.createElement('ul');
      for (const check of rule.checks || []) {
        checks.append(textNode('li', `${check.label}: ${{ met: 'выполнено', not_met: 'не выполнено', unknown: 'неизвестно' }[check.status] || check.status}`));
      }
      block.append(checks);
      if (rule.note) block.append(textNode('p', rule.note));
      details.append(block);
    }
    navigator.append(details);
  }

  function renderReferences(data) {
    references.replaceChildren(textNode('h3', 'Основание в документации изготовителя'));
    for (const item of data?.recommendations || []) {
      references.append(textNode('h4', item.title), textNode('p', item.appliesTo), textNode('p', item.text));
      for (const condition of item.conditions || []) references.append(textNode('p', condition));
      for (const source of item.sources || []) {
        if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(source.documentId) || !Number.isInteger(source.page) || source.page < 1) continue;
        const link = textNode('a', `${source.title} · стр. ${source.page}`);
        link.href = `/api/customer/documents/${source.documentId}/pdf#page=${source.page}`;
        link.target = '_blank'; link.rel = 'noopener';
        references.append(link, textNode('blockquote', source.quote));
      }
    }
    if (!data?.recommendations?.length) references.append(textNode('p', 'Проверенной паспортной рекомендации для выбранного узла нет.'));
    for (const limitation of data?.limitations || []) references.append(textNode('p', limitation));
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
    const captureLabel = { camera: 'Кадр камеры', uploaded_photo: 'Загруженное фото', uploaded_video: 'Кадр загруженного видео' }[data.provenance?.captureSource] || 'Кадр';
    byId('inspection-diagnosis-source').textContent = `${captureLabel} + подтверждение инженера. ` +
      ({ demo_scenario: 'Показания из примера.', manual: 'Показания введены оператором.', none: 'Телеметрии нет.' }[data.provenance?.telemetry] || '');
    renderGuidance(data.guidance);
    renderReferences(data.manufacturer);
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
    if (!savePayload) {
      if (closeupJpeg && !closeupLabel.value.trim()) {
        closeupLabel.focus(); showCloseupStatus('Укажите узел на снимке.', true); return;
      }
      savePayload = { ...preview, requestId: requestId() };
      if (closeupJpeg) {
        savePayload.closeup = { componentLabel: closeupLabel.value.trim(), point: closeupPoint, image: closeupJpeg };
        const type = byId('closeup-defect').value;
        if (type) savePayload.closeup.defectType = type;
        if (closeupMeasurement) savePayload.closeup.measurement = closeupMeasurement;
      }
    }
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
      saved = data.record.id;
      saveButton.textContent = 'Осмотр сохранён';
      setMessage(`Осмотр сохранён в журнале ${payload.serial}.`);
    } catch (error) {
      if (activeRevision !== revision || error.name === 'AbortError') return;
      saveButton.textContent = 'Повторить сохранение';
      setMessage(`Не удалось подтвердить сохранение. ${error.message} Повтор использует тот же номер запроса и не создаёт дубль.`, true);
    } finally {
      if (activeRevision === revision) { pending = false; controller = null; sync(); }
    }
  }

  window.CyberInspection = {
    openCloseup,
    update(result, busy) {
      cameraBusy = Boolean(busy);
      const valid = result?.source === 'local_vision' && ['defect', 'clear'].includes(result.status) &&
        typeof result.analysisId === 'string' && result.analysisId.length > 0 && result.analysisId.length <= 128;
      panel.hidden = !valid;
      if ((valid ? result.analysisId : null) !== (current?.analysisId || null)) {
        current = valid ? result : null;
        resetCloseup();
        invalidate();
        for (const select of confirms.querySelectorAll('select')) select.value = '';
        gauge.value = note.value = '';
        if (valid) {
          byId('inspection-frame').textContent = result.videoReview ? 'В журнале сохранятся сводка, таймкоды и опорный кадр видео.'
            : result.regionNote ? 'В журнале сохранится выбранный фрагмент.' : 'В журнале сохранится проверенный кадр.';
          if (result.regionNote) note.value = result.regionNote;
          if (!serialReady) loadSerial();
        }
      }
      sync();
    },
  };
  for (const id of ['closeup-file', 'closeup-camera']) byId(id).addEventListener('change', selectCloseup);
  byId('closeup-overview').addEventListener('click', event => {
    if (!closeupPoint || fields.disabled) return;
    const rect = event.target.getBoundingClientRect();
    if (rect.width && rect.height) setCloseupPoint({x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height});
  });
  byId('closeup-overview').addEventListener('keydown', event => {
    if (!closeupPoint || fields.disabled) return;
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!step) return;
    event.preventDefault();
    setCloseupPoint({x: closeupPoint.x + 0.01 * step[0], y: closeupPoint.y + 0.01 * step[1]});
  });
  byId('closeup-measure').addEventListener('click', async () => {
    if (!closeupJpeg || fields.disabled || preparingCloseup || !window.CyberPhotoMeasure) return;
    const version = closeupVersion;
    try {
      const value = await window.CyberPhotoMeasure.choose(closeupImage, closeupMeasurement);
      if (version !== closeupVersion || value === undefined) return;
      closeupMeasurement = value;
      const mm = value && window.CyberPhotoMeasure.lengthMm(value, closeupImage.naturalWidth, closeupImage.naturalHeight);
      byId('closeup-length').textContent = value && Number.isFinite(mm) ? '≈ ' + number.format(mm) + ' мм' : '';
      showCloseupStatus();
    } catch (error) { if (version === closeupVersion) showCloseupStatus(error.message, true); }
  });
  closeupOpen.addEventListener('click', () => {
    if (!current) return;
    const box = current.box;
    openCloseup(closeupPoint || {x: box.x + box.width / 2, y: box.y + box.height / 2}, closeupTarget || current.component, closeupAnchor || 'primary');
  });
  byId('closeup-remove').addEventListener('click', () => {
    resetCloseup();
    sync();
    closeupOpen.focus();
  });
  serial.addEventListener('input', () => {
    if (closeupPoint && closeupPanel.dataset.serial && serial.value.trim() !== closeupPanel.dataset.serial) resetCloseup();
    serialReady = ''; serialLoad += 1; invalidate();
    if (serial.value.trim() && !passports) loadPassports().then(syncMotorResource).catch(() => {});
  });
  for (const input of [serial, activeEquipment]) input?.addEventListener('focus', () => {
    loadCatalog().catch(() => setMessage('Список оборудования не загрузился. Введите серийный номер вручную.', true));
  });
  serial.addEventListener('change', loadSerial);
  motorHours?.addEventListener('input', syncMotorResource);
  motorSerial?.addEventListener('input', () => { motorHours.value = ''; syncMotorResource(); });
  if (motorPanel && serial.value.trim()) loadPassports().then(() => {
    if (passports?.equipment.some(item => item.kind === 'motor' && item.serial === serial.value.trim()) && !current) {
      component.replaceChildren(new Option('Электродвигатель', 'motor'));
    }
    syncMotorResource();
  }).catch(() => {});
  telemetry.addEventListener('change', invalidate);
  manual.addEventListener('input', invalidate);
  component.addEventListener('change', invalidate);
  confirms.addEventListener('change', invalidate);
  gauge.addEventListener('input', invalidate);
  note.addEventListener('input', invalidate);
  previewButton.addEventListener('click', () => runPreview().catch((error) => setMessage(error.message, true)));
  form.addEventListener('submit', (event) => saveInspection(event).catch((error) => setMessage(error.message, true)));
  window.addEventListener('pagehide', () => { controller?.abort(); });
})();
