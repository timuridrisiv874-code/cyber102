(() => {
if (window.CyberWebBoot?.blocked) return;
const byId = (id) => document.getElementById(id);
const serialInput = byId('serial');
const scenarioInput = byId('scenario');
const runButton = byId('run');
const message = byId('checkpoint-message');
const zoneLayer = byId('zones');
const zoneLabel = byId('zone-label');
const saveButton = byId('save-button');
const inspectionNote = byId('inspection-note');
const saveMessage = byId('save-message');
const historyPanel = byId('inspection-history');
const historyMessage = byId('history-message');
const historyRecords = byId('history-records');
const historyDay = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
const historyTime = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const workActions = {checked: 'Узел проверен', serviced: 'Узел обслужен', replaced: 'Узел заменён'};
const defectNames = {corrosion: 'Коррозия', liquid: 'Следы жидкости', crack: 'Трещина или разрыв',
  wiring: 'Повреждение проводки', coating: 'Повреждение покрытия', other: 'Другой признак'};
const millimetres = value => value.toLocaleString('ru-RU', {maximumSignificantDigits: 4}) + ' мм';
const lookupMessage = byId('lookup-message');
let serial = '';
let saveContext = null;
let historyLoad = 0;
let linkedRecord = new URLSearchParams(location.search).get('record');

const labels = {
  vibration: 'Вибрация', temperature: 'Температура', current: 'Ток двигателя',
  broadband_vibration: 'Широкополосная вибрация', high_frequency_vibration: 'Высокочастотная вибрация',
  inlet_pressure: 'Давление на всасывании', axial_vibration: 'Осевая вибрация', coupling_vibration: 'Вибрация муфты',
  low_frequency_vibration: 'Низкочастотная вибрация', one_x_dominant: 'Преобладает 1-я гармоника',
  efficiency_drop_pct: 'Снижение КПД', hours: 'Наработка', previous_hours: 'Предыдущая наработка',
  bearing_overheat_sustained: 'Устойчивый перегрев подшипника', bearing_temperature_delta_c: 'Температура выше базовой',
  phase_imbalance_pct: 'Дисбаланс токов', terminal_temperature_rising: 'Температура клемм растёт',
  discharge_pressure: 'Давление по SCADA', sound_sensor_present: 'Есть акустический датчик', gravel_noise: 'Шум «гравия»',
  seal_leak: 'Влага в зоне уплотнения', foundation_issue: 'Признаки ослабления крепления',
  oil_issue: 'Признаки ухудшения масла или низкого уровня', terminal_damage: 'Следы повреждения клемм',
  gauge_pressure: 'Показание аналогового манометра', guard_issue: 'Проблема с защитным кожухом',
  flow_unstable: 'Нестабильный поток',
};
const states = { rising: 'растёт', falling: 'падает', normal: 'в норме', high: 'высокая', slightly_high: 'слегка повышен' };
const units = { efficiency_drop_pct: '%', hours: 'моточ', previous_hours: 'моточ', bearing_temperature_delta_c: '°C',
  phase_imbalance_pct: '%', discharge_pressure: 'МПа', gauge_pressure: 'МПа' };
const zoneNames = { inlet: 'Всасывание', pump: 'Корпус насоса', seal: 'Уплотнение', bearing: 'Подшипник',
  oil: 'Контроль масла', coupling: 'Муфта', guard: 'Защитный кожух', motor: 'Двигатель', terminal: 'Клеммная коробка',
  anchors: 'Фундаментное крепление', gauge: 'Манометр' };
const severities = { critical: 'Критический', warning: 'Предупреждение', info: 'Обслуживание' };

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function term(label, value) {
  const group = element('div');
  group.append(element('dt', label), element('dd', value));
  return group;
}

async function api(path, parameters, options) {
  const query = parameters ? `?${new URLSearchParams(parameters)}` : '';
  const response = await fetch(`/api/${path}${query}`, { cache: 'no-store', ...options });
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Сервер недоступен. Обновите страницу.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
  return data;
}

function setMessage(text = '', error = false) {
  message.textContent = text;
  message.dataset.error = String(error);
}

function clearDiagnosis() {
  saveContext = null;
  inspectionNote.value = '';
  inspectionNote.readOnly = false;
  saveButton.disabled = false;
  saveButton.textContent = 'Сохранить пример';
  saveMessage.textContent = '';
  saveMessage.dataset.error = 'false';
  byId('diagnosis').hidden = true;
  zoneLayer.replaceChildren();
  zoneLabel.hidden = true;
}

async function loadPassport(requestedSerial) {
  const passport = byId('passport');
  const qr = byId('qr');
  const qrImage = byId('qr-image');
  const lookupButton = byId('lookup-button');
  const printQr = byId('print-qr');
  serial = '';
  historyLoad += 1;
  historyPanel.hidden = true;
  historyRecords.replaceChildren();
  passport.hidden = qr.hidden = true;
  qr.open = false;
  qrImage.removeAttribute('src');
  scenarioInput.replaceChildren();
  scenarioInput.disabled = runButton.disabled = lookupButton.disabled = true;
  clearDiagnosis();
  setMessage();
  lookupMessage.textContent = 'Открываем журнал…';
  lookupMessage.dataset.error = 'false';
  try {
    const data = await api('scenarios', { serial: requestedSerial });
    const equipment = data.passport;
    serial = equipment.serial;
    serialInput.value = serial;
    passport.replaceChildren(term('Модель', equipment.model), term('Серийный номер', serial),
      term('Срок службы', equipment.serviceLife), term('Следующее ТО', equipment.nextService));
    if (equipment.source === 'Демонстрационные данные') passport.append(term('Источник', 'Пример паспорта'));
    passport.hidden = false;
    const url = new URL(location.href);
    url.searchParams.set('serial', serial);
    history.replaceState(null, '', url);
    scenarioInput.replaceChildren(...data.scenarios.map(({ id, label }) => new Option(label, id)));
    scenarioInput.disabled = runButton.disabled = data.scenarios.length === 0;
    if (data.qrAvailable) {
      byId('qr-error').hidden = true;
      printQr.hidden = true;
      qrImage.hidden = false;
      qrImage.src = `/api/qr?${new URLSearchParams({ serial })}`;
      qr.hidden = false;
    }
    setMessage(data.scenarios.length ? '' : 'Для этого насоса пока нет сценариев.');
    lookupMessage.textContent = '';
    loadHistory(serial);
  } catch (error) {
    lookupMessage.textContent = error.message;
    lookupMessage.dataset.error = 'true';
  } finally {
    lookupButton.disabled = false;
  }
}

function severityBadge(level) {
  const badge = element('span', severities[level] ?? 'Результат', 'severity');
  badge.dataset.severity = level;
  return badge;
}

const releaseCheckNotes = new Set([
  'Автопроверка релиза 0.6: синтетическое фото, ручные демонстрационные значения.',
  'Проверка версии 0.5: синтетический тестовый кадр, демонстрационная телеметрия.',
  'Проверка развёртывания — chromium',
  'Проверка развёртывания — webkit',
]);

const frameDialog = byId('journal-frame');
const frameImage = byId('journal-frame-image');
const frameStatus = byId('journal-frame-status');
const frameTools = byId('journal-frame-tools');
const boxButton = byId('journal-frame-box');
let frameOpener;

function validMeasurement(m) {
  return m && [m.referenceMm, m.lengthMm].every(n => Number.isFinite(n) && n > 0) &&
    [m.reference, m.target].every(pair => Array.isArray(pair) && pair.length === 2 &&
      pair.every(p => p && [p.x, p.y].every(n => Number.isFinite(n) && n >= 0 && n <= 1)));
}

function addMeasureLines(measurement) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'journal-measure');
  svg.setAttribute('aria-hidden', 'true');
  for (const [kind, pair] of [['reference', measurement.reference], ['target', measurement.target]]) {
    const line = document.createElementNS(svg.namespaceURI, 'line');
    line.setAttribute('class', kind);
    for (let i = 0; i < pair.length; i++) {
      line.setAttribute(`x${i + 1}`, `${pair[i].x * 100}%`);
      line.setAttribute(`y${i + 1}`, `${pair[i].y * 100}%`);
    }
    svg.append(line);
    for (const p of pair) {
      const dot = document.createElementNS(svg.namespaceURI, 'circle');
      dot.setAttribute('class', kind);
      dot.setAttribute('cx', `${p.x * 100}%`);
      dot.setAttribute('cy', `${p.y * 100}%`);
      dot.setAttribute('r', '4');
      svg.append(dot);
    }
  }
  frameImage.append(svg);
}

