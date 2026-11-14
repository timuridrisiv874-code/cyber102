(() => {
if (window.CyberWebBoot?.blocked) return;
const byId = id => document.getElementById(id);
const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 });
const time = value => value ? value.slice(0, 16).replace('T', ' ') : '—';
const svgNamespace = 'http://www.w3.org/2000/svg';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function message(id, text = '', error = false) {
  const node = byId(id);
  node.textContent = text;
  node.dataset.error = String(error);
}

async function api(path, options, onProgress) {
  const request = path === '/analyze' && window.CyberAnalysisJobs ? window.CyberAnalysisJobs.request : fetch;
  const response = await request(path, { cache: 'no-store', ...options }, onProgress);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(data?.error || `Ошибка загрузки (${response.status}).`);
  return data;
}

const photoGrid = byId('photo-grid');
const categorySelect = byId('category');
const scopeSelect = byId('inspection-scope');
const selectedImage = byId('selected-image');
const analyzePhoto = byId('analyze-photo');
const photoBox = byId('photo-box');
const photoResult = byId('photo-result');
const photoBatchSize = 12;
let photos = [];
let categories = [];
const scopeTitles = new Map();
let selectedPhoto = null;
let photoLimit = photoBatchSize;
let photoAnalysis = null;

function resetPhoto() {
  photoAnalysis?.abort();
  photoAnalysis = null;
  selectedPhoto = null;
  analyzePhoto.disabled = true;
  byId('photo-cancel').hidden = true;
  selectedImage.removeAttribute('src');
  byId('photo-detail').hidden = true;
  photoResult.hidden = photoBox.hidden = true;
  message('photo-status');
}

function selectPhoto(photo) {
  resetPhoto();
  selectedPhoto = photo;
  byId('photo-title').textContent = photo.filename;
  byId('photo-source').textContent = `${photo.categoryTitle} · Серийный номер не подтверждён`;
  const scope = photo.inspectionScope;
  const scopeTitle = scopeTitles.get(scope?.id) ?? 'Контекст не установлен';
  byId('photo-scope').textContent = [scopeTitle, scope?.note].filter(Boolean).join('. ');
  const aliases = byId('photo-aliases');
  aliases.hidden = !photo.aliases?.length;
  aliases.textContent = aliases.hidden ? '' : `Другие имена: ${photo.aliases.join(', ')}`;
  selectedImage.alt = `${photo.categoryTitle} — ${photo.filename}`;
  selectedImage.src = photo.url;
  byId('photo-detail').hidden = false;
  for (const button of photoGrid.querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.photo === photo.id));
  }
  byId('photo-title').focus();
}

function renderPhotos() {
  const category = categorySelect.value;
  const filtered = photosInScope().filter(photo => !category || photo.category === category);
  byId('photo-count').textContent = filtered.length ? `Фото по фильтрам: ${filtered.length}` : 'По этим фильтрам фотографий нет.';
  photoGrid.replaceChildren(...filtered.slice(0, photoLimit).map(photo => {
    const button = element('button', undefined, 'photo-option');
    button.type = 'button';
    button.dataset.photo = photo.id;
    button.setAttribute('aria-pressed', String(selectedPhoto?.id === photo.id));
    const image = element('img');
    image.src = photo.url;
    image.alt = '';
    image.loading = 'lazy';
    button.append(image, element('span', photo.filename));
    button.addEventListener('click', () => selectPhoto(photo));
    return button;
  }));
  byId('more-photos').hidden = filtered.length <= photoLimit;
}

function photosInScope() {
  const scope = scopeSelect.value;
  return photos.filter(photo => !scope || (photo.inspectionScope?.id ?? 'unreviewed') === scope);
}

function updateCategories() {
  const selectedCategory = categorySelect.value;
  const scoped = photosInScope();
  categorySelect.replaceChildren(new Option(`Все категории (${scoped.length})`, ''), ...categories.map(({ id, title }) => {
    const count = scoped.filter(photo => photo.category === id).length;
    return new Option(`${title} (${count})`, id);
  }));
  if (categories.some(({ id }) => id === selectedCategory)) categorySelect.value = selectedCategory;
}

