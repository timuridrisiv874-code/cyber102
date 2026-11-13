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
    $('replay-message').textContent = `Строка ${index + 1} из ${count} · эмулятор`;
    $('replay-timestamp').replaceChildren();
    for (const [label, value] of [
      ['Время исходной записи', `${snapshot.sourceTimestamp.replace('T', ' ')} · часовой пояс не указан`],
      ['Получено с сервера', new Date(snapshot.emittedAt).toLocaleString('ru-RU')],
      ['Частота вращения', typeof row.rpm === 'number' ? `${row.rpm} об/мин` : 'Нет данных'],
      ['Серийный номер', 'Не установлен в исходном наборе'],
    ]) $('replay-timestamp').append(node('dt', label), node('dd', value));
    $('telemetry-channels').replaceChildren();
    for (const [group, key, assessmentKey, title, unit] of channels) {
      const assessment = row.assessments?.[group]?.[assessmentKey];
      const card = node('article', null, 'workspace-card');
      card.dataset.level = assessment?.level || 'unknown';
      const value = row[group]?.[key];
      card.append(node('h3', title), node('p', typeof value === 'number' ? `${value.toLocaleString('ru-RU')} ${unit}` : 'Нет данных', 'workspace-value'));
      card.append(node('p', levels[assessment?.level] || 'Оценка недоступна', 'workspace-note'));
      $('telemetry-channels').append(card);
    }
    const disagreements = ['vibration', 'temperature'].filter(group => row.assessments?.[group]?.disagreement);
    $('telemetry-disagreement').textContent = disagreements.length
      ? 'Метка в исходной таблице отличается от оценки по описанным порогам. Для высокочастотного ускорения указан порог предупреждения, но порог тревоги не задан. Исходная метка сохранена в происхождении строки.'
      : 'Оценки рассчитаны по порогам из описаний баз. Это оценка измерений, а не заключение об исправности оборудования.';
    $('replay-provenance').textContent = JSON.stringify({
      source: snapshot.source, datasetId: snapshot.datasetId, quality: snapshot.quality,
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
    $('replay-message').textContent = 'Получаем строку с сервера…';
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
    if (playing) { stop(); $('replay-message').textContent = index < 0 ? 'Пауза' : `Строка ${index + 1} из ${count} · пауза`; }
    else { playing = true; controls(); load(index >= count - 1 ? 0 : Math.max(0, index + 1)); }
  });
  step.addEventListener('click', () => { stop(); load(Math.min(count - 1, index + 1)); });
  reset.addEventListener('click', () => { stop(); load(0); });
  position.addEventListener('input', () => { stop(); load(Number(position.value)); });
  speed.addEventListener('change', () => { if (playing) { clearTimeout(timer); if (!request) timer = setTimeout(() => load(index + 1), Number(speed.value)); } });
  function suspend() {
    stop();
    position.value = String(Math.max(0, index));
    $('replay-message').textContent = index < 0 ? 'Загрузка приостановлена.' : `Строка ${index + 1} из ${count} · пауза`;
  }
  function resume() {
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
})();
