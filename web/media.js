// Files are decoded locally. Only normalized JPEG frames reach the local analysis API.
(() => {
  const abortError = () => new DOMException('Операция отменена', 'AbortError');
  const FILE_TYPES = new Map(Object.entries({
    mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm',
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp',
    heic: 'image/heic', heif: 'image/heif', avif: 'image/avif', gif: 'image/gif',
  }));
  function mediaType(file) {
    if (/^(video|image)\//.test(file.type)) return file.type;
    // Some mobile file providers supply a generic MIME even for a valid
    // JPEG or MP4. The image/video decoder still validates the contents.
    if (file.type && !/^(application|binary)\/octet-stream$/i.test(file.type)) return null;
    const extension = /\.([a-z0-9]+)$/i.exec(file.name)?.[1].toLowerCase();
    return FILE_TYPES.get(extension);
  }
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
      let catalogPhoto = false, waitingForVideoFrame = false, selectedVideo = null;
      function clear() {
        generation += 1;
        loader?.abort(); loader = null;
        video.pause(); video.removeAttribute('src'); video.load();
        image.removeAttribute('src');
        if (url) URL.revokeObjectURL(url);
        url = null; kind = null; selectedVideo = null; catalogPhoto = false; loading = false; waitingForVideoFrame = false; input.value = '';
      }
      const state = {
        get selectedVideo() { return selectedVideo; },
        get kind() { return kind; }, get loading() { return loading; },
        get version() { return generation; },
        get catalogPhoto() { return catalogPhoto; },
        get element() { return kind === 'uploaded_video' ? video : image; },
        get ready() { return !loading && (kind === 'uploaded_video' ? video.readyState >= 2 && video.videoWidth > 0 : kind === 'uploaded_photo' && image.complete && image.naturalWidth > 0); },
        clear,
        async openCatalogPhoto(path) {
          beforeChange(); clear();
          const token = generation;
          kind = 'uploaded_photo'; catalogPhoto = true; loading = true;
          loader = new AbortController();
          changed();
          try {
            const loaded = waitFor(image, 'load', loader.signal);
            image.src = path;
            await loaded;
            if (token !== generation) return false;
            loading = false; loader = null;
            message(); changed();
            return true;
          } catch (error) {
            if (token !== generation) return false;
            clear(); changed();
            message('Фото из подборки недоступно. Можно загрузить своё.', true);
            return false;
          }
        },
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
      async function openSelectedFile() {
        const file = input.files?.[0];
        if (!file) return;
        const mime = mediaType(file);
        const isVideo = mime?.startsWith('video/');
        const limit = isVideo ? 150 : 20;
        if (!mime || !file.size || file.size > limit * 1024 * 1024) {
          input.value = '';
          message(mime ? `Файл пустой или превышает ${limit} МБ. Выберите меньший файл.` :
            'Выберите фото или видео. Поддержка формата зависит от браузера.', true);
          return;
        }
        beforeChange(); clear();
        const token = generation;
        try {
          kind = isVideo ? 'uploaded_video' : 'uploaded_photo';
          if (isVideo) selectedVideo = file;
          loading = true; loader = new AbortController();
          url = URL.createObjectURL(mime === file.type ? file : file.slice(0, file.size, mime));
          message('Открываем файл на устройстве…'); changed();
          if (kind === 'uploaded_video') {
            const metadata = waitFor(video, 'loadedmetadata', loader.signal);
            video.src = url; video.load();
            await metadata;
            if (token !== generation) return;
            if (!Number.isFinite(video.duration) || video.duration <= 0 || video.duration > 120) throw new Error('Выберите видео длительностью до 2 минут.');
            // A mobile browser can wait for a tap on the native player before
            // decoding any pixels. Metadata is valid; keep the file available.
            waitingForVideoFrame = video.readyState < 2;
          } else {
            const loaded = waitFor(image, 'load', loader.signal);
            image.src = url; await loaded;
            if (!image.naturalWidth || image.naturalWidth * image.naturalHeight > 50000000) throw new Error('Изображение слишком большое. Уменьшите его до 50 мегапикселей.');
          }
          if (token !== generation) return;
          loading = false; loader = null;
          message(waitingForVideoFrame ? 'Нажмите «Воспроизвести» в плеере, чтобы открыть первый кадр.' : window.CyberOffline?.enabled
            ? kind === 'uploaded_video'
              ? 'Видео открыто. Выберите кадр и нажмите «Проверить кадр». Анализ выполняется на телефоне.'
              : 'Фото открыто. Нажмите «Проверить кадр»: снимок обработает SmolVLM на телефоне.'
            : '');
        } catch (error) {
          if (token !== generation) return;
          clear();
          if (error.name !== 'AbortError') message(error.message, true);
        }
        changed();
      }
      input.addEventListener('change', openSelectedFile);
      // A native picker can finish while the deferred app scripts are still
      // arriving. Defer callbacks until the owner has assigned the media state.
      if (input.files?.length) queueMicrotask(openSelectedFile);
      function videoChanged() {
        if (waitingForVideoFrame && state.ready) {
          waitingForVideoFrame = false; message();
        }
        changed();
      }
      video.addEventListener('error', () => {
        if (kind !== 'uploaded_video' || loading || !video.error) return;
        clear(); message('Видео не удалось прочитать. Выберите MP4 (H.264).', true); changed();
      });
      ['loadeddata', 'canplay', 'playing', 'resize', 'timeupdate', 'seeked'].forEach(event => video.addEventListener(event, videoChanged));
      return state;
    },
  };
})();