function renderDocuments(documents) {
  byId('source-documents').hidden = !documents.length;
  byId('document-list').replaceChildren(...documents.map(document => {
    const details = element('details', undefined, 'material-details');
    details.append(element('summary', document.title));
    for (const section of document.sections) {
      details.append(element('h4', section.heading));
      details.append(...section.paragraphs.map(text => element('p', text, 'material-note')));
    }
    return details;
  }));
}

async function loadCatalog() {
  try {
    const data = await api('/api/customer/materials');
    photos = data.images.filter(photo => /^\/api\/customer\/images\/[\w-]+$/.test(photo.url));
    categories = data.summary.categories;
    const scopes = data.summary.scopes;
    for (const { id, title } of scopes) scopeTitles.set(id, title);
    scopeSelect.replaceChildren(...scopes.map(({ id, title, count }) => new Option(`${title} (${count})`, id)),
      new Option(`Все условия (${photos.length})`, ''));
    scopeSelect.value = 'external';
    updateCategories();
    byId('catalog').hidden = !photos.length;
    renderPhotos();
    renderDocuments(data.documents);
    const counts = `Уникальных фото: ${photos.length}. Файлов в подборке: ${data.summary.files}.`;
    message('catalog-status');
    byId('catalog-provenance').textContent = [counts, data.notice].filter(Boolean).join(' ');
  } catch (error) {
    message('catalog-status', `Не удалось загрузить каталог. ${error.message}`, true);
  }
}

