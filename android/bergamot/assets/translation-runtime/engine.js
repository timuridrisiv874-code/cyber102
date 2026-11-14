'use strict';

// A dedicated worker keeps inference off both the activity and the visible WebView.
function runTranslationWorker() {
  let service;
  let model;
  const config = [
    'beam-size: 1', 'normalize: 1.0', 'word-penalty: 0', 'max-length-break: 128',
    'mini-batch-words: 1024', 'workspace: 128', 'max-length-factor: 2.0',
    'skip-cost: true', 'cpu-threads: 0', 'quiet: true', 'quiet-translation: true',
    'gemm-precision: int8shiftAll', ''
  ].join('\n');

  function initialize(data) {
    self.Module = {
      wasmBinary: new Uint8Array(data.wasm),
      onRuntimeInitialized() {
        try {
          const m = self.Module;
          function aligned(buffer, alignment) {
            const memory = new m.AlignedMemory(buffer.byteLength, alignment);
            memory.getByteArrayView().set(new Uint8Array(buffer));
            return memory;
          }
          const vocab = new m.AlignedMemoryList();
          vocab.push_back(aligned(data.vocab, 64));
          service = new m.BlockingService({cacheSize: 0});
          model = new m.TranslationModel(config, aligned(data.model, 256),
            aligned(data.lex, 64), vocab, null);
          // The model now owns the aligned buffers. Release the original input copies.
          data = null;
          self.postMessage({type: 'ready'});
        } catch (error) {
          self.postMessage({type: 'error', value: String(error)});
        }
      },
      onAbort(reason) {
        self.postMessage({type: 'error', value: String(reason)});
      }
    };
    importScripts(data.glue);
  }

  function translate(source) {
    const m = self.Module;
    const input = new m.VectorString();
    const options = new m.VectorResponseOptions();
    let results, response;
    try {
      input.push_back(source);
      results = service.translate(model, input, options);
      response = results.get(0);
      return response.getTranslatedText();
    } finally {
      if (response) response.delete();
      if (results) results.delete();
      options.delete();
      input.delete();
    }
  }

  self.onmessage = ({data}) => {
    try {
      if (data.type === 'init') initialize(data);
      else if (data.type === 'translate') self.postMessage({type: 'result', value: translate(data.value)});
    } catch (error) {
      self.postMessage({type: 'error', value: String(error)});
    }
  };
}

(async () => {
  let workerUrl, glueUrl, worker;
  function revokeAssets() {
    if (workerUrl) URL.revokeObjectURL(workerUrl);
    if (glueUrl) URL.revokeObjectURL(glueUrl);
    workerUrl = glueUrl = null;
  }
  function stop() {
    if (worker) worker.terminate();
    revokeAssets();
  }
  window.addEventListener('pagehide', stop);
  function fail(reason) {
    stop();
    TranslationBridge.failed(String(reason));
  }
  try {
    async function read(file) {
      const response = await fetch(file, {credentials: 'omit', cache: 'no-store'});
      if (!response.ok) throw new Error(file + ' HTTP ' + response.status);
      return response.arrayBuffer();
    }
    // Fetch packaged assets from the document so every read is intercepted by WebViewClient,
    // including WebView versions that handle worker subresource requests separately.
    const [glue, wasm, model, lex, vocab] = await Promise.all([
      'bergamot-translator-worker.js', 'bergamot-translator-worker.wasm',
      'model.enru.intgemm.alphas.bin', 'lex.50.50.enru.s2t.bin', 'vocab.enru.spm'
    ].map(read));
    glueUrl = URL.createObjectURL(new Blob([glue], {type: 'text/javascript'}));
    workerUrl = URL.createObjectURL(new Blob(['(', runTranslationWorker.toString(), ')()'], {type: 'text/javascript'}));
    worker = new Worker(workerUrl);
    worker.onerror = event => fail(event.message || 'Worker failed');
    worker.onmessage = ({data}) => {
      if (data.type === 'ready') {
        revokeAssets();
        TranslationBridge.ready();
      } else if (data.type === 'result') {
        TranslationBridge.translated(data.value);
      } else {
        fail(data.value);
      }
    };
    window.addEventListener('message', event => {
      if (typeof event.data === 'string') worker.postMessage({type: 'translate', value: event.data});
    });
    worker.postMessage({type: 'init', glue: glueUrl, wasm, model, lex, vocab}, [wasm, model, lex, vocab]);
  } catch (error) {
    fail(error);
  }
})();
