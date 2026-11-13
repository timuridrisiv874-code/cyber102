const byId = (id) => document.getElementById(id);
const serialInput = byId('serial');
const scenarioInput = byId('scenario');
const runButton = byId('run');
const message = byId('checkpoint-message');
const zoneLayer = byId('zones');
const zoneLabel = byId('zone-label');
let serial = '';

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

async function api(path, parameters) {
  const query = new URLSearchParams(parameters);
  const response = await fetch(`/api/${path}?${query}`, { cache: 'no-store' });
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Для диагностики запустите локальный сервер Cyber102. Команда указана в README.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Локальный сервер вернул ошибку.');
  return data;
}

function setMessage(text = '', error = false) {
  message.textContent = text;
  message.dataset.error = String(error);
}

function clearDiagnosis() {
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
  passport.hidden = qr.hidden = true;
  qr.open = false;
  qrImage.removeAttribute('src');
  scenarioInput.replaceChildren();
  scenarioInput.disabled = runButton.disabled = lookupButton.disabled = true;
  clearDiagnosis();
  setMessage('Загружаем паспорт…');
  try {
    const data = await api('scenarios', { serial: requestedSerial });
    const equipment = data.passport;
    serial = equipment.serial;
    serialInput.value = serial;
    passport.replaceChildren(term('Модель', equipment.model), term('Серийный номер', serial),
      term('Срок службы', equipment.serviceLife), term('Следующее ТО', equipment.nextService));
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
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    lookupButton.disabled = false;
  }
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
    const severity = element('span', severities[finding.severity], 'severity');
    severity.dataset.severity = finding.severity;
    title.append(severity, document.createTextNode(finding.title));
    const evidence = element('ul', undefined, 'evidence');
    evidence.append(...finding.evidence.map((item) => element('li', item)));
    card.append(title, element('p', finding.diagnosis), evidence, element('p', finding.recommendation));
    return card;
  });
  const results = byId('findings');
  results.replaceChildren(...cards);
  cards.forEach((card, index) => card.addEventListener('toggle', () => {
    if (card.open) {
      cards.filter((other) => other !== card).forEach((other) => { other.open = false; });
      showZones(data.findings[index], data.zones);
    } else if (cards.every((other) => !other.open)) {
      zoneLayer.replaceChildren();
      zoneLabel.hidden = true;
    }
  }));
  if (cards.length) cards[0].open = true;
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
    setMessage('Введите серийный номер.', true);
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