function showPhotoResult(data) {
  if (!['clear', 'defect', 'unusable', 'no_marker'].includes(data.status)) throw new Error('Неизвестный ответ анализа.');
  if (data.status === 'unusable' || data.status === 'no_marker') {
    message('photo-status', 'Не удалось оценить узел. Выберите более чёткое фото.');
    return;
  }
  const badge = element('span', data.finding, 'finding');
  badge.dataset.status = data.status;
  photoResult.replaceChildren(
    badge,
    element('h3', data.component),
    element('p', data.observation || '', 'material-note'),
    element('p', data.recommendation, 'material-note'),
  );
  photoResult.hidden = false;
  const { x, y, width, height } = data.box ?? {};
  if ([x, y, width, height].every(Number.isFinite) && x >= 0 && y >= 0 && width > 0 && height > 0
      && x + width <= 1.000001 && y + height <= 1.000001) {
    Object.assign(photoBox.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` });
    photoBox.dataset.status = data.status;
    photoBox.hidden = false;
  }
  message('photo-status');
  const details = element('details', undefined, 'data-note');
  details.append(element('summary', 'О результате'), element('p', 'Наблюдение требует подтверждения инженером.', 'material-note'));
  photoResult.append(details);
}

function changePhotoFilter() {
  resetPhoto();
  photoLimit = photoBatchSize;
  renderPhotos();
}
categorySelect.addEventListener('change', changePhotoFilter);
scopeSelect.addEventListener('change', () => {
  updateCategories();
  changePhotoFilter();
});
byId('more-photos').addEventListener('click', () => {
  photoLimit += photoBatchSize;
  renderPhotos();
  photoGrid.children[photoLimit - photoBatchSize]?.focus();
});
selectedImage.addEventListener('load', () => { analyzePhoto.disabled = !selectedPhoto || Boolean(photoAnalysis); });
selectedImage.addEventListener('error', () => {
  if (!selectedPhoto) return;
  analyzePhoto.disabled = true;
  message('photo-status', 'Не удалось открыть фотографию.', true);
});

byId('photo-cancel').addEventListener('click', () => {
  const current = photoAnalysis; photoAnalysis = null; current?.abort();
  byId('photo-cancel').hidden = true; analyzePhoto.disabled = !selectedPhoto;
  message('photo-status', 'Анализ отменён.');
});

analyzePhoto.addEventListener('click', async () => {
  if (!selectedPhoto || photoAnalysis) return;
  const controller = new AbortController();
  photoAnalysis = controller;
  const timeout = setTimeout(() => controller.abort(), 13 * 60 * 1000);
  analyzePhoto.disabled = true;
  byId('photo-cancel').hidden = false;
  photoResult.hidden = photoBox.hidden = true;
  message('photo-status', 'Проверяем фото…');
  try {
    const response = await fetch(selectedPhoto.url, { signal: controller.signal });
    if (!response.ok) throw new Error('Не удалось загрузить изображение для анализа.');
    const body = new FormData();
    body.append('file', await response.blob(), 'frame.jpg');
    const result = await api('/analyze', { method: 'POST', body, signal: controller.signal }, value => {
      if (photoAnalysis === controller && !controller.signal.aborted) message('photo-status', value);
    });
    if (photoAnalysis === controller) showPhotoResult(result);
  } catch (error) {
    if (photoAnalysis !== controller) return;
    message('photo-status', error.name === 'AbortError' ? 'Проверка заняла слишком много времени. Попробуйте ещё раз.' : error.message, true);
  } finally {
    clearTimeout(timeout);
    if (photoAnalysis === controller) {
      photoAnalysis = null;
      analyzePhoto.disabled = false;
      byId('photo-cancel').hidden = true;
    }
  }
});

const sensorGroups = {
  vibration: {
    title: 'Вибрация',
    channels: [
      ['rms_overall', 'СКЗ общее', 'rms_mm_s'],
      ['one_x', 'Амплитуда 1×', 'one_x_mm_s'],
      ['two_x', 'Амплитуда 2×', 'two_x_mm_s'],
      ['high_frequency', 'ВЧ шум', 'high_frequency_g'],
    ],
  },
  temperature: {
    title: 'Температура',
    channels: [
      ['bearing', 'Подшипник насоса', 'bearing_c'],
      ['winding', 'Обмотки электродвигателя', 'winding_c'],
    ],
  },
};
const unitName = unit => unit === 'mm/s' ? 'мм/с' : unit;
const assessmentLabels = {
  below_threshold: 'Без превышения', warning: 'Предупреждение', alarm: 'Тревога', unknown: 'Неизвестно',
};

function svg(tag, attributes, text) {
  const node = document.createElementNS(svgNamespace, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function sensorChart(rows, group, field, label, unit) {
  const chart = svg('svg', { viewBox: '0 0 420 142', class: 'sensor-chart', role: 'img', 'aria-label': `${label}, ${unit}` });
  const values = rows.map(row => row[group][field]);
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return element('p', 'В этом канале нет значений.', 'material-note');
  const minimum = Math.min(...finite);
  const maximum = Math.max(...finite);
  const scale = maximum - minimum || 1;
  const x = index => 50 + 356 * index / Math.max(rows.length - 1, 1);
  const y = value => maximum === minimum ? 62 : 112 - 100 * (value - minimum) / scale;
  for (const value of new Set([minimum, maximum])) {
    chart.append(svg('line', { x1: 50, x2: 406, y1: y(value), y2: y(value), class: 'grid-line' }));
    chart.append(svg('text', { x: 46, y: y(value) + 4, 'text-anchor': 'end' }, number.format(value)));
  }
  const path = values.map((value, index) => Number.isFinite(value) ?
    `${index && Number.isFinite(values[index - 1]) ? 'L' : 'M'}${x(index).toFixed(1)},${y(value).toFixed(1)}` : '').join(' ');
  chart.append(svg('path', { d: path, class: 'trend-line' }));
  if (finite.length === 1) {
    const index = values.findIndex(Number.isFinite);
    chart.append(svg('circle', { cx: x(index), cy: y(values[index]), r: 2.5, class: 'trend-point' }));
  }
  chart.append(svg('text', { x: 50, y: 134 }, time(rows[0].timestamp).slice(0, 10)));
  chart.append(svg('text', { x: 406, y: 134, 'text-anchor': 'end' }, time(rows.at(-1).timestamp).slice(0, 10)));
  return chart;
}

function thresholdText(threshold) {
  const unit = unitName(threshold.unit);
  const limit = value => `${threshold.operator ?? ''} ${number.format(value)} ${unit}`;
  const parts = [Number.isFinite(threshold.warning) ? `предупреждение ${limit(threshold.warning)}` : 'порог предупреждения не указан',
    Number.isFinite(threshold.alarm) ? `тревога ${limit(threshold.alarm)}` : 'порог тревоги не указан'];
  if (Number.isFinite(threshold.approximateBaseline)) parts.push(`ориентир в описании ≈ ${number.format(threshold.approximateBaseline)} ${unit}`);
  return parts.join(' · ');
}

function readingsTable(rows, group, channel, field, unit) {
  const wrapper = element('div', undefined, 'sensor-table-wrap');
  const table = element('table', undefined, 'sensor-table');
  table.append(element('caption', 'Последние 20 записей'));
  const headings = element('tr');
  for (const name of ['Дата и время', unit, 'Оценка канала', 'Метка XLSX', 'Строка XLSX']) {
    const th = element('th', name);
    th.scope = 'col';
    headings.append(th);
  }
  const head = element('thead');
  head.append(headings);
  const body = element('tbody');
  for (const row of rows.slice(-20).reverse()) {
    const tr = element('tr');
    const level = row.assessments[group][channel]?.level;
    tr.dataset.level = level;
    const value = row[group][field];
    for (const cell of [time(row.timestamp), Number.isFinite(value) ? number.format(value) : '—',
      assessmentLabels[level] ?? 'Неизвестно', row[group].source_label || '—', row.provenance[group].row]) {
      tr.append(element('td', String(cell ?? '—')));
    }
    body.append(tr);
  }
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function measurementCard(data, group, { title, channels }) {
  const card = element('section', undefined, 'measurement-card');
  const source = data.sources.find(item => item.id === `${group}_data`);
  const thresholdList = element('ul', undefined, 'sensor-thresholds material-note');
  const chartContent = element('div');
  const tableDetails = element('details', undefined, 'material-details');
  const tableContent = element('div');
  tableDetails.append(element('summary', 'Показать 20 строк'), tableContent);
  const pickerLabel = element('label', 'Канал', 'field');
  const picker = element('select');
  pickerLabel.append(picker);
  picker.replaceChildren(...channels.map(([id, label]) => new Option(label, id)));
  for (const [id, label] of channels) {
    const threshold = data.thresholds[group][id];
    thresholdList.append(element('li', `${label} — ${thresholdText(threshold)}`));
  }
  const doc = data.sources.find(item => item.id === `${group}_description`);
  const discrepancies = data.summary[group].labelDisagreements;
  const showChannel = () => {
    const [channel, label, field] = channels.find(([id]) => id === picker.value);
    const unit = unitName(data.thresholds[group][channel].unit);
    const range = data.summary[group].ranges[field];
    chartContent.replaceChildren(
      element('p', `Значений: ${range.count}. Пропусков: ${range.missing}.`, 'material-note'),
      sensorChart(data.rows, group, field, label, unit),
    );
    tableContent.replaceChildren(readingsTable(data.rows, group, channel, field, unit));
  };
  picker.addEventListener('change', showChannel);
  const sourceDetails = element('details', undefined, 'data-note');
  sourceDetails.append(element('summary', 'Источник и пороги'),
    element('p', source ? `${source.file} · лист «${source.sheet}»` : 'Источник ряда не указан.', 'material-note'), thresholdList,
    element('p', doc ? `Пороги из «${doc.file}».` : 'Источник порогов не указан.', 'material-note'));
  card.append(element('h3', title), pickerLabel, chartContent);
  if (discrepancies) card.append(element('p', `Расхождения исходных меток и оценки: ${discrepancies}`, 'material-note'));
  card.append(sourceDetails, tableDetails);
  showChannel();
  return card;
}

async function loadSensors() {
  try {
    const data = await api('/api/customer/sensors');
    const { dataset, summary } = data;
    byId('sensor-note').textContent = [dataset.relationshipNote,
      'Часовой пояс не указан. Ток, давление, КПД и наработка отсутствуют.'].filter(Boolean).join(' ');
    message('sensor-status', `${summary.rowCount} записей · шаг ${dataset.intervalHours} ч · без привязки к оборудованию`);
    byId('sensor-provenance').textContent = `${time(summary.firstTimestamp)} — ${time(summary.lastTimestamp)}. ${summary.futureDatedRows} записей позднее ${summary.asOfLocalDate}.`;
    byId('measurement-grid').replaceChildren(...Object.entries(sensorGroups).map(([id, group]) => measurementCard(data, id, group)));
  } catch (error) {
    message('sensor-status', `Не удалось загрузить измерения. ${error.message}`, true);
  }
}

const groupTitles = { telemetry: 'Подтверждённые показания', vision: 'Подтверждённые наблюдения' };
const thresholdNames = {
  efficiency_drop_pct: 'Порог снижения КПД',
  bearing_temperature_delta_c: 'Порог перегрева над базой',
  phase_imbalance_pct: 'Порог дисбаланса фаз',
  pressure_mismatch_pct: 'Порог расхождения давления',
  service_intervals_hours: 'Интервалы ТО',
};
const thresholdUnits = {
  efficiency_drop_pct: '%', bearing_temperature_delta_c: '°C', phase_imbalance_pct: '%',
  pressure_mismatch_pct: '%', service_intervals_hours: 'ч',
};
const severities = { critical: 'Критический', warning: 'Предупреждение', info: 'Обслуживание' };
const manualForm = byId('manual-form');
const findingsPanel = byId('manual-findings');
const runRules = byId('run-rules');
let rulesMetadata = null;
let fieldControls = new Map();
let diagnosis = null;

function makeField(group, field) {
  const label = element('label', field.unit ? `${field.label} (${field.unit})` : field.label, 'field');
  const control = element(field.type === 'number' ? 'input' : 'select');
  control.id = `${group}-${field.key}`;
  if (field.type === 'number') {
    control.type = 'number';
    control.step = 'any';
    control.placeholder = 'Неизвестно';
  } else {
    const options = field.type === 'boolean' ? [{ value: 'true', label: 'Да' }, { value: 'false', label: 'Нет' }] : field.options;
    control.replaceChildren(new Option('Неизвестно', ''), ...options.map(({ value, label }) => new Option(label, value)));
  }
  fieldControls.set(`${group}.${field.key}`, { group, field, control });
  label.append(control);
  return label;
}

function readInputs() {
  const input = { telemetry: {}, vision: {} };
  for (const { group, field, control } of fieldControls.values()) {
    if (!control.value) continue;
    const value = field.type === 'number' ? control.valueAsNumber :
      field.type === 'boolean' ? control.value === 'true' : control.value;
    if (field.type !== 'number' || Number.isFinite(value)) input[group][field.key] = value;
  }
  return input;
}

function hasInput(key, values) {
  const [group, field] = key.split('.');
  return Object.prototype.hasOwnProperty.call(values[group] ?? {}, field);
}

function thresholdLabel(key, value) {
  if (!Array.isArray(value) && !Number.isFinite(value)) return thresholdNames[key] ?? key;
  const values = Array.isArray(value) ? value.map(item => number.format(item)).join(', ') : number.format(value);
  return `${thresholdNames[key] ?? key} профиля (${values} ${thresholdUnits[key] ?? ''})`;
}

function coverageCards(inputs) {
  const values = { ...inputs, thresholds: rulesMetadata.thresholds };
  const cards = byId('coverage-list');
  if (!cards.children.length) {
    cards.replaceChildren(...rulesMetadata.rules.map(rule => {
      const details = element('details', undefined, 'material-details coverage-card');
      const summary = element('summary', rule.title);
      summary.append(element('span', undefined, 'coverage-state'));
      details.append(summary, element('p', rule.note, 'material-note'), element('ul', undefined, 'material-note'));
      return details;
    }));
  }
  let ready = 0;
  rulesMetadata.rules.forEach((rule, index) => {
    const card = cards.children[index];
    const soundAvailable = inputs.telemetry.sound_sensor_present;
    const applicable = rule.id === 'cavitation' && typeof soundAvailable === 'boolean'
      ? rule.requirements.filter(requirements => requirements.includes('telemetry.gravel_noise') === soundAvailable)
      : rule.requirements;
    const combinations = applicable.map(fields => ({ fields, missing: fields.filter(key => !hasInput(key, values)) }));
    const covered = combinations.some(({ missing }) => missing.length === 0);
    if (covered) ready += 1;
    card.dataset.covered = String(covered);
    card.querySelector('.coverage-state').textContent = covered ? 'Входы заполнены' : 'Нужны данные';
    card.querySelector('ul').replaceChildren(...combinations.map(({ fields, missing }) => {
      const names = fields.map(key => {
        if (key.startsWith('thresholds.')) {
          const field = key.split('.')[1];
          return thresholdLabel(field, values.thresholds[field]);
        }
        return fieldControls.get(key)?.field.label ?? key;
      });
      const missingNames = missing.map(key => fieldControls.get(key)?.field.label ?? thresholdNames[key.split('.')[1]] ?? key);
      const summary = missing.length ? `Не указаны: ${missingNames.join(', ')}.` : 'Все поля указаны.';
      return element('li', `${names.join(' + ')}. ${summary}`);
    }));
  });
  byId('coverage-title').textContent = `Полнота данных (${ready} из ${rulesMetadata.rules.length})`;
}

async function loadRules() {
  try {
    rulesMetadata = await api('/api/customer/rules');
    const groups = Object.entries(rulesMetadata.fields).map(([group, fields]) => {
      const details = element('details', undefined, 'material-details');
      const content = element('div', undefined, 'rule-fields-grid');
      content.append(...fields.map(field => makeField(group, field)));
      details.append(element('summary', groupTitles[group]), content);
      return details;
    });
    byId('rule-fields').replaceChildren(...groups);
    coverageCards(readInputs());
    manualForm.hidden = byId('rule-coverage').hidden = false;
    message('rules-status');
  } catch (error) {
    message('rules-status', `Не удалось загрузить правила. ${error.message}`, true);
  }
}

function showFindings(findings) {
  findingsPanel.replaceChildren(...findings.map(finding => {
    const card = element('article', undefined, 'finding-card');
    const badge = element('span', severities[finding.severity] ?? 'Результат', 'severity');
    badge.dataset.severity = finding.severity;
    const title = element('h3');
    title.append(badge, document.createTextNode(finding.title));
    const evidence = element('ul', undefined, 'evidence');
    evidence.append(...finding.evidence.map(text => element('li', text)));
    card.append(title, element('p', finding.diagnosis), evidence, element('p', finding.recommendation));
    return card;
  }));
  message('diagnosis-status', findings.length ? `Сработало правил: ${findings.length}.` : 'По введённым данным ни одна связка не сработала.');
}

manualForm.addEventListener('input', () => {
  diagnosis?.abort();
  diagnosis = null;
  runRules.disabled = false;
  findingsPanel.replaceChildren();
  message('diagnosis-status');
  coverageCards(readInputs());
});

manualForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (diagnosis) return;
  const inputs = readInputs();
  if (!Object.keys(inputs.telemetry).length && !Object.keys(inputs.vision).length) {
    message('diagnosis-status', 'Укажите хотя бы одно подтверждённое значение.', true);
    return;
  }
  const controller = new AbortController();
  diagnosis = controller;
  runRules.disabled = true;
  message('diagnosis-status', 'Проверяем введённые данные…');
  findingsPanel.replaceChildren();
  try {
    const data = await api('/api/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serial: rulesMetadata.profile?.serial ?? 'P-102', ...inputs }),
      signal: controller.signal,
    });
    if (diagnosis === controller) showFindings(data.findings);
  } catch (error) {
    if (diagnosis === controller) message('diagnosis-status', error.message, true);
  } finally {
    if (diagnosis === controller) {
      diagnosis = null;
      runRules.disabled = false;
    }
  }
});

window.addEventListener('pagehide', () => {
  if (photoAnalysis) {
    photoAnalysis.abort();
    photoAnalysis = null;
    analyzePhoto.disabled = false;
    byId('photo-cancel').hidden = true;
    message('photo-status');
  }
  if (diagnosis) {
    diagnosis.abort();
    diagnosis = null;
    runRules.disabled = false;
    message('diagnosis-status');
  }
});

loadCatalog();
loadSensors();
loadRules();
})();
