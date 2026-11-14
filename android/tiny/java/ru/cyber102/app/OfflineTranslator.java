package ru.cyber102.app;

import android.content.Context;
import android.content.res.AssetManager;
import android.os.SystemClock;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;
import java.util.regex.Pattern;

/** On-device English to Russian translation using the model embedded in the APK. */
final class OfflineTranslator implements AutoCloseable {
    static final String MODEL_ID = "Helsinki-NLP/opus-mt_tiny_eng-rus";
    private static final Pattern SENTENCE_BREAK = Pattern.compile("(?<=[.!?])\\s+|\\n+");
    private final AssetManager assets;
    private long handle;

    OfflineTranslator(Context context) {
        assets = context.getApplicationContext().getAssets();
    }

    synchronized long prepare() throws IOException {
        if (handle != 0) return 0;
        long start = SystemClock.elapsedRealtime();
        try {
            System.loadLibrary("cyber_translation");
            handle = nativeOpen(assets);
        } catch (LinkageError e) {
            throw new IOException("Не удалось загрузить офлайн-переводчик", e);
        }
        if (handle == 0) throw new IOException("Не удалось открыть модель перевода");
        return SystemClock.elapsedRealtime() - start;
    }

    synchronized long prepare(BooleanSupplier cancelled) throws IOException {
        checkCancelled(cancelled);
        long elapsed = prepare();
        checkCancelled(cancelled);
        return elapsed;
    }

    synchronized String translate(String english, BooleanSupplier cancelled) throws IOException {
        checkCancelled(cancelled);
        if (handle == 0) prepare();
        StringBuilder result = new StringBuilder();
        for (String sentence : SENTENCE_BREAK.split(english.trim())) {
            if (sentence.trim().isEmpty()) continue;
            checkCancelled(cancelled);
            byte[] ru = nativeTranslate(handle, sentence.trim().getBytes(StandardCharsets.UTF_8));
            if (ru == null || ru.length == 0) throw new IOException("Переводчик вернул пустой текст");
            if (result.length() > 0) result.append(' ');
            result.append(new String(ru, StandardCharsets.UTF_8));
        }
        checkCancelled(cancelled);
        if (result.length() == 0) throw new IOException("Нет текста для перевода");
        return result.toString();
    }

    private static void checkCancelled(BooleanSupplier cancelled) {
        if (cancelled.getAsBoolean()) throw new CancellationException("Перевод остановлен");
    }

    @Override public synchronized void close() {
        if (handle != 0) {
            nativeClose(handle);
            handle = 0;
        }
    }

    private static native long nativeOpen(AssetManager assets) throws IOException;
    private static native byte[] nativeTranslate(long handle, byte[] sentence) throws IOException;
    private static native void nativeClose(long handle);
}
