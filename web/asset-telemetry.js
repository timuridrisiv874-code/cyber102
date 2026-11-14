'use strict';
(() => {
  if (window.CyberWebBoot?.blocked) return;
  const field = document.getElementById('active-equipment');
  const panel = document.getElementById('asset-telemetry');
  const status = document.getElementById('asset-telemetry-status');
  const values = document.getElementById('asset-telemetry-values');
  const knownPump = /^21031004-[1-4]$/;
  let request, scheduled;
  async function refresh() {
    clearTimeout(scheduled);
    request?.abort();
    const serial = field.value.trim();
    panel.hidden = !knownPump.test(serial);
    if (panel.hidden || !panel.open || document.hidden) return;
    const active = new AbortController();
    request = active;
    try {
      const response = await fetch('/api/telemetry/simulated?' + new URLSearchParams({serial}), {
        signal: active.signal, cache: 'no-store',
      });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (active.signal.aborted || field.value.trim() !== serial) return;
      if (data.equipmentSerial !== serial || data.source !== 'simulated') throw new Error();
      if (!data.latest) {
        status.textContent = 'Ожидаем первый пакет эмулятора…';
        values.replaceChildren();
        return;
      }
      status.textContent = 'Эмуляция · ' + new Date(data.latest.timestamp).toLocaleTimeString('ru-RU');
      values.replaceChildren(...data.channels.map(channel => {
        const row = document.createElement('div');
        const term = document.createElement('dt'), number = document.createElement('dd');
        term.textContent = channel.label;
        number.textContent = data.latest.values[channel.id].toLocaleString('ru-RU') + ' ' + channel.unit;
        row.append(term, number);
        const first = data.samples[0]?.values[channel.id];
        if (typeof first === 'number' && data.samples.length > 1) {
          const delta = Math.round((data.latest.values[channel.id] - first) * 100) / 100;
          const trend = document.createElement('small');
          trend.textContent = (delta > 0 ? '+' : '') + delta.toLocaleString('ru-RU') + ' за ' +
            (data.samples.length - 1) * data.periodSeconds + ' с';
          number.append(document.createElement('br'), trend);
        }
        return row;
      }));
    } catch (error) {
      if (!active.signal.aborted) {
        status.textContent = 'Эмулятор временно недоступен.';
        values.replaceChildren();
      }
    } finally {
      if (!active.signal.aborted) scheduled = setTimeout(refresh, 5000);
    }
  }
  field.addEventListener('input', () => { values.replaceChildren(); status.textContent = ''; refresh(); });
  field.addEventListener('change', refresh);
  panel.addEventListener('toggle', refresh);
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('pagehide', () => { clearTimeout(scheduled); request?.abort(); });
  refresh();
})();
