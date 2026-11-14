'use strict';
(() => {
  if (window.CyberWebBoot?.blocked) return;
  const $ = id => document.getElementById(id);
  let documents = [], equipment = [], currentPassports = {}, catalogReady = false;
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
      details.append(list); card.append(details);
      if (item.serial && (item.kind === 'pump' || item.kind === 'motor')) {
        const action = item.kind === 'motor' ? 'Рассчитать ресурс двигателя' : 'Начать осмотр';
        const inspect = node('a', action, 'text-link');
        inspect.href = `index.html?${new URLSearchParams({serial: item.serial})}`;
        inspect.setAttribute('aria-label', `${action} ${item.serial}`);
        card.append(inspect);
      }
      const knowledge = node('button', 'Поиск по оборудованию', 'button secondary');
      knowledge.type = 'button';
      knowledge.onclick = () => selectKnowledge(item.serial);
      card.append(knowledge);
      $('equipment-grid').append(card);
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
    if (!catalogReady) return;
    const query = $('document-search').value.trim().toLocaleLowerCase('ru-RU');
    const matches = documents.map(document => ({document, pages: (document.pages || []).filter(page => query && page.text?.toLocaleLowerCase('ru-RU').includes(query))}))
      .filter(({document, pages}) => !query || `${document.title} ${document.filename}`.toLocaleLowerCase('ru-RU').includes(query) || pages.length);
    $('document-results').textContent = query ? `Найдено документов: ${matches.length} из ${documents.length}` : `${documents.length} документов`;
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
  function external(source) {
    const link = node('a', source.title || 'Источник', 'source-link');
    if (!/^https:\/\/(?:knz\.ru|am-systems\.ru)\//.test(source.url)) return node('span', link.textContent);
    link.href = source.url; link.target = '_blank'; link.rel = 'noopener';
    return link;
  }

  let knowledgeRequest;
  async function searchKnowledge() {
    knowledgeRequest?.abort();
    const request = new AbortController();
    knowledgeRequest = request;
    const selected = $('knowledge-target').value;
    if (!selected) return;
    const component = $('knowledge-component');
    const params = new URLSearchParams({q: $('knowledge-query').value.trim()});
    if (selected.startsWith('serial:')) {
      params.set('serial', selected.slice(7));
      if (component.value) params.set('component', component.value);
    } else params.set('model', selected.slice(6));
    const results = $('knowledge-results');
    results.replaceChildren(node('p', 'Поиск в документации…'));
    try {
      const response = await fetch('/api/equipment/knowledge?' + params, {signal: request.signal, cache: 'no-store'});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (request.signal.aborted) return;
      const value = component.value;
      component.replaceChildren(new Option('Основной', ''), ...(data.availableComponents || []).map(item => new Option(item.label, item.key)));
      component.value = [...component.options].some(option => option.value === value) ? value : '';
      component.disabled = !data.availableComponents?.length;
      results.replaceChildren();
      if (data.relatedEquipment?.length) {
        const line = node('div', null, 'document-item');
        line.append(node('h3', 'Компоненты паспортной группы'));
        for (const item of data.relatedEquipment) {
          const button = node('button', item.model + ' · ' + item.serial, 'button secondary');
          button.type = 'button';
          button.onclick = () => selectKnowledge(item.serial);
          line.append(button);
        }
        results.append(line);
      }
      if (data.telemetry) {
        const link = node('a', data.equipment.kind === 'pump' ? 'Открыть насос и эмулятор' : 'Открыть архив показаний', 'source-link');
        link.href = data.equipment.kind === 'pump' ? '/index.html?' + new URLSearchParams({serial: data.equipment.serial}) : data.telemetry.url;
        results.append(link, node('p', data.telemetry.message, 'workspace-note'));
      }
      if (!data.matches.length) results.append(node('p', 'По этой теме в документации выбранного оборудования совпадений нет.'));
      for (const item of data.matches) {
        const card = node('article', null, 'document-item');
        card.append(node('h3', item.title), node('p', item.text));
        card.append(item.source.documentId
          ? sourceLink(item.source.documentId, item.source.page, item.source.title + ' · стр. ' + item.source.page)
          : external(item.source));
        results.append(card);
      }
    } catch (error) {
      if (!request.signal.aborted) results.replaceChildren(node('p', 'Не удалось загрузить справку. Повторите поиск.'));
    }
  }
  function selectKnowledge(serial) {
    $('knowledge-target').value = 'serial:' + serial;
    $('knowledge-component').value = '';
    $('knowledge-panel').scrollIntoView();
    searchKnowledge();
  }
  $('knowledge-form').addEventListener('submit', event => { event.preventDefault(); searchKnowledge(); });
  $('knowledge-target').addEventListener('change', () => { $('knowledge-component').value = ''; searchKnowledge(); });
  $('knowledge-component').addEventListener('change', searchKnowledge);

  function renderKnowledge(catalog, passports) {
    $('knowledge-target').replaceChildren(...(passports?.equipment || []).map(item =>
      new Option(item.title + ' · ' + item.serial, 'serial:' + item.serial)));
    const models = $('manufacturer-models');
    models.replaceChildren();
    for (const row of catalog?.models || []) {
      $('knowledge-target').append(new Option(row.model + ' · каталог', 'model:' + row.id));
      const card = node('article', null, 'workspace-card');
      card.append(node('h3', row.model), node('p', row.typeLabel), node('p', row.manufacturer));
      const facts = node('dl', null, 'workspace-facts');
      for (const [label, value] of Object.entries(row.fields)) facts.append(node('dt', label), node('dd', value));
      if (row.photo) {
        const photo = node('img', null, 'pump-reference-photo');
        photo.src = row.photo.image; photo.alt = row.photo.title; photo.loading = 'lazy';
        card.append(photo, node('p', row.photo.title, 'workspace-note'));
      }
      card.append(facts, external(row.source), external(row.manual));
      models.append(card);
    }
    $('pump-gallery')?.remove();
    if (catalog?.photos?.length) {
      const gallery = node('details', null, 'workspace-card');
      gallery.id = 'pump-gallery';
      gallery.append(node('summary', 'Фотографии серии AMS PCP'),
                     node('p', 'Исполнение и серийные номера на фотографиях не подтверждены.', 'workspace-note'));
      for (const photo of catalog.photos) {
        const illustration = node('img', null, 'pump-reference-photo');
        illustration.src = photo.image; illustration.alt = photo.title; illustration.loading = 'lazy';
        const figure = node('figure', null, 'pump-photo-figure');
        const caption = node('figcaption');
        caption.append(external(photo));
        if (photo.originalUrl) caption.append(external({title: 'Оригинал фото', url: photo.originalUrl}));
        figure.append(illustration, caption);
        gallery.append(figure);
      }
      gallery.open = true;
      $('equipment-grid').before(gallery);
    }
    $('knowledge-panel').hidden = !$('knowledge-target').options.length;
  }

  function initialize() {
    const active = new AbortController(); controller = active;
    const current = ++generation;
    const timeout = setTimeout(() => active.abort(), 30000);
    catalogReady = false;
    $('document-count').textContent = '—';
    $('document-results').textContent = '';
    $('document-search').disabled = true;
    $('equipment-kind').disabled = true;
    $('document-list').replaceChildren();
    $('documents-status').textContent = 'Загружаем документы…';
    $('documents-status').dataset.error = 'false';
    Promise.allSettled(['/api/customer/documents', '/api/customer/equipment-passports', '/api/equipment/models'].map(async url => {
    const response = await fetch(url, {signal: active.signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Документы недоступны.');
    return data;
  })).then(([catalogResult, passportsResult, knowledgeResult]) => {
    if (current !== generation) return;
    const catalog = catalogResult.status === 'fulfilled' && Array.isArray(catalogResult.value?.documents) ? catalogResult.value : null;
    const passports = passportsResult.status === 'fulfilled' && Array.isArray(passportsResult.value?.equipment) ? passportsResult.value : null;
    catalogReady = Boolean(catalog);
    documents = catalog?.documents || [];
    const kindTitles = {pump:'Насосы',motor:'Электродвигатели',gearbox:'Редукторы',temperature_transmitter:'Преобразователи температуры'};
    $('equipment-kind').replaceChildren();
    for (const kind of [...new Set((passports?.equipment || []).map(item => item.kind))]) {
      const option = node('option', kindTitles[kind] || kind); option.value = kind; $('equipment-kind').append(option);
    }
    const all = node('option', 'Все компоненты'); all.value = 'all'; $('equipment-kind').append(all);
    $('equipment-kind').value = (passports?.equipment || []).some(item => item.kind === 'pump') ? 'pump' : 'all';
    if (passports) renderEquipment(passports);
    else {
      $('equipment-grid').replaceChildren(node('p', 'Паспортные данные не загрузились.'));
      $('passport-limitations').replaceChildren(); $('requirements-panel').hidden = true;
    }
    renderKnowledge(knowledgeResult.status === 'fulfilled' ? knowledgeResult.value : null, passports);
    $('equipment-kind').disabled = !passports;
    renderDocuments();
    $('document-count').textContent = catalogReady ? `${documents.length} PDF` : 'Недоступно';
    $('document-search').disabled = !catalogReady;
    const problems = [!catalog && 'Библиотека временно недоступна.', !passports && 'Паспортные записи временно недоступны.'].filter(Boolean);
    $('documents-status').textContent = problems.join(' ');
    $('documents-status').dataset.error = String(Boolean(problems.length));
    loaded = catalogReady && Boolean(passports);
  }).catch(error => {
    if (current !== generation) return;
    $('document-count').textContent = 'Недоступно';
    $('documents-status').textContent = error.name === 'AbortError' ? 'Загрузка остановлена. Обновите страницу.' : 'Не удалось загрузить документы. Обновите страницу.';
    $('documents-status').dataset.error = 'true';
  }).finally(() => { clearTimeout(timeout); if (current === generation) controller = null; });
  }
  initialize();
})();
