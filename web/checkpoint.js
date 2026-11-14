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
const lookupMessage = byId('lookup-message');
let serial = '';
let saveContext = null;
let historyLoad = 0;

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

function historyEntry(record, date, scenarios) {
  const entry = element('li', undefined, 'history-entry');
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
    if (title !== source) details.append(element('p', source));
    if (record.provenance.telemetry === 'demo_scenario') entry.append(element('p', `Пример показаний · ${scenario}`));
    else details.append(element('p', record.provenance.telemetry === 'manual' ? 'Показания введены вручную.' : 'Показаний датчиков нет.'));
    if (typeof result?.model === 'string') details.append(element('p', `${result.model}. Оборудование выбрано инженером.`));
    if (Number.isFinite(result?.media?.videoTimeSeconds)) details.append(element('p', `Время в видео — ${result.media.videoTimeSeconds} с.`));
    if (typeof result?.observation === 'string' && result.observation) entry.append(element('p', result.observation));
    const confirmed = Object.entries(record.vision ?? {}).filter(([, value]) => value !== null && value !== undefined);
    details.append(element('p', confirmed.length ? confirmed.map(([key, value]) => `${labels[key] ?? key}: ${observationValue(key, value)}`).join(' · ') : 'Признаки инженером не оценены.'));
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
    if (!findings.length) details.append(element('p', 'Правила не сработали. Неоценённые признаки остаются неизвестными.'));
    entry.append(details);
    if (typeof evidence?.analysisId === 'string' && /^[0-9a-f-]{36}$/.test(evidence.analysisId)) {
      const link = element('a', 'Открыть кадр', 'history-evidence');
      link.href = `/api/analyses/${evidence.analysisId}/image`;
      link.target = '_blank'; link.rel = 'noopener';
      entry.append(link);
    }
  }
  if (record.note) entry.append(element('p', record.note, 'history-note'));
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

function renderHistory(records) {
  const scenarios = new Map(Array.from(scenarioInput.options, ({ value, text }) => [value, text]));
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
