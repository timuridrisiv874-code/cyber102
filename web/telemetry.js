'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const play = $('replay-play'), step = $('replay-step'), reset = $('replay-reset');
  const position = $('replay-position'), speed = $('replay-speed');
  let count = 0, index = -1, playing = false, timer, request, sequence = 0;
  const levels = {below_threshold: 'Ниже заданных порогов', warning: 'Предупреждение', alarm: 'Тревога', unknown: 'Порог не задан'};
  const channels = [
    ['vibration', 'rms_mm_s', 'rms_overall', 'Общая вибрация RMS', 'мм/с'],
    ['vibration', 'one_x_mm_s', 'one_x', 'Первая гармоника · 1×', 'мм/с'],
    ['vibration', 'two_x_mm_s', 'two_x', 'Вторая гармоника · 2×', 'мм/с'],
    ['vibration', 'high_frequency_g', 'high_frequency', 'Высокочастотное ускорение', 'g'],
    ['temperature', 'bearing_c', 'bearing', 'Температура подшипника', '°C'],
    ['temperature', 'winding_c', 'winding', 'Температура обмотки', '°C'],
  ];
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  let trendRows = [], trendRequest, trendLoaded = false;
  function svgElement(tag, attributes, text) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (text != null) element.textContent = text;
    return element;
  }
  function renderTrend() {
    const values = trendRows.map((row, i) => ({i, value: row.vibration?.rms_mm_s})).filter(point => Number.isFinite(point.value));
    if (!values.length) return;
    const width = 900, height = 220, left = 42, right = 14, top = 18, bottom = 30;
    const max = Math.max(...values.map(point => point.value)) * 1.1 || 1;
    const x = i => left + i / Math.max(1, trendRows.length - 1) * (width - left - right);
    const y = value => top + (1 - value / max) * (height - top - bottom);
    const svg = svgElement('svg', {viewBox: `0 0 ${width} ${height}`, 'aria-hidden': 'true'});
    for (let i = 0; i <= 3; i++) {
      const value = max * i / 3;
      svg.append(svgElement('line', {x1:left, x2:width-right, y1:y(value), y2:y(value), class:'trend-grid'}));
      svg.append(svgElement('text', {x:left-10, y:y(value)+4, 'text-anchor':'end'}, value.toLocaleString('ru-RU', {maximumFractionDigits:1})));
    }
    // Break the line at missing records rather than implying measured continuity.
    let path = '', previous = -2;
    for (const point of values) { path += `${point.i === previous + 1 ? 'L' : 'M'}${x(point.i).toFixed(2)},${y(point.value).toFixed(2)} `; previous = point.i; }
    svg.append(svgElement('path', {d:path, class:'trend-series'}));
    for (const i of [0, Math.floor((trendRows.length-1)/2), trendRows.length-1]) {
      const label = String(trendRows[i]?.timestamp || '').slice(5,16).replace('T',' ');
      svg.append(svgElement('text', {x:x(i), y:height-6, 'text-anchor':i===0?'start':i===trendRows.length-1?'end':'middle'}, label));
    }
    const point = values.find(point => point.i === index);
    if (point) {
      svg.append(svgElement('line', {x1:x(index), x2:x(index), y1:top, y2:height-bottom, class:'trend-cursor'}));
      svg.append(svgElement('circle', {cx:x(index), cy:y(point.value), r:5, class:'trend-current'}));
    }
    $('telemetry-trend-chart').replaceChildren(svg);
    const current = point ? ` Текущая запись: ${point.value.toLocaleString('ru-RU')} мм/с.` : '';
    $('telemetry-trend-chart').setAttribute('aria-label', `Общая вибрация RMS: ${values.length} измерений.${current}`);
  }
  async function loadTrend() {
    if (trendLoaded || trendRequest) return;
    const controller = new AbortController(); trendRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/customer/sensors', {signal:controller.signal, cache:'no-store'});
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.rows)) throw new Error('График недоступен. Обновите страницу.');
      if (trendRequest !== controller) return;
      trendRows = data.rows; trendLoaded = true;
      $('telemetry-trend-status').textContent = trendRows.some(row => Number.isFinite(row.vibration?.rms_mm_s)) ? '' : 'В наборе нет измерений RMS.';
      renderTrend();
    } catch (error) {
      if (trendRequest === controller) $('telemetry-trend-status').textContent = 'График не загрузился. Обновите страницу.';
    } finally { clearTimeout(timeout); if (trendRequest === controller) trendRequest = null; }
  }
  function controls() {
    play.textContent = playing ? 'Пауза' : 'Воспроизвести';
    $('playback-state').textContent = playing ? 'Воспроизведение' : 'Пауза';
    play.disabled = !count;
    step.disabled = !count || index >= count - 1;
    reset.disabled = !count;
    position.disabled = !count;
  }
  function pause() { playing = false; clearTimeout(timer); controls(); }
  function cancel() { sequence++; request?.abort(); request = null; }
  function stop() { pause(); cancel(); }
  function render(snapshot) {
    const row = snapshot.row;
    index = snapshot.index;
    position.value = String(index);
    renderTrend();
    $('replay-message').textContent = `Запись ${index + 1} из ${count}`;
    $('replay-timestamp').replaceChildren();
    for (const [label, value] of [
      ['Время записи', snapshot.sourceTimestamp.replace('T', ' ')],
      ['Частота вращения', typeof row.rpm === 'number' ? `${row.rpm} об/мин` : 'Нет данных'],
      ['Оборудование', 'Не указано в наборе'],
    ]) $('replay-timestamp').append(node('dt', label), node('dd', value));
    $('telemetry-channels').replaceChildren();
    for (const [group, key, assessmentKey, title, unit] of channels) {
      const assessment = row.assessments?.[group]?.[assessmentKey];
      const card = node('article', null, 'workspace-card');
      card.dataset.level = assessment?.level || 'unknown';
      const value = row[group]?.[key];
      card.append(node('h3', title), node('p', typeof value === 'number' ? `${value.toLocaleString('ru-RU')} ${unit}` : 'Нет данных', 'workspace-value'));
      card.append(node('p', levels[assessment?.level] || 'Оценка недоступна', 'workspace-note'));
      if (assessmentKey === 'high_frequency') card.append(node('p', 'Порог тревоги не задан', 'channel-caveat'));
      $('telemetry-channels').append(card);
    }
    const disagreements = ['vibration', 'temperature'].filter(group => row.assessments?.[group]?.disagreement);
    $('telemetry-disagreement').textContent = disagreements.length
      ? 'Исходная метка и оценка по порогам расходятся.'
      : 'Оценка по порогам из описания набора.';
    $('replay-provenance').textContent = JSON.stringify({
      emittedAt: snapshot.emittedAt, sourceTimestamp: snapshot.sourceTimestamp, timezone: 'Не указан', source: snapshot.source, datasetId: snapshot.datasetId, quality: snapshot.quality,
      sourceLabels: {vibration: row.vibration.source_label, temperature: row.temperature.source_label},
      assessments: row.assessments, provenance: row.provenance,
    }, null, 2);
    controls();
  }
  async function load(next) {
    cancel();
    const current = sequence;
    const controller = new AbortController(); request = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    $('replay-message').textContent = 'Загружаем запись…';
    try {
      const response = await fetch(`/api/telemetry/snapshot?index=${next}`, {signal: controller.signal, cache: 'no-store'});
      const snapshot = await response.json();
      if (!response.ok) throw new Error(snapshot.error || 'Не удалось получить показания.');
      if (current !== sequence) return;
      if (snapshot.index !== next || snapshot.rowCount !== count || snapshot.source !== 'customer_replay'
          || typeof snapshot.sourceTimestamp !== 'string' || !Number.isFinite(Date.parse(snapshot.emittedAt))
          || !snapshot.row?.vibration || !snapshot.row?.temperature || !snapshot.row?.assessments || !snapshot.row?.provenance) {
        throw new Error('Источник вернул некорректную запись.');
      }
      render(snapshot);
      if (playing && index < count - 1) timer = setTimeout(() => load(index + 1), Number(speed.value));
      else if (playing) { pause(); $('playback-state').textContent = 'Конец набора'; }
    } catch (error) {
      if (current !== sequence) return;
      pause();
      $('telemetry-channels').replaceChildren();
      $('replay-timestamp').replaceChildren();
      $('replay-provenance').textContent = '';
      $('telemetry-disagreement').textContent = '';
      position.value = String(Math.max(0, index));
      $('replay-message').textContent = error.name === 'AbortError' ? 'Сервер не ответил вовремя. Повторите загрузку.' : error.message;
    } finally { clearTimeout(timeout); if (current === sequence) request = null; }
  }
  play.addEventListener('click', () => {
    if (playing) { stop(); $('replay-message').textContent = index < 0 ? 'Пауза' : `Запись ${index + 1} из ${count} · пауза`; }
    else { playing = true; controls(); load(index >= count - 1 ? 0 : Math.max(0, index + 1)); }
  });
  step.addEventListener('click', () => { stop(); load(Math.min(count - 1, index + 1)); });
  reset.addEventListener('click', () => { stop(); load(0); });
  position.addEventListener('input', () => { stop(); load(Number(position.value)); });
  speed.addEventListener('change', () => { if (playing) { clearTimeout(timer); if (!request) timer = setTimeout(() => load(index + 1), Number(speed.value)); } });
  function suspend() {
    trendRequest?.abort(); trendRequest = null;
    stop();
    position.value = String(Math.max(0, index));
    $('replay-message').textContent = index < 0 ? 'Загрузка приостановлена.' : `Запись ${index + 1} из ${count} · пауза`;
  }
  function resume() {
    if (!document.hidden) loadTrend();
    if (!document.hidden && !request && (!count || index < 0)) {
      if (count) load(0); else initialize();
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) suspend(); else resume(); });
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', resume);
  async function initialize() {
    const controller = new AbortController(); request = controller;
    const current = sequence;
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch('/api/telemetry/catalog', {signal: controller.signal, cache: 'no-store'});
      const catalog = await response.json();
      if (!response.ok) throw new Error(catalog.error || 'Не удалось загрузить источник.');
      if (current !== sequence) return;
      if (!Number.isInteger(catalog.rowCount) || catalog.rowCount < 1) throw new Error('В источнике нет строк.');
      count = catalog.rowCount;
      position.max = String(count - 1); controls();
      await load(0);
    } catch (error) {
      if (current === sequence) $('replay-message').textContent = error.name === 'AbortError' ? 'Источник не ответил вовремя.' : error.message;
    } finally { clearTimeout(timeout); if (current === sequence) request = null; }
  }
  initialize();
  loadTrend();
})();
