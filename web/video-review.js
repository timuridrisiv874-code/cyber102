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
    const clearFrames = [], uncertainFrames = [];
    for (const row of ordered) {
      const componentKnown = Object.prototype.hasOwnProperty.call(COMPONENTS, row.component) && row.component !== 'none';
      if (componentKnown && row.status === 'defect' && ['corrosion', 'wet_patch', 'visible_damage'].includes(row.condition)) {
        const key = `${row.component}:${row.condition}`;
        if (!grouped.has(key)) grouped.set(key, { component: row.component, condition: row.condition, observations: [], episodes: [] });
        const group = grouped.get(key);
        const previous = group.observations.at(-1);
        // Adjacent sampled frames form one episode. A gap starts another,
        // without inferring when the defect appeared or disappeared.
        if (!previous || previous.frameIndex + 1 !== row.frameIndex) group.episodes.push([]);
        group.episodes.at(-1).push(row);
        group.observations.push(row);
      } else if (componentKnown && row.status === 'clear' && row.condition === 'no_visible_defect') clearFrames.push(row);
      else uncertainFrames.push(row);
    }
    const groups = [...grouped.values()];
    const clearCount = clearFrames.length, uncertainCount = uncertainFrames.length;
    const mode = !ordered.length ? 'empty' : groups.length ? 'defects' : clearCount && !uncertainCount ? 'clear' : clearCount ? 'partial' : 'uncertain';
    return { mode, groups, visibleGroups: groups.slice(0, 3), additionalGroups: groups.slice(3),
      clearFrames, uncertainFrames, clearCount, uncertainCount, frameCount: ordered.length };
  }

  function summarize(grouped) {
    const { mode, groups, frameCount, clearCount, uncertainCount } = grouped;
    if (mode === 'empty') return 'Нет проверенных кадров.';
    if (mode === 'uncertain') return 'Ни один из выбранных кадров не позволил оценить состояние узла.';
    if (mode === 'clear') return 'На выбранных кадрах явных внешних дефектов не замечено.';
    if (mode === 'partial') return `Явных дефектов не замечено на ${clearCount} из ${frameCount} кадров. Остальные оценить не удалось.`;
    const withFindings = frameCount - clearCount - uncertainCount;
    const labels = { corrosion: 'коррозию', wet_patch: 'следы жидкости', visible_damage: 'видимые повреждения' };
    const findings = [...new Set(groups.map(group => labels[group.condition]))];
    const list = new Intl.ListFormat('ru', {type: 'conjunction'}).format(findings);
    const counts = `Модель отметила ${list} на ${withFindings} из ${frameCount} выбранных кадров.`;
    return uncertainCount ? `${counts} Кадров без оценки: ${uncertainCount}.` : counts;
  }

  function evidenceTime(seconds) {
    const ms = Math.round(seconds * 1000);
    const fraction = String(ms % 1000).padStart(3, '0').replace(/0+$/, '');
    return `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}${fraction ? '.' + fraction : ''}`;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { groupObservations, summarize, evidenceTime };
  if (typeof window === 'undefined') return;

  window.CyberVideoReview = {
    groupObservations,
    create({ media, beforeStart = () => {}, onBusyChange = () => {}, onSelectFrame = beforeStart,
             onInspectFrame, isInspecting = () => false }) {
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
        const blocked = busy || isInspecting() || !ready() || !available;
        if (runButton) runButton.disabled = blocked;
        if (results && !results.hidden) {
          for (const button of results.querySelectorAll('button')) {
            button.disabled = blocked || Boolean(seekController && button.classList.contains('video-review-inspect'));
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
        if (clear) message('Проверяем восемь кадров, выбранных равномерно по ролику.');
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
        if (Array.isArray(data?.observations) && !data.observations.length) throw new Error('Модель не вернула наблюдений по отправленным кадрам. Повторите разбор.');
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
        const fragment = document.createDocumentFragment();
        fragment.append(text('p', 'video-review-summary', summarize(grouped)));
        fragment.append(text('p', 'video-review-meta', `Длительность ${evidenceTime(captured.durationSeconds)} · Проверено кадров: ${grouped.frameCount}. Промежутки между ними не проверялись.`));
        if (grouped.groups.some(group => group.observations.some(row => /[\u3400-\u9fff\u3040-\u30ff]/u.test(row.observation)))) {
          fragment.append(text('p', 'video-review-meta', 'В описании модели есть языковые ошибки. Сверяйте наблюдения с кадрами.'));
        }
        const previews = new Map();
        function preview(row) {
          if (!previews.has(row.frameIndex)) previews.set(row.frameIndex, thumbnail(captured.frames[row.frameIndex], row, token));
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
        async function groupCard(group) {
          const name = `${COMPONENTS[group.component]} · ${CONDITIONS[group.condition]}`;
          const card = document.createElement('article'); card.className = 'video-review-frame';
          card.append(text('h4', 'video-review-finding', name));
          const seekButton = document.createElement('button'); seekButton.type = 'button'; seekButton.className = 'video-review-seek';
          const image = document.createElement('img');
          const caption = text('span', 'video-review-time', '');
          const observation = text('p', 'video-review-observation', '');
          seekButton.append(image, caption);
          card.append(seekButton, observation);
          let selected = group.observations[0];
          const buttons = new Map();
          function showSelected(row, url) {
            selected = row;
            const time = evidenceTime(row.timestampSeconds);
            const frame = captured.frames[row.frameIndex];
            image.src = url; image.alt = `${COMPONENTS[row.component]} · ${time}`;
            image.width = frame.width; image.height = frame.height;
            caption.textContent = `Кадр ${time}`;
            seekButton.setAttribute('aria-label', `Открыть кадр ${time} в видео. ${name}`);
            observation.textContent = row.observation;
            for (const [index, button] of buttons) {
              if (index === row.frameIndex) button.setAttribute('aria-current', 'true');
              else button.removeAttribute('aria-current');
            }
          }
          seekButton.addEventListener('click', () => navigate(selected));
          if (group.observations.length > 1) {
            const episodes = document.createElement('div'); episodes.className = 'video-review-episodes';
            group.episodes.forEach((rows, index) => {
              const episode = document.createElement('div'); episode.className = 'video-review-episode video-review-times';
              const label = group.episodes.length > 1 ? `Эпизод ${index + 1}` : 'Проверенные моменты';
              episode.append(text('span', 'video-review-episode-label', label));
              for (const row of rows) {
                const button = timeButton(row, next => navigate(next, showSelected), `${name}. ${label}`);
                buttons.set(row.frameIndex, button); episode.append(button);
              }
              episodes.append(episode);
            });
            card.append(episodes);
          }
          if (onInspectFrame) card.append(inspectButton(() => selected));
          showSelected(selected, await preview(selected));
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
        }
        if (grouped.clearCount || grouped.uncertainCount) {
          const other = document.createElement('details'); other.className = 'video-review-more video-review-evidence';
          const label = grouped.clearCount && grouped.uncertainCount ? 'Кадры без находок и без оценки'
            : grouped.clearCount ? 'Кадры без явных дефектов' : 'Кадры без оценки';
          other.append(text('summary', '', label));
          const otherButtons = [];
          let selected = null;
          const inspect = onInspectFrame ? inspectButton(() => selected) : null;
          if (inspect) inspect.hidden = true;
          const selectedText = text('p', 'video-review-observation', '');
          selectedText.setAttribute('aria-live', 'polite');
          for (const [label, rows] of [['Без явных дефектов', grouped.clearFrames], ['Не удалось оценить', grouped.uncertainFrames]]) {
            if (!rows.length) continue;
            const line = document.createElement('div'); line.className = 'video-review-times';
            line.append(text('span', 'video-review-episode-label', `${label} (${rows.length})`));
            for (const row of rows) {
              const button = timeButton(row, next => navigate(next, () => {
                selected = next;
                if (inspect) inspect.hidden = false;
                for (const item of otherButtons) item.removeAttribute('aria-current');
                button.setAttribute('aria-current', 'true');
                selectedText.textContent = `${evidenceTime(next.timestampSeconds)} · ${label}${next.observation ? '. ' + next.observation : ''}`;
              }), label);
              otherButtons.push(button); line.append(button);
            }
            other.append(line);
          }
          other.append(selectedText);
          if (inspect) other.append(inspect);
          fragment.append(other);
        }
        fragment.append(text('p', 'video-review-meta', 'Разбор не сохранён в историю.'));
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
          if (active(token)) message('Разбор готов.');
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
      message('Проверяем восемь кадров, выбранных равномерно по ролику.');
      state.update();
      return state;
    },
  };
})();
