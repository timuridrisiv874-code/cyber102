package ru.cyber102.app;

import android.content.Context;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.os.Trace;
import android.os.StatFs;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Owns the single on-device model session. No HTTP client or model download path. */
final class SmolEngineManager {
    interface Listener { void onEvent(JSONObject event); }
    private static final int MAX_IMAGE_BYTES = 1536 * 1024;
    private static final int MAX_IMAGE_BASE64 = 4 * ((MAX_IMAGE_BYTES + 2) / 3);
    private static final String MODEL_ID = "LFM3-VL-2B";
    private static final int MAX_OUTPUT_TOKENS = 320;
    private static final String PROMPT =
            "Inspect this image. Briefly identify the visible industrial equipment or component, then describe clearly "
            + "visible damage or defects and their location. If no clear defect is visible, say so. Report only what "
            + "the image supports; do not infer internal faults, causes, operating status or severity. Use at most "
            + "70 words. Answer in English.";
    private static final String QUICK_PROMPT =
            "In English, name the visible industrial component, then state the clearest visible defect and its location. "
            + "If none is clear or the component is uncertain, say so. Use at most 28 words.";
    private final Context context;
    private final Listener listener;
    // Created, reused and closed only on the model executor.
    private OfflineTranslator translator;
    private final Handler main = new Handler(Looper.getMainLooper());
    // JNI owns one process-wide engine. Recreated activities must use the same
    // queue, and old activity teardown may never unload a new owner's session.
    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor(
            task -> new Thread(task, "Cyber102-lfm"));
    private static final AtomicReference<SmolEngineManager> OWNER = new AtomicReference<>();
    private final ExecutorService executor = EXECUTOR;
    private final AtomicBoolean busy = new AtomicBoolean();
    private final AtomicInteger generation = new AtomicInteger();
    private volatile boolean loaded;
    private volatile int sessionLoads;
    private volatile boolean closed;
    private volatile String state = "idle";
    private volatile String stateMessage = "Модель ещё не загружена";
    private volatile int progress;
    private volatile String revision = "";
    private volatile String modelId = MODEL_ID;
    private volatile long modelBytes;
    private volatile String modelFileName = "файл языковой модели из комплекта этой сборки";
    private volatile String projectorFileName = "файл визуального проектора из того же комплекта";

    SmolEngineManager(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        SmolEngineManager previous = OWNER.getAndSet(this);
        if (previous != null) {
            previous.generation.incrementAndGet();
            if (SmolNative.isAvailable()) SmolNative.cancel();
            executor.execute(() -> {
                previous.releaseTranslator();
                if (OWNER.get() == this && SmolNative.isAvailable()) SmolNative.unload();
            });
        }
    }

    JSONObject snapshot() {
        return json("type", "status", "state", state, "message", stateMessage, "progress", progress,
                "loaded", loaded, "sessionLoads", sessionLoads, "busy", busy.get(), "available", SmolNative.isAvailable(),
                "model", modelId, "revision", revision, "modelBytes", modelBytes,
                "modelFileName", modelFileName, "projectorFileName", projectorFileName);
    }

    String importFileName(String role) { return "model".equals(role) ? modelFileName : projectorFileName; }

    private static JSONObject json(Object... pairs) {
        JSONObject value = new JSONObject();
        try { for (int i = 0; i < pairs.length; i += 2) value.put((String) pairs[i], pairs[i + 1]); }
        catch (org.json.JSONException error) { throw new IllegalStateException(error); }
        return value;
    }

    private void emit(JSONObject event) {
        main.post(() -> { if (!closed && OWNER.get() == this) listener.onEvent(event); });
    }

    private void status(String value, String message, int percent) {
        state = value; stateMessage = message; progress = percent;
        emit(snapshot());
    }

    private int start(String requestId) {
        if (closed || OWNER.get() != this || !busy.compareAndSet(false, true)) {
            emit(json("type", "error", "requestId", requestId, "code", "busy",
                    "message", "На телефоне уже выполняется операция. Дождитесь завершения отмены."));
            return -1;
        }
        return generation.incrementAndGet();
    }

