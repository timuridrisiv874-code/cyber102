/* A standalone HTML file has no application API. Keep navigation explicit. */
(() => {
  'use strict';
  const ORIGIN = 'https://cyber102.94.41.87.102.sslip.io';
  const PAGES = new Set(['index.html', 'documents.html', 'checkpoint.html', 'materials.html', 'telemetry.html']);
  function applicationUrl(href) {
    const source = new URL(href);
    const page = source.pathname.split('/').pop();
    const target = new URL(PAGES.has(page) ? `/${page}` : '/', ORIGIN);
    const serial = source.searchParams.get('serial')?.trim();
    if (serial && serial.length <= 128 && !/[\u0000-\u001f\u007f]/.test(serial)) target.searchParams.set('serial', serial);
    return target.href;
  }
  function requiresServer(protocol, offline = false) { return protocol === 'file:' && !offline; }
  if (typeof module !== 'undefined' && module.exports) module.exports = {applicationUrl, requiresServer};
  if (typeof window === 'undefined') return;
  const blocked = requiresServer(location.protocol, window.CyberOffline?.enabled === true);
  window.CyberWebBoot = {blocked};
  if (!blocked) return;
  const main = document.createElement('main');
  main.className = 'local-file-screen'; main.id = 'main-content';
  const brand = document.createElement('p'); brand.className = 'local-file-brand'; brand.textContent = 'Cyber102';
  const title = document.createElement('h1'); title.textContent = 'Это локальная копия';
  const message = document.createElement('p'); message.textContent = 'Данные и анализ доступны в приложении.';
  const link = document.createElement('a');
  link.id = 'open-application'; link.className = 'button primary'; link.textContent = 'Открыть приложение';
  link.href = applicationUrl(location.href);
  main.append(brand, title, message, link);
  document.body.classList.add('local-file-page');
  document.body.replaceChildren(main);
})();
