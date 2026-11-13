/* Browser-only asynchronous analysis. Legacy bundles do not load this helper. */
(() => {
  'use strict';
  const DEADLINE_MS = 13 * 60 * 1000;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const JOB_ID = /^[0-9a-f]{32}$/;
  const abortError = () => new DOMException('Анализ отменён.', 'AbortError');
  function progressText(job) {
    if (job.status === 'queued') return Number.isInteger(job.position) && job.position > 0
      ? `В очереди · позиция ${job.position}` : 'В очереди…';
    return job.status === 'cancelling' ? 'Отменяем анализ…' : 'Анализируем…';
  }
  function create({ fetch: fetcher = globalThis.fetch.bind(globalThis), storage,
    uuid = () => globalThis.crypto.randomUUID(), deadlineMs = DEADLINE_MS,
    delay = (ms, signal) => new Promise((resolve, reject) => {
      if (signal.aborted) { reject(abortError()); return; }
      const stop = () => { clearTimeout(timer); reject(abortError()); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve(); }, ms);
      signal.addEventListener('abort', stop, {once:true});
    }) } = {}) {
    let capabilitiesPromise, client;
    function clientId() {
      if (client) return client;
      try { storage ??= globalThis.sessionStorage; client = storage?.getItem('cyber102.analysis-client.v1'); } catch (_) { /* Private mode may deny storage. */ }
      if (!UUID.test(client || '')) {
        client = uuid();
        if (!UUID.test(client)) throw new Error('Не удалось создать идентификатор запроса. Обновите страницу.');
        try { storage?.setItem('cyber102.analysis-client.v1', client); } catch (_) { /* In-memory token remains stable for this page. */ }
      }
      return client;
    }
    function setCapabilities(value) { capabilitiesPromise = Promise.resolve(value); }
    async function discover() {
      if (!capabilitiesPromise) capabilitiesPromise = (async () => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5000);
        try {
          const response = await fetcher('/api/capabilities', {signal:controller.signal, cache:'no-store', credentials:'same-origin', redirect:'error'});
          if (response.status === 404) return {};
          if (!response.ok) throw new Error('Не удалось проверить доступность анализа. Повторите позже.');
          return await response.json();
        } finally { clearTimeout(timer); }
      })().catch(error => { capabilitiesPromise = null; throw error.name === 'AbortError' ? new Error('Сервис анализа не ответил. Повторите позже.') : error; });
      return capabilitiesPromise;
    }
    function queuedResponse(response) {
      Object.defineProperty(response, 'analysisQueued', {value:true});
      return response;
    }
    function jsonResponse(data, status = 200) {
      return queuedResponse(new Response(JSON.stringify(data), {status, headers:{'Content-Type':'application/json'}}));
    }
    function validateJob(data, expectedId) {
      const job = data?.job;
      if (!job || !JOB_ID.test(job.id || '') || (expectedId && job.id !== expectedId)
          || !['queued','running','cancelling','succeeded','failed','cancelled','expired'].includes(job.status)) {
        throw new Error('Сервер вернул некорректное состояние очереди. Повторите позже.');
      }
      return job;
    }
    async function request(endpoint, options = {}, onProgress = () => {}) {
      const originalSignal = options.signal;
      if (originalSignal?.aborted) throw abortError();
      const controller = new AbortController();
      let submitted = false, terminal = false, cancelSent = false, requestId, identity, timedOut = false;
      const cancel = () => {
        if (!submitted || terminal || cancelSent) return;
        cancelSent = true;
        // Independent from the aborted upload/poll; request-key cancellation also
        // covers acceptance before the client has received the server job ID.
        try {
          Promise.resolve(fetcher(`/api/analysis-jobs/by-request/${requestId}/cancel`, {
            method:'POST', headers:{'X-Analysis-Client':identity}, credentials:'same-origin',
            cache:'no-store', redirect:'error', keepalive:true,
          })).catch(() => {});
        } catch (_) { /* TTL still bounds a request if the network disappears. */ }
      };
      const stop = () => { controller.abort(); cancel(); };
      originalSignal?.addEventListener('abort', stop, {once:true});
      const timer = setTimeout(() => { timedOut = true; stop(); }, deadlineMs);
      try {
        // Race discovery against cancellation without aborting another caller's discovery.
        const capabilities = await new Promise((resolve, reject) => {
          const abort = () => reject(abortError());
          controller.signal.addEventListener('abort', abort, {once:true});
          discover().then(resolve, reject).finally(() => controller.signal.removeEventListener('abort', abort));
        });
        if (controller.signal.aborted) throw abortError();
        if (capabilities?.vision?.queue?.enabled !== true) {
          return await fetcher(endpoint, {...options, signal:controller.signal});
        }
        identity = clientId(); requestId = uuid();
        if (!UUID.test(requestId)) throw new Error('Не удалось создать идентификатор анализа. Обновите страницу.');
        const headers = new Headers(options.headers);
        headers.set('Prefer','respond-async'); headers.set('X-Analysis-Client',identity); headers.set('X-Analysis-Request',requestId);
        submitted = true;
        const accepted = await fetcher(endpoint, {...options, headers, signal:controller.signal, credentials:'same-origin', redirect:'error'});
        if (controller.signal.aborted) throw abortError();
        if (accepted.status !== 202) { terminal = true; return queuedResponse(accepted); }
        let job = validateJob(await accepted.json());
        const jobId = job.id;
        const suggestedInterval = Number(capabilities.vision.queue.pollIntervalSeconds);
        const interval = Number.isFinite(suggestedInterval) && suggestedInterval > 0
          ? Math.max(500, Math.min(5000, suggestedInterval * 1000)) : 2000;
        let failures = 0, lastProgress;
        for (;;) {
          if (controller.signal.aborted) throw abortError();
          if (job.status === 'succeeded') {
            if (!job.result || typeof job.result !== 'object' || Array.isArray(job.result)) throw new Error('В очереди отсутствует результат анализа. Повторите позже.');
            terminal = true; return jsonResponse(job.result);
          }
          if (job.status === 'failed' || job.status === 'expired') {
            terminal = true;
            const status = Number.isInteger(job.errorStatus) && job.errorStatus >= 400 && job.errorStatus <= 599 ? job.errorStatus : job.status === 'expired' ? 410 : 502;
            return jsonResponse({error:typeof job.error === 'string' ? job.error : 'Анализ не завершился. Повторите позже.', code:job.code}, status);
          }
          if (job.status === 'cancelled') { terminal = true; throw abortError(); }
          const progress = progressText(job);
          if (progress !== lastProgress) { lastProgress = progress; onProgress(progress); }
          await delay(interval, controller.signal);
          let poll;
          try {
            poll = await fetcher(`/api/analysis-jobs/${jobId}`, {headers:{'X-Analysis-Client':identity}, signal:controller.signal,
              cache:'no-store', credentials:'same-origin', redirect:'error'});
          } catch (error) {
            if (controller.signal.aborted) throw error;
            if (++failures >= 3) throw new Error('Связь с анализом прервалась. Повторите позже.');
            continue;
          }
          if (!poll.ok) {
            if (poll.status >= 500 && ++failures < 3) continue;
            terminal = poll.status === 404;
            if (poll.status === 404) return jsonResponse({error:'Результат больше недоступен. Запустите анализ ещё раз.', code:'analysis_result_unavailable'}, 410);
            throw new Error('Не удалось получить состояние анализа. Повторите позже.');
          }
          job = validateJob(await poll.json(), jobId); failures = 0;
        }
      } catch (error) {
        cancel();
        if (timedOut) throw new Error('Время ожидания анализа истекло. Повторите позже.');
        if (originalSignal?.aborted || controller.signal.aborted) throw abortError();
        throw error;
      } finally { clearTimeout(timer); originalSignal?.removeEventListener('abort', stop); }
    }
    return {request, setCapabilities};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create, progressText, DEADLINE_MS};
  if (typeof window !== 'undefined') window.CyberAnalysisJobs = create();
})();
