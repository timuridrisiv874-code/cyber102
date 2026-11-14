package ru.cyber102.app;

import static org.junit.Assert.*;

import android.content.Context;
import android.os.Debug;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.AtomicFile;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Assume;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

/** Optional replay of fixed English captions using the translator shipped with the app. */
@RunWith(AndroidJUnit4.class)
public final class OfflineTranslationTest {
    @Test(timeout = 1200000) public void translateArchivedReports() throws Exception {
        String name = InstrumentationRegistry.getArguments().getString("translationInput");
        Assume.assumeTrue("Supply -e translationInput <basename.json> in the app cache", name != null);
        assertTrue("Input must be a JSON basename", name.matches("[a-zA-Z0-9_-]+\\.json"));
        Context target = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertEquals("Test requires airplane mode", 1,
                Settings.Global.getInt(target.getContentResolver(), Settings.Global.AIRPLANE_MODE_ON, 0));
        JSONArray captures = readCorpus(new File(target.getCacheDir(), name));
        assertTrue("At least one caption required", captures.length() > 0);
        File folder = new File(target.getFilesDir(), "qa");
        assertTrue(folder.isDirectory() || folder.mkdirs());
        AtomicFile destination = new AtomicFile(new File(folder, "translations.json"));
        Set<String> ids = new HashSet<>();
        JSONArray records = new JSONArray();
        JSONObject report = new JSONObject().put("translatorModel", OfflineTranslator.MODEL_ID)
                .put("records", records).put("complete", false)
                .put("androidApi", android.os.Build.VERSION.SDK_INT).put("input", name);
        int failed = 0;
        try (OfflineTranslator translator = new OfflineTranslator(target)) {
            report.put("prepareMs", translator.prepare());
            store(destination, report);
            for (int i = 0; i < captures.length(); i++) {
                JSONObject item = captures.getJSONObject(i);
                String id = item.getString("id");
                String en = item.getString("en");
                assertTrue("Duplicate id: " + id, ids.add(id));
                assertFalse("Empty source: " + id, en.trim().isEmpty());
                JSONObject result = new JSONObject().put("id", id).put("en", en);
                if (item.has("panel")) result.put("panel", item.get("panel"));
                long started = SystemClock.elapsedRealtimeNanos();
                try {
                    String translated = translator.translate(en, () -> false);
                    if (translated == null || translated.trim().isEmpty()) {
                        throw new IllegalStateException("Empty translation");
                    }
                    result.put("ru", translated);
                } catch (Exception | LinkageError error) {
                    failed++;
                    result.put("ru", "").put("error", error.toString());
                }
                result.put("translationMs", (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000d);
                records.put(result);
                store(destination, report);
            }
            report.put("processPssKiBAfterTranslation", Debug.getPss());
        }
        report.put("complete", true).put("failed", failed);
        store(destination, report);
        assertEquals("Translation failures", 0, failed);
    }

    private static JSONArray readCorpus(File file) throws Exception {
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            byte[] chunk = new byte[8192];
            int count;
            while ((count = input.read(chunk)) != -1) {
                assertTrue("Unexpectedly large test corpus", bytes.size() + count <= 1_000_000);
                bytes.write(chunk, 0, count);
            }
            return new JSONArray(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        }
    }

    private static void store(AtomicFile destination, JSONObject report) throws Exception {
        FileOutputStream output = destination.startWrite();
        try {
            output.write(report.toString(2).getBytes(StandardCharsets.UTF_8));
            destination.finishWrite(output);
        } catch (Exception error) {
            destination.failWrite(output);
            throw error;
        }
    }
}
