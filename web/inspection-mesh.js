(() => {
  'use strict';
  if (window.CyberWebBoot?.blocked) return;
  const endpoint = '/api/mesh-jobs';
  async function request(path, options) {
    const response = await fetch(endpoint + path, { cache: path.endsWith('/mesh') ? 'default' : 'no-store', ...options });
    if (response.ok) return response;
    let error;
    try { error = (await response.json()).message; } catch {}
    throw new Error(error || 'Сервис 3D временно недоступен.');
  }
  const cachedMesh = window.CyberMeshCache?.create({request});

  function renderer(canvas, meta, buffer) {
    const { vertices, triangles, indexBytes } = meta;
    if (!Number.isInteger(vertices) || vertices < 3 || indexBytes !== 4 ||
        !Number.isInteger(triangles) || triangles < 1 || buffer.byteLength !== vertices * 15 + triangles * 12) {
      throw new Error('Invalid mesh');
    }
    const gl = canvas.getContext('webgl', { antialias: true });
    if (!gl || !gl.getExtension('OES_element_index_uint')) throw new Error('WebGL unavailable');
    const positions = new Float32Array(buffer, 0, vertices * 3);
    const indices = new Uint32Array(buffer, vertices * 12, triangles * 3);
    const colors = new Uint8Array(buffer, vertices * 12 + triangles * 12);
    const program = gl.createProgram();
    function attach(type, source) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      gl.attachShader(program, shader);
      gl.deleteShader(shader);
    }
    attach(gl.VERTEX_SHADER, `
      attribute vec3 position, color;
      uniform vec4 rotation;
      uniform vec2 scale;
      varying vec3 rgb;
      void main() {
        float x = position.x * rotation.x + position.z * rotation.y;
        float z = position.z * rotation.x - position.x * rotation.y;
        float y = position.y * rotation.z - z * rotation.w;
        float depth = z * rotation.z + position.y * rotation.w;
        float w = 1.0 + 0.19 * depth;
        gl_Position = vec4(x * scale.x, -y * scale.y, depth * 0.45 * w, w);
        rgb = color;
      }
    `);
    attach(gl.FRAGMENT_SHADER, `
      precision mediump float;
      varying vec3 rgb;
      void main() { gl_FragColor = vec4(rgb, 1.0); }
    `);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    for (const [name, data, type] of [['position', positions, gl.FLOAT], ['color', colors, gl.UNSIGNED_BYTE]]) {
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const location = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 3, type, type === gl.UNSIGNED_BYTE, 0, 0);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    gl.enable(gl.DEPTH_TEST);
    const rotation = gl.getUniformLocation(program, 'rotation');
    const scale = gl.getUniformLocation(program, 'scale');
    function project(point, view) {
      const width = canvas.clientWidth, height = canvas.clientHeight;
      const a = Math.cos(view.yaw), b = Math.sin(view.yaw);
      const c = Math.cos(view.pitch), d = Math.sin(view.pitch);
      const x = point[0] * a + point[2] * b;
      const z = point[2] * a - point[0] * b;
      const y = point[1] * c - z * d;
      const depth = z * c + point[1] * d;
      const w = 1 + .19 * depth;
      const size = Math.min(width, height) * view.zoom;
      return { x: width / 2 + x * size / (2 * w),
        y: height / 2 + y * size / (2 * w), depth, w };
    }
    function pick(x, y, view) {
      const projected = new Float32Array(vertices * 3);
      for (let i = 0; i < vertices; i++) {
        const p = project(positions.subarray(i * 3, i * 3 + 3), view);
        projected[i * 3] = p.x;
        projected[i * 3 + 1] = p.y;
        projected[i * 3 + 2] = p.depth;
      }
      let nearest = Infinity, hit = null;
      for (let i = 0; i < indices.length; i += 3) {
        const a = indices[i] * 3, b = indices[i + 1] * 3, c = indices[i + 2] * 3;
        const ax = projected[a], ay = projected[a + 1];
        const bx = projected[b], by = projected[b + 1];
        const cx = projected[c], cy = projected[c + 1];
        if (x < Math.min(ax, bx, cx) || x > Math.max(ax, bx, cx) ||
            y < Math.min(ay, by, cy) || y > Math.max(ay, by, cy)) continue;
        const area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
        if (Math.abs(area) < 1e-8) continue;
        const u = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / area;
        const v = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / area;
        const t = 1 - u - v;
        if (u < 0 || v < 0 || t < 0) continue;
        const depth = u * projected[a + 2] + v * projected[b + 2] + t * projected[c + 2];
        if (depth >= nearest) continue;
        const weights = [u / (1 + .19 * projected[a + 2]),
          v / (1 + .19 * projected[b + 2]), t / (1 + .19 * projected[c + 2])];
        const total = weights[0] + weights[1] + weights[2];
        hit = [0, 1, 2].map(offset => (weights[0] * positions[a + offset] +
          weights[1] * positions[b + offset] + weights[2] * positions[c + offset]) / total);
        nearest = depth;
      }
      return hit;
    }
    function draw({ yaw, pitch, zoom }) {
      const width = canvas.clientWidth, height = canvas.clientHeight;
      if (!width || !height || gl.isContextLost()) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const size = Math.min(width, height) * zoom;
      const pixelWidth = Math.round(width * ratio), pixelHeight = Math.round(height * ratio);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
        gl.viewport(0, 0, pixelWidth, pixelHeight);
      }
      const a = Math.cos(yaw), b = Math.sin(yaw), c = Math.cos(pitch), d = Math.sin(pitch);
      gl.uniform4f(rotation, a, b, c, d);
      gl.uniform2f(scale, size / width, size / height);
      gl.clearColor(.09, .145, .16, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.drawElements(gl.TRIANGLES, indices.length, gl.UNSIGNED_INT, 0);
    }
    return { draw, pick, project };
  }

  function model(panel, onEquipment) {
    const canvas = panel.querySelector('canvas');
    const upload = panel.querySelector('.mesh-start');
    const input = panel.querySelector('input');
    const status = panel.querySelector('.mesh-status');
    const toolbar = panel.querySelector('.mesh-toolbar');
    const markerLayer = panel.querySelector('.mesh-markers');
    const tooltip = panel.querySelector('.mesh-tooltip');
    const findings = panel.querySelector('.mesh-findings');
    const findingList = panel.querySelector('.mesh-findings-list');
    const sourceVideo = panel.querySelector('.mesh-source-video');
    const sourceDetails = panel.querySelector('.mesh-source');
    const findingsHelp = panel.querySelector('.mesh-findings-help');
    const markButton = panel.querySelector('#mesh-mark-mode');
    const initialView = { yaw: 0, pitch: 0,
      zoom: window.matchMedia('(max-width: 640px)').matches ? .9 : 1 };
    const view = { ...initialView };
    let render, drag, meta, buffer, currentFile, sourceUrl, busy = false, marking = false;
    let hoveredMark = null;
    const marks = [];
    function positionTooltip() {
      if (!hoveredMark || !render || hoveredMark.button.hidden || !hoveredMark.label) {
        hoveredMark = null;
        tooltip.hidden = true;
        return;
      }
      const point = render.project(hoveredMark.point, view);
      tooltip.textContent = hoveredMark.label;
      tooltip.hidden = false;
      const gap = 20, edge = 8;
      let left = point.x + gap;
      if (left + tooltip.offsetWidth > canvas.clientWidth - edge) {
        left = point.x - tooltip.offsetWidth - gap;
      }
      tooltip.style.left = `${Math.max(edge, Math.min(left, canvas.clientWidth - tooltip.offsetWidth - edge))}px`;
      tooltip.style.top = `${Math.max(edge, Math.min(point.y - tooltip.offsetHeight / 2,
        canvas.clientHeight - tooltip.offsetHeight - edge))}px`;
    }
    function showTooltip(mark) {
      hoveredMark = mark;
      positionTooltip();
    }
    function hideTooltip(mark) {
      if (hoveredMark !== mark) return;
      hoveredMark = null;
      tooltip.hidden = true;
    }
    function draw() {
      render?.draw(view);
      if (!render) return;
      for (const [index, mark] of marks.entries()) {
        const point = render.project(mark.point, view);
        mark.button.style.left = `${point.x}px`;
        mark.button.style.top = `${point.y}px`;
        mark.button.hidden = point.w <= 0 || point.x < 0 || point.y < 0 ||
          point.x > canvas.clientWidth || point.y > canvas.clientHeight;
        mark.button.textContent = String(index + 1);
        mark.button.setAttribute('aria-label', `Повреждение ${index + 1}: ${mark.label || 'без описания'}`);
      }
      positionTooltip();
    }
    const message = (text, error = false) => {
      status.textContent = text;
      status.dataset.error = String(error);
    };
    function syncFindings() {
      findingList.replaceChildren();
      for (const [index, mark] of marks.entries()) {
        const row = document.createElement('li');
        row.className = 'mesh-finding-row';
        const number = document.createElement('span');
        number.className = 'mesh-finding-number';
        number.textContent = String(index + 1);
        row.append(number);
        if (mark.label) {
          const label = document.createElement('span');
          label.className = 'mesh-finding-label';
          label.textContent = mark.label;
          row.append(label);
          if (mark.auto) {
            const evidence = document.createElement('button');
            evidence.type = 'button';
            evidence.className = 'mesh-evidence-link';
            evidence.textContent = `Кадр ${mark.time.toFixed(2)} с`;
            evidence.setAttribute('aria-label', `Открыть исходное видео на ${mark.time.toFixed(2)} секунде`);
            evidence.addEventListener('click', () => seekSource(mark.time, true));
            row.append(evidence);
          }
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'mesh-remove-mark';
          remove.textContent = 'Убрать';
          remove.setAttribute('aria-label', `Убрать повреждение ${index + 1}`);
          remove.addEventListener('click', () => {
            hideTooltip(mark);
            marks.splice(marks.indexOf(mark), 1);
            mark.button.remove();
            syncFindings();
            draw();
          });
          row.append(remove);
        } else {
          row.classList.add('mesh-finding-draft');
          const input = document.createElement('input');
          input.type = 'text';
          input.maxLength = 100;
          input.placeholder = 'Что видно в этой точке?';
          input.setAttribute('aria-label', `Описание повреждения ${index + 1}`);
          const save = document.createElement('button');
          save.type = 'button';
          save.textContent = 'Сохранить';
          const cancel = document.createElement('button');
          cancel.type = 'button';
          cancel.textContent = 'Отмена';
          const removeDraft = () => {
            hideTooltip(mark);
            marks.splice(marks.indexOf(mark), 1);
            mark.button.remove();
            syncFindings();
            draw();
          };
          save.addEventListener('click', () => {
            const label = input.value.trim();
            if (!label) { input.focus(); return; }
            mark.label = label;
            syncFindings();
            draw();
          });
          input.addEventListener('keydown', event => {
            if (event.key === 'Enter') save.click();
            if (event.key === 'Escape') removeDraft();
          });
          cancel.addEventListener('click', removeDraft);
          row.append(input, save, cancel);
          queueMicrotask(() => input.focus());
        }
        findingList.append(row);
      }
    }
    function seekSource(time, scroll = false) {
      sourceDetails.open = true;
      const seek = () => { sourceVideo.currentTime = time; };
      if (sourceVideo.readyState >= 1) seek();
      else sourceVideo.addEventListener('loadedmetadata', seek, { once: true });
      if (scroll) sourceDetails.scrollIntoView({ block: 'nearest' });
    }
    function addMark(point, label = '', time = null) {
      if (!point || marks.length >= 8) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mesh-marker';
      markerLayer.append(button);
      const mark = { point, label, time, auto: Boolean(label) && Number.isFinite(time), button };
      marks.push(mark);
      button.addEventListener('click', () => {
        if (window.matchMedia('(hover: none)').matches) { showTooltip(mark); return; }
        findingList.children[marks.indexOf(mark)]?.scrollIntoView({ block: 'nearest' });
      });
      button.addEventListener('pointerenter', () => showTooltip(mark));
      button.addEventListener('pointerleave', event => {
        if (event.pointerType !== 'touch') hideTooltip(mark);
      });
      button.addEventListener('focus', () => showTooltip(mark));
      button.addEventListener('blur', () => hideTooltip(mark));
      marking = false;
      markButton.setAttribute('aria-pressed', 'false');
      message('');
      syncFindings();
      draw();
    }
    markButton.addEventListener('click', () => {
      marking = !marking;
      markButton.setAttribute('aria-pressed', String(marking));
      message(marking ? 'Нажмите на повреждённое место на модели.' : '');
    });
    function activate() {
      try {
        render = renderer(canvas, meta, buffer);
        upload.hidden = true;
        canvas.hidden = toolbar.hidden = markerLayer.hidden = findings.hidden = false;
        message('');
        draw();
      } catch {
        message('Этот браузер не смог открыть 3D. Попробуйте другой браузер.', true);
      }
    }

    toolbar.addEventListener('click', event => {
      const action = event.target.closest('button')?.dataset.action;
      if (action === 'left') view.yaw += .3;
      if (action === 'right') view.yaw -= .3;
      if (action === 'zoom-in') view.zoom = Math.min(2, view.zoom + .2);
      if (action === 'zoom-out') view.zoom = Math.max(.75, view.zoom - .2);
      if (action === 'reset') Object.assign(view, initialView);
      draw();
    });
    canvas.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY,
        startX: event.clientX, startY: event.clientY, moved: false };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
      if (drag?.id !== event.pointerId) return;
      if (Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) > 4) drag.moved = true;
      if (marking) return;
      view.yaw -= (event.clientX - drag.x) * .009;
      view.pitch = Math.max(-1.15, Math.min(1.15, view.pitch + (event.clientY - drag.y) * .009));
      drag.x = event.clientX;
      drag.y = event.clientY;
      draw();
    });
    canvas.addEventListener('pointerup', event => {
      if (drag?.id !== event.pointerId) return;
      if (marking && !drag.moved) {
        const rect = canvas.getBoundingClientRect();
        const point = render?.pick(event.clientX - rect.left, event.clientY - rect.top, view);
        if (point) addMark(point);
        else message('Нажмите на поверхность модели, а не на фон.', true);
      }
      drag = null;
    });
    canvas.addEventListener('lostpointercapture', () => { drag = null; });
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      render = null;
      message('Восстанавливаем 3D-просмотр…');
    });
    canvas.addEventListener('webglcontextrestored', activate);
    new ResizeObserver(draw).observe(canvas.parentElement);
    const stages = {
      queued: 'Видео загружено.',
      frames: 'Выбираем кадры из видео…',
      building: 'Строим 3D-модель. Это может занять несколько минут…',
      exporting: 'Готовим модель для просмотра…',
    };
    async function build(file) {
      if (!file || file === currentFile || busy) return;
      if (file.size < 1 || file.size > 40 * 1024 * 1024) {
        message('Выберите видео до 40 МБ.', true);
        return;
      }
      busy = true;
      onEquipment(null);
      input.disabled = true;
      hoveredMark = null;
      tooltip.hidden = true;
      canvas.hidden = toolbar.hidden = markerLayer.hidden = findings.hidden = true;
      upload.hidden = false;
      render = null;
      marking = false;
      markButton.setAttribute('aria-pressed', 'false');
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 24 * 60 * 1000);
      const signal = controller.signal;
      try {
        message('Загружаем видео…');
        let job = cachedMesh ? await cachedMesh.start(file, signal) : await (await request('', { method: 'POST', body: file, signal,
          headers: { 'Content-Type': file.type || 'application/octet-stream',
            'X-Cyber102-Mesh-Profile': 'fast' } })).json();
        if (!/^[0-9a-f]{40}$/.test(job.id)) throw new Error('Сервис 3D недоступен.');
        const path = '/' + job.id;
        while (stages[job.state]) {
          message(stages[job.state]);
          await new Promise(resolve => setTimeout(resolve, 1800));
          job = await (await request(path, { signal })).json();
        }
        if (job.state !== 'ready') throw new Error(job.message || 'Не удалось построить модель.');
        message(stages.exporting);
        buffer = await (await request(path + '/mesh', { signal })).arrayBuffer();
        meta = job;
        currentFile = file;
        marks.splice(0);
        hoveredMark = null;
        tooltip.hidden = true;
        markerLayer.replaceChildren();
        syncFindings();
        if (sourceUrl) URL.revokeObjectURL(sourceUrl);
        sourceUrl = URL.createObjectURL(file);
        sourceVideo.src = sourceUrl;
        const suggestions = Array.isArray(job.autoMarks) ? job.autoMarks.slice(0, 5) : [];
        for (const item of suggestions) {
          if (!item || !Array.isArray(item.point) || item.point.length !== 3 ||
              item.point.some(value => !Number.isFinite(value) || Math.abs(value) > 3) ||
              typeof item.label !== 'string' || item.label.length > 100 ||
              !Number.isFinite(item.time) || item.time < 0 || item.time > 30) continue;
          addMark(item.point, item.label, item.time);
        }
        findingsHelp.textContent = marks.length
          ? 'Сверьте метки с исходным видео.'
          : 'Повреждения не найдены. При необходимости добавьте точку.';
        sourceDetails.open = false;
        if (window.matchMedia('(min-width: 1450px)').matches) {
          if (marks[0]?.auto) seekSource(marks[0].time);
          else sourceDetails.open = true;
        }
        Object.assign(view, initialView);
        activate();
        onEquipment(job.equipmentLabel);
      } catch (error) {
        currentFile = null;
        message(error.name === 'AbortError' ? 'Сервер не успел завершить 3D. Попробуйте видео покороче.' : error.message, true);
      } finally {
        clearTimeout(timer);
        busy = input.disabled = false;
      }
    }
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      input.value = '';
      build(file);
    });
    panel.querySelector('#mesh-replace').addEventListener('click', () => input.click());
    panel.addEventListener('pointerdown', event => {
      if (!event.target.closest('.mesh-marker') && hoveredMark) hideTooltip(hoveredMark);
    });
    return { get busy() { return busy; }, open(file) { draw(); build(file); } };
  }

  window.CyberInspectionMesh = {
    create({ media, openModel, openMedia, onModeChange = () => {}, onEquipment = () => {} }) {
      const modes = document.getElementById('inspection-modes');
      const panel = document.getElementById('inspection-mesh');
      if (!modes || !panel) return null;
      const input = document.getElementById('media-file');
      const label = document.getElementById('media-file-label');
      const preview = model(panel, onEquipment);
      let mode = 'photo', mediaVersion = media.version;
      function display() {
        for (const button of modes.querySelectorAll('button')) {
          button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
        }
        document.body.dataset.captureSource = mode;
        panel.hidden = mode !== 'mesh';
        input.accept = mode === 'video' ? 'video/*,.mov,.mp4,.m4v,.webm' : 'image/*,.heic,.heif';
        const prompt = mode === 'video' ? 'Выбрать видео' : 'Выбрать фото';
        label.textContent = prompt;
        input.setAttribute('aria-label', prompt);
        onModeChange(mode);
      }
      input.addEventListener('cancel', () => {
        if (!media.kind || mode === 'mesh') return;
        mode = media.kind === 'uploaded_video' ? 'video' : 'photo';
        display();
      });
      modes.addEventListener('click', event => {
        const next = event.target.closest('button')?.dataset.mode;
        if (!next) return;
        mode = next;
        if (mode !== 'mesh' && media.kind && media.kind !== 'uploaded_' + mode) media.reset();
        display();
        if (mode === 'mesh') {
          openModel();
          preview.open(media.selectedVideo);
        } else {
          openMedia();
          if (!document.getElementById('inspection-library') && media.kind !== 'uploaded_' + mode) input.click();
        }
      });
      modes.hidden = false;
      display();
      return {
        get mode() { return mode; },
        get busy() { return preview.busy; },
        openFile(file) {
          if (preview.busy) return false;
          mode = 'mesh'; display(); openModel(); preview.open(file);
          return true;
        },
        sync() {
          if (media.version === mediaVersion) return;
          mediaVersion = media.version;
          if (mode === 'mesh' || !media.kind) return;
          mode = media.kind === 'uploaded_video' ? 'video' : 'photo';
          display();
        },
      };
    },
  };
})();