function openJournalFrame(button, path, title, context, {box, point, measurement}) {
  const photo = new Image();
  photo.alt = title;
  const validBox = box && [box.x, box.y, box.width, box.height].every(Number.isFinite) &&
    box.x >= 0 && box.y >= 0 && box.width > 0 && box.height > 0 &&
    box.x + box.width <= 1.000001 && box.y + box.height <= 1.000001;
  frameImage.replaceChildren(photo);
  const measured = validMeasurement(measurement);
  if (measured) addMeasureLines(measurement);
  if (point && [point.x, point.y].every(n => Number.isFinite(n) && n >= 0 && n <= 1)) {
    const pin = element('span', undefined, 'journal-frame-point');
    pin.setAttribute('role', 'img');
    pin.setAttribute('aria-label', 'Позиция узла на общем кадре');
    Object.assign(pin.style, {left: `${point.x * 100}%`, top: `${point.y * 100}%`});
    frameImage.append(pin);
  }
  if (validBox) {
    const marker = element('span', undefined, 'journal-frame-marker');
    marker.setAttribute('aria-hidden', 'true');
    Object.assign(marker.style, {left: `${box.x * 100}%`, top: `${box.y * 100}%`,
      width: `${box.width * 100}%`, height: `${box.height * 100}%`});
    frameImage.append(marker);
  }
  frameImage.hidden = true;
  frameImage.classList.toggle('with-marker', Boolean(measured));
  boxButton.setAttribute('aria-pressed', String(Boolean(measured)));
  boxButton.textContent = measured ? 'Линии измерения' : 'Рамка узла';
  const legend = byId('journal-measure-legend');
  legend.hidden = !measured;
  if (measured) {
    byId('journal-reference-length').textContent = 'Эталон ' + millimetres(measurement.referenceMm);
    byId('journal-target-length').textContent = 'Дефект ≈ ' + millimetres(measurement.lengthMm);
  }
  frameTools.hidden = true;
  frameStatus.textContent = 'Загружаем кадр…';
  byId('journal-frame-title').textContent = title;
  byId('journal-frame-context').textContent = context;
  photo.onload = () => {
    if (photo.parentNode !== frameImage) return;
    frameImage.style.setProperty('--photo-width', `${photo.naturalWidth}px`);
    frameImage.style.setProperty('--photo-ratio', String(photo.naturalWidth / photo.naturalHeight));
    frameStatus.textContent = '';
    frameImage.hidden = false;
    frameTools.hidden = !(validBox || measured);
  };
  photo.onerror = () => {
    if (photo.parentNode === frameImage) frameStatus.textContent = 'Не удалось загрузить кадр.';
  };
  frameOpener = button;
  frameDialog.showModal();
  photo.src = path;
}

