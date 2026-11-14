'use strict';
(() => {
  if (window.CyberWebBoot?.blocked) return;
  const $ = id => document.getElementById(id);
  let documents = [], currentPassports = {}, catalogReady = false, knowledgeReady = false;
  const equipmentPhotos = new Map([
    ['AMS PCP60S12/3 01 Ex B1 HNBR DTS', {
      image: 'ams-pcp-oil.jpg', title: 'Фото серии AMS PCP',
      url: 'https://am-systems.ru/nefteprodukty-i-nefteshlamy/', credit: 'АМС',
    }],
    ['BRA160M4 FБУ1', {
      image: 'eldin-ba-bra.jpg', title: 'Каталожное фото серии ЭЛДИН ВА / ВРА',
      url: 'https://uralenergotel.ru/product/ba160sa6', credit: 'УралЭнергоТел',
    }],
    ['WXRF87-AM160-5.30-M1-Ø300', {
      image: 'wanshsin-wxr.jpg', title: 'Каталожный вид мотор-редуктора WANSHSIN WXR',
      url: 'https://www.wanshsin.com/hyzyjsj/511.html', credit: 'WANSHSIN',
    }],
    ['ПТ 0304Ex-ВТ', {
      image: 'elemer-pt0304.jpg', title: 'Каталожное фото исполнений ЭЛЕМЕР ПТ 0304-ВТ',
      url: 'https://manometry.ru/pt-0304', credit: 'Манотек',
    }],
  ]);
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function targetLabel(item) {
    const kind = {pump:'Насос', motor:'Двигатель', gearbox:'Редуктор', temperature_transmitter:'Датчик температуры'}[item.kind] || 'Оборудование';
    return kind + ' · ' + item.serial;
  }
  function equipmentPhoto(photo, showTitle = false) {
    const figure = node('figure', null, 'equipment-card-photo');
    const image = node('img');
    image.src = '/equipment-photos/' + photo.image;
    image.alt = photo.title;
    image.loading = 'lazy';
    image.decoding = 'async';
    figure.append(image);
    if (showTitle) figure.append(node('figcaption', photo.title, 'workspace-note'));
    return figure;
  }
  function sourceLink(documentId, page, label) {
    if (typeof documentId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(documentId)) {
      return node('span', label || 'Источник не найден');
    }
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
    const equipment = passports.equipment || [];
    const selected = $('equipment-kind').value;
    const pumpGallery = $('pump-gallery');
    if (pumpGallery) pumpGallery.hidden = selected !== 'pump' && selected !== 'all';
    for (const item of equipment.filter(item => selected === 'all' || item.kind === selected)) {
      const card = node('article', null, 'workspace-card');
      card.dataset.equipment = item.id;
      const title = item.title || item.id;
      const modelSuffix = item.model ? ' · ' + item.model : '';
      // The model already has its own labelled field below the equipment name.
      card.append(node('h3', modelSuffix && title.endsWith(modelSuffix)
        ? title.slice(0, -modelSuffix.length) : title));
      const photo = equipmentPhotos.get(item.model);
      if (photo) card.append(equipmentPhoto(photo));
      const facts = node('dl', null, 'workspace-facts');
      for (const [title, value] of [['Модель', item.model], ['Серийный номер', item.serial]]) {
        facts.append(node('dt', title), node('dd', valueText(value)));
      }
      card.append(facts);
      const details = node('details', null, 'workspace-details');
      details.append(node('summary', 'Паспорт и характеристики'));
      const extra = node('dl', null, 'workspace-facts');
      if (item.manufacturer) extra.append(node('dt', 'Изготовитель'), node('dd', valueText(item.manufacturer)));
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
      if (photo) {
        const credit = node('p', 'Каталожное фото серии · ', 'workspace-note');
        credit.append(external({url: photo.url, title: photo.credit}));
        details.append(credit);
      }
      const actions = node('div', null, 'equipment-card-actions');
      if (item.serial && (item.kind === 'pump' || item.kind === 'motor')) {
        const action = item.kind === 'motor' ? 'Рассчитать ресурс двигателя' : 'Начать осмотр';
        const inspect = node('a', action, 'text-link');
        inspect.href = `index.html?${new URLSearchParams({serial: item.serial})}`;
        inspect.setAttribute('aria-label', `${action} ${item.serial}`);
        actions.append(inspect);
      }
      if (knowledgeReady) {
        const knowledge = node('button', 'Найти в документации', 'button secondary');
        knowledge.type = 'button';
        knowledge.onclick = () => selectKnowledge(item.serial);
        actions.append(knowledge);
      }
      if (actions.childElementCount) card.append(actions);
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
    const matches = documents.map(document => ({document, pages: (Array.isArray(document.pages) ? document.pages : [])
      .filter(page => query && typeof page?.text === 'string' && page.text.toLocaleLowerCase('ru-RU').includes(query))}))
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
  $('documents-retry').addEventListener('click', initialize);
  $('document-search').addEventListener('input', renderDocuments);
  $('equipment-kind').addEventListener('change', () => renderEquipment(currentPassports));
  const installationPhotos = [
    {
      image: 'industrial-pipes-valves.jpg', title: 'Трубопроводы на промышленной установке',
      url: 'https://commons.wikimedia.org/wiki/File:Close_view_of_pipes_and_valves_on_a_machine_in_an_industrial_setting.jpg',
      credit: 'Shixart1985 · Wikimedia Commons',
      license: {url: 'https://creativecommons.org/licenses/by/2.0/', title: 'CC BY 2.0'},
    },
    {
      image: 'gate-valve.jpg', title: 'Фланцевая задвижка',
      url: 'https://commons.wikimedia.org/wiki/File:Valve.jpg', credit: 'Heather Smith · The Alloy Valve Stockist',
      license: {url: 'https://creativecommons.org/licenses/by/3.0/', title: 'CC BY 3.0'},
    },
    {
      image: 'wika-gauge.jpg', title: 'Стрелочный манометр с трубкой Бурдона',
      url: 'https://blog.wika.com/us/products/pressure-products/eugene-bourdon-history-bourdon-gauge/', credit: 'WIKA',
    },
  ];
  $('other-equipment-photos').append(...installationPhotos.map(photo => equipmentPhoto(photo, true)));
  for (const photo of installationPhotos) {
    const credit = node('li', photo.title + ' · ');
    credit.append(external({url: photo.url, title: photo.credit}));
    if (photo.license) credit.append(document.createTextNode(' · '), external(photo.license));
    $('equipment-photo-credits').append(credit);
  }
  function external(source) {
    const link = node('a', source.title || 'Источник', 'source-link');
    if (!/^https:\/\/[a-z0-9.-]+\//i.test(source.url)) return node('span', link.textContent);
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
    } else if (selected.startsWith('model:')) params.set('model', selected.slice(6));
    else params.set('section', selected.slice(8));
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
        if (item.applicability === 'technical_reference') {
          card.append(node('p', 'Отраслевая справка · ' + item.source.publisher, 'workspace-note'));
        }
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
      new Option(targetLabel(item), 'serial:' + item.serial)));
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
    for (const section of catalog?.sections || []) {
      $('knowledge-target').append(new Option(section.title + ' · справка', 'section:' + section.id));
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
      const selected = $('equipment-kind').value;
      gallery.hidden = selected !== 'pump' && selected !== 'all';
      $('equipment-grid').after(gallery);
    }
    $('knowledge-form').hidden = !knowledgeReady;
    $('knowledge-panel').hidden = !models.childElementCount && (!knowledgeReady || !$('knowledge-target').options.length);
    $('knowledge-title').textContent = knowledgeReady ? 'База знаний оборудования' : 'Каталог насосов';
  }

  async function loadKnowledge(signal) {
    const options = {signal, cache: 'no-store'};
    try {
      const response = await fetch('/api/equipment/models', options);
      if (response.ok) return {catalog: await response.json(), searchable: true};
    } catch (error) {
      if (signal.aborted) throw error;
    }
    const fallback = await fetch('/equipment-photos/catalog.json', options);
    if (!fallback.ok) throw new Error('Фотокаталог недоступен.');
    return {catalog: await fallback.json(), searchable: false};
  }

  async function initialize() {
    if (controller) return;
    const active = new AbortController();
    controller = active;
    const current = ++generation;
    const status = $('documents-status'), retry = $('documents-retry'), picker = $('equipment-kind');
    status.textContent = 'Загружаем документы…';
    status.dataset.error = 'false';
    retry.hidden = true;
    if (!catalogReady) {
      $('document-count').textContent = '—';
      $('document-search').disabled = true;
      $('document-results').textContent = '';
    }
    if (!currentPassports.equipment) picker.disabled = true;
    async function read(url, key) {
      const response = await fetch(url, {signal: active.signal, cache: 'no-store'});
      if (!response.ok) throw new Error('Request failed');
      const data = await response.json();
      if (!Array.isArray(data?.[key])) throw new Error('Invalid catalog');
      return data;
    }
    const timeout = setTimeout(() => active.abort(), 30000);
    const render = async (request, show) => {
      const data = await request;
      if (current === generation) show(data);
    };
    const library = render(read('/api/customer/documents', 'documents'), catalog => {
      documents = catalog.documents.filter(item => typeof item?.id === 'string');
      catalogReady = true;
      $('document-count').textContent = `${documents.length} PDF`;
      $('document-search').disabled = false;
      renderDocuments();
    });
    let knowledge;
    const passports = render(read('/api/customer/equipment-passports', 'equipment'), data => {
      const kindTitles = {pump:'Насосы',motor:'Электродвигатели',gearbox:'Редукторы',temperature_transmitter:'Преобразователи температуры'};
      const previous = picker.value;
      data.equipment = data.equipment.filter(item => item && typeof item.kind === 'string');
      const kinds = [...new Set(data.equipment.map(item => item.kind))];
      picker.replaceChildren(...kinds.map(kind => new Option(kindTitles[kind] || kind, kind)),
        new Option('Все компоненты', 'all'));
      picker.value = previous === 'all' || kinds.includes(previous) ? previous : kinds.includes('pump') ? 'pump' : 'all';
      renderEquipment(data);
      picker.disabled = false;
      renderKnowledge(knowledge?.catalog, data);
    });
    const references = render(loadKnowledge(active.signal), data => {
      knowledge = data;
      knowledgeReady = data.searchable;
      renderKnowledge(data.catalog, currentPassports);
      if (currentPassports.equipment) renderEquipment(currentPassports);
    });
    const results = await Promise.allSettled([library, passports, references]);
    clearTimeout(timeout);
    if (current !== generation) return;
    controller = null;
    const problems = ['Библиотека временно недоступна.', 'Паспорта временно недоступны.', 'База знаний временно недоступна.']
      .filter((text, index) => results[index].status === 'rejected');
    if (!catalogReady) $('document-count').textContent = 'Недоступно';
    status.textContent = problems.join(' ');
    status.dataset.error = String(Boolean(problems.length));
    retry.hidden = !problems.length;
    loaded = !problems.length;
  }
  initialize();
})();
