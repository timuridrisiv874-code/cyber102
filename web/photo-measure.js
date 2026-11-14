// Compare two segments on the same photograph using a coplanar reference.
(() => {
  function distance(a, b, width, height) { return Math.hypot((a.x-b.x)*width, (a.y-b.y)*height); }
  function lengthMm({referenceMm, reference, target}, width, height) {
    return referenceMm * distance(...target, width, height) / distance(...reference, width, height);
  }
  function qrSquare(location, version, width, height) {
    const corners = ['topLeftCorner', 'topRightCorner', 'bottomRightCorner', 'bottomLeftCorner'].map(key => location?.[key]);
    if (!Number.isInteger(version) || version < 1 || version > 40 || corners.some(p =>
      !p || !Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < 1 || p.x >= width - 1 || p.y < 1 || p.y >= height - 1)) {
      throw new Error('В кадр должен попадать весь QR с белыми полями.');
    }
    const modules = 17 + version * 4;
    const edges = corners.map((p, i) => ({x: corners[(i + 1) % 4].x - p.x, y: corners[(i + 1) % 4].y - p.y}));
    const sides = edges.map(p => Math.hypot(p.x, p.y));
    const shortest = Math.min(...sides);
    if (shortest < Math.max(60, modules * 3)) throw new Error('QR слишком мелкий. Снимите узел и метку крупнее.');
    // A projected square must have nearly equal sides and nearly right angles in original pixels.
    const tilted = Math.max(...sides) > shortest * 1.10 || edges.some((a, i) => {
      const b = edges[(i + 1) % 4];
      return (a.x * b.y - a.y * b.x <= 0) || Math.abs(a.x * b.x + a.y * b.y) > 0.12 * sides[i] * sides[(i + 1) % 4];
    });
    if (tilted) throw new Error('QR снят под углом. Снимите метку и дефект прямо, в одной плоскости.');
    return corners.map(p => ({x: p.x / width, y: p.y / height}));
  }

  function hasCrispQrEdge(pixels, corners, modules) {
    // Compare sharpness across the three corner finder patterns, whose outer border is black.
    const {width, height, data} = pixels;
    const positions = corners.map(p => ({x:p.x * width, y:p.y * height}));
    function light(x, y) {
      const ix = Math.max(0, Math.min(width - 1, Math.round(x))), iy = Math.max(0, Math.min(height - 1, Math.round(y)));
      const i = 4 * (iy * width + ix);
      return 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }
    let sharp = 0;
    for (const i of [0, 1, 3]) {
      const p = positions[i];
      for (const direction of [1, -1]) {
        const side = positions[(i + 4 + direction) % 4], inside = positions[(i + 4 - direction) % 4];
        const sample = (along, across) => light(p.x + ((side.x - p.x) * along + (inside.x - p.x) * across) / modules,
          p.y + ((side.y - p.y) * along + (inside.y - p.y) * across) / modules);
        for (const along of [2.5, 4.5]) {
          const contrast = sample(along, -0.8) - sample(along, 0.8);
          if (contrast < 45) continue;
          // Allow a small decoder offset from the true printed boundary.
          const edge = Math.max(...[-0.2, 0, 0.2].map(offset => sample(along, offset - 0.2) - sample(along, offset + 0.2)));
          if (edge > contrast * 0.68) sharp++;
        }
      }
    }
    return sharp >= 8;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {lengthMm, qrSquare, hasCrispQrEdge};
  if (typeof window === 'undefined') return;
  function choose(photo, previous) {
    if (!photo.complete || !photo.naturalWidth || document.getElementById('measure-photo-dialog')) return Promise.resolve(undefined);
    const w = photo.naturalWidth, h = photo.naturalHeight;
    const dialog = document.createElement('dialog'); dialog.id = 'measure-photo-dialog';
    dialog.setAttribute('aria-labelledby','measure-title');
    dialog.innerHTML = `<h3 id="measure-title">Размер по фото</h3>
      <p>Снимите дефект прямо, с линейкой или QR в той же плоскости.</p>
      <button type="button" id="measure-qr">Взять сторону QR</button>
      <p id="measure-qr-status" role="status" hidden></p>
      <label for="measure-reference-mm">Длина известного отрезка, мм</label>
      <input id="measure-reference-mm" type="number" inputmode="decimal" min="0.001" max="10000" step="any" placeholder="Например, 50">
      <div id="measure-surface" tabindex="0" role="button" aria-label="Выбор точек на фото. Стрелки перемещают курсор, Enter ставит точку."><img alt="Крупный план для измерения"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"></svg></div>
      <output id="measure-status" aria-live="polite"></output><div class="measure-actions"><button type="button" data-undo>Шаг назад</button><button type="button" data-clear>Убрать замер</button><button type="button" data-cancel>Отмена</button><button id="measure-apply" type="button">Применить</button></div>`;
    document.body.append(dialog);
    const surface = dialog.querySelector('#measure-surface'), svg = surface.querySelector('svg');
    surface.querySelector('img').src = photo.currentSrc || photo.src;
    const input = dialog.querySelector('input'), apply = dialog.querySelector('#measure-apply');
    dialog.querySelector('[data-clear]').hidden = !previous;
    const points = previous ? [...previous.reference,...previous.target].map(p=>({...p})) : [];
    input.value = previous?.referenceMm ?? '';
    const qrButton = dialog.querySelector('#measure-qr'), qrStatus = dialog.querySelector('#measure-qr-status');
    let cursor = {x:0.5,y:0.5}, keyboard = false, result, qrCorners = null, qrRequest = 0;
    const value = () => ({referenceMm:input.valueAsNumber, reference:points.slice(0,2), target:points.slice(2)});
    const instructions = ['Отметьте начало известного отрезка.', 'Отметьте конец известного отрезка.', 'Отметьте начало дефекта.', 'Отметьте конец дефекта.'];
    function redraw() {
      svg.replaceChildren();
      if (qrCorners) {
        const polygon = document.createElementNS(svg.namespaceURI,'polygon');
        polygon.setAttribute('points',qrCorners.map(p => [p.x*100,p.y*100].join(',')).join(' '));
        polygon.setAttribute('fill','none');
        polygon.setAttribute('stroke','#65f5ff');
        polygon.setAttribute('stroke-width','1.5');
        polygon.setAttribute('vector-effect','non-scaling-stroke');
        svg.append(polygon);
      }
      const marks = [...points];
      if (keyboard && points.length < 4) marks.push(cursor);
      for (let i=0;i<marks.length;i++) {
        const p = marks[i], color = i < 2 ? '#65f5ff' : '#ffb94b';
        if (i%2 && i<points.length) {
          const line = document.createElementNS(svg.namespaceURI,'line');
          for (const [name,val] of Object.entries({x1:marks[i-1].x*100,y1:marks[i-1].y*100,x2:p.x*100,y2:p.y*100,stroke:color})) line.setAttribute(name,String(val));
          svg.append(line);
        }
        const marker = document.createElementNS(svg.namespaceURI,'line');
        for (const [name,val] of Object.entries({x1:p.x*100,y1:p.y*100,x2:p.x*100+0.01,y2:p.y*100,stroke:i<points.length?color:'white'})) marker.setAttribute(name,String(val));
        marker.classList.add('measure-mark'); svg.append(marker);
      }
      let message = instructions[points.length] || '';
      if (points.length>=2 && distance(points[0],points[1],w,h)<12) message = 'Выберите более длинный известный отрезок. Вернитесь на шаг назад.';
      else if (points.length===4 && distance(points[2],points[3],w,h)<2) message = 'Концы дефекта слишком близки. Вернитесь на шаг назад.';
      else if (points.length===4 && (!input.value || !input.validity.valid)) message = 'Укажите длину известного отрезка.';
      apply.disabled = points.length!==4 || Boolean(message);
      dialog.querySelector('[data-undo]').disabled = !points.length;
      dialog.querySelector('label[for=measure-reference-mm]').textContent = qrCorners ? 'Сторона чёрного квадрата QR, мм (без полей)' : 'Длина известного отрезка, мм';
      dialog.querySelector('output').textContent = message || `Длина по фото ≈ ${lengthMm(value(),w,h).toLocaleString('ru-RU',{maximumFractionDigits:1})} мм`;
    }
    function cancelQrScan() { qrRequest++; qrButton.disabled = false; qrStatus.hidden = true; }
    function add(p) { if (points.length<4) { cancelQrScan(); points.push(p); redraw(); } }
    qrButton.addEventListener('click', async () => {
      const id = ++qrRequest;
      qrStatus.textContent = 'Ищем QR на снимке…'; qrStatus.hidden = false; qrButton.disabled = true;
      try {
        let decode;
        try { decode = await window.CyberEquipmentQr.decoder(); }
        catch { throw new Error('Сканер QR недоступен. Отметьте линейку вручную.'); }
        if (id !== qrRequest || !dialog.open) return;
        const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext('2d', {willReadFrequently: true});
        ctx.drawImage(photo, 0, 0);
        const pixels = ctx.getImageData(0, 0, w, h);
        const code = decode(pixels.data, w, h, {inversionAttempts:'dontInvert'});
        if (!code) throw new Error('QR не прочитан. Сфокусируйтесь на метке или отметьте линейку вручную.');
        const corners = qrSquare(code.location, code.version, w, h);
        if (!hasCrispQrEdge(pixels, corners, 17 + code.version * 4))
          throw new Error('Граница QR размыта или плохо освещена. Повторите снимок или отметьте линейку вручную.');
        qrCorners = corners;
        points.splice(0, points.length, corners[0], corners[1]);
        input.value = '';
        keyboard = false;
        qrStatus.textContent = 'Измерьте чёрную сторону наклейки без белых полей и введите её длину.';
        redraw(); input.focus();
      } catch (error) {
        if (id === qrRequest && dialog.open) qrStatus.textContent = error.message || 'QR не прочитан. Отметьте линейку вручную.';
      } finally { if (id === qrRequest) qrButton.disabled = false; }
    });
    surface.addEventListener('click', e => {
      if (!e.detail) return;
      keyboard=false; const r=surface.getBoundingClientRect();
      add({x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))});
    });
    surface.addEventListener('keydown', e => {
      const delta = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];
      if (delta) {e.preventDefault();keyboard=true; const step=e.shiftKey?0.05:0.01;cursor={x:Math.max(0,Math.min(1,cursor.x+delta[0]*step)),y:Math.max(0,Math.min(1,cursor.y+delta[1]*step))};redraw();}
      if (e.key==='Enter'||e.key===' ') { e.preventDefault();keyboard=true;add({...cursor}); }
    });
    input.addEventListener('input',redraw);
    dialog.querySelector('[data-undo]').onclick=()=>{
      cancelQrScan();
      if (qrCorners && points.length === 2) { points.length = 0; qrCorners = null; input.value = ''; }
      else points.pop();
      redraw();
    };
    dialog.querySelector('[data-clear]').onclick=()=>{result=null;dialog.close();};
    dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
    apply.onclick=()=>{if(!apply.disabled){result=value();dialog.close();}};
    return new Promise(resolve=>{
      dialog.addEventListener('close',()=>{dialog.remove();resolve(result);},{once:true});
      dialog.showModal();redraw();
    });
  }
  window.CyberPhotoMeasure={lengthMm,choose};
})();