byId('journal-frame-close').addEventListener('click', () => frameDialog.close());
frameDialog.addEventListener('click', event => {
  if (event.target === frameDialog) {
    const bounds = frameDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom) frameDialog.close();
  }
});
frameDialog.addEventListener('close', () => {
  frameImage.replaceChildren();
  frameOpener?.focus({preventScroll: true});
  frameOpener = null;
});
boxButton.addEventListener('click', () => {
  boxButton.setAttribute('aria-pressed', String(frameImage.classList.toggle('with-marker')));
});

function frameButton(label, path, context, overlay = {}) {
  const button = element('button', label, 'history-evidence');
  button.type = 'button';
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-controls', 'journal-frame');
  button.addEventListener('click', () => openJournalFrame(button, path,
    label === 'Открыть кадр' ? 'Кадр осмотра' : label, context, overlay));
  return button;
}

const workDialog = byId('journal-work');
const workForm = byId('work-form');
const workStatus = byId('work-message');
const workSubmit = byId('work-submit');
const workDate = byId('work-date');
const workAction = byId('work-action');
const workPreview = byId('work-preview');
let workDraft = null;
let changingPhoto = 0;

function localDate(date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0')].join('-');
}

function openWork(record, context, opener) {
  workForm.reset();
  workDate.value = workDate.max = localDate(new Date());
  workStatus.textContent = '';
  workSubmit.disabled = byId('work-fields').disabled = byId('work-close').disabled = false;
  byId('work-context').textContent = context;
  workPreview.removeAttribute('src');
  workPreview.hidden = byId('work-remove-photo').hidden = true;
  workDraft = {record, opener, requestId: requestId(), image: '', saving: false};
  workDialog.showModal();
}

