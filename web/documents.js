'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let documents = [], equipment = [], currentPassports = {};
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function sourceLink(documentId, page, label) {
    if (!documents.some(document => document.id === documentId)) return node('span', label || 'Источник не найден');
    const link = node('a', label || `Оригинал · стр. ${page || 1}`, 'source-link');
    link.href = `/api/customer/documents/${encodeURIComponent(documentId)}/pdf#page=${Number.isInteger(page) && page > 0 ? page : 1}`;
    link.target = '_blank'; link.rel = 'noopener';
    return link;
  }
  function valueText(value) {
    if (value == null) return 'Не установлен';
    if (typeof value === 'object') {
      if ('value' in value) return `${valueText(value.value)}${value.unit ? ` ${value.unit}` : ''}`;
      if (Array.isArray(value)) return value.map(valueText).join(', ');
      return Object.entries(value).map(([key, entry]) => `${key}: ${valueText(entry)}`).join('; ');
    }
    return String(value);
  }
  function renderEquipment(passports) {
    currentPassports = passports;
    $('equipment-grid').replaceChildren();
    equipment = passports.equipment || [];
    const selected = $('equipment-kind').value;
    for (const item of equipment.filter(item => selected === 'all' || item.kind === selected)) {
      const card = node('article', null, 'workspace-card');
      card.dataset.equipment = item.id;
      card.append(node('h3', item.title || item.id));
      const facts = node('dl', null, 'workspace-facts');
      for (const [title, value] of [['Модель', item.model], ['Серийный номер', item.serial], ['Изготовитель', item.manufacturer]]) {
        facts.append(node('dt', title), node('dd', valueText(value)));
      }
      card.append(facts);
      if (item.typeLabel) card.append(node('p', item.typeLabel, 'workspace-note'));
      const details = node('details', null, 'workspace-details');
      details.append(node('summary', 'Характеристики и источники'));
      const extra = node('dl', null, 'workspace-facts');
      for (const [key, value] of Object.entries(item.fields || {})) {
        if (['serial', 'model', 'manufacturer'].includes(key)) continue;
        let display = valueText(value);
        if (Array.isArray(value?.value) && (key.endsWith('_range') || key === 'dimensions')) display = `${value.value.join(key === 'dimensions' ? ' × ' : '–')} ${value.unit || ''}`.trim();
        extra.append(node('dt', value?.label || key), node('dd', display));
      }
      details.append(extra);
      const list = node('ul');
      for (const source of item.sources || []) {
        const entry = node('li');
        entry.append(sourceLink(source.documentId, source.page, `Стр. ${source.page} · ${source.label || item.fields?.[source.field]?.label || source.field || 'паспорт'}`));
        if (source.quote) entry.append(node('p', source.quote, 'workspace-note'));
        list.append(entry);
      }
      details.append(list); card.append(details); $('equipment-grid').append(card);
    }
    if (!equipment.length) $('equipment-grid').append(node('p', 'Проверенные паспортные данные пока не добавлены.'));
    $('passport-limitations').replaceChildren();
    for (const limitation of passports.limitations || []) $('passport-limitations').append(node('p', valueText(limitation)));
    const requirements = passports.requirements || [];
    $('requirements-panel').hidden = !requirements.length;
    $('passport-requirements').replaceChildren();
    for (const item of requirements) {
      const block = node('article', null, 'document-item');
      block.append(node('h3', item.title || item.topic || 'Требование документации'));
      block.append(node('p', item.text || item.description || item.recommendation || '', 'workspace-note'));
      if (item.appliesTo) block.append(node('p', `Применимость: ${valueText(item.appliesTo)}`, 'workspace-note'));
      for (const source of item.sources || (item.source ? [item.source] : [])) block.append(sourceLink(source.documentId, source.page));
      $('passport-requirements').append(block);
    }
  }
  function renderDocuments() {
    const query = $('document-search').value.trim().toLocaleLowerCase('ru-RU');
    const matches = documents.map(document => ({document, pages: (document.pages || []).filter(page => query && page.text?.toLocaleLowerCase('ru-RU').includes(query))}))
      .filter(({document, pages}) => !query || `${document.title} ${document.filename}`.toLocaleLowerCase('ru-RU').includes(query) || pages.length);
    $('document-results').textContent = query ? `Найдено документов: ${matches.length} из ${documents.length}` : `Документов: ${documents.length}. Оригиналы доступны на сервере команды.`;
    $('document-list').replaceChildren();
    for (const {document, pages} of matches) {
      const article = node('article', null, 'document-item');
      article.append(node('h3', document.title));
      article.append(node('p', `${document.pageCount} стр. · ${(document.bytes / 1048576).toLocaleString('ru-RU', {maximumFractionDigits: 1})} МБ`, 'workspace-note'));
      article.append(sourceLink(document.id, pages[0]?.page || 1, 'Открыть PDF ↗'));
      for (const page of pages.slice(0, 3)) {
        const details = node('details');
        details.append(node('summary', `Совпадение · страница ${page.page}`));
        const offset = page.text.toLocaleLowerCase('ru-RU').indexOf(query);
        const start = Math.max(0, offset - 120), end = Math.min(page.text.length, offset + query.length + 260);
        details.append(node('p', `${start ? '…' : ''}${page.text.slice(start, end)}${end < page.text.length ? '…' : ''}`, 'document-match'));
        details.append(sourceLink(document.id, page.page)); article.append(details);
      }
      if (pages.length > 3) article.append(node('p', `Ещё совпадения на ${pages.length - 3} стр. — откройте оригинал.`, 'workspace-note'));
      $('document-list').append(article);
    }
  }
  let controller, generation = 0, loaded = false;
  window.addEventListener('pagehide', () => {
    generation++; controller?.abort(); controller = null;
    if (!loaded) $('documents-status').textContent = 'Загрузка приостановлена.';
  });
  window.addEventListener('pageshow', () => { if (!loaded && !controller) initialize(); });
  $('document-search').addEventListener('input', renderDocuments);
  $('equipment-kind').addEventListener('change', () => renderEquipment(currentPassports));
  function initialize() {
    const active = new AbortController(); controller = active;
    const current = ++generation;
    const timeout = setTimeout(() => active.abort(), 30000);
    $('documents-status').textContent = 'Загружаем документы…';
    Promise.allSettled(['/api/customer/documents', '/api/customer/equipment-passports'].map(async url => {
    const response = await fetch(url, {signal: active.signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Документы недоступны.');
    return data;
  })).then(([catalogResult, passportsResult]) => {
    if (current !== generation) return;
    const catalog = catalogResult.status === 'fulfilled' ? catalogResult.value : null;
    const passports = passportsResult.status === 'fulfilled' ? passportsResult.value : null;
    documents = Array.isArray(catalog?.documents) ? catalog.documents : [];
    const kindTitles = {pump:'Насосы',motor:'Электродвигатели',gearbox:'Редукторы',temperature_transmitter:'Преобразователи температуры'};
    $('equipment-kind').replaceChildren();
    for (const kind of [...new Set((passports?.equipment || []).map(item => item.kind))]) {
      const option = node('option', kindTitles[kind] || kind); option.value = kind; $('equipment-kind').append(option);
    }
    const all = node('option', 'Все компоненты'); all.value = 'all'; $('equipment-kind').append(all);
    $('equipment-kind').value = (passports?.equipment || []).some(item => item.kind === 'pump') ? 'pump' : 'all';
    renderEquipment(passports || {}); renderDocuments();
    $('document-count').textContent = `${documents.length} PDF`;
    const problems = [!catalog && 'Библиотека временно недоступна.', !passports && 'Паспортные записи временно недоступны.'].filter(Boolean);
    $('documents-status').textContent = `Загружено ${documents.length} документов · ${equipment.length} паспортных записей. ${problems.join(' ')}`.trim();
    loaded = true;
  }).catch(error => {
    if (current !== generation) return;
    $('documents-status').textContent = error.name === 'AbortError' ? 'Загрузка остановлена. Обновите страницу.' : error.message;
  }).finally(() => { clearTimeout(timeout); if (current === generation) controller = null; });
  }
  initialize();
})();
