// Read one equipment label on demand from the live camera; frames stay local.
(() => {
  function serialFromLabel(raw, origin) {
    if (typeof raw !== 'string' || raw.length > 1024) return null;
    let serial = raw.trim();
    if (/^https?:\/\//i.test(serial)) {
      let url;
      try { url = new URL(serial); } catch { return null; }
      if (url.origin !== origin || url.username || url.password || !['/checkpoint.html','/index.html','/'].includes(url.pathname)
          || url.searchParams.getAll('serial').length !== 1) return null;
      serial = url.searchParams.get('serial');
    }
    return /^[\p{L}\p{N}][\p{L}\p{N}._ /-]{0,127}$/u.test(serial) ? serial : null;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { serialFromLabel };
  if (typeof window === 'undefined' || window.CyberWebBoot?.blocked) return;
  const video = document.getElementById('video'), button = document.getElementById('equipment-qr');
  if (!button) return;
  const field = document.getElementById('active-equipment'), status = document.getElementById('qr-scan-status');
  const canvas = document.createElement('canvas');
  let library, generation = 0, running = false, timeout, frameTimer, request;
  function decoder() {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (!library) library = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/vendor/jsQR-1.4.0.js';
      script.onload = () => window.jsQR ? resolve(window.jsQR) : reject(new Error());
      script.onerror = reject;
      document.head.append(script);
    }).catch(error => { library = null; throw error; });
    return library;
  }
  window.CyberEquipmentQr = { decoder };
  function announce(message) {
    status.textContent = message;
    status.hidden = !message;
  }
  function stop(message = '') {
    generation++; running = false;
    clearTimeout(timeout); clearTimeout(frameTimer); request?.abort();
    button.setAttribute('aria-pressed','false');
    announce(message);
  }
  const activeCamera = () => !video.hidden && Boolean(video.srcObject) && !document.hidden && !field.disabled;
  function sync() {
    button.hidden = !activeCamera();
    if (button.hidden && running) stop();
    if (button.hidden) announce('');
  }
  async function start() {
    if (running) { stop(); return; }
    if (!activeCamera()) return;
    running = true;
    const token = ++generation;
    button.setAttribute('aria-pressed','true');
    announce('Наведите камеру на QR оборудования');
    timeout = setTimeout(() => stop('QR не прочитан. Попробуйте ещё раз или введите номер.'), 20000);
    try {
      const decode = await decoder();
      const ctx = canvas.getContext('2d', {willReadFrequently:true});
      async function frame() {
        if (token !== generation || !activeCamera()) return;
        try {
          if (video.readyState >= 2 && video.videoWidth) {
            const scale = Math.min(1, 900 / Math.max(video.videoWidth, video.videoHeight));
            canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
            canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const label = decode(pixels.data, pixels.width, pixels.height, {inversionAttempts:'dontInvert'});
            if (label) {
              const serial = serialFromLabel(label.data, location.origin);
              if (!serial) { stop('Это не метка оборудования стенда.'); return; }
              request = new AbortController();
              const response = await fetch('/api/scenarios?' + new URLSearchParams({serial}), {signal:request.signal, cache:'no-store'});
              if (token !== generation) return;
              if (response.status === 404) { stop('Оборудование с таким номером отсутствует в журнале.'); return; }
              if (!response.ok) throw new Error();
              const data = await response.json();
              if (token !== generation || !activeCamera()) return;
              if (data.passport?.serial !== serial) { stop('Не удалось подтвердить серийный номер.'); return; }
              field.value = serial;
              field.dispatchEvent(new Event('input', {bubbles:true}));
              field.dispatchEvent(new Event('change', {bubbles:true}));
              stop('Выбрано оборудование ' + serial); return;
            }
          }
          frameTimer = setTimeout(frame, 220);
        } catch { if (token === generation) stop('Не удалось прочитать метку. Повторите или введите номер.'); }
      }
      await frame();
    } catch { if (token === generation) stop('Сканер не загрузился. Повторите или введите номер.'); }
  }
  button.addEventListener('click', start);
  const observer = new MutationObserver(sync);
  observer.observe(video, {attributes:true, attributeFilter:['hidden']});
  observer.observe(field, {attributes:true, attributeFilter:['disabled']});
  video.addEventListener('loadeddata', sync); video.addEventListener('emptied', sync);
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('pagehide', () => stop());
  sync();
})();