function clearWorkPhoto() {
  changingPhoto++;
  workDraft.image = '';
  workDraft.requestId = requestId();
  workPreview.hidden = byId('work-remove-photo').hidden = true;
  workPreview.removeAttribute('src');
  workStatus.textContent = '';
  workSubmit.disabled = false;
}

async function chooseWorkPhoto(input) {
  const file = input.files?.[0];
  if (!workDraft || workDraft.saving || !file) return;
  input.value = '';
  const draft = workDraft, token = ++changingPhoto;
  workSubmit.disabled = true;
  workStatus.textContent = 'Открываем фото…';
  workStatus.dataset.error = 'false';
  try {
    const image = await window.CyberInspectionPhoto.toJpeg(file);
    if (token !== changingPhoto || draft !== workDraft) return;
    draft.image = image;
    draft.requestId = requestId();
    workPreview.src = 'data:image/jpeg;base64,' + image;
    workPreview.hidden = byId('work-remove-photo').hidden = false;
    workStatus.textContent = '';
  } catch (error) {
    if (token !== changingPhoto || draft !== workDraft) return;
    workStatus.textContent = window.CyberInspectionPhoto ? error.message : 'Модуль фото не загрузился. Обновите страницу.';
    workStatus.dataset.error = 'true';
  } finally {
    if (token === changingPhoto && draft === workDraft) workSubmit.disabled = false;
  }
}

for (const id of ['work-photo', 'work-camera']) {
  byId(id).addEventListener('change', event => chooseWorkPhoto(event.target));
}
byId('work-remove-photo').addEventListener('click', clearWorkPhoto);
for (const control of [workDate, workAction]) {
  control.addEventListener('input', () => { if (workDraft) workDraft.requestId = requestId(); });
}
byId('work-close').addEventListener('click', () => workDialog.close());
workDialog.addEventListener('cancel', event => { if (workDraft?.saving) event.preventDefault(); });
workDialog.addEventListener('close', () => {
  changingPhoto++;
  workDraft?.opener?.focus({preventScroll: true});
  workDraft = null;
  workPreview.removeAttribute('src');
  workPreview.hidden = true;
});

