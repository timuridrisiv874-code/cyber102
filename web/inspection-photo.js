/* Normalize an engineer's evidence photo before including it in an inspection. */
(() => {
  const mimeByExtension = { jpeg: 'image/jpeg', jpg: 'image/jpeg', png: 'image/png', webp: 'image/webp',
    heic: 'image/heic', heif: 'image/heif', avif: 'image/avif', gif: 'image/gif' };
  async function toJpeg(file) {
    if (!file || !file.size || file.size > 20 * 1024 * 1024) throw new Error('Выберите фотографию размером до 20 МБ.');
    const extension = file.name?.split('.').pop()?.toLowerCase();
    const mime = file.type && !/^(application|binary)\/octet-stream$/i.test(file.type)
      ? file.type : mimeByExtension[extension];
    if (!mime?.startsWith('image/')) throw new Error('Для этого снимка выберите фотографию.');
    const url = URL.createObjectURL(mime === file.type ? file : file.slice(0, file.size, mime));
    const image = new Image();
    try {
      image.src = url;
      try { await image.decode(); }
      catch { throw new Error('Фотография не открылась. Выберите JPEG, PNG или другой снимок из галереи.'); }
      const { naturalWidth: width, naturalHeight: height } = image;
      if (!width || !height || width * height > 50_000_000) throw new Error('Выберите фото разрешением до 50 мегапикселей.');
      const canvas = document.createElement('canvas');
      const scale = Math.min(1, 1600 / Math.max(width, height));
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Не удалось подготовить фотографию.');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.84, 0.65]) {
        const data = canvas.toDataURL('image/jpeg', quality);
        if (data.startsWith('data:image/jpeg;base64,') && data.length < 2 * 1024 * 1024 * 4 / 3) {
          return data.slice('data:image/jpeg;base64,'.length);
        }
      }
      throw new Error('Фотографию не удалось уменьшить. Выберите другой снимок.');
    } finally { URL.revokeObjectURL(url); }
  }
  window.CyberInspectionPhoto = { toJpeg };
})();
