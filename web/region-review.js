// A local full-frame preview. The returned rectangle always uses source pixels.
(() => {
  function sourcePoint(clientX, clientY, displayed, width, height) {
    if (!(displayed.width > 0 && displayed.height > 0)) return null;
    const clamp = value => Math.max(0, Math.min(1, value));
    return { x: clamp((clientX - displayed.left) / displayed.width) * width,
      y: clamp((clientY - displayed.top) / displayed.height) * height };
  }

  function rectangle(first, second) {
    if (!first || !second || first.x === second.x || first.y === second.y) return null;
    const x = Math.floor(Math.min(first.x, second.x)), y = Math.floor(Math.min(first.y, second.y));
    return { x, y, width: Math.ceil(Math.max(first.x, second.x)) - x,
      height: Math.ceil(Math.max(first.y, second.y)) - y };
  }

  function fits(box, width, height) {
    return box && [box.x, box.y, box.width, box.height].every(Number.isSafeInteger)
      && box.x >= 0 && box.y >= 0 && box.width > 0 && box.height > 0
      && box.x + box.width <= width && box.y + box.height <= height;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {sourcePoint, rectangle, fits};
  if (typeof window === 'undefined') return;

  let cancelPrevious;
  async function choose(sourceCanvas, {signal} = {}) {
    if (signal?.aborted) return null;
    if (!(sourceCanvas instanceof HTMLCanvasElement) || !sourceCanvas.width || !sourceCanvas.height) {
      throw new Error('Для выбора области нужен готовый кадр.');
    }
    cancelPrevious?.();
    const width = sourceCanvas.width, height = sourceCanvas.height;
    const scale = Math.min(1, 1600 / Math.max(width, height));
    const preview = document.createElement('canvas');
    preview.width = Math.max(1, Math.round(width * scale));
    preview.height = Math.max(1, Math.round(height * scale));
    const copy = preview.getContext('2d');
    if (!copy) throw new Error('Не удалось показать кадр.');
    copy.drawImage(sourceCanvas, 0, 0, preview.width, preview.height);
    const previousFocus = document.activeElement;
    const dialog = document.createElement('dialog');
    dialog.className = 'region-review';
    dialog.setAttribute('aria-labelledby', 'region-review-title');
    const make = (tag, label) => {
      const node = document.createElement(tag);
      if (label !== undefined) node.textContent = label;
      return node;
    };
    const title = make('h2', 'Выбрать область');
    title.id = 'region-review-title'; title.tabIndex = -1;
    const instructions = make('p', 'Обведите участок кадра или укажите два противоположных угла.');
    instructions.className = 'region-review-hint';
    const stage = make('div'); stage.className = 'region-review-stage';
    const canvas = make('canvas'); canvas.width = preview.width; canvas.height = preview.height;
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', 'Полный кадр с выбранной областью');
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Не удалось показать область.');
    stage.append(canvas);
    const details = make('details'); details.className = 'region-review-coordinates';
    details.append(make('summary', 'Координаты в пикселях'));
    const fields = make('div'); fields.className = 'region-review-fields';
    const inputs = {};
    for (const [key, name, min, max] of [['x','Слева',0,width-1], ['y','Сверху',0,height-1],
      ['width','Ширина',1,width], ['height','Высота',1,height]]) {
      const label = make('label', name), input = make('input');
      input.type = 'number'; input.inputMode = 'numeric'; input.step = '1'; input.required = true;
      input.min = String(min); input.max = String(max);
      label.append(input); fields.append(label); inputs[key] = input;
    }
    details.append(fields);
    const status = make('p'); status.className = 'region-review-status'; status.setAttribute('role', 'status');
    const actions = make('div'); actions.className = 'region-review-actions';
    const cancel = make('button', 'Отмена'), confirm = make('button', 'Проверить область');
    cancel.type = confirm.type = 'button'; cancel.className = 'button secondary'; confirm.className = 'button primary';
    actions.append(cancel, confirm); dialog.append(title, instructions, stage, details, status, actions);
    const initialWidth = Math.max(1, Math.round(width / 2)), initialHeight = Math.max(1, Math.round(height / 2));
    let selected = {x:Math.floor((width-initialWidth)/2), y:Math.floor((height-initialHeight)/2), width:initialWidth, height:initialHeight};
    let pendingPoint = null, drag = null;

    function draw() {
      const w = canvas.width, h = canvas.height;
      context.drawImage(preview, 0, 0);
      const line = Math.max(1, w / (canvas.getBoundingClientRect().width || w));
      if (selected) {
        const x = selected.x*w/width, y = selected.y*h/height, bw = selected.width*w/width, bh = selected.height*h/height;
        context.fillStyle = 'rgba(0,0,0,.58)';
        context.fillRect(0,0,w,y); context.fillRect(0,y+bh,w,h-y-bh);
        context.fillRect(0,y,x,bh); context.fillRect(x+bw,y,w-x-bw,bh);
        context.lineWidth = 3*line; context.strokeStyle = '#27231f'; context.strokeRect(x,y,bw,bh);
        context.lineWidth = 1.6*line; context.strokeStyle = '#fff'; context.strokeRect(x,y,bw,bh);
      } else if (pendingPoint) {
        const x = pendingPoint.x*w/width, y = pendingPoint.y*h/height;
        context.lineWidth = 3*line; context.strokeStyle = '#fff'; context.beginPath();
        context.moveTo(x-8*line,y); context.lineTo(x+8*line,y);
        context.moveTo(x,y-8*line); context.lineTo(x,y+8*line); context.stroke();
      }
    }
    function render(updateInputs = true) {
      if (updateInputs) for (const [key,input] of Object.entries(inputs)) input.value = selected ? String(selected[key]) : '';
      confirm.disabled = !selected || Boolean(pendingPoint);
      const value = pendingPoint ? 'Укажите противоположный угол.' : selected
        ? `Выбрано ${selected.width} × ${selected.height} пикселей из ${width} × ${height}.`
        : `Задайте область внутри кадра ${width} × ${height} пикселей.`;
      if (status.textContent !== value) status.textContent = value;
      draw();
    }
    fields.addEventListener('input', () => {
      if (drag) return;
      const box = Object.fromEntries(Object.entries(inputs).map(([key,input]) => [key,input.valueAsNumber]));
      pendingPoint = null;
      selected = fits(box,width,height) ? box : null;
      render(false);
    });
    const point = event => sourcePoint(event.clientX,event.clientY,canvas.getBoundingClientRect(),width,height);
    canvas.addEventListener('pointerdown', event => {
      if (drag || !event.isPrimary || event.button !== 0) return;
      const start = point(event); if (!start) return;
      event.preventDefault(); canvas.setPointerCapture(event.pointerId);
      drag = {id:event.pointerId, anchor:pendingPoint || start, clientX:event.clientX, clientY:event.clientY,
        previous:selected, pending:pendingPoint, moved:false};
    });
    function moved(event) {
      return Math.hypot(event.clientX-drag.clientX,event.clientY-drag.clientY) >= 4;
    }
    canvas.addEventListener('pointermove', event => {
      if (drag?.id !== event.pointerId) return;
      drag.moved ||= moved(event);
      if (!drag.moved) return;
      pendingPoint = null; selected = rectangle(drag.anchor,point(event)); render();
    });
    canvas.addEventListener('pointerup', event => {
      if (drag?.id !== event.pointerId) return;
      if (drag.pending || drag.moved || moved(event)) {
        selected = rectangle(drag.anchor,point(event)); pendingPoint = selected ? null : drag.anchor;
      } else { selected = null; pendingPoint = drag.anchor; }
      drag = null; render();
    });
    function cancelDrag(event) {
      if (drag?.id !== event.pointerId) return;
      selected = drag.previous; pendingPoint = drag.pending; drag = null; render();
    }
    canvas.addEventListener('pointercancel',cancelDrag);
    canvas.addEventListener('lostpointercapture',cancelDrag);

    return new Promise((resolve, reject) => {
      let settled = false;
      function finish(result) {
        if (settled) return;
        settled = true; signal?.removeEventListener('abort',onCancel);
        if (cancelPrevious === onCancel) cancelPrevious = undefined;
        if (dialog.open) dialog.close();
        dialog.remove();
        if (previousFocus?.isConnected) previousFocus.focus({preventScroll:true});
        resolve(result);
      }
      const onCancel = () => finish(null);
      cancelPrevious = onCancel;
      cancel.addEventListener('click',onCancel);
      confirm.addEventListener('click',() => { if (!pendingPoint && fits(selected,width,height)) finish({...selected}); });
      dialog.addEventListener('cancel',event => {event.preventDefault();onCancel();});
      dialog.addEventListener('close',onCancel);
      signal?.addEventListener('abort',onCancel,{once:true});
      document.body.append(dialog);
      try { dialog.showModal(); render(); title.focus({preventScroll:true}); }
      catch (error) {
        signal?.removeEventListener('abort',onCancel); cancelPrevious = undefined; dialog.remove(); reject(error);
      }
    });
  }
  window.CyberRegionReview = {choose};
})();