workForm.addEventListener('submit', async event => {
  event.preventDefault();
  const draft = workDraft;
  if (!draft || draft.saving || workSubmit.disabled || !workForm.reportValidity()) return;
  draft.saving = true;
  workSubmit.disabled = byId('work-fields').disabled = byId('work-close').disabled = true;
  workStatus.textContent = 'Сохраняем работу…';
  workStatus.dataset.error = 'false';
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 45000);
  try {
    const {record} = await api(`history/${draft.record.id}/work`, null, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, signal: controller.signal,
      body: JSON.stringify({requestId: draft.requestId, action: workAction.value,
        date: workDate.value, ...(draft.image ? {image: draft.image} : {})})});
    if (record?.id !== draft.record.id || !Array.isArray(record.work) || !record.work.length) {
      throw new Error('Сервер не подтвердил сохранение работы.');
    }
    const previous = draft.opener.closest('.history-entry');
    if (previous?.isConnected) {
      const updated = historyEntry(record, new Date(record.capturedAt), scenarioLabels());
      previous.replaceWith(updated);
      draft.opener = updated.querySelector('.work-trigger');
    }
    workDialog.close();
  } catch (error) {
    const issue = error.name === 'AbortError' || error instanceof TypeError ?
      'Не удалось получить ответ сервера.' : error.message;
    workStatus.textContent = `${issue} Можно повторить сохранение.`;
    workStatus.dataset.error = 'true';
  } finally {
    clearTimeout(deadline);
    draft.saving = false;
    workSubmit.disabled = byId('work-fields').disabled = byId('work-close').disabled = false;
  }
});

