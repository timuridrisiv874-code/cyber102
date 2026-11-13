package ru.cyber102.app;

import static org.junit.Assert.*;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.Debug;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Base64;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.widget.Button;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Runs the shipped WebView, Java bridge and real GGUF/JNI in airplane mode.
 * No mocked model, HTTP server, fixed diagnosis or semantic accuracy assertion.
 */
@RunWith(AndroidJUnit4.class)
public final class OfflineInferenceTest {
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private WebView web;

    @Test public void blankFramesAreRejectedBeforeInference() throws Exception {
        Bitmap frame = Bitmap.createBitmap(128, 128, Bitmap.Config.ARGB_8888);
        try {
            for (int color : new int[]{0xffffffff, 0xff000000, 0xff777777}) {
                frame.eraseColor(color);
                ByteArrayOutputStream output = new ByteArrayOutputStream();
                frame.compress(Bitmap.CompressFormat.JPEG, 90, output);
                try { OfflineFrameQuality.check(output.toByteArray()); fail("Blank frame accepted"); }
                catch (java.io.IOException expected) { assertTrue(expected.getMessage().contains("однотонный")); }
            }
            try (InputStream input = instrumentation.getContext().getAssets().open("pump.jpg")) {
                ByteArrayOutputStream output = new ByteArrayOutputStream(); byte[] block = new byte[8192]; int count;
                while ((count = input.read(block)) != -1) output.write(block, 0, count);
                OfflineFrameQuality.check(output.toByteArray());
            }
        } finally { frame.recycle(); }
    }

