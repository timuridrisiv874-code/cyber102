/* Shared navigation and selected-equipment context for the web workspace. */
(() => {
  'use strict';
  const active = document.getElementById('active-equipment');
  const inspection = document.getElementById('inspection-serial');
  const lookup = document.getElementById('serial');
  const selected = document.getElementById('shell-selected-equipment');
  const hint = document.getElementById('shell-equipment-hint');
  const workspacePages = new Set(['/', '/index.html', '/documents.html', '/checkpoint.html', '/materials.html', '/telemetry.html']);
  let syncing = false;
  let serial = inspection?.value || lookup?.value || new URLSearchParams(location.search).get('serial') || '';

  function updateContext(value) {
    serial = value.trim();
    if (selected) selected.textContent = serial === 'P-102' ? 'P-102 · демонстрация' : serial ? `Оборудование · ${serial}` : 'Оборудование не выбрано';
    if (hint) hint.textContent = serial === 'P-102' ? 'Демонстрационные данные. Сверьте номер агрегата.' : 'Сверьте номер с табличкой перед осмотром.';
    for (const link of document.querySelectorAll('a[href]')) {
      const href = link.getAttribute('href');
      if (href.startsWith('#')) continue;
      const url = new URL(href, location.href);
      // Keep context throughout the workspace, including links inside page content.
      // Original PDFs and external resources retain their original addresses.
      if (url.origin !== location.origin || !workspacePages.has(url.pathname)) continue;
      if (serial) url.searchParams.set('serial', serial); else url.searchParams.delete('serial');
      link.href = `${url.pathname}${url.search}${url.hash}`;
    }
  }
  function sync(source, target, event) {
    if (syncing) return;
    syncing = true;
    try {
      if (target) {
        target.value = source.value;
        target.dispatchEvent(new Event(event.type, { bubbles: true }));
      }
      updateContext(source.value);
    } finally { syncing = false; }
  }
  if (active && inspection) {
    active.value = inspection.value;
    for (const type of ['input', 'change']) {
      active.addEventListener(type, event => sync(active, inspection, event));
      inspection.addEventListener(type, event => sync(inspection, active, event));
    }
    const fields = document.getElementById('inspection-fields');
    if (fields) {
      const updateDisabled = () => { active.disabled = fields.disabled || inspection.disabled; };
      new MutationObserver(updateDisabled).observe(fields, { attributes: true, subtree: true, attributeFilter: ['disabled'] });
      updateDisabled();
    }
  }
  if (lookup) for (const type of ['input', 'change']) lookup.addEventListener(type, () => updateContext(lookup.value));
  updateContext(serial);
})();