function historyEntry(record, date, scenarios) {
  const entry = element('li', undefined, 'history-entry');
  entry.dataset.recordId = record.id;
  const captured = record.provenance?.source === 'camera_inspection';
  const scenario = scenarios.get(record.scenarioId) ?? record.scenarioId ?? 'Проверка правил';
  const evidence = record.evidence;
  const result = evidence?.result;
  const findings = Array.isArray(record.findings) ? record.findings : [];
  const source = { camera: 'Осмотр с камеры', uploaded_photo: 'Фото', uploaded_video: 'Видеокадр' }[record.provenance?.captureSource] || 'Осмотр с камеры';
  const title = captured ? result?.finding || findings[0]?.title || source : scenario;
  const heading = element('div', undefined, 'history-entry-heading');
  if (Number.isFinite(date.getTime())) {
    const time = element('time', historyTime.format(date));
    time.dateTime = record.capturedAt;
    heading.append(time);
  }
  heading.append(element('strong', title));
  entry.append(heading);
  if (!captured) {
    entry.append(element('p', 'Пример показаний'));
  } else {
    const details = element('details', undefined, 'data-note history-entry-details');
    details.append(element('summary', 'Данные осмотра'));
    if (record.provenance.telemetry === 'demo_scenario') entry.append(element('p', `Пример показаний · ${scenario}`));
    const videoTime = result?.media?.videoTimeSeconds;
    const moment = Number.isFinite(videoTime) ? `Время в видео — ${videoTime.toLocaleString('ru-RU')} с` : '';
    if (moment) details.append(element('p', moment));
    const measurements = record.provenance.telemetry === 'manual' ? record.telemetry : {};
    for (const [key, value] of Object.entries({...measurements, ...record.vision})) {
      if (value !== null && value !== undefined && value !== '') {
        details.append(element('p', `${labels[key] ?? key}: ${observationValue(key, value)}`));
      }
    }
    const closeup = uuid.test(record.id) && record.closeup;
    const nodeName = closeup?.componentLabel || result?.component || '';
    if (closeup?.componentLabel) entry.append(element('p', nodeName, 'inspected-node'));
    const engineerType = defectNames[closeup?.defectType];
    const measured = validMeasurement(closeup?.measurement);
    if (engineerType || measured) {
      const annotation = [engineerType, measured && '≈ ' + millimetres(closeup.measurement.lengthMm)].filter(Boolean);
      entry.append(element('p', 'Отметка инженера · ' + annotation.join(' · '), 'journal-annotation'));
    }
    const note = result?.observation?.trim();
    if (note) {
      const normalized = value => value.toLocaleLowerCase('ru').replace(/[.\s]+$/u, '').trim();
      const component = result.component;
      if (normalized(note) === normalized(title)) {
        if (component && component !== title && !closeup) entry.append(element('p', component));
      } else if (component && normalized(note) === normalized(`${component}: ${title}`)) {
        if (!closeup) entry.append(element('p', component));
      } else entry.append(element('p', note));
    }
    for (const recommendation of record.provenance.manufacturer?.recommendations || []) {
      details.append(element('p', `${recommendation.title}: ${recommendation.text} Применимость: ${recommendation.appliesTo}`));
      for (const source of recommendation.sources || []) {
        if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(source.documentId) || !Number.isInteger(source.page) || source.page < 1) continue;
        const link = element('a', `${source.title} · стр. ${source.page}`);
        link.href = `/api/customer/documents/${source.documentId}/pdf#page=${source.page}`;
        link.target = '_blank'; link.rel = 'noopener';
        details.append(link, element('blockquote', source.quote));
      }
    }
    if (details.childElementCount > 1) entry.append(details);
    if (uuid.test(evidence?.analysisId)) {
      const context = [record.serial, nodeName, Number.isFinite(date.getTime()) && `${historyDay.format(date)} · ${historyTime.format(date)}`, moment].filter(Boolean).join(' · ');
      const photos = element('div', undefined, 'journal-photos');
      photos.append(frameButton(closeup || record.work?.length ? 'Общий вид' : 'Открыть кадр',
        `/api/analyses/${evidence.analysisId}/image`, context, {box: result?.box, point: closeup?.point}));
      if (closeup) photos.append(frameButton('Крупный план', `/api/history/${record.id}/closeup/image`, context, {measurement: closeup.measurement}));
      entry.append(photos);
      if (uuid.test(record.id)) {
        for (const work of record.work || []) {
          if (!Object.hasOwn(workActions, work.action) || !/^\d{4}-\d{2}-\d{2}$/.test(work.date)) continue;
          const day = new Date(work.date + 'T12:00:00');
          if (!Number.isFinite(day.getTime())) continue;
          const event = element('div', undefined, 'journal-work-event');
          const time = element('time', historyDay.format(day));
          time.dateTime = work.date;
          event.append(element('strong', workActions[work.action]), time);
          if (uuid.test(work.id) && work.imageUrl) {
            const caption = [record.serial, nodeName, workActions[work.action], time.textContent].filter(Boolean).join(' · ');
            event.append(frameButton('Фото после', `/api/history/${record.id}/work/${work.id}/image`, caption));
          }
          entry.append(event);
        }
        const action = element('button', 'Зафиксировать работу', 'button secondary work-trigger');
        action.type = 'button';
        action.setAttribute('aria-haspopup', 'dialog');
        action.setAttribute('aria-controls', 'journal-work');
        action.addEventListener('click', () => openWork(record, context, action));
        entry.append(action);
      }
    }
  }
  if (record.note && !releaseCheckNotes.has(record.note.trim())) entry.append(element('p', record.note, 'history-note'));
  if (findings.length) {
    const list = element('ul');
    for (const finding of findings) {
      const row = element('li');
      row.append(severityBadge(finding.severity), document.createTextNode(finding.title));
      list.append(row);
    }
    entry.append(list);
  }
  return entry;
}

function scenarioLabels() {
  return new Map(Array.from(scenarioInput.options, ({value, text}) => [value, text]));
}

function renderHistory(records) {
  const scenarios = scenarioLabels();
  const groups = new Map();
  const fragment = document.createDocumentFragment();
  for (const record of records) {
    const date = new Date(record.capturedAt);
    const day = Number.isFinite(date.getTime()) ? historyDay.format(date) : 'Дата не указана';
    if (!groups.has(day)) {
      const group = element('li', undefined, 'history-day-group');
      const list = element('ol', undefined, 'history-day-records');
      group.append(element('h3', day, 'history-day-title'), list);
      groups.set(day, list);
      fragment.append(group);
    }
    groups.get(day).append(historyEntry(record, date, scenarios));
  }
  historyRecords.replaceChildren(fragment);
  if (uuid.test(linkedRecord)) {
    const entry = historyRecords.querySelector(`[data-record-id="${linkedRecord}"]`);
    if (entry) {
      entry.tabIndex = -1;
      entry.focus({preventScroll: true});
      entry.scrollIntoView({block: 'nearest'});
    }
    linkedRecord = null;
  }
}

