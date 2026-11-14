(() => {
  window.CyberInspectionLibrary = {
    create({media, sourceModes}) {
      const picker = document.getElementById('inspection-library');
      const home = document.querySelector('.capture-source-controls');
      const meshHome = document.getElementById('mesh-library-home');
      if (!picker || !home || !meshHome || !sourceModes) return null;
      const summary = picker.querySelector('summary');
      const status = picker.querySelector('[role=status]');
      const buttons = [...picker.querySelectorAll('button[data-url]')];
      const cached = new Map();
      let mode = sourceModes.mode, generation = 0, controller = null;
      let equipment = null, shownMeshChoices = false;

      function updateSummary() {
        summary.textContent = mode === 'mesh'
          ? equipment ? `Предполагаемое оборудование: ${equipment}` : 'Выбрать готовое видео'
          : 'Готовые материалы';
        picker.toggleAttribute('data-equipment', mode === 'mesh' && Boolean(equipment));
      }

      function message(value = '') {
        status.textContent = value;
        status.hidden = !value;
      }
      function cancel() {
        generation += 1;
        controller?.abort(); controller = null;
        picker.removeAttribute('aria-busy');
        for (const button of buttons) button.disabled = false;
        message();
      }
      function setMode(next) {
        if (next !== mode) { cancel(); picker.open = false; }
        mode = next;
        const destination = mode === 'mesh' ? meshHome : home;
        if (picker.parentElement !== destination) destination.append(picker);
        if (mode === 'mesh' && !shownMeshChoices) { picker.open = true; shownMeshChoices = true; }
        updateSummary();
        for (const button of buttons) button.hidden = button.dataset.kind !== (mode === 'photo' ? 'photo' : 'video');
        const featured = buttons.find(button => !button.hidden);
        const cover = document.getElementById('open-example-photo');
        if (featured && cover) {
          const title = featured.querySelector('span').textContent;
          const image = cover.querySelector('img');
          image.src = featured.querySelector('img').src;
          image.alt = title;
          cover.dataset.kind = featured.dataset.kind;
          cover.setAttribute('aria-label', 'Открыть: ' + title);
          cover.querySelector('.example-photo-action').textContent = mode === 'photo' ? 'Открыть фото' : 'Открыть видео';
        }
      }
      async function videoFile(button, signal) {
        const path = button.dataset.url;
        const url = new URL(path, location.href);
        if (url.origin !== location.origin || !/^\/inspection-library\/[a-z-]+\.mp4$/.test(url.pathname)) {
          throw new Error('Материал недоступен. Можно загрузить свой файл.');
        }
        if (cached.has(path)) return cached.get(path);
        const response = await fetch(url, {signal, credentials:'same-origin', redirect:'error'});
        if (!response.ok) throw new Error('Не удалось открыть материал. Попробуйте ещё раз.');
        const blob = await response.blob();
        if (!blob.size || blob.size > 40 * 1024 * 1024) throw new Error('Материал недоступен. Можно загрузить свой файл.');
        const file = new File([blob], button.dataset.name, {type:'video/mp4'});
        cached.set(path, file);
        return file;
      }
      async function choose(button) {
        if (mode === 'mesh' && sourceModes.busy) {
          message('Дождитесь завершения 3D, чтобы выбрать другой материал.');
          return;
        }
        cancel();
        const token = generation, selectedMode = mode;
        const request = new AbortController();
        controller = request;
        const signal = request.signal;
        picker.setAttribute('aria-busy', 'true');
        for (const item of buttons) item.disabled = true;
        message('Открываем материал…');
        const timer = setTimeout(() => request.abort(), 20000);
        try {
          const file = button.dataset.kind === 'video' ? await videoFile(button, signal) : null;
          if (token !== generation || signal.aborted) return;
          clearTimeout(timer);
          controller = null;
          picker.removeAttribute('aria-busy');
          for (const item of buttons) item.disabled = false;
          message();
          picker.open = false;
          if (selectedMode === 'mesh') {
            if (!sourceModes.openFile(file)) {
              picker.open = true;
              message('Дождитесь завершения 3D, чтобы выбрать другой материал.');
            }
          } else if (file) await media.openFile(file);
          else await media.openCatalogPhoto(button.dataset.url);
        } catch (error) {
          if (token !== generation) return;
          cancel();
          message(error.name === 'AbortError'
            ? 'Загрузка заняла слишком много времени. Повторите попытку.'
            : 'Не удалось открыть материал. Проверьте подключение и повторите попытку.');
        } finally { clearTimeout(timer); }
      }
      for (const button of buttons) button.addEventListener('click', () => choose(button));
      window.addEventListener('pagehide', cancel);
      setMode(mode);
      picker.hidden = false;
      return {setMode, cancel, setEquipment(label) {
        equipment = typeof label === 'string' && label.length <= 60 ? label : null;
        updateSummary();
      }, openFeatured() {
        const button = buttons.find(button => !button.hidden);
        if (button) return choose(button);
      }};
    },
  };
})();
