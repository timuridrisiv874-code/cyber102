'use strict';
(() => {
  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
  function chartWindow(length, start = 0, size = length) {
    if (!Number.isInteger(length) || length < 1) return {start:0, end:-1, size:0};
    size = clamp(Math.round(size) || 1, 1, length);
    start = clamp(Math.round(start) || 0, 0, length - size);
    return {start, end:start + size - 1, size};
  }
  function zoomWindow(view, factor, anchor, length) {
    if (!view.size || !Number.isFinite(factor) || factor <= 0) return chartWindow(length);
    const size = clamp(Math.round(view.size * factor), Math.min(20, length), length);
    anchor = clamp(anchor, view.start, view.end);
    const fraction = view.size > 1 ? (anchor - view.start) / (view.size - 1) : .5;
    return chartWindow(length, anchor - fraction * (size - 1), size);
  }
  function revealIndex(view, target, length) {
    return target >= view.start && target <= view.end ? view
      : chartWindow(length, target - Math.floor(view.size / 2), view.size);
  }
  function indexAtFraction(fraction, view) {
    return view.size ? view.start + Math.round(clamp(fraction, 0, 1) * (view.size - 1)) : -1;
  }
  function keyIndex(key, current, view, length) {
    const delta = {ArrowLeft:-1, ArrowRight:1, PageUp:-Math.max(1, Math.floor(view.size / 10)), PageDown:Math.max(1, Math.floor(view.size / 10))}[key];
    if (delta != null) return clamp(current + delta, 0, length - 1);
    return key === 'Home' ? 0 : key === 'End' ? length - 1 : current;
  }
  function seriesSegments(rows, view) {
    const segments = []; let segment;
    for (let i = view.start; i <= view.end; i++) {
      const value = rows[i]?.vibration?.rms_mm_s;
      if (!Number.isFinite(value)) { segment = null; continue; }
      if (!segment) { segment = []; segments.push(segment); }
      segment.push({index:i, value});
    }
    return segments;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {chartWindow, zoomWindow, revealIndex, indexAtFraction, keyIndex, seriesSegments};
  if (typeof document === 'undefined') return;
  if (window.CyberWebBoot?.blocked) return;
  const $ = id => document.getElementById(id);
  const play = $('replay-play'), step = $('replay-step'), reset = $('replay-reset');
  const position = $('replay-position'), speed = $('replay-speed');
  let count = 0, index = -1, playing = false, timer, request, sequence = 0, datasetId;
  let pendingIndex = null, snapshotReady = false;
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
  const chart = $('telemetry-trend-chart'), zoomIn = $('trend-zoom-in'), zoomOut = $('trend-zoom-out');
  const zoomReset = $('trend-zoom-reset'), windowPosition = $('trend-window'), choosePoint = $('trend-select');
  const valueFormat = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:3});
  let trendRows = [], trendRequest, trendLoaded = false, trendDatasetId;
  let view = chartWindow(0), previewIndex = null, geometry, previewLayer, pointerStart, hoverFrame;
  function svgElement(tag, attributes, text) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (text != null) element.textContent = text;
    return element;
  }
  function activePoint() {
    return previewIndex ?? (index >= view.start && index <= view.end ? index : view.start);
  }
  function dateLabel(i, full = false) {
    const raw = String(trendRows[i]?.timestamp || '');
    return full ? raw.slice(0,16).replace('T',' ') : raw.slice(5,16).replace('T',' ');
  }
  function valueLabel(i) {
    const value = trendRows[i]?.vibration?.rms_mm_s;
    return Number.isFinite(value) ? `${valueFormat.format(value)} мм/с` : 'Нет данных';
  }
  function updateReadout(announce = false) {
    const current = activePoint(), ready = view.size > 0;
    const selected = snapshotReady && current === index;
    $('trend-value').textContent = ready ? valueLabel(current) : '—';
    $('trend-time').textContent = ready ? dateLabel(current, true) : '';
    $('trend-record').textContent = ready ? `${current === pendingIndex ? 'Загрузка' : selected ? 'Выбрана' : 'Просмотр'} · запись ${current + 1}` : '';
    choosePoint.disabled = !ready || !count || current === pendingIndex || selected;
    if (announce && ready) {
      const announcement = `Запись ${current + 1}. ${dateLabel(current, true)}. ${valueLabel(current)}.`;
      if ($('trend-announcement').textContent !== announcement) $('trend-announcement').textContent = announcement;
    }
    if (!previewLayer || !geometry) return;
    previewLayer.replaceChildren();
    if (previewIndex == null || selected || current < view.start || current > view.end) return;
    const x = geometry.x(current), value = trendRows[current]?.vibration?.rms_mm_s;
    previewLayer.append(svgElement('line', {x1:x, x2:x, y1:geometry.top, y2:geometry.bottom, class:'trend-preview-line'}));
    if (Number.isFinite(value)) previewLayer.append(svgElement('circle', {cx:x, cy:geometry.y(value), r:5, class:'trend-preview-point'}));
  }
  function renderTrend() {
    if (!trendRows.length) return;
    if (!view.size) view = chartWindow(trendRows.length);
    const width = Math.max(240, Math.round(chart.getBoundingClientRect().width)), height = 240;
    const left = 42, right = 12, top = 16, bottom = height - 30;
    const segments = seriesSegments(trendRows, view), values = segments.flat();
    const max = (values.length ? Math.max(...values.map(point => point.value)) * 1.1 : 1) || 1;
    const x = i => left + (i - view.start) / Math.max(1, view.size - 1) * (width - left - right);
    const y = value => top + (1 - value / max) * (bottom - top);
    geometry = {width, height, left, right, top, bottom, x, y};
    const svg = svgElement('svg', {viewBox:`0 0 ${width} ${height}`, 'aria-hidden':'true'});
    for (let i = 0; i <= 3; i++) {
      const value = max * i / 3;
      svg.append(svgElement('line', {x1:left, x2:width-right, y1:y(value), y2:y(value), class:'trend-grid'}));
      svg.append(svgElement('text', {x:left-10, y:y(value)+4, 'text-anchor':'end'}, value.toLocaleString('ru-RU', {maximumFractionDigits:1})));
    }
    for (const segment of segments) {
      const path = segment.map((point, i) => `${i ? 'L' : 'M'}${x(point.index).toFixed(2)},${y(point.value).toFixed(2)}`).join(' ');
      svg.append(svgElement('path', {d:path, class:'trend-series'}));
      if (segment.length === 1) svg.append(svgElement('circle', {cx:x(segment[0].index), cy:y(segment[0].value), r:2, class:'trend-isolated'}));
    }
    const ticks = width < 480 ? [view.start, view.end] : [view.start, Math.floor((view.start+view.end)/2), view.end];
    for (const i of [...new Set(ticks)]) svg.append(svgElement('text', {x:x(i), y:height-6,
      'text-anchor':i===view.start?'start':i===view.end?'end':'middle'}, dateLabel(i)));
    if (snapshotReady && index >= view.start && index <= view.end) {
      const value = trendRows[index]?.vibration?.rms_mm_s;
      svg.append(svgElement('line', {x1:x(index), x2:x(index), y1:top, y2:bottom, class:'trend-cursor'}));
      if (Number.isFinite(value)) svg.append(svgElement('circle', {cx:x(index), cy:y(value), r:6, class:'trend-current'}));
      else svg.append(svgElement('path', {d:`M${x(index)-4},${bottom-4}L${x(index)+4},${bottom+4}M${x(index)-4},${bottom+4}L${x(index)+4},${bottom-4}`, class:'trend-missing'}));
    }
    previewLayer = svgElement('g', {}); svg.append(previewLayer); chart.replaceChildren(svg);
    zoomIn.disabled = view.size <= Math.min(20, trendRows.length);
    zoomOut.disabled = zoomReset.disabled = view.size === trendRows.length;
    windowPosition.max = String(trendRows.length - view.size); windowPosition.value = String(view.start);
    $('trend-window-control').hidden = view.size === trendRows.length;
    $('trend-range').textContent = `${view.start + 1}–${view.end + 1} из ${trendRows.length}`;
    windowPosition.setAttribute('aria-valuetext', `Записи ${view.start + 1}–${view.end + 1}`);
    chart.setAttribute('aria-label', `Общая вибрация. Записи ${view.start + 1}–${view.end + 1} из ${trendRows.length}`);
    $('telemetry-trend-status').textContent = values.length ? '' : 'В этом периоде нет измерений RMS.';
    if (previewIndex != null && (previewIndex < view.start || previewIndex > view.end)) previewIndex = null;
    updateReadout();
  }
  function showPoint(target, announce = false) {
    if (!trendRows.length) return;
    previewIndex = clamp(target, 0, trendRows.length - 1);
    const nextView = revealIndex(view, previewIndex, trendRows.length);
    if (nextView !== view) { view = nextView; renderTrend(); }
    updateReadout(announce);
  }
  function selectPoint(target = activePoint()) {
    if (!count || target < 0 || target >= count) return;
    showPoint(target);
    if (target === pendingIndex) { pause(); return; }
    if (snapshotReady && target === index) { stop(); updateReadout(); return; }
    stop(); load(target);
  }
  function zoom(factor) {
    if (!trendRows.length) return;
    view = zoomWindow(view, factor, activePoint(), trendRows.length); renderTrend();
  }
  zoomIn.addEventListener('click', () => zoom(.5));
  zoomOut.addEventListener('click', () => zoom(2));
  zoomReset.addEventListener('click', () => { view = chartWindow(trendRows.length); renderTrend(); });
  windowPosition.addEventListener('input', () => { view = chartWindow(trendRows.length, Number(windowPosition.value), view.size); previewIndex = null; renderTrend(); });
  choosePoint.addEventListener('click', () => selectPoint());
  function pointFromEvent(event) {
    const rect = chart.getBoundingClientRect();
    const x = (event.clientX - rect.left) / Math.max(1, rect.width) * geometry.width;
    return indexAtFraction((x - geometry.left) / (geometry.width - geometry.left - geometry.right), view);
  }
  chart.addEventListener('pointermove', event => {
    if (!geometry || event.pointerType !== 'mouse' || event.buttons) return;
    cancelAnimationFrame(hoverFrame);
    hoverFrame = requestAnimationFrame(() => showPoint(pointFromEvent(event)));
  });
  function clearPointer() { pointerStart = null; cancelAnimationFrame(hoverFrame); }
  chart.addEventListener('pointerleave', clearPointer);
  chart.addEventListener('pointercancel', clearPointer);
  chart.addEventListener('pointerdown', event => {
    if (!geometry || !event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return;
    pointerStart = {id:event.pointerId, x:event.clientX, y:event.clientY};
  });
  chart.addEventListener('pointerup', event => {
    const start = pointerStart; pointerStart = null;
    if (!geometry || !start || event.pointerId !== start.id || Math.hypot(event.clientX-start.x,event.clientY-start.y) > 8) return;
    cancelAnimationFrame(hoverFrame);
    const target = pointFromEvent(event); showPoint(target);
    chart.focus({preventScroll:true}); selectPoint(target);
  });
  chart.addEventListener('focus', () => { if (view.size) showPoint(activePoint(), true); });
  chart.addEventListener('keydown', event => {
    if (!view.size || event.ctrlKey || event.metaKey || event.altKey) return;
    if (['ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(event.key)) {
      event.preventDefault(); showPoint(keyIndex(event.key, activePoint(), view, trendRows.length), true);
    } else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectPoint(); }
    else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(.5); }
    else if (event.key === '-') { event.preventDefault(); zoom(2); }
    else if (event.key === 'Escape') { event.preventDefault(); previewIndex = null; updateReadout(true); }
  });
  let chartWidth = 0;
  const resizeTrend = () => {
    const width = Math.round(chart.getBoundingClientRect().width);
    if (width !== chartWidth) { chartWidth = width; renderTrend(); }
  };
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resizeTrend).observe(chart);
  else window.addEventListener('resize', resizeTrend);
  async function loadTrend() {
    if (trendLoaded || trendRequest) return;
    const controller = new AbortController(); trendRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/customer/sensors', {signal:controller.signal, cache:'no-store'});
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.rows) || !data.rows.length || data.summary?.rowCount !== data.rows.length
          || !data.dataset?.id || (datasetId && (data.dataset.id !== datasetId || data.rows.length !== count))) {
        throw new Error('График недоступен. Обновите страницу.');
      }
      if (trendRequest !== controller) return;
      trendRows = data.rows; trendDatasetId = data.dataset.id; trendLoaded = true;
      view = chartWindow(trendRows.length); renderTrend();
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
  function cancel() { sequence++; request?.abort(); request = null; pendingIndex = null; chart.setAttribute('aria-busy','false'); }
  function stop() { pause(); cancel(); }
  function render(snapshot) {
    const row = snapshot.row;
    index = snapshot.index; pendingIndex = null; snapshotReady = true;
    position.value = String(index);
    if (previewIndex === index) previewIndex = null;
    if (view.size && previewIndex == null) view = revealIndex(view, index, trendRows.length);
    chart.setAttribute('aria-busy', 'false'); renderTrend();
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
      : '';
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
    pendingIndex = next; chart.setAttribute('aria-busy', 'true'); updateReadout();
    const controller = new AbortController(); request = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    $('replay-message').textContent = 'Загружаем запись…';
    try {
      const response = await fetch(`/api/telemetry/snapshot?index=${next}`, {signal: controller.signal, cache: 'no-store'});
      const snapshot = await response.json();
      if (!response.ok) throw new Error(snapshot.error || 'Не удалось получить показания.');
      if (current !== sequence) return;
      if (snapshot.index !== next || snapshot.rowCount !== count || snapshot.source !== 'customer_replay'
          || (datasetId && snapshot.datasetId !== datasetId)
          || typeof snapshot.sourceTimestamp !== 'string' || !Number.isFinite(Date.parse(snapshot.emittedAt))
          || !snapshot.row?.vibration || !snapshot.row?.temperature || !snapshot.row?.assessments || !snapshot.row?.provenance) {
        throw new Error('Источник вернул некорректную запись.');
      }
      render(snapshot);
      if (playing && index < count - 1) timer = setTimeout(() => load(index + 1), Number(speed.value));
      else if (playing) { pause(); $('playback-state').textContent = 'Конец набора'; }
    } catch (error) {
      if (current !== sequence) return;
      pause(); pendingIndex = null; snapshotReady = false;
      chart.setAttribute('aria-busy', 'false'); renderTrend();
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
    clearPointer(); previewIndex = null; updateReadout(); stop();
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
      count = catalog.rowCount; datasetId = catalog.dataset?.id;
      if (trendLoaded && (trendDatasetId !== datasetId || trendRows.length !== count)) {
        trendRows = []; trendLoaded = false; view = chartWindow(0); chart.replaceChildren();
        zoomIn.disabled = zoomOut.disabled = zoomReset.disabled = choosePoint.disabled = true;
        $('trend-window-control').hidden = true; updateReadout(); loadTrend();
      }
      position.max = String(count - 1); controls();
      await load(0);
    } catch (error) {
      if (current === sequence) $('replay-message').textContent = error.name === 'AbortError' ? 'Источник не ответил вовремя.' : error.message;
    } finally { clearTimeout(timeout); if (current === sequence) request = null; }
  }
  initialize();
  loadTrend();
})();