async function loadHistory(requestedSerial) {
  const request = ++historyLoad;
  historyPanel.hidden = false;
  historyMessage.textContent = 'Загружаем историю…';
  historyMessage.dataset.error = 'false';
  try {
    const { records } = await api('history', { serial: requestedSerial });
    if (request !== historyLoad) return;
    renderHistory(records);
    historyMessage.textContent = records.length ? '' : 'Сохранённых осмотров пока нет.';
  } catch (error) {
    if (request !== historyLoad) return;
    historyMessage.textContent = `Не удалось загрузить историю. ${error.message}`;
    historyMessage.dataset.error = 'true';
  }
}

function requestId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

function observationValue(key, value) {
  if (value === true || value === false) return value ? 'Да' : 'Нет';
  if (typeof value === 'number') return `${value.toLocaleString('ru-RU')}${units[key] ? ' ' + units[key] : ''}`;
  return states[value] ?? value;
}

function showZones(finding, zones) {
  zoneLayer.replaceChildren();
  const names = [];
  for (const id of finding.zones) {
    const box = zones[id];
    if (!box) continue;
    const marker = element('div', undefined, 'zone');
    marker.dataset.severity = finding.severity;
    Object.assign(marker.style, { left: `${box.x * 100}%`, top: `${box.y * 100}%`,
      width: `${box.width * 100}%`, height: `${box.height * 100}%` });
    zoneLayer.append(marker);
    names.push(zoneNames[id] ?? id);
  }
  zoneLabel.hidden = names.length === 0;
  zoneLabel.textContent = names.join(' · ');
}

function renderDiagnosis(data) {
  const observations = { ...data.telemetry, ...data.vision };
  byId('observations').replaceChildren(...Object.entries(observations).map(([key, value]) =>
    term(labels[key] ?? key, observationValue(key, value))));
  byId('summary').textContent = data.findings.length ? `Сработало правил: ${data.findings.length}` :
    'По этим данным ни одно правило не сработало.';
  const cards = data.findings.map((finding) => {
    const card = element('details', undefined, 'finding-card');
    const title = element('summary');
    title.append(severityBadge(finding.severity), document.createTextNode(finding.title));
    const evidence = element('ul', undefined, 'evidence');
    evidence.append(...finding.evidence.map((item) => element('li', item)));
    card.append(title, element('p', finding.diagnosis), evidence, element('p', finding.recommendation));
    return card;
  });
  const results = byId('findings');
  results.replaceChildren(...cards);
  cards.forEach((card, index) => card.addEventListener('toggle', () => {
    if (byId('diagnosis').hidden || !results.contains(card)) return;
    if (card.open) {
      cards.filter((other) => other !== card).forEach((other) => { other.open = false; });
      showZones(data.findings[index], data.zones);
    } else if (cards.every((other) => !other.open)) {
      zoneLayer.replaceChildren();
      zoneLabel.hidden = true;
    }
  }));
  if (cards.length) cards[0].open = true;
  saveContext = { serial: data.passport.serial, scenarioId: data.scenario.id, requestId: requestId() };
  byId('diagnosis').hidden = false;
}

