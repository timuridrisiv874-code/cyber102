// One local VLM request compares a sequence; the original video stays on this device.
(() => {
  const FRAME_COUNT = 8;
  const MAX_BATCH_BYTES = 8 * 1024 * 1024;
  const MAX_FRAME_BYTES = 3 * 1024 * 1024;
  const COMPONENTS = { pump: 'Корпус насоса', motor: 'Электродвигатель', coupling: 'Муфта или кожух',
    pipe: 'Трубопровод или фланец', bearing: 'Подшипниковый узел', valve: 'Запорная арматура',
    terminal: 'Клеммная коробка', other: 'Другое оборудование', none: 'Узел не определён' };
  const CONDITIONS = { corrosion: 'Видимая коррозия', wet_patch: 'Видимые следы жидкости',
    visible_damage: 'Видимое повреждение', no_visible_defect: 'Явных внешних дефектов не замечено',
    uncertain: 'Недостаточно данных' };
  const DETAIL_NAMES = { crack: 'Признаки трещины', terminal_damage: 'Признаки повреждения клеммы или проводки' };
  const DETAIL_COLORS = {crack: '#f87171', terminal_damage: '#65dce3'};
  const aborted = () => new DOMException('Операция отменена', 'AbortError');
  const text = (tag, className, value) => {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = value;
    return element;
  };
  const decodedSize = encoded => Math.floor(encoded.length * 3 / 4) - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0);

  // A run describes successive sampled frames, not the identity or duration of a physical defect.
  function sampledEpisodes(rows) {
    const episodes = [];
    for (const row of rows) {
      const previous = episodes.at(-1);
      if (previous && previous.at(-1).frameIndex + 1 === row.frameIndex) previous.push(row);
      else episodes.push([row]);
    }
    return episodes;
  }

  function groupObservations(observations) {
    if (!Array.isArray(observations)) throw new Error('Ожидается список наблюдений.');
    const seen = new Set();
    const ordered = observations.map(row => {
      if (!row || !Number.isInteger(row.frameIndex) || row.frameIndex < 0 || seen.has(row.frameIndex)
          || !Number.isFinite(row.timestampSeconds) || row.timestampSeconds < 0) {
        throw new Error('Некорректные позиции наблюдений.');
      }
      seen.add(row.frameIndex);
      return row;
    }).sort((a, b) => a.timestampSeconds - b.timestampSeconds || a.frameIndex - b.frameIndex);
    const grouped = new Map();
    const clearFrames = [], uncertainFrames = [];
    for (const row of ordered) {
      const componentKnown = Object.prototype.hasOwnProperty.call(COMPONENTS, row.component) && row.component !== 'none';
      if (componentKnown && row.status === 'defect' && ['corrosion', 'wet_patch', 'visible_damage'].includes(row.condition)) {
        const key = `${row.component}:${row.condition}`;
        if (!grouped.has(key)) grouped.set(key, { component: row.component, condition: row.condition, observations: [] });
        grouped.get(key).observations.push(row);
      } else if (componentKnown && row.status === 'clear' && row.condition === 'no_visible_defect') clearFrames.push(row);
      else uncertainFrames.push(row);
    }
    const groups = [...grouped.values()].map(group => ({...group, episodes: sampledEpisodes(group.observations)}));
    const clearCount = clearFrames.length, uncertainCount = uncertainFrames.length;
    const mode = !ordered.length ? 'empty' : groups.length ? 'defects' : clearCount && !uncertainCount ? 'clear' : clearCount ? 'partial' : 'uncertain';
    return { mode, groups, clearFrames, uncertainFrames, clearCount, uncertainCount, frameCount: ordered.length };
  }

  function summarize(grouped, detailKinds = []) {
    const names = {corrosion: 'коррозия', wet_patch: 'следы жидкости', visible_damage: 'видимые повреждения',
      crack: 'признаки трещины', terminal_damage: 'признаки повреждения клеммы или проводки'};
    const kinds = new Set(detailKinds);
    const findings = [...new Set(grouped.groups.map(group => names[group.condition]))];
    for (const kind of ['crack', 'terminal_damage']) if (kinds.has(kind)) findings.push(names[kind]);
    if (!findings.length) {
      if (grouped.mode === 'empty') return 'Не удалось проверить видео. Повторите попытку.';
      if (grouped.mode === 'uncertain') return 'Не удалось оценить состояние. Снимите оборудование крупнее и при хорошем освещении.';
      if (grouped.mode === 'partial') return 'Часть видео не удалось оценить. Снимите эти участки крупнее.';
      return 'На проверенных кадрах видимых дефектов не обнаружено.';
    }
    const list = new Intl.ListFormat('ru', {type: 'conjunction'}).format(findings);
    const summary = list[0].toLocaleUpperCase('ru') + list.slice(1) + '.';
    return grouped.uncertainCount ? `${summary} Часть видео не удалось оценить. Снимите эти участки крупнее.` : summary;
  }

  function observationText(value, heading, row = {}) {
    if (typeof value !== 'string') return '';
    const normalized = text => text.toLocaleLowerCase('ru').replace(/[^а-яёa-z0-9]+/gu, ' ').trim();
    if (normalized(value) === normalized(heading)) return '';
    const component = row.component === 'other' ? 'Другой промышленный узел' : COMPONENTS[row.component];
    if (component && CONDITIONS[row.condition] &&
        normalized(value) === normalized(`${component}: ${CONDITIONS[row.condition]}`)) return '';
    return value.trim();
  }

  function evidenceTime(seconds) {
    const ms = Math.round(seconds * 1000);
    const fraction = String(ms % 1000).padStart(3, '0').replace(/0+$/, '');
    return `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}${fraction ? '.' + fraction : ''}`;
  }

  // One frame can contain an overview observation and several more precise findings.
  // Keep those labels and boxes together; a change in findings starts another slide.
  function galleryEpisodes(grouped, observations, detailBoxes) {
    const overview = new Map();
    for (const group of grouped.groups) {
      for (const row of group.observations) overview.set(row.frameIndex, group);
    }
    const episodes = [];
    let previousKey = null;
    for (const row of observations) {
      const group = overview.get(row.frameIndex);
      const kinds = [...new Set((detailBoxes.get(row.frameIndex) || []).map(item => item.kind))].sort();
      if (!group && !kinds.length) continue;
      const finding = group ? row : {...row, observation: '', box: null};
      const key = [group?.component, group?.condition, ...kinds].join('/');
      const previous = episodes.at(-1);
      if (key === previousKey && previous?.rows.at(-1).frameIndex + 1 === row.frameIndex) {
        previous.rows.push(finding);
      } else {
        const name = group ? `${COMPONENTS[group.component]} · ${CONDITIONS[group.condition]}` : 'Уточнённые признаки';
        episodes.push({name, detailKinds: kinds, rows: [finding]});
      }
      previousKey = key;
    }
    return episodes;
  }

  function createGallery(cards, canChange) {
    const gallery = document.createElement('section'); gallery.className = 'video-review-gallery';
    gallery.setAttribute('role', 'region');
    gallery.setAttribute('aria-roledescription', 'карусель');
    gallery.setAttribute('aria-label', 'Находки на видео');
    const host = document.createElement('div'); host.className = 'video-review-slide-host';
    cards.forEach((card, index) => card.setAttribute('aria-label', `${index + 1} из ${cards.length}`));
    host.append(cards[0]);
    if (cards.length === 1) { gallery.append(host); return gallery; }
    gallery.tabIndex = 0;
    const controls = document.createElement('div'); controls.className = 'video-review-gallery-nav';
    const previous = text('button', 'video-review-prev', '←'), next = text('button', 'video-review-next', '→');
    const position = text('span', 'video-review-position', '');
    position.setAttribute('aria-live', 'polite'); position.setAttribute('aria-atomic', 'true');
    previous.type = next.type = 'button';
    previous.setAttribute('aria-label', 'Предыдущая находка'); next.setAttribute('aria-label', 'Следующая находка');
    controls.append(previous, position, next); gallery.append(controls, host);
    let current = 0;
    function update() {
      position.textContent = `${current + 1} из ${cards.length}`;
      for (const [button, atEnd] of [[previous, current === 0], [next, current === cards.length - 1]]) {
        button.dataset.galleryBoundary = String(atEnd);
        button.disabled = atEnd;
      }
    }
    function move(delta) {
      const index = current + delta;
      if (index < 0 || index >= cards.length || !canChange()) return;
      const focused = document.activeElement;
      current = index;
      const slideHadFocus = host.contains(focused);
      host.replaceChildren(cards[index]); update();
      if (slideHadFocus || (focused === next && next.disabled) || (focused === previous && previous.disabled)) {
        gallery.focus({preventScroll:true});
      }
    }
    previous.addEventListener('click', () => move(-1)); next.addEventListener('click', () => move(1));
    gallery.addEventListener('keydown', event => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1);
      }
    });
    let start = null, suppressClick = false;
    host.addEventListener('pointerdown', event => {
      start = null; suppressClick = false;
      if (!event.isPrimary || event.button !== 0 || !canChange()) return;
      start = {id:event.pointerId, x:event.clientX, y:event.clientY};
    });
    host.addEventListener('pointercancel', () => { start = null; });
    host.addEventListener('pointerup', event => {
      if (!start || event.pointerId !== start.id) return;
      const dx = event.clientX - start.x, dy = event.clientY - start.y;
      start = null;
      if (Math.abs(dx) < 44 || Math.abs(dx) < 1.5 * Math.abs(dy)) return;
      suppressClick = true;
      move(dx < 0 ? 1 : -1);
    });
    host.addEventListener('click', event => {
      if (suppressClick && event.detail) {
        event.preventDefault(); event.stopImmediatePropagation(); suppressClick = false;
      }
    }, true);
    update();
    return gallery;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { groupObservations, summarize, observationText, evidenceTime, galleryEpisodes };
  if (typeof window === 'undefined') return;

  window.CyberVideoReview = {
    groupObservations, summarize, evidenceTime, components: COMPONENTS, conditions: CONDITIONS,
    detailNames: DETAIL_NAMES,
    create({ media, beforeStart = () => {}, onBusyChange = () => {}, onSelectFrame = beforeStart,
             onInspectFrame, onUseOverview, isInspecting = () => false }) {
      const panel = document.getElementById('video-review-panel');
      const runButton = document.getElementById('video-review-run');
      const cancelButton = document.getElementById('video-review-cancel');
      const status = document.getElementById('video-review-status');
      const results = document.getElementById('video-review-results');
      let busy = false, generation = 0, controller = null, seekController = null, sourceVersion = media.version;
      let available = true;
      const idleMessage = () => available ? ''
        : 'Проверка видео временно недоступна. Можно проверить отдельный кадр.';
      const ready = () => media.kind === 'uploaded_video' && media.ready && !media.loading;
      const message = (value, error = false) => {
        if (!status) return;
        status.textContent = value;
        status.hidden = media.kind !== 'uploaded_video' || !value;
        status.classList.toggle('is-error', error);
      };
      const active = token => token === generation && sourceVersion === media.version;
      function syncButtons() {
        const videoSelected = media.kind === 'uploaded_video';
        if (panel) panel.hidden = !videoSelected;
        if (runButton) runButton.hidden = !videoSelected;
        if (status) status.hidden = !videoSelected || !status.textContent;
        const blocked = busy || isInspecting() || !ready() || !available;
        if (runButton) runButton.disabled = blocked;
        if (results && !results.hidden) {
          for (const button of results.querySelectorAll('button')) {
            button.disabled = blocked || button.dataset.galleryBoundary === 'true'
              || Boolean(seekController && button.classList.contains('video-review-inspect'));
          }
        }
        if (cancelButton) cancelButton.hidden = !busy;
        panel?.setAttribute('aria-busy', String(busy));
      }
      function stop(clear, announce = true) {
        const wasBusy = busy;
        seekController?.abort(); seekController = null;
        if (!clear && !wasBusy) { syncButtons(); return; }
        generation += 1;
        controller?.abort(); controller = null; busy = false;
        if (clear && results) { results.replaceChildren(); results.hidden = true; }
        if (clear) message(idleMessage());
        else if (wasBusy) message('Проверка отменена.');
        syncButtons();
        if (announce && wasBusy) onBusyChange();
      }
      const state = {
        get busy() { return busy; },
        update() {
          if (sourceVersion !== media.version) {
            sourceVersion = media.version;
            stop(true, false);
          }
          syncButtons();
        },
        cancel() { stop(false); },
        reset() { sourceVersion = media.version; stop(true); },
        setAvailable(value) {
          available = Boolean(value);
          if (!available) {
            stop(true);
            message(idleMessage());
          }
          syncButtons();
        },
      };

      async function captureFrames(token, signal) {
        const video = media.element;
        const duration = video.duration;
        if (!Number.isFinite(duration) || duration <= 0 || duration > 120) throw new Error('Выберите видео длительностью до 2 минут.');
        const position = video.currentTime;
        const scale = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
        canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
        const context = canvas.getContext('2d');
        if (!context) throw new Error('Не удалось открыть видео. Попробуйте другой файл.');
        const frames = [];
        let batchBytes = 0;
        message('Подготавливаем видео…');
        for (let index = 0; index < FRAME_COUNT; index += 1) {
          if (!active(token) || signal.aborted) throw aborted();
          const timestamp = duration * (index + 0.5) / FRAME_COUNT;
          await media.seek(timestamp, signal);
          if (!active(token) || signal.aborted) throw aborted();
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          const dataUrl = canvas.toDataURL('image/jpeg', 0.78);
          if (!dataUrl.startsWith('data:image/jpeg;base64,')) throw new Error('Не удалось открыть видео. Попробуйте другой файл.');
          const image = dataUrl.slice(dataUrl.indexOf(',') + 1);
          const bytes = decodedSize(image);
          batchBytes += bytes;
          if (!bytes || bytes > MAX_FRAME_BYTES || batchBytes > MAX_BATCH_BYTES) throw new Error('Кадры получились слишком большими. Выберите видео меньшего разрешения.');
          frames.push({ image, timestampSeconds: timestamp, dataUrl, width: canvas.width, height: canvas.height });
        }
        if (active(token) && !signal.aborted) await media.seek(position, signal);
        return { frames, durationSeconds: duration };
      }

      async function thumbnail(frame, observation, token, verifiedBoxes = []) {
        const image = new Image();
        image.src = frame.dataUrl;
        await image.decode();
        if (!active(token)) throw aborted();
        const canvas = document.createElement('canvas');
        canvas.width = frame.width; canvas.height = frame.height;
        const context = canvas.getContext('2d');
        if (!context) return frame.dataUrl;
        context.drawImage(image, 0, 0);
        const box = observation.box;
        if (observation.status !== 'unusable' && Array.isArray(box) && box.length === 4
            && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0
            && box[2] <= 1000 && box[3] <= 1000 && box[2] > box[0] && box[3] > box[1]) {
          context.lineWidth = Math.max(3, canvas.width / 220);
          context.strokeStyle = observation.status === 'defect' ? '#f6ad55' : '#6ee7b7';
          context.strokeRect(box[0] * canvas.width / 1000, box[1] * canvas.height / 1000,
            (box[2] - box[0]) * canvas.width / 1000, (box[3] - box[1]) * canvas.height / 1000);
        }
        context.lineWidth = Math.max(3, canvas.width / 220);
        for (const {box, kind} of verifiedBoxes) {
          const [left, top, right, bottom] = box;
          context.strokeStyle = DETAIL_COLORS[kind];
          context.strokeRect(left * canvas.width / 1000, top * canvas.height / 1000,
            (right - left) * canvas.width / 1000, (bottom - top) * canvas.height / 1000);
        }
        return canvas.toDataURL('image/jpeg', 0.85);
      }

      async function renderResult(data, captured, token) {
        if (Array.isArray(data?.observations) && !data.observations.length) throw new Error('Не удалось получить результат. Повторите проверку.');
        if (!data || typeof data.summary !== 'string' || typeof data.model !== 'string'
            || !Array.isArray(data.observations) || data.observations.length !== captured.frames.length) {
          throw new Error('Не удалось получить полный результат. Повторите проверку.');
        }
        for (let index = 0; index < data.observations.length; index += 1) {
          const observation = data.observations[index];
          const frame = captured.frames[index];
          if (!observation || observation.frameIndex !== index || observation.timestampSeconds !== frame.timestampSeconds) {
            throw new Error('Не удалось сопоставить результат с видео. Повторите проверку.');
          }
        }
        const grouped = groupObservations(data.observations);
        const fragment = document.createDocumentFragment();
        const summary = text('p', 'video-review-summary', summarize(grouped));
        fragment.append(summary);
        const context = data.equipmentContext;
        if (context?.references?.length) {
          const sources = document.createElement('details');
          sources.append(text('summary', '', 'Документы · ' + context.serial));
          for (const item of context.references) {
            const url = item.source?.url;
            const local = typeof url === 'string' && /^\/api\/customer\/documents\/[a-z0-9-]+\/pdf#page=\d+$/.test(url);
            const external = item.applicability === 'technical_reference' && /^https:\/\/[a-z0-9.-]+\//i.test(url);
            if (!local && !external) continue;
            const suffix = local ? ' · стр. ' + item.source.page : ' · ' + item.source.publisher;
            const link = text('a', 'source-link', item.title + suffix);
            link.href = url; link.target = '_blank'; link.rel = 'noopener';
            const row = document.createElement('p');
            row.append(link); sources.append(row);
          }
          fragment.append(sources);
        }
        const representative = data.representativeFrame;
        if (onUseOverview && representative) {
          const index = representative.media?.frameIndex;
          const frame = Number.isInteger(index) && captured.frames[index];
          if (!frame || representative.media.captureSource !== 'uploaded_video' ||
              representative.media.videoTimeSeconds !== frame.timestampSeconds ||
              !['defect', 'clear'].includes(representative.status) ||
              representative.status !== data.observations[index].status || !representative.analysisId ||
              representative.videoReview?.sampledFrameCount !== captured.frames.length) {
            throw new Error('Не удалось подготовить осмотр. Повторите проверку.');
          }
          const image = new Image();
          image.src = frame.dataUrl;
          await image.decode();
          if (!active(token)) throw aborted();
          const use = text('button', 'button primary', 'Оформить видеоосмотр');
          use.type = 'button';
          use.addEventListener('click', () => {
            if (!active(token) || busy || isInspecting() || !ready()) return;
            seekController?.abort(); seekController = null;
            try { onUseOverview(representative, image); }
            catch (error) { message(error.message, true); }
          });
          fragment.append(use);
        }
        const detailBoxes = new Map();
        if (Array.isArray(data.defectDetails)) {
          const kinds = new Set();
          if (data.defectDetails.length !== captured.frames.length) throw new Error('Не удалось получить полный результат. Повторите проверку.');
          for (const [index, row] of data.defectDetails.entries()) {
            if (row?.frameIndex !== index || row.timestampSeconds !== captured.frames[index].timestampSeconds
                || !Array.isArray(row.boxes)) {
              throw new Error('Не удалось сопоставить результат с видео. Повторите проверку.');
            }
            const boxes = row.boxes.filter(item => Object.hasOwn(DETAIL_NAMES, item.kind));
            if (boxes.length) detailBoxes.set(index, boxes);
            for (const item of boxes) kinds.add(item.kind);
          }
          if (kinds.size) summary.textContent = summarize(grouped, kinds);
        }
        const previews = new Map();
        function preview(row) {
          if (!previews.has(row.frameIndex)) {
            const boxes = detailBoxes.get(row.frameIndex) || [];
            previews.set(row.frameIndex, thumbnail(captured.frames[row.frameIndex], row, token, boxes));
          }
          return previews.get(row.frameIndex);
        }
        async function navigate(row, showSelected, inspect = false) {
          if (!active(token) || busy || isInspecting() || !ready()) return;
          if (inspect && seekController) return;
          seekController?.abort();
          const navigation = new AbortController(); seekController = navigation;
          syncButtons();
          try {
            onSelectFrame();
            if (!active(token) || navigation.signal.aborted || isInspecting()) return;
            const frame = captured.frames[row.frameIndex];
            const [url] = await Promise.all([showSelected ? preview(row) : null, media.seek(frame.timestampSeconds, navigation.signal)]);
            if (!active(token) || navigation.signal.aborted || busy || isInspecting() || !ready()) return;
            if (Math.abs(media.element.currentTime - frame.timestampSeconds) >= 0.1) {
              message('Позиция видео изменилась. Откройте выбранный момент ещё раз.', true);
              return;
            }
            showSelected?.(row, url);
            if (inspect) await onInspectFrame();
          } catch (error) {
            if (active(token) && !navigation.signal.aborted && error.name !== 'AbortError') message(error.message, true);
          } finally {
            if (seekController === navigation) { seekController = null; syncButtons(); }
          }
        }
        function timeButton(row, select, label) {
          const time = evidenceTime(row.timestampSeconds);
          const button = text('button', 'video-review-time-link', time);
          button.type = 'button';
          button.setAttribute('aria-label', `${label}. Открыть кадр ${time}`);
          button.addEventListener('click', () => select(row));
          return button;
        }
        function inspectButton(getSelected) {
          const button = text('button', 'button secondary video-review-inspect', 'Уточнить кадр');
          button.type = 'button';
          button.addEventListener('click', () => {
            const row = getSelected();
            if (row) navigate(row, null, true);
          });
          return button;
        }
        async function episodeCard({name, detailKinds, rows}) {
          const card = document.createElement('article'); card.className = 'video-review-slide video-review-frame';
          card.setAttribute('role', 'group');
          card.setAttribute('aria-roledescription', 'слайд');
          card.append(text('h4', 'video-review-slide-title video-review-finding', name));
          for (const kind of detailKinds) {
            const item = text('p', 'video-review-detail-label', DETAIL_NAMES[kind]);
            const swatch = text('span', '', '■ ');
            swatch.style.color = DETAIL_COLORS[kind]; swatch.setAttribute('aria-hidden', 'true');
            item.prepend(swatch); card.append(item);
          }
          const seekButton = document.createElement('button'); seekButton.type = 'button'; seekButton.className = 'video-review-seek';
          const image = document.createElement('img'); image.draggable = false;
          const caption = text('span', 'video-review-time', '');
          caption.hidden = rows.length > 1;
          const observation = text('p', 'video-review-observation', '');
          seekButton.append(image, caption);
          card.append(seekButton, observation);
          let selected = rows[0];
          const buttons = new Map();
          function showSelected(row, url) {
            selected = row;
            const time = evidenceTime(row.timestampSeconds);
            const frame = captured.frames[row.frameIndex];
            image.src = url; image.alt = `${name} · ${time}`;
            image.width = frame.width; image.height = frame.height;
            caption.textContent = time;
            seekButton.setAttribute('aria-label', `Открыть кадр ${time} в видео. ${name}`);
            observation.textContent = observationText(row.observation, name, row);
            observation.hidden = !observation.textContent;
            for (const [index, button] of buttons) {
              if (index === row.frameIndex) button.setAttribute('aria-current', 'true');
              else button.removeAttribute('aria-current');
            }
          }
          seekButton.addEventListener('click', () => navigate(selected));
          if (rows.length > 1) {
            const times = document.createElement('div'); times.className = 'video-review-times';
            for (const row of rows) {
              const button = timeButton(row, next => navigate(next, showSelected), name);
              buttons.set(row.frameIndex, button); times.append(button);
            }
            card.append(times);
          }
          if (onInspectFrame) card.append(inspectButton(() => selected));
          showSelected(selected, await preview(selected));
          return card;
        }
        const slides = galleryEpisodes(grouped, data.observations, detailBoxes);
        if (slides.length) {
          const cards = await Promise.all(slides.map(episodeCard));
          fragment.append(createGallery(cards, () => active(token) && ready() && !busy && !isInspecting()));
        }
        const clear = grouped.clearFrames.filter(row => !detailBoxes.has(row.frameIndex));
        const uncertain = grouped.uncertainFrames.filter(row => !detailBoxes.has(row.frameIndex));
        if (clear.length || uncertain.length) {
          const other = document.createElement('details'); other.className = 'video-review-more video-review-evidence';
          const label = clear.length && uncertain.length ? 'Остальные кадры'
            : clear.length ? 'Без видимых дефектов' : 'Нужно переснять';
          other.append(text('summary', '', label));
          const otherButtons = [];
          let selected = null;
          const inspect = onInspectFrame ? inspectButton(() => selected) : null;
          if (inspect) inspect.hidden = true;
          const selectedText = text('p', 'video-review-observation', '');
          selectedText.hidden = true;
          selectedText.setAttribute('aria-live', 'polite');
          for (const [label, rows] of [['Без видимых дефектов', clear], ['Нужно переснять', uncertain]]) {
            if (!rows.length) continue;
            const line = document.createElement('div'); line.className = 'video-review-times';
            if (clear.length && uncertain.length) line.append(text('span', 'video-review-episode-label', label));
            for (const row of rows) {
              const button = timeButton(row, next => navigate(next, () => {
                selected = next;
                if (inspect) inspect.hidden = false;
                for (const item of otherButtons) item.removeAttribute('aria-current');
                button.setAttribute('aria-current', 'true');
                selectedText.textContent = observationText(next.observation, CONDITIONS[next.condition] || label, next);
                selectedText.hidden = !selectedText.textContent;
              }), label);
              otherButtons.push(button); line.append(button);
            }
            other.append(line);
          }
          other.append(selectedText);
          if (inspect) other.append(inspect);
          fragment.append(other);
        }
        if (!active(token)) throw aborted();
        results.replaceChildren(fragment); results.hidden = false;
      }

      async function run() {
        if (busy || isInspecting() || !ready() || !available || !results) return;
        seekController?.abort(); seekController = null;
        beforeStart();
        if (!ready()) return;
        sourceVersion = media.version;
        const token = ++generation;
        controller = new AbortController();
        const signal = controller.signal;
        busy = true; results.replaceChildren(); results.hidden = true;
        syncButtons(); onBusyChange();
        let timer, timedOut = false;
        try {
          const captured = await captureFrames(token, signal);
          if (!active(token) || signal.aborted) throw aborted();
          message('Проверяем видео…');
          timer = setTimeout(() => { timedOut = true; controller?.abort(); }, 13 * 60 * 1000);
          const request = window.CyberAnalysisJobs?.request || fetch;
          const serial = document.getElementById('active-equipment')?.value.trim();
          const response = await request('/api/video-review', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
            cache: 'no-store', redirect: 'error', signal,
            body: JSON.stringify({ durationSeconds: captured.durationSeconds,
              ...(serial ? {equipmentSerial: serial} : {}),
              frames: captured.frames.map(({ image, timestampSeconds }) => ({ image, timestampSeconds })) }),
          }, value => { if (active(token) && !signal.aborted) message(value); });
          const data = await response.json();
          clearTimeout(timer);
          if (!active(token) || signal.aborted) throw aborted();
          if (!response.ok) throw new Error(data.code === 'vision_busy'
            ? 'Сервис занят. Повторите через несколько секунд.'
            : typeof data.error === 'string' ? data.error : 'Не удалось разобрать видео. Повторите попытку.');
          await renderResult(data, captured, token);
          if (active(token)) message('');
        } catch (error) {
          if (active(token)) message(timedOut ? 'Проверка заняла слишком много времени. Повторите позже.'
            : error.name === 'AbortError' ? 'Проверка отменена.'
            : error.name === 'TypeError' ? 'Не удалось связаться с сервисом. Проверьте подключение и повторите попытку.'
            : error.name === 'SyntaxError' ? 'Не удалось получить результат. Повторите проверку.'
            : error.message || 'Не удалось проверить видео. Повторите попытку.', error.name !== 'AbortError' || timedOut);
        } finally {
          clearTimeout(timer);
          if (active(token)) { busy = false; controller = null; syncButtons(); onBusyChange(); }
        }
      }
      runButton?.addEventListener('click', run);
      cancelButton?.addEventListener('click', state.cancel);
      window.addEventListener('pagehide', state.cancel);
      message(idleMessage());
      state.update();
      return state;
    },
  };
})();