    void load() {
        if (loaded) { emit(snapshot()); return; }
        int token = start("");
        if (token < 0) return;
        executor.execute(() -> {
            try {
                ensureLoaded(token);
                status("ready", modelId + " готова. Можно проверять кадры один за другим.", 100);
            } catch (Exception error) {
                if (token != generation.get()) status("idle", "Подготовка модели отменена.", 0);
                else status("error", safeMessage(error), 0);
            } finally {
                busy.set(false);
                emit(snapshot());
            }
        });
    }

    // Invoked only on the single model executor. A frame can resume a released session.
    private void ensureLoaded(int token) throws Exception {
        check(token);
        if (loaded) return;
        Trace.beginSection("Cyber102.lfm.prepare");
        try {
            check(token);
            if (!SmolNative.initialize()) throw new IOException("Не удалось загрузить движок LFM3. Для этой сборки нужен 64-битный Android на ARM.");
            long epoch = SmolNative.beginOperation();
            check(token);
            status("preparing", "Проверяем и подготавливаем модель на телефоне…", 0);
            List<ModelFile> files = manifest();
            status("preparing", "Проверяем файлы " + modelId + " на телефоне…", 0);
            File folder = modelFolder();
            for (int i = 0; i < files.size(); i++) prepare(files.get(i), folder, token, i);
            check(token);
            status("loading", "Загружаем " + modelId + " в память телефона…", 100);
            int processors = Runtime.getRuntime().availableProcessors();
            // Leave CPU capacity for the camera and WebView during generation.
            int threads = Math.max(2, Math.min(6, processors * 3 / 4));
            SmolNative.load(new File(folder, "model.gguf").getAbsolutePath(),
                    new File(folder, "projector.gguf").getAbsolutePath(), threads, epoch);
            check(token);
            loaded = true;
            sessionLoads++;
        } catch (Exception error) {
            loaded = false;
            if (OWNER.get() == this && SmolNative.isAvailable()) SmolNative.unload();
            throw error;
        } finally {
            Trace.endSection();
        }
    }

    void analyze(String requestId, String encoded) {
        analyze(requestId, encoded, "detailed");
    }

