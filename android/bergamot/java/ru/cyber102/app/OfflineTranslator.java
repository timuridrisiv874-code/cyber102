package ru.cyber102.app;

import android.annotation.SuppressLint;
import android.content.Context;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebMessage;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Collections;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.BooleanSupplier;

/** A private local WebView runs the packaged Firefox translator in a dedicated JS worker. */
final class OfflineTranslator implements AutoCloseable {
    static final String MODEL_ID = "Mozilla Firefox EN-RU 2.1";
    private static final String ORIGIN = "https://translation.invalid";
    private static final String ROOT = ORIGIN + "/translation-runtime/";
    private static final Uri TARGET_ORIGIN = Uri.parse(ORIGIN);
    private static final long START_TIMEOUT_MS = 90_000;
    private static final long TRANSLATION_TIMEOUT_MS = 90_000;
    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());
    // Only called by the model executor. WebView access is confined to the main thread.
    private RuntimeSession session;

    OfflineTranslator(Context context) {
        this.context = context.getApplicationContext();
    }

    long prepare() throws IOException {
        return prepare(() -> false);
    }

    synchronized long prepare(BooleanSupplier cancelled) throws IOException {
        checkCaller(cancelled);
        if (session != null && session.failure == null) return 0;
        close();
        long start = SystemClock.elapsedRealtime();
        RuntimeSession runtime = new RuntimeSession();
        session = runtime;
        main.post(runtime::open);
        try {
            await(runtime.ready, cancelled, START_TIMEOUT_MS);
            return SystemClock.elapsedRealtime() - start;
        } catch (IOException | RuntimeException error) {
            close();
            throw error;
        }
    }

    synchronized String translate(String english, BooleanSupplier cancelled) throws IOException {
        checkCaller(cancelled);
        if (english == null || english.trim().isEmpty()) throw new IOException("Нет текста для перевода");
        prepare(cancelled);
        RuntimeSession runtime = session;
        CompletableFuture<String> result = new CompletableFuture<>();
        runtime.pending = result;
        main.post(() -> {
            if (runtime.stopped) return;
            if (runtime.failure != null) {
                result.completeExceptionally(runtime.failure);
                return;
            }
            try {
                runtime.web.postWebMessage(new WebMessage(english), TARGET_ORIGIN);
            } catch (RuntimeException error) {
                runtime.fail(error.toString());
            }
        });
        try {
            String russian = await(result, cancelled, TRANSLATION_TIMEOUT_MS);
            if (russian == null || russian.trim().isEmpty()) throw new IOException("Переводчик вернул пустой текст");
            return russian.trim();
        } catch (IOException | RuntimeException error) {
            close();
            throw error;
        } finally {
            runtime.pending = null;
        }
    }

    private static void checkCaller(BooleanSupplier cancelled) {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IllegalStateException("Перевод требует фонового потока");
        if (cancelled.getAsBoolean()) throw new CancellationException("Перевод остановлен");
    }

    private static <T> T await(CompletableFuture<T> future, BooleanSupplier cancelled, long timeoutMs)
            throws IOException {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (true) {
            checkCaller(cancelled);
            long remaining = deadline - SystemClock.elapsedRealtime();
            if (remaining <= 0) throw new IOException("Превышено время ожидания перевода");
            try {
                T value = future.get(Math.min(remaining, 100), TimeUnit.MILLISECONDS);
                checkCaller(cancelled);
                return value;
            } catch (TimeoutException waiting) {
                // Poll to interrupt an in-flight worker when the analysis is cancelled.
            } catch (ExecutionException error) {
                throw new IOException("Ошибка офлайн-переводчика", error.getCause());
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                throw new CancellationException("Перевод остановлен");
            }
        }
    }

    @Override public synchronized void close() {
        RuntimeSession old = session;
        session = null;
        if (old != null) {
            old.stopped = true;
            main.post(old::destroy);
        }
    }

    private final class RuntimeSession {
        final CompletableFuture<Boolean> ready = new CompletableFuture<>();
        volatile CompletableFuture<String> pending;
        volatile IOException failure;
        volatile boolean stopped;
        WebView web;

        @SuppressLint("SetJavaScriptEnabled")
        void open() {
            if (stopped) return;
            try {
                web = new WebView(context);
                WebSettings settings = web.getSettings();
                settings.setAllowFileAccess(false);
                settings.setAllowContentAccess(false);
                settings.setBlockNetworkLoads(true);
                settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
                settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
                settings.setJavaScriptEnabled(true);
                web.addJavascriptInterface(this, "TranslationBridge");
                web.setWebViewClient(new WebViewClient() {
                    @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                        return true;
                    }
                    @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                        return asset(request);
                    }
                    @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                        fail("Процесс перевода завершился");
                        destroy();
                        return true;
                    }
                });
                web.loadUrl(ROOT + "engine.html");
            } catch (RuntimeException | LinkageError error) {
                fail(error.toString());
            }
        }

        @JavascriptInterface public void ready() {
            if (!stopped) ready.complete(true);
        }

        @JavascriptInterface public void translated(String text) {
            CompletableFuture<String> active = pending;
            if (!stopped && active != null) active.complete(text);
        }

        @JavascriptInterface public void failed(String reason) {
            if (!stopped) fail(reason);
        }

        void fail(String reason) {
            IOException error = new IOException(reason);
            failure = error;
            ready.completeExceptionally(error);
            CompletableFuture<String> active = pending;
            if (active != null) active.completeExceptionally(error);
        }

        void destroy() {
            if (web != null) {
                web.removeJavascriptInterface("TranslationBridge");
                web.destroy();
                web = null;
            }
        }
    }

    private WebResourceResponse asset(WebResourceRequest request) {
        String url = request.getUrl().toString();
        if (!"GET".equals(request.getMethod()) || !url.startsWith(ROOT)) return missing();
        String name = url.substring(ROOT.length());
        String mime;
        switch (name) {
            case "engine.html": mime = "text/html"; break;
            case "engine.js":
            case "bergamot-translator-worker.js": mime = "text/javascript"; break;
            case "bergamot-translator-worker.wasm": mime = "application/wasm"; break;
            case "model.enru.intgemm.alphas.bin":
            case "lex.50.50.enru.s2t.bin":
            case "vocab.enru.spm": mime = "application/octet-stream"; break;
            default: return missing();
        }
        try {
            InputStream input = context.getAssets().open("translation-runtime/" + name);
            return new WebResourceResponse(mime, mime.startsWith("text/") ? "UTF-8" : null,
                    200, "OK", Collections.singletonMap("Cache-Control", "no-store"), input);
        } catch (IOException error) {
            return missing();
        }
    }

    private static WebResourceResponse missing() {
        return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found",
                Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
    }
}
