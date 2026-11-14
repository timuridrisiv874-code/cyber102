/* Retrieve visual examples independently of the inspection and its selected serial. */
(() => {
  'use strict';
  function safeLink(value) {
    return typeof value === 'string' && (/^https:\/\/[^\s/]+\//i.test(value) ||
      /^\/api\/customer\/documents\/[a-z0-9-]+\/pdf#page=\d+$/.test(value));
  }
  function safeImage(value) {
    return typeof value === 'string' && (/^\/equipment-photos\/[a-z0-9-]+\.jpg$/.test(value) ||
      /^\/api\/customer\/images\/[a-f0-9]{64}$/.test(value));
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {safeLink, safeImage};
  if (typeof window === 'undefined') return;
  window.CyberEquipmentSearch = {create() {
    const panel = document.getElementById('similar-equipment');
    if (!panel || window.CyberOffline?.enabled) return null;
    const status = document.getElementById('similar-equipment-status');
    const list = document.getElementById('similar-equipment-list');
    const retry = document.getElementById('similar-equipment-retry');
    let generation = 0, controller = null, lastImage = null, lastKey = null;
    function reset() {
      generation++;
      controller?.abort(); controller = null;
      lastImage = null; lastKey = null;
      list.replaceChildren(); panel.hidden = true; retry.hidden = true;
      panel.removeAttribute('aria-busy');
    }
    function render(data) {
      if (!Array.isArray(data?.matches)) throw new Error('Поиск вернул неполный ответ.');
      for (const item of data.matches.slice(0, 5)) {
        if (!safeImage(item.imageUrl) || typeof item.title !== 'string') continue;
        const card = document.createElement('article'); card.className = 'similar-equipment-card';
        const image = document.createElement('img');
        image.src = item.imageUrl; image.alt = item.title; image.loading = 'lazy';
        const body = document.createElement('div'), title = document.createElement('h4');
        title.textContent = item.title;
        const source = document.createElement('p');
        source.className = 'workspace-note';
        source.textContent = item.source === 'catalogue' ? 'Каталожный образец' : 'Фото из подборки · экземпляр не установлен';
        body.append(title, source);
        const links = document.createElement('ul');
        for (const ref of (item.references || []).slice(0, 3)) {
          if (!safeLink(ref.url) || typeof ref.title !== 'string') continue;
          const row = document.createElement('li'), link = document.createElement('a');
          link.href = ref.url; link.textContent = ref.title; link.target = '_blank'; link.rel = 'noopener';
          row.append(link); links.append(row);
        }
        body.append(links); card.append(image, body); list.append(card);
      }
      status.textContent = list.childElementCount
        ? 'Похожие образцы и справки. Модель и серийный номер нужно сверить с табличкой.'
        : 'Подходящих образцов не найдено. Попробуйте снять оборудование целиком при хорошем освещении.';
    }
    async function run(image) {
      const token = ++generation;
      controller?.abort();
      const request = new AbortController(); controller = request;
      const timeout = setTimeout(() => request.abort(), 15000);
      list.replaceChildren(); panel.hidden = false; retry.hidden = true;
      panel.setAttribute('aria-busy', 'true'); status.textContent = 'Ищем похожее оборудование…';
      try {
        const body = new FormData(); body.append('file', image, 'equipment.jpg');
        let response;
        for (let attempt = 0; attempt < 3; attempt++) {
          response = await fetch('/api/equipment/similar', {method:'POST', body, signal:request.signal, cache:'no-store'});
          if (response.ok) break;
          const data = await response.json().catch(() => ({}));
          if (token !== generation) return;
          if (data.code !== 'visual_search_busy' || attempt === 2) {
            throw new Error(data.error || 'Поиск похожего оборудования временно недоступен.');
          }
          await new Promise(resolve => setTimeout(resolve, 1200));
          if (token !== generation || request.signal.aborted) return;
        }
        const data = await response.json();
        if (token !== generation) return;
        render(data);
      } catch (error) {
        if (token !== generation) return;
        status.textContent = error.name === 'AbortError' ? 'Поиск не ответил вовремя. Повторите попытку.' : error.message;
        retry.hidden = false;
      } finally {
        clearTimeout(timeout);
        if (token === generation) { controller = null; panel.removeAttribute('aria-busy'); }
      }
    }
    async function search(canvas, key) {
      if (key && key === lastKey) return;
      reset(); lastKey = key;
      const token = generation;
      const image = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .85));
      if (!image || token !== generation) return;
      lastImage = image;
      await run(image);
    }
    retry.addEventListener('click', () => { if (lastImage) run(lastImage); });
    window.addEventListener('pagehide', reset);
    return {search, reset};
  }};
})();