    @Test(timeout = 900000) public void realOfflinePhotoCancelSaveAndWarmRetry() throws Exception {
        Context target = instrumentation.getTargetContext();
        assertEquals("Run this test with airplane mode enabled", 1,
                Settings.Global.getInt(target.getContentResolver(), Settings.Global.AIRPLANE_MODE_ON, 0));
        SharedPreferences settings = target.getSharedPreferences("MainActivity", Context.MODE_PRIVATE);
        String originalAddress = settings.getString("address", null);
        assertTrue(settings.edit().remove("address").commit());
        Intent intent = new Intent(target, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        Activity activity = instrumentation.startActivitySync(intent);
        String originalHistory = null;
        boolean historyCaptured = false;
        try {
            long startupDeadline = SystemClock.elapsedRealtime() + 60000;
            AtomicReference<Button> choice = new AtomicReference<>();
            while (choice.get() == null && SystemClock.elapsedRealtime() < startupDeadline) {
                instrumentation.runOnMainSync(() -> choice.set(findButton(
                        activity.getWindow().getDecorView(), "Офлайн на телефоне")));
                if (choice.get() == null) SystemClock.sleep(100);
            }
            assertFalse("Opening settings must not load the native library", SmolNative.isAvailable());
            instrumentation.runOnMainSync(() -> {
                Button offline = findButton(activity.getWindow().getDecorView(), "Офлайн на телефоне");
                assertNotNull("Offline entry is visible without a server", offline);
                offline.performClick();
                web = findWebView(activity.getWindow().getDecorView());
                assertNotNull(web);
            });
            await("offline page and native bridge", "Boolean(window.CyberOffline?.enabled && window.CyberSmolNative)", 60000);
            await("real weights prepared and loaded", "JSON.parse(CyberSmolNative.state()).loaded", 240000);
            assertTrue("arm64 native library must load in the model worker", SmolNative.isAvailable());
            assertTrue(SmolNative.isLoaded());
            assertEquals("https://appassets.androidplatform.net/offline/index.html", stringJs("location.href"));
            // Reject a web network request by CSP even if the device's network changes.
            js("window.networkProbe='pending'; fetch('https://example.com/cyber102-offline-probe').then(()=>networkProbe='leak',()=>networkProbe='blocked'); true");
            await("network isolation", "window.networkProbe === 'blocked'", 10000);

            byte[] bytes;
            try (InputStream input = instrumentation.getContext().getAssets().open("pump.jpg");
                 ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[8192]; int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                bytes = output.toByteArray();
            }
            String encoded = Base64.encodeToString(bytes, Base64.NO_WRAP);
            js("(()=>{const b=atob(" + JSONObject.quote(encoded) + ");const a=Uint8Array.from(b,c=>c.charCodeAt(0));const dt=new DataTransfer();dt.items.add(new File([a],'pump.jpg',{type:'image/jpeg'}));const el=document.getElementById('media-file');el.files=dt.files;el.dispatchEvent(new Event('change',{bubbles:true}));return true;})()");
            await("uploaded photo decoded", "!document.getElementById('check').disabled", 30000);
            if ("true".equals(InstrumentationRegistry.getArguments().getString("cancelOnly"))) {
                long first = cancelActiveInference();
                long second = cancelActiveInference();
                Bundle report = new Bundle();
                report.putString("stream", "\nOFFLINE_CANCEL_MS " + first + ", " + second + "\n");
                instrumentation.sendStatus(0, report);
                return;
            }
            js("document.getElementById('check').click(); true");
            await("real model observation", "!document.getElementById('offline-result').hidden", 240000);
            String observation = stringJs("document.querySelector('#offline-result .offline-observation').textContent");
            assertTrue(observation.trim().length() > 2);
            assertEquals("true", js("document.getElementById('detection').hidden || getComputedStyle(document.getElementById('detection')).display==='none'"));
            assertEquals("true", js("document.getElementById('inspection-workflow').hidden"));
            originalHistory = js("localStorage.getItem('cyber102.offline.inspections.v1')");
            historyCaptured = true;
            js("document.getElementById('offline-note').value='Android instrumentation: actual GGUF in airplane mode';document.getElementById('offline-save').click();true");
            JSONObject saved = new JSONArray(stringJs("localStorage.getItem('cyber102.offline.inspections.v1')")).getJSONObject(0);
            assertEquals("on_device", saved.getString("source"));
            assertEquals(observation, saved.getString("observation"));
            assertFalse(saved.has("image"));
            assertFalse(saved.has("base64"));
            js("document.getElementById('offline-result').scrollIntoView({block:'start',behavior:'instant'});true");
            SystemClock.sleep(300);
            capture(target, "offline-real-result.png");
            Bundle report = new Bundle();
            report.putString("stream", "\nREAL_OFFLINE_RESULT " + saved + "\nAPP_PSS_KIB " + Debug.getPss() + "\n");
            instrumentation.sendStatus(0, report);

            long cancelMs = cancelActiveInference();
            js("document.getElementById('check').click();true");
            await("warm inference after cancellation", "!document.getElementById('offline-result').hidden", 240000);
            assertTrue(stringJs("document.querySelector('#offline-result .offline-observation').textContent").trim().length() > 2);
            assertEquals(1, Settings.Global.getInt(target.getContentResolver(), Settings.Global.AIRPLANE_MODE_ON, 0));
            assertEquals("https://appassets.androidplatform.net/offline/index.html",
                    target.getSharedPreferences("MainActivity", Context.MODE_PRIVATE).getString("address", ""));
            Bundle cancellation = new Bundle();
            cancellation.putString("stream", "\nOFFLINE_CANCEL_MS " + cancelMs + "\n");
            instrumentation.sendStatus(0, cancellation);
        } finally {
            try {
                if (historyCaptured) {
                    String key = "'cyber102.offline.inspections.v1'";
                    if ("null".equals(originalHistory)) js("localStorage.removeItem(" + key + ")");
                    else js("localStorage.setItem(" + key + "," + originalHistory + ")");
                }
            } finally {
                instrumentation.runOnMainSync(activity::finish);
                SharedPreferences.Editor restore = settings.edit();
                if (originalAddress == null) restore.remove("address");
                else restore.putString("address", originalAddress);
                assertTrue(restore.commit());
            }
        }
    }

    private long cancelActiveInference() throws Exception {
        js("document.getElementById('check').click();true");
        await("inference started", "JSON.parse(CyberSmolNative.state()).state === 'analyzing'", 10000);
        SystemClock.sleep(1000);
        long started = SystemClock.elapsedRealtime();
        js("document.getElementById('offline-cancel').click();true");
        await("cancel finishes", "!JSON.parse(CyberSmolNative.state()).busy && !document.getElementById('check').disabled", 60000);
        long elapsed = SystemClock.elapsedRealtime() - started;
        assertEquals("true", js("document.getElementById('offline-result').hidden"));
        assertTrue("The model remains loaded after cancellation", SmolNative.isLoaded());
        return elapsed;
    }

    private String js(String code) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> value = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> web.evaluateJavascript(code, result -> { value.set(result); done.countDown(); }));
        assertTrue("WebView script callback", done.await(45, TimeUnit.SECONDS));
        return value.get();
    }
    private String stringJs(String code) throws Exception { return new JSONArray("[" + js(code) + "]").getString(0); }
    private void await(String description, String expression, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        while (SystemClock.elapsedRealtime() < deadline) {
            if ("true".equals(js(expression))) return;
            SystemClock.sleep(750);
        }
        fail(description + ": " + js("JSON.stringify({state:window.CyberSmolNative?.state(),body:document.body?.innerText?.slice(-4000)})"));
    }
    private void capture(Context context, String name) throws Exception {
        Bitmap screenshot = instrumentation.getUiAutomation().takeScreenshot();
        assertNotNull(screenshot);
        File folder = new File(context.getFilesDir(), "qa");
        assertTrue(folder.isDirectory() || folder.mkdirs());
        try (FileOutputStream output = new FileOutputStream(new File(folder, name))) {
            screenshot.compress(Bitmap.CompressFormat.PNG, 100, output);
        } finally { screenshot.recycle(); }
    }
    private static Button findButton(View view, String text) {
        if (view instanceof Button && text.contentEquals(((Button) view).getText())) return (Button) view;
        if (view instanceof ViewGroup) for (int i=0;i<((ViewGroup)view).getChildCount();i++) {
            Button child = findButton(((ViewGroup)view).getChildAt(i), text); if (child != null) return child;
        }
        return null;
    }
    private static WebView findWebView(View view) {
        if (view instanceof WebView) return (WebView)view;
        if (view instanceof ViewGroup) for (int i=0;i<((ViewGroup)view).getChildCount();i++) {
            WebView child = findWebView(((ViewGroup)view).getChildAt(i)); if (child != null) return child;
        }
        return null;
    }
}
