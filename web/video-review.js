// One local VLM request compares a sequence; the original video stays on this device.
(() => {
  const FRAME_COUNT = 8;
  const MAX_BATCH_BYTES = 8 * 1024 * 1024;
  const MAX_FRAME_BYTES = 3 * 1024 * 1024;
  const COMPONENTS = { pump: 'Корпус насоса', motor: 'Электродвигатель', coupling: 'Муфта или кожух',
    pipe: 'Трубопровод или фланец', bearing: 'Подшипниковый узел', valve: 'Запорная арматура',
    terminal: 'Клеммная коробка', other: 'Другое оборудование', none: 'Узел не определён' };
  const CONDITIONS = { corrosion: 'Возможная коррозия', wet_patch: 'Возможные следы жидкости',
    visible_damage: 'Возможное повреждение', no_visible_defect: 'Явных внешних дефектов не замечено',
    uncertain: 'Недостаточно данных' };
  const aborted = () => new DOMException('Операция отменена', 'AbortError');
  const text = (tag, className, value) => {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = value;
    return element;
  };
  const decodedSize = encoded => Math.floor(encoded.length * 3 / 4) - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0);

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
    let clearCount = 0, uncertainCount = 0;
    for (const row of ordered) {
      const componentKnown = Object.prototype.hasOwnProperty.call(COMPONENTS, row.component) && row.component !== 'none';
      if (componentKnown && row.status === 'defect' && ['corrosion', 'wet_patch', 'visible_damage'].includes(row.condition)) {
        const key = `${row.component}:${row.condition}`;
        if (!grouped.has(key)) grouped.set(key, { component: row.component, condition: row.condition, observations: [] });
        grouped.get(key).observations.push(row);
      } else if (componentKnown && row.status === 'clear' && row.condition === 'no_visible_defect') clearCount += 1;
      else uncertainCount += 1;
    }
    const groups = [...grouped.values()];
    const mode = groups.length ? 'defects' : clearCount && !uncertainCount ? 'clear' : clearCount ? 'partial' : 'uncertain';
    return { mode, groups, visibleGroups: groups.slice(0, 3), additionalGroups: groups.slice(3),
      clearCount, uncertainCount, frameCount: ordered.length };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { groupObservations };
  if (typeof window === 'undefined') return;

  window.CyberVideoReview = {
    groupObservations,
    create({ media, beforeStart = () => {}, onBusyChange = () => {} }) {
      const panel = document.getElementById('video-review-panel');
      const runButton = document.getElementById('video-review-run');
      const cancelButton = document.getElementById('video-review-cancel');
      const status = document.getElementById('video-review-status');
      const results = document.getElementById('video-review-results');
      let busy = false, generation = 0, controller = null, seekController = null, sourceVersion = media.version;
      let available = true;
      const ready = () => media.kind === 'uploaded_video' && media.ready && !media.loading;
      const message = (value, error = false) => {
        if (!status) return;
        status.textContent = value;
        status.classList.toggle('is-error', error);
      };
      const active = token => token === generation && sourceVersion === media.version;
      function syncButtons() {
        if (panel) panel.hidden = media.kind !== 'uploaded_video';
        if (runButton) runButton.disabled = busy || !ready() || !available;
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
        if (clear) message('Разбор сравнит 8 кадров и покажет общий вывод и кадры с возможными дефектами.');
        else if (wasBusy) message('Разбор видео отменён.');
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
            message('Совместный разбор видео сейчас недоступен на сервере. Можно проверить отдельный кадр.');
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
        if (!context) throw new Error('Браузер не смог подготовить кадры видео.');
        const frames = [];
        let batchBytes = 0;
        for (let index = 0; index < FRAME_COUNT; index += 1) {
          if (!active(token) || signal.aborted) throw aborted();
          const timestamp = duration * (index + 0.5) / FRAME_COUNT;
          message(`Подготавливаем кадр ${index + 1} из ${FRAME_COUNT} · ${window.CyberMedia.time(timestamp)}`);
          await media.seek(timestamp, signal);
          if (!active(token) || signal.aborted) throw aborted();
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          const dataUrl = canvas.toDataURL('image/jpeg', 0.78);
          if (!dataUrl.startsWith('data:image/jpeg;base64,')) throw new Error('Не удалось подготовить JPEG-кадр.');
          const image = dataUrl.slice(dataUrl.indexOf(',') + 1);
          const bytes = decodedSize(image);
          batchBytes += bytes;
          if (!bytes || bytes > MAX_FRAME_BYTES || batchBytes > MAX_BATCH_BYTES) throw new Error('Кадры получились слишком большими. Выберите видео меньшего разрешения.');
          frames.push({ image, timestampSeconds: timestamp, dataUrl, width: canvas.width, height: canvas.height });
        }
        if (active(token) && !signal.aborted) await media.seek(position, signal);
        return { frames, durationSeconds: duration };
      }

      async function thumbnail(frame, observation, token) {
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
        return canvas.toDataURL('image/jpeg', 0.85);
      }

      async function renderResult(data, captured, token) {
        if (!data || typeof data.summary !== 'string' || typeof data.model !== 'string'
            || !Array.isArray(data.observations) || data.observations.length !== captured.frames.length) {
          throw new Error('Сервер вернул неполный разбор видео. Повторите попытку.');
        }
        for (let index = 0; index < data.observations.length; index += 1) {
          const observation = data.observations[index];
          const frame = captured.frames[index];
          if (!observation || observation.frameIndex !== index || observation.timestampSeconds !== frame.timestampSeconds) {
            throw new Error('Позиции наблюдений не совпали с кадрами. Результат не показан.');
          }
        }
        const grouped = groupObservations(data.observations);
        let summary;
        if (grouped.mode === 'clear') summary = 'На выбранных кадрах явных внешних дефектов не обнаружено.';
        else if (grouped.mode === 'partial') summary = 'На части кадров явных внешних дефектов не обнаружено. Остальные кадры не позволяют уверенно оценить состояние.';
        else if (grouped.mode === 'uncertain') summary = 'По выбранным кадрам не удалось уверенно оценить состояние оборудования. Снимите один узел крупнее.';
        else {
          summary = data.summary.trim().replace(/\s+/g, ' ');
          if (summary.length > 300) summary = `${summary.slice(0, 300).replace(/\s+\S*$/, '')}…`;
          if (!summary) summary = 'На выбранных кадрах отмечены возможные внешние дефекты.';
          if (grouped.uncertainCount) summary += ' Часть кадров не позволяет сделать уверенный вывод.';
        }
        const fragment = document.createDocumentFragment();
        const shownText = [summary, ...grouped.groups.map(group => group.observations[0].observation || '')].join(' ');
        if (/[\u3400-\u9fff\u3040-\u30ff]/u.test(shownText)) {
          fragment.append(text('p', 'video-review-meta', 'В описании модели есть языковые ошибки. Сверяйте наблюдения с кадрами.'));
        }
        fragment.append(text('p', 'video-review-summary', summary));
        fragment.append(text('p', 'video-review-meta', `${captured.frames.length} выбранных кадров · ${window.CyberMedia.time(captured.durationSeconds)}`));
        function bindSeek(button, frame) {
          button.addEventListener('click', async () => {
            if (!active(token) || busy || !ready()) return;
            seekController?.abort();
            const navigation = new AbortController(); seekController = navigation;
            beforeStart();
            if (!active(token) || !ready()) return;
            try { await media.seek(frame.timestampSeconds, navigation.signal); }
            catch (error) { if (active(token) && error.name !== 'AbortError') message(error.message, true); }
            finally { if (seekController === navigation) seekController = null; }
          });
        }
        async function groupCard(group) {
          const observation = group.observations[0];
          const frame = captured.frames[observation.frameIndex];
          const card = document.createElement('article'); card.className = 'video-review-frame';
          const seekButton = document.createElement('button'); seekButton.type = 'button'; seekButton.className = 'video-review-seek';
          seekButton.setAttribute('aria-label', `Открыть кадр ${window.CyberMedia.time(frame.timestampSeconds)}`);
          const image = document.createElement('img');
          image.src = await thumbnail(frame, observation, token);
          image.alt = `${COMPONENTS[observation.component] || 'Узел'} · ${window.CyberMedia.time(frame.timestampSeconds)}`;
          image.width = frame.width; image.height = frame.height;
          seekButton.append(image, text('span', 'video-review-time', window.CyberMedia.time(frame.timestampSeconds)));
          bindSeek(seekButton, frame);
          card.append(seekButton, text('p', 'video-review-finding', `${COMPONENTS[observation.component] || 'Узел не определён'} · ${CONDITIONS[observation.condition] || 'Недостаточно данных'}`));
          card.append(text('p', 'video-review-observation', observation.observation || 'По этому кадру нельзя сделать уверенное наблюдение.'));
          if (group.observations.length > 1) {
            const times = document.createElement('div'); times.className = 'video-review-times';
            times.append(text('span', '', 'Похожие признаки:'));
            for (const next of group.observations.slice(1, 4)) {
              const button = text('button', 'video-review-time-link', window.CyberMedia.time(next.timestampSeconds));
              button.type = 'button';
              button.setAttribute('aria-label', `Открыть кадр ${window.CyberMedia.time(next.timestampSeconds)}`);
              bindSeek(button, captured.frames[next.frameIndex]); times.append(button);
            }
            if (group.observations.length > 4) times.append(text('span', '', `и ещё ${group.observations.length - 4}`));
            card.append(times);
          }
          return card;
        }
        if (grouped.groups.length) {
          const grid = document.createElement('div'); grid.className = 'video-review-grid';
          for (const group of grouped.visibleGroups) grid.append(await groupCard(group));
          fragment.append(grid);
          if (grouped.additionalGroups.length) {
            const more = document.createElement('details'); more.className = 'video-review-more';
            more.append(text('summary', '', `Другие признаки (${grouped.additionalGroups.length})`));
            const extra = document.createElement('div'); extra.className = 'video-review-grid';
            for (const group of grouped.additionalGroups) extra.append(await groupCard(group));
            more.append(extra); fragment.append(more);
          }
          fragment.append(text('p', 'video-review-meta', 'Проверены выбранные кадры. Разбор не сохранён в историю.'));
        }
        if (!active(token)) throw aborted();
        results.replaceChildren(fragment); results.hidden = false;
      }

      async function run() {
        if (busy || !ready() || !available || !results) return;
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
          message('Сопоставляем кадры и готовим общий вывод… Видео остаётся на устройстве.');
          timer = setTimeout(() => { timedOut = true; controller?.abort(); }, 13 * 60 * 1000);
          const request = window.CyberAnalysisJobs?.request || fetch;
          const response = await request('/api/video-review', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
            cache: 'no-store', redirect: 'error', signal,
            body: JSON.stringify({ durationSeconds: captured.durationSeconds,
              frames: captured.frames.map(({ image, timestampSeconds }) => ({ image, timestampSeconds })) }),
          }, value => { if (active(token) && !signal.aborted) message(value); });
          const data = await response.json();
          clearTimeout(timer);
          if (!active(token) || signal.aborted) throw aborted();
          if (!response.ok) throw new Error(data.code === 'vision_busy'
            ? 'Модель занята другим осмотром. Повторите через несколько секунд.'
            : typeof data.error === 'string' ? data.error : 'Не удалось разобрать видео. Повторите попытку.');
          await renderResult(data, captured, token);
          if (active(token)) message(groupObservations(data.observations).groups.length
            ? 'Разбор готов. Нажмите на кадр или отметку времени, чтобы вернуться к этому моменту видео.'
            : 'Готово.');
        } catch (error) {
          if (active(token)) message(timedOut ? 'Модель не ответила вовремя. Повторите разбор позже.'
            : error.name === 'AbortError' ? 'Разбор видео отменён.' : error.message || 'Не удалось разобрать видео.', error.name !== 'AbortError' || timedOut);
        } finally {
          clearTimeout(timer);
          if (active(token)) { busy = false; controller = null; syncButtons(); onBusyChange(); }
        }
      }
      runButton?.addEventListener('click', run);
      cancelButton?.addEventListener('click', state.cancel);
      window.addEventListener('pagehide', state.cancel);
      message('Разбор сравнит 8 кадров и покажет общий вывод и кадры с возможными дефектами.');
      state.update();
      return state;
    },
  };
})();
