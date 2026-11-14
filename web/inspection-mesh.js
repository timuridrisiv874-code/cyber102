(() => {
  'use strict';
  if (window.CyberWebBoot?.blocked) return;
  const endpoint = '/api/mesh-jobs';
  async function request(path, options) {
    const response = await fetch(endpoint + path, { cache: 'no-store', ...options });
    if (response.ok) return response;
    let error;
    try { error = (await response.json()).message; } catch {}
    throw new Error(error || 'Сервис 3D временно недоступен.');
  }

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
    return ({ yaw, pitch, zoom }) => {
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
    };
  }

  function model(panel) {
    const canvas = panel.querySelector('canvas');
    const upload = panel.querySelector('.mesh-start');
    const input = panel.querySelector('input');
    const status = panel.querySelector('.mesh-status');
    const toolbar = panel.querySelector('.mesh-toolbar');
    const initialView = { yaw: 0, pitch: 0, zoom: 1 };
    const view = { ...initialView };
    let render, drag, meta, buffer, currentFile, busy = false;
    const draw = () => render?.(view);
    const message = (text, error = false) => {
      status.textContent = text;
      status.dataset.error = String(error);
    };
    function activate() {
      try {
        render = renderer(canvas, meta, buffer);
        upload.hidden = true;
        canvas.hidden = toolbar.hidden = false;
        message('');
        draw();
      } catch {
        message('Этот браузер не смог открыть 3D. Попробуйте другой браузер.', true);
      }
    }

    toolbar.addEventListener('click', event => {
      const action = event.target.closest('button')?.dataset.action;
      if (action === 'left') view.yaw -= .3;
      if (action === 'right') view.yaw += .3;
      if (action === 'zoom-in') view.zoom = Math.min(2, view.zoom + .2);
      if (action === 'zoom-out') view.zoom = Math.max(.75, view.zoom - .2);
      if (action === 'reset') Object.assign(view, initialView);
      draw();
    });
    canvas.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
      if (drag?.id !== event.pointerId) return;
      view.yaw += (event.clientX - drag.x) * .009;
      view.pitch = Math.max(-1.15, Math.min(1.15, view.pitch + (event.clientY - drag.y) * .009));
      drag.x = event.clientX;
      drag.y = event.clientY;
      draw();
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
      input.disabled = true;
      canvas.hidden = toolbar.hidden = true;
      upload.hidden = false;
      render = null;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 24 * 60 * 1000);
      const signal = controller.signal;
      try {
        message('Загружаем видео…');
        let job = await (await request('', { method: 'POST', body: file, signal,
          headers: { 'Content-Type': file.type || 'application/octet-stream' } })).json();
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
        Object.assign(view, initialView);
        activate();
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
    return { open(file) { draw(); build(file); } };
  }

  window.CyberInspectionMesh = {
    create({ media, openModel, openMedia }) {
      const modes = document.getElementById('inspection-modes');
      const panel = document.getElementById('inspection-mesh');
      if (!modes || !panel) return null;
      const input = document.getElementById('media-file');
      const label = document.getElementById('media-file-label');
      const preview = model(panel);
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
        display();
        if (mode === 'mesh') {
          openModel();
          preview.open(media.selectedVideo);
        } else {
          openMedia();
          if (media.kind === 'uploaded_' + mode) return;
          input.click();
        }
      });
      modes.hidden = false;
      display();
      return {
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
