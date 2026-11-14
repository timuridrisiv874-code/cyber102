// Files are decoded locally. Only normalized JPEG frames reach the local analysis API.
(() => {
  const abortError = () => new DOMException('Операция отменена', 'AbortError');
  function waitFor(element, event, signal, ready, timeout = 20000) {
    return new Promise((resolve, reject) => {
      let timer;
      const clean = () => { clearTimeout(timer); element.removeEventListener(event, done); element.removeEventListener('error', fail); signal?.removeEventListener('abort', cancel); };
      const done = () => { clean(); resolve(); };
      const fail = () => { clean(); reject(new Error('Браузер не поддерживает этот файл. Для фото используйте JPEG/PNG/WebP, для видео — MP4 (H.264).')); };
      const cancel = () => { clean(); reject(abortError()); };
      if (signal?.aborted) return cancel();
      if (ready?.()) return done();
      element.addEventListener(event, done, { once: true });
      element.addEventListener('error', fail, { once: true });
      signal?.addEventListener('abort', cancel, { once: true });
      timer = setTimeout(() => { clean(); reject(new Error('Файл не удалось прочитать вовремя. Выберите другой файл.')); }, timeout);
    });
  }
  window.CyberMedia = {
    time(seconds) { const ticks = Math.round(Math.max(0, Number(seconds) || 0) * 10); return `${Math.floor(ticks / 600)}:${((ticks % 600) / 10).toFixed(1).padStart(4, '0')}`; },
    create({ input, image, video, beforeChange, changed, message }) {
      let kind = null, url = null, loading = false, generation = 0, loader = null;
      function clear() {
        generation += 1;
        loader?.abort(); loader = null;
        video.pause(); video.removeAttribute('src'); video.load();
        image.removeAttribute('src');
        if (url) URL.revokeObjectURL(url);
        url = null; kind = null; loading = false; input.value = '';
      }
      const state = {
        get kind() { return kind; }, get loading() { return loading; },
        get version() { return generation; },
        get element() { return kind === 'uploaded_video' ? video : image; },
        get ready() { return !loading && (kind === 'uploaded_video' ? video.readyState >= 2 && video.videoWidth > 0 : kind === 'uploaded_photo' && image.complete && image.naturalWidth > 0); },
        clear,
        pause() { video.pause(); },
        metadata() { return { captureSource: kind, ...(kind === 'uploaded_video' ? { videoTimeSeconds: Number(video.currentTime.toFixed(3)) } : {}) }; },
        async seek(seconds, signal) {
          if (kind !== 'uploaded_video' || !state.ready) throw new Error('Дождитесь загрузки видео.');
          video.pause();
          const target = Math.max(0, Math.min(seconds, Math.max(0, video.duration - 0.001)));
          if (Math.abs(video.currentTime - target) < 0.001 && !video.seeking) return;
          const waiting = waitFor(video, 'seeked', signal, null, 15000);
          video.currentTime = target;
          await waiting;
          if (signal?.aborted) throw abortError();
        },
      };
      input.addEventListener('change', async () => {
        const file = input.files?.[0];
        if (!file) return;
        beforeChange(); clear();
        const token = generation;
        let nextKind = file.type.startsWith('video/') ? 'uploaded_video' : file.type.startsWith('image/') ? 'uploaded_photo' : null;
        if (!nextKind && !file.type) nextKind = /\.(mp4|mov|m4v|webm)$/i.test(file.name) ? 'uploaded_video' : /\.(jpg|jpeg|png|webp|heic|heif|avif)$/i.test(file.name) ? 'uploaded_photo' : null;
        try {
          if (!nextKind) throw new Error('Выберите фото или видео. Поддержка формата зависит от браузера.');
          const limit = nextKind === 'uploaded_video' ? 150 : 20;
          if (!file.size || file.size > limit * 1024 * 1024) throw new Error(`Файл пустой или превышает ${limit} МБ. Выберите меньший файл.`);
          kind = nextKind; loading = true; loader = new AbortController();
          url = URL.createObjectURL(file);
          message('Открываем файл на устройстве…'); changed();
          if (kind === 'uploaded_video') {
            const metadata = waitFor(video, 'loadedmetadata', loader.signal);
            video.src = url; video.load();
            await metadata;
            if (!Number.isFinite(video.duration) || video.duration <= 0 || video.duration > 120) throw new Error('Выберите видео длительностью до 2 минут.');
            await waitFor(video, 'loadeddata', loader.signal, () => video.readyState >= 2);
          } else {
            const loaded = waitFor(image, 'load', loader.signal);
            image.src = url; await loaded;
            if (!image.naturalWidth || image.naturalWidth * image.naturalHeight > 50000000) throw new Error('Изображение слишком большое. Уменьшите его до 50 мегапикселей.');
          }
          if (token !== generation) return;
          loading = false; loader = null;
          message(window.CyberOffline?.enabled
            ? kind === 'uploaded_video'
              ? 'Видео открыто. Выберите кадр и нажмите «Проверить кадр». Анализ выполняется на телефоне.'
              : 'Фото открыто. Нажмите «Проверить кадр»: снимок обработает LFM3 на телефоне.'
            : kind === 'uploaded_video'
              ? 'Видео открыто. Выберите кадр или включите видеопроверку по интервалу. На сервер отправляются только выбранные кадры.'
              : 'Фото открыто. Нажмите «Проверить кадр»: снимок обработает серверная модель.');
        } catch (error) {
          if (token !== generation) return;
          clear();
          if (error.name !== 'AbortError') message(error.message, true);
        }
        changed();
      });
      ['loadeddata', 'resize', 'timeupdate', 'seeked'].forEach(event => video.addEventListener(event, changed));
      return state;
    },
  };
})();
