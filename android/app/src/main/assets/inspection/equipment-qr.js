// Decode one label on request, using the inspection camera's existing stream.
(() => {
  const button = document.getElementById('equipment-qr');
  const video = document.getElementById('video');
  const status = document.getElementById('qr-scan-status');
  const badge = document.getElementById('qr-selected');
  const value = document.getElementById('qr-value');
  const field = document.getElementById('active-equipment');
  const offline = window.CyberOffline?.enabled === true;
  const canvas = document.createElement('canvas');
  let library, embeddedCatalog, request, selected = null;
  let scanning = false, generation = 0, timer, deadline;

  function identifier(raw) {
    if (typeof raw !== 'string' || raw.length > 2048) return '';
    let id = raw.trim();
    if (/^https?:\/\//i.test(id)) {
      let url;
      try { url = new URL(id); } catch { return ''; }
      if (url.username || url.password || url.pathname !== '/checkpoint.html' ||
          url.searchParams.getAll('serial').length !== 1 || (!offline && url.origin !== location.origin)) return '';
      id = url.searchParams.get('serial').trim();
    }
    return /^[\p{L}\p{N}][\p{L}\p{N}._ /-]{0,127}$/u.test(id) ? id : '';
  }
  function decoder() {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (!library) library = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/__mobile_inspection/jsQR-1.4.0.js';
      script.onload = () => window.jsQR ? resolve(window.jsQR) : reject(new Error());
      script.onerror = reject;
      document.head.append(script);
    }).catch(error => { library = null; throw error; });
    return library;
  }
  async function findEquipment(serial) {
    let rows;
    if (offline) {
      if (!embeddedCatalog) {
        const catalog = JSON.parse(window.CyberSmolNative.equipmentCatalog());
        if (catalog?.schemaVersion !== 1 || !Array.isArray(catalog.equipment)) throw new Error();
        embeddedCatalog = catalog.equipment;
      }
      rows = embeddedCatalog;
    } else {
      request = new AbortController();
      const response = await fetch('/api/equipment', {cache: 'no-store', signal: request.signal});
      if (!response.ok) throw new Error();
      rows = (await response.json()).equipment;
    }
    if (!Array.isArray(rows)) throw new Error();
    const found = rows.find(row => row.serial === serial);
    if (!found) return null;
    const name = found.name || found.title;
    if (typeof name !== 'string' || !name.trim()) throw new Error();
    return { serial, name, model: found.model || '', manufacturer: found.manufacturer || '',
      demo: found.demo === true || found.source === 'Демонстрационные данные',
      journalSupported: offline || !found.kind || found.kind === 'pump' };
  }
  function showObject(item) {
    selected = item;
    badge.hidden = !item;
    if (!item) return;
    document.getElementById('qr-name').textContent = item.name;
    value.textContent = 'Серийный номер · ' + item.serial;
    const model = document.getElementById('qr-model');
    model.hidden = !item.model || item.name.includes(item.model);
    model.textContent = item.model;
    const maker = document.getElementById('qr-maker');
    maker.hidden = !item.manufacturer;
    maker.textContent = item.manufacturer;
    document.getElementById('qr-demo').hidden = !item.demo;

  }
  function select(item) {
    if (!offline) {
      // Changing the field invalidates the old inspection; only pumps have a server journal.
      field.value = item?.serial || '';
      field.dispatchEvent(new Event('input', {bubbles: true}));
      if (!item || item.journalSupported) field.dispatchEvent(new Event('change', {bubbles: true}));
    }
    showObject(item);
  }
  function stop(message = '') {
    generation++;
    scanning = false;
    request?.abort();
    request = null;
    clearTimeout(timer);
    clearTimeout(deadline);
    button.setAttribute('aria-pressed', 'false');
    status.textContent = message;
    status.hidden = !message;
  }
  function live() {
    return !document.hidden && !video.hidden && Boolean(video.srcObject) &&
      (offline || !field.disabled);
  }
  function sync() {
    button.hidden = !live();
    if (button.hidden && scanning) stop();
  }
  async function start() {
    if (scanning) { stop(); return; }
    if (!live()) return;
    const epoch = ++generation;
    scanning = true;
    button.setAttribute('aria-pressed', 'true');
    status.hidden = false;
    status.textContent = 'Наведите камеру на QR-код оборудования';
    deadline = setTimeout(() => stop(request ? 'Каталог не ответил. Повторите поиск.' : 'QR-код не прочитан. Можно попробовать ещё раз.'), 20000);
    try {
      const decode = await decoder();
      const ctx = canvas.getContext('2d', {willReadFrequently: true});
      async function frame() {
        if (epoch !== generation) return;
        if (!live()) { stop(); return; }
        try {
          if (video.readyState >= 2 && video.videoWidth) {
            const scale = Math.min(1, 900 / Math.max(video.videoWidth, video.videoHeight));
            canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
            canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const label = decode(pixels.data, pixels.width, pixels.height);
            if (label) {
              const id = identifier(label.data);
              if (!id) { stop('Ожидается номер оборудования или QR-ссылка на его паспорт.'); return; }
              status.textContent = 'Поиск в каталоге…';
              try {
                const item = await findEquipment(id);
                if (epoch !== generation || !live()) return;
                if (!item) { stop('Объект с номером «' + id + '» не найден в каталоге.' + (selected ? ' Выбор сохранён.' : '')); return; }
                stop(item.journalSupported ? '' : 'Серверный журнал осмотров доступен для насосов.');
                select(item);
              } catch {
                if (epoch === generation) stop('Каталог недоступен. Повторите поиск.');
              }
              return;
            }
          }
          timer = setTimeout(frame, 240);
        } catch { stop('Не удалось прочитать кадр. Повторите сканирование.'); }
      }
      frame();
    } catch { if (epoch === generation) stop('Не удалось запустить сканер. Повторите попытку.'); }
  }
  button.addEventListener('click', start);
  document.getElementById('qr-clear').addEventListener('click', () => { stop(); select(null); });
  if (!offline) field.addEventListener('input', () => {
    if (scanning) stop();
    if (field.value !== selected?.serial) showObject(null);
  });
  new MutationObserver(sync).observe(video, {attributes: true, attributeFilter: ['hidden']});
  new MutationObserver(sync).observe(field, {attributes: true, attributeFilter: ['disabled']});
  video.addEventListener('loadeddata', sync);
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('pagehide', () => stop());
  window.CyberEquipmentQR = { selected: () => selected?.serial || '', object: () => selected };
  sync();
})();
