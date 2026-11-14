(() => {
  const STORAGE_KEY = 'cyber102.mesh-cache.v1';
  const MAX_AGE = 7 * 86400000;
  const ID = /^[a-f0-9]{40}$/;
  const HASH = /^[a-f0-9]{64}$/;
  const STATES = ['queued', 'frames', 'building', 'exporting', 'ready'];
  function create({request, storage, crypto = globalThis.crypto, clock = Date.now}) {
    function records() {
      try {
        storage ??= globalThis.localStorage;
        const value = JSON.parse(storage?.getItem(STORAGE_KEY) || '[]');
        return Array.isArray(value) ? value.filter(row => row && HASH.test(row.key) && ID.test(row.id)
          && Number.isFinite(row.when) && clock() - row.when >= 0 && clock() - row.when < MAX_AGE).slice(-16) : [];
      } catch { return []; }
    }
    function remember(key, id) {
      if (!key || !ID.test(id)) return;
      try {
        const rows = records().filter(row => row.key !== key);
        rows.push({key, id, when:clock()});
        storage?.setItem(STORAGE_KEY, JSON.stringify(rows.slice(-16)));
      } catch { /* The server cache remains available when private mode denies storage. */ }
    }
    async function keyFor(file, signal) {
      try {
        const capabilities = await (await request('/capabilities', {signal})).json();
        if (!HASH.test(capabilities.cacheVersion || '') || !crypto?.subtle) return null;
        const fingerprint = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        const input = new TextEncoder().encode([...new Uint8Array(fingerprint)]
          .map(value => value.toString(16).padStart(2, '0')).join('') + ':fast:' + capabilities.cacheVersion);
        const key = await crypto.subtle.digest('SHA-256', input);
        return [...new Uint8Array(key)].map(value => value.toString(16).padStart(2, '0')).join('');
      } catch (error) {
        if (signal?.aborted) throw error;
        return null;
      }
    }
    return {async start(file, signal) {
      const key = await keyFor(file, signal);
      if (signal?.aborted) throw new DOMException('Операция отменена', 'AbortError');
      const saved = key && records().find(row => row.key === key);
      if (saved) {
        try {
          const job = await (await request('/' + saved.id, {signal})).json();
          if (job.id === saved.id && STATES.includes(job.state)) {
            remember(key, job.id);
            return job;
          }
        } catch (error) { if (signal?.aborted) throw error; }
      }
      const job = await (await request('', {method:'POST', body:file, signal,
        headers:{'Content-Type':file.type || 'application/octet-stream', 'X-Cyber102-Mesh-Profile':'fast'}})).json();
      if (STATES.includes(job.state)) remember(key, job.id);
      return job;
    }};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create, STORAGE_KEY};
  if (typeof window !== 'undefined') window.CyberMeshCache = {create};
})();