function drawSchematic() {
  const ctx = byId('pump-diagram').getContext('2d');
  ctx.fillStyle = '#112b23';
  ctx.fillRect(0, 0, 1000, 500);
  ctx.strokeStyle = '#24473a';
  ctx.lineWidth = 1;
  for (let x = 40; x < 1000; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 500); ctx.stroke();
  }
  for (let y = 40; y < 500; y += 40) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(1000, y); ctx.stroke();
  }
  const metal = (x, y, width, height, color = '#497766') => {
    ctx.fillStyle = color;
    ctx.strokeStyle = '#a3c1aa';
    ctx.lineWidth = 2;
    ctx.fillRect(x, y, width, height);
    ctx.strokeRect(x, y, width, height);
  };
  const ring = (x, y, radius, fill) => {
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = fill; ctx.fill(); ctx.stroke();
  };
  metal(150, 365, 760, 47, '#315846');
  metal(249, 311, 141, 54);
  metal(725, 312, 155, 53);
  ring(319, 240, 104, '#497766');
  ring(320, 244, 68, '#325a4a');
  ring(320, 244, 33, '#548775');
  metal(55, 218, 219, 51);
  metal(130, 208, 21, 71);
  metal(399, 225, 64, 47, '#315846');
  metal(450, 194, 113, 105);
  metal(467, 168, 20, 43);
  metal(549, 234, 156, 26);
  metal(553, 169, 137, 141, '#9a7048');
  metal(690, 161, 229, 161, '#386354');
  for (let y = 177; y < 320; y += 22) metal(711, y, 188, 6, '#5a8b77');
  metal(755, 100, 114, 63);
  metal(291, 99, 12, 40);
  ring(298, 87, 33, '#dbe9de');
  ctx.strokeStyle = '#203c2f';
  ctx.beginPath(); ctx.moveTo(298, 87); ctx.lineTo(312, 73); ctx.stroke();
  for (const x of [176, 401, 661, 882]) ring(x, 389, 8, '#a3c1aa');
  ctx.fillStyle = '#d7e9de';
  ctx.font = '600 25px sans-serif';
  ctx.fillText('Насос', 276, 456);
  ctx.fillText('Муфта', 574, 456);
  ctx.fillText('Двигатель', 747, 456);
}

byId('lookup').addEventListener('submit', (event) => {
  event.preventDefault();
  const requested = serialInput.value.trim();
  if (!requested) {
    lookupMessage.textContent = 'Введите серийный номер.';
    lookupMessage.dataset.error = 'true';
    return;
  }
  loadPassport(requested);
});
byId('analysis-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const lookupButton = byId('lookup-button');
  lookupButton.disabled = scenarioInput.disabled = runButton.disabled = true;
  clearDiagnosis();
  setMessage('Проверяем показания…');
  try {
    const data = await api('diagnose', { serial, scenario: scenarioInput.value });
    renderDiagnosis(data);
    setMessage('Проверка завершена.');
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    lookupButton.disabled = scenarioInput.disabled = runButton.disabled = false;
  }
});
scenarioInput.addEventListener('change', () => { clearDiagnosis(); setMessage(); });
byId('save-inspection').addEventListener('submit', async (event) => {
  event.preventDefault();
  const context = saveContext;
  if (!context || saveButton.disabled) return;
  if (context.note === undefined) context.note = inspectionNote.value;
  inspectionNote.readOnly = saveButton.disabled = true;
  saveButton.textContent = 'Сохраняем…';
  saveMessage.textContent = '';
  saveMessage.dataset.error = 'false';
  try {
    await api('history', null, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(context),
    });
    if (context.serial === serial) loadHistory(serial);
    if (saveContext !== context) return;
    saveButton.textContent = 'Пример сохранён';
    saveMessage.textContent = 'Запись добавлена в историю.';
  } catch (error) {
    if (saveContext !== context) return;
    saveButton.disabled = false;
    saveButton.textContent = 'Повторить сохранение';
    saveMessage.textContent = `Не удалось подтвердить сохранение. ${error.message}`;
    saveMessage.dataset.error = 'true';
  }
});
byId('qr-image').addEventListener('load', () => { byId('print-qr').hidden = false; });
byId('qr-image').addEventListener('error', () => {
  byId('qr-image').hidden = true;
  byId('qr-error').hidden = false;
  byId('print-qr').hidden = true;
});
byId('print-qr').addEventListener('click', () => window.print());
drawSchematic();
serialInput.value = new URLSearchParams(location.search).get('serial') || serialInput.value;
loadPassport(serialInput.value);
})();