    void analyze(String requestId, String encoded, String profile) {
        if (requestId == null || !requestId.matches("[a-zA-Z0-9_-]{1,64}")) return;
        boolean quick = "quick".equals(profile);
        if (!quick && !"detailed".equals(profile)) {
            emit(json("type", "error", "requestId", requestId, "code", "invalid_profile", "message", "Неизвестный режим офлайн-анализа."));
            return;
        }
        if (encoded == null || encoded.length() > MAX_IMAGE_BASE64) {
            emit(json("type", "error", "requestId", requestId, "code", "invalid_image", "message", "Кадр слишком большой для офлайн-анализа."));
            return;
        }
        int token = start(requestId);
        if (token < 0) return;
        executor.execute(() -> {
            File image = null;
            try {
                byte[] jpeg = Base64.decode(encoded, Base64.NO_WRAP);
                if (jpeg.length == 0 || jpeg.length > MAX_IMAGE_BYTES || jpeg.length < 3 || (jpeg[0] & 255) != 255 || (jpeg[1] & 255) != 216) throw new IOException("Ожидается JPEG-кадр.");
                BitmapFactory.Options bounds = new BitmapFactory.Options(); bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, bounds);
                int edge = quick ? 512 : 1024;
                if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || bounds.outWidth > edge || bounds.outHeight > edge)
                    throw new IOException("Размер кадра для выбранного режима должен быть не более " + edge + " × " + edge + ".");
                OfflineFrameQuality.check(jpeg);
                check(token);
                image = File.createTempFile("lfm-frame-", ".jpg", context.getCacheDir());
                try (FileOutputStream output = new FileOutputStream(image)) { output.write(jpeg); }
                boolean cold = !loaded;
                long prepareStarted = SystemClock.elapsedRealtimeNanos();
                ensureLoaded(token);
                double loadMs = cold ? (SystemClock.elapsedRealtimeNanos() - prepareStarted) / 1_000_000d : 0;
                long epoch = SmolNative.beginOperation();
                check(token);
                status("analyzing", "LFM3 анализирует кадр на телефоне…", 0);
                JSONObject result;
                Trace.beginSection("Cyber102.lfm.inference");
                try {
                    result = new JSONObject(SmolNative.analyze(image.getAbsolutePath(), quick ? QUICK_PROMPT : PROMPT,
                            quick ? 64 : MAX_OUTPUT_TOKENS, epoch));
                } finally {
                    Trace.endSection();
                }
                check(token);
                if (result.optBoolean("cancelled")) throw new IOException("Операция отменена.");
                String text = result.optString("text", "").trim();
                if (text.isEmpty() || text.length() > 4000) throw new IOException("Модель не вернула пригодное наблюдение. Попробуйте другой кадр.");
                result.put("modelLoadMs", loadMs);
                result.put("inferenceProfile", profile);
                result.put("imageWidth", bounds.outWidth);
                result.put("imageHeight", bounds.outHeight);
                addRussianTranslation(result, text, token);
                check(token);
                emit(json("type", "result", "requestId", requestId, "result", result, "model", modelId,
                        "modelVersion", revision + (quick ? ".inspection-quick-en-v1" : ".inspection-en-v1")));
                status("ready", "Наблюдение готово. Проверьте его по изображению.", 100);
            } catch (Exception error) {
                emit(json("type", "error", "requestId", requestId, "code", token != generation.get() ? "cancelled" : "inference_failed",
                        "message", token != generation.get() ? "Анализ отменён." : safeMessage(error)));
                status(loaded ? "ready" : "idle", token != generation.get() ? "Анализ отменён." : safeMessage(error), 0);
            } finally {
                if (image != null) image.delete();
                busy.set(false); emit(snapshot());
            }
        });
    }

    private void addRussianTranslation(JSONObject result, String english, int token) throws Exception {
        status("translating", "Переводим описание на русский на телефоне…", 0);
        Trace.beginSection("Cyber102.translate.enru");
        long prepareStarted = SystemClock.elapsedRealtimeNanos();
        long decodeStarted = 0;
        try {
            if (translator == null) translator = new OfflineTranslator(context);
            result.put("translationLoadMs", translator.prepare(() -> cancelled(token)));
            check(token);
            decodeStarted = SystemClock.elapsedRealtimeNanos();
            String russian = translator.translate(english, () -> cancelled(token));
            check(token);
            if (russian == null || russian.trim().isEmpty() || russian.length() > 12000) {
                throw new IOException("Переводчик не вернул полный ответ.");
            }
            result.put("russian", russian.trim());
            result.put("translatorModel", OfflineTranslator.MODEL_ID);
        } catch (Exception | LinkageError error) {
            check(token);
            // A translation failure must not discard the visual observation.
            android.util.Log.w("Cyber102", "Offline translation unavailable", error);
            result.put("translationFailed", true);
            if (!result.has("translationLoadMs")) {
                result.put("translationLoadMs", (SystemClock.elapsedRealtimeNanos() - prepareStarted) / 1_000_000d);
            }
            releaseTranslator();
        } finally {
            if (decodeStarted != 0) {
                result.put("translationMs", (SystemClock.elapsedRealtimeNanos() - decodeStarted) / 1_000_000d);
            }
            Trace.endSection();
        }
    }

    private void releaseTranslator() {
        if (translator != null) {
            translator.close();
            translator = null;
        }
    }

    void importFile(Uri uri, String role) {
        if (!"model".equals(role) && !"projector".equals(role)) return;
        int token = start("");
        if (token < 0) return;
        executor.execute(() -> {
            boolean imported = false;
            try {
                loaded = false;
                check(token);
                if (SmolNative.isAvailable()) SmolNative.unload();
                ModelFile selected = null;
                for (ModelFile file : manifest()) if (file.role.equals(role)) selected = file;
                if (selected == null) throw new IOException("Файл не указан в манифесте модели.");
                status("importing", "Копируем и проверяем выбранный файл модели…", 0);
                try (InputStream input = context.getContentResolver().openInputStream(uri)) {
                    if (input == null) throw new IOException("Не удалось открыть выбранный файл.");
                    copy(input, new File(modelFolder(), role + ".gguf"), selected, token, role.equals("projector") ? 1 : 0);
                }
                check(token);
                imported = true;
                status("idle", "Файл проверен и сохранён на телефоне.", 100);
            } catch (Exception error) {
                status("error", token != generation.get() ? "Импорт отменён." : safeMessage(error), 0);
            } finally { busy.set(false); emit(snapshot()); }
            if (imported) emit(json("type", "imported", "role", role));
        });
    }

    private static String safeMessage(Exception error) {
        String text = error.getMessage();
        return text == null || text.isEmpty() ? "Не удалось выполнить офлайн-анализ." : text.substring(0, Math.min(300, text.length()));
    }

    private File modelFolder() throws IOException {
        File folder = new File(context.getFilesDir(), "lfm3-vl-2b");
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("Не удалось создать папку модели.");
        return folder;
    }

    private List<ModelFile> manifest() throws Exception {
        byte[] data;
        try (InputStream input = context.getAssets().open("models/model-manifest.json")) {
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            byte[] block = new byte[4096]; int count;
            while ((count = input.read(block)) != -1) {
                if (bytes.size() + count > 32768) throw new IOException("Манифест модели слишком большой.");
                bytes.write(block, 0, count);
            }
            data = bytes.toByteArray();
        }
        JSONObject metadata = new JSONObject(new String(data, StandardCharsets.UTF_8));
        String selectedModel = metadata.getString("modelId");
        if (!MODEL_ID.equals(selectedModel)) throw new IOException("Неизвестная офлайн-модель.");
        String selectedRevision = metadata.getString("revision");
        if (!selectedRevision.matches("[A-Za-z0-9._-]{1,128}")) throw new IOException("Некорректная версия модели.");
        JSONArray rows = metadata.getJSONArray("files");
        if (rows.length() != 2) throw new IOException("Нужны модель и визуальный проектор.");
        List<ModelFile> files = new ArrayList<>();
        for (int i = 0; i < rows.length(); i++) files.add(new ModelFile(rows.getJSONObject(i)));
        if (files.get(0).role.equals(files.get(1).role)) throw new IOException("Повторная роль файла модели.");
        long total = files.get(0).size + files.get(1).size;
        if (total > 3L * 1024 * 1024 * 1024) throw new IOException("Пакет офлайн-модели слишком большой.");
        for (ModelFile file : files) {
            String name = file.asset.substring("models/".length());
            if (file.role.equals("model")) modelFileName = name; else projectorFileName = name;
        }
        modelId = selectedModel; revision = selectedRevision; modelBytes = total;
        return files;
    }

    private void prepare(ModelFile model, File folder, int token, int index) throws Exception {
        File destination = new File(folder, model.role + ".gguf");
        if (destination.isFile() && destination.length() == model.size) {
            ModelVerificationStamp stamp = verificationStamp(model);
            check(token);
            String before = stamp.identity(destination);
            if (stamp.matches(before)) return;
            try (FileInputStream input = new FileInputStream(destination)) {
                String opened = stamp.identity(input.getFD());
                if (model.sha256.equals(hash(input, token))) {
                    check(token);
                    if (opened != null && (!opened.equals(stamp.identity(input.getFD())) || !opened.equals(stamp.identity(destination)))) {
                        throw new IOException("Файл модели изменился во время проверки.");
                    }
                    stamp.record(opened);
                    return;
                }
            }
        }
        try (InputStream input = context.getAssets().open(model.asset)) {
            copy(input, destination, model, token, index);
        } catch (java.io.FileNotFoundException error) {
            throw new IOException("В этой облегчённой сборке нет весов. Нажмите «Импортировать модель» и выберите два официальных GGUF-файла.");
        }
    }

    private ModelVerificationStamp verificationStamp(ModelFile model) {
        return new ModelVerificationStamp(context.getNoBackupFilesDir(), model.role, model.sha256);
    }

    private void copy(InputStream input, File destination, ModelFile model, int token, int index) throws Exception {
        File temporary = new File(destination.getParentFile(), destination.getName() + ".partial");
        if (temporary.exists() && !temporary.delete()) throw new IOException("Не удалось очистить незавершённый файл модели.");
        long required = model.size + 64L * 1024 * 1024;
        long available = new StatFs(destination.getParentFile().getAbsolutePath()).getAvailableBytes();
        if (available < required) throw new IOException("Для подготовки этого файла требуется " + displayBytes(required) +
                " свободного места; доступно " + displayBytes(available) + ". Размер всего комплекта: " + displayBytes(modelBytes) + ".");
        MessageDigest digest = MessageDigest.getInstance("SHA-256"); long total = 0, updateAt = 0;
        try {
            try (FileOutputStream output = new FileOutputStream(temporary)) {
                byte[] bytes = new byte[256 * 1024]; int count;
                while ((count = input.read(bytes)) != -1) {
                    check(token); total += count;
                    if (total > model.size) throw new IOException("Выбранный файл больше ожидаемого. Используйте файл указанной версии.");
                    output.write(bytes, 0, count); digest.update(bytes, 0, count);
                    long now = android.os.SystemClock.elapsedRealtime();
                    if (now - updateAt >= 250) { updateAt = now; status("preparing", "Проверяем файл " + (index + 1) + " из 2…", (int) (100 * total / model.size)); }
                }
                output.getFD().sync();
                if (total != model.size || !hex(digest.digest()).equals(model.sha256)) throw new IOException("Контрольная сумма файла не совпадает с комплектом LFM3.");
                check(token);
                Files.move(temporary.toPath(), destination.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
                // rename may change ctime. The still-open descriptor identifies the file we actually verified.
                ModelVerificationStamp stamp = verificationStamp(model);
                stamp.record(stamp.identity(output.getFD()));
            }
        } finally { temporary.delete(); }
    }

    private String hash(InputStream input, int token) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] bytes = new byte[256 * 1024]; int count;
        while ((count = input.read(bytes)) != -1) { check(token); digest.update(bytes, 0, count); }
        return hex(digest.digest());
    }

    private static String hex(byte[] data) {
        StringBuilder out = new StringBuilder(data.length * 2);
        for (byte value : data) out.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        return out.toString();
    }

    private static String displayBytes(long bytes) {
        return bytes >= 1000000000L ? String.format(java.util.Locale.ROOT, "%.2f ГБ", bytes / 1000000000d) :
                ((bytes + 999999L) / 1000000L) + " МБ";
    }

    private boolean cancelled(int token) {
        return closed || OWNER.get() != this || token != generation.get() || Thread.currentThread().isInterrupted();
    }

    private void check(int token) throws IOException {
        if (cancelled(token)) throw new IOException("Операция отменена.");
    }

    void cancel() {
        generation.incrementAndGet();
        if (OWNER.get() == this && SmolNative.isAvailable()) {
            Trace.beginSection("Cyber102.lfm.cancel");
            try {
                SmolNative.cancel();
            } finally {
                Trace.endSection();
            }
        }
        if (busy.get()) status("cancelling", "Останавливаем текущую операцию…", 0);
    }

    void unload() {
        loaded = false;
        cancel();
        if (!closed && OWNER.get() == this) executor.execute(() -> {
            if (OWNER.get() != this) return;
            releaseTranslator();
            if (SmolNative.isAvailable()) SmolNative.unload();
            loaded = false;
            status("idle", "Модель выгружена из памяти.", 0);
        });
    }

    void close() {
        closed = true; cancel();
        if (OWNER.compareAndSet(this, null)) executor.execute(() -> {
            releaseTranslator();
            if (OWNER.get() == null && SmolNative.isAvailable()) SmolNative.unload();
            loaded = false;
        });
    }

    private static final class ModelFile {
        final String role, asset, sha256; final long size;
        ModelFile(JSONObject row) throws Exception {
            role = row.getString("role"); asset = row.getString("asset"); sha256 = row.getString("sha256"); size = row.getLong("size");
            if ((!role.equals("model") && !role.equals("projector")) || !asset.matches("models/[A-Za-z0-9._-]+\\.gguf") ||
                    !sha256.matches("[a-f0-9]{64}") || size < 1024 || size > 2L * 1024 * 1024 * 1024) throw new IOException("Некорректный манифест офлайн-модели.");
        }
    }
}
