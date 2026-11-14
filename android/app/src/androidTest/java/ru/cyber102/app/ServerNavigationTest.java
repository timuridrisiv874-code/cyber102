package ru.cyber102.app;

import static org.junit.Assert.*;
import static org.junit.Assume.assumeTrue;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.webkit.WebView;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Predicate;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Opt-in end-to-end navigation on a trusted public or enterprise HTTPS Cyber102 without login.
 * Pass -e serverUrl https://host/index.html?serial=P-102. Only reads the four public screens.
 */
@RunWith(AndroidJUnit4.class)
public class ServerNavigationTest {
    private final Instrumentation runner = InstrumentationRegistry.getInstrumentation();
    private Activity activity;
    private WebView web;

    @Test(timeout = 180000) public void serverSectionsShareNavigationAndOfflineKeepsItsOwnScreen() throws Exception {
        String address = InstrumentationRegistry.getArguments().getString("serverUrl", "");
        assumeTrue("Supply a trusted HTTPS Cyber102 URL for this network test", !address.isEmpty());
        Uri uri = Uri.parse(address);
        assertEquals("https", uri.getScheme());
        assertNotNull(uri.getHost());
        assertNull(uri.getUserInfo());
        assertEquals("/index.html", uri.getPath());
        String serial = uri.getQueryParameter("serial");
        assertNotNull("Include ?serial=P-102 or another public test serial", serial);

        Context target = runner.getTargetContext();
        SharedPreferences settings = target.getSharedPreferences("MainActivity", Context.MODE_PRIVATE);
        String previousAddress = settings.getString("address", null);
        String previousRecent = settings.getString("recent_servers", null);
        assertTrue(settings.edit().remove("address").commit());
        try {
            activity = runner.startActivitySync(new Intent(target, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
            press("Выбрать сервер");
            AccessibilityNodeInfo field = nativeNode(n -> n.isEditable() && "IP-адрес или домен".contentEquals(
                    n.getHintText() == null ? "" : n.getHintText()));
            Bundle value = new Bundle();
            value.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, address);
            assertTrue(field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, value));
            press("Подключить");
            web = currentWeb(null);
            String[] pages = {"index.html", "documents.html", "materials.html", "checkpoint.html", "telemetry.html", "index.html"};
            String[] sections = {"inspection", "equipment", "equipment", "journal", "readings", "inspection"};
            for (int i = 0; i < pages.length; i++) {
                String path = "/" + pages[i];
                boolean materials = pages[i].equals("materials.html");
                String primaryPath = materials ? "/documents.html" : path;
                if (i > 0) {
                    String selector = materials ? ".equipment-sections a" : ".app-nav a";
                    assertEquals("true", js("(()=>{const a=[...document.querySelectorAll(" + JSONObject.quote(selector) + ")].find("
                            + "x=>new URL(x.href).pathname===" + JSONObject.quote(path) + ");if(!a)return false;a.click();return true})()"));
                }
                String check = "(()=>{const nav=document.querySelector('.app-nav'),active=nav?.querySelector('[aria-current=page]');"
                        + "return document.readyState==='complete' && location.pathname===" + JSONObject.quote(path)
                        + " && location.origin===" + JSONObject.quote("https://" + uri.getEncodedAuthority())
                        + " && new URLSearchParams(location.search).get('serial')===" + JSONObject.quote(serial)
                        + " && document.body.dataset.page===" + JSONObject.quote(sections[i])
                        + " && [...nav.querySelectorAll('a')].map(a=>a.textContent.trim()).join('|')==='Осмотр|Оборудование|Журнал|Показания'"
                        + " && !!active && new URL(active.href).pathname===" + JSONObject.quote(primaryPath)
                        + " && [...document.styleSheets].some(s=>s.href && new URL(s.href).pathname==='/workspace.css' && s.cssRules.length>0)"
                        + " && getComputedStyle(nav).display==='flex' && !document.querySelector('[src*=\"/__mobile_inspection/\"],[href*=\"/__mobile_inspection/\"]')"
                        + " && typeof window.CyberSmolNative==='undefined'})()";
                waitFor(sections[i], check);
                Bundle result = new Bundle();
                result.putString("stream", "\nSERVER_SCREEN " + pages[i] + " " + js("document.querySelector('h1').textContent") + "\n");
                runner.sendStatus(0, result);
                if (i < 3 && "true".equals(InstrumentationRegistry.getArguments().getString("screenshots"))) {
                    if (materials) {
                        waitFor("photo gallery and its first thumbnail",
                                "document.querySelector('.equipment-sections [aria-current=page]')?.textContent.trim()==='Фото и примеры'"
                                        + " && document.querySelector('#photo-grid img')?.naturalWidth>0");
                    }
                    CountDownLatch painted = new CountDownLatch(1);
                    runner.runOnMainSync(() -> web.postVisualStateCallback(0, new WebView.VisualStateCallback() {
                        @Override public void onComplete(long id) {
                            web.postOnAnimation(() -> web.postOnAnimation(painted::countDown));
                        }
                    }));
                    assertTrue("WebView paints latest page", painted.await(10, TimeUnit.SECONDS));
                    runner.getUiAutomation().waitForIdle(350, 4000);
                    Bitmap image = runner.getUiAutomation().takeScreenshot();
                    assertNotNull(image);
                    try (FileOutputStream out = new FileOutputStream(new File(target.getCacheDir(), "qa-server-" + (materials ? "materials" : sections[i]) + ".png"))) {
                        assertTrue(image.compress(Bitmap.CompressFormat.PNG, 100, out));
                    } finally { image.recycle(); }
                }
            }
            press("Режим");
            press("Офлайн на телефоне");
            web = currentWeb(web);
            waitFor("offline profile, local CSS and native bridge",
                    "location.href==='https://appassets.androidplatform.net/offline/index.html' && window.CyberOffline?.enabled===true"
                            + " && !!window.CyberSmolNative && !!document.querySelector('#offline-analysis-profile')"
                            + " && [...document.styleSheets].some(s=>s.href?.includes('/__mobile_inspection/') && s.cssRules.length>0)");
        } finally {
            if (activity != null) runner.runOnMainSync(activity::finish);
            assertTrue(settings.edit().putString("address", previousAddress).putString("recent_servers", previousRecent).commit());
        }
    }

    private void press(String label) {
        AccessibilityNodeInfo node = nativeNode(n -> label.equalsIgnoreCase(String.valueOf(n.getText())) && n.isClickable());
        assertTrue(label, node.performAction(AccessibilityNodeInfo.ACTION_CLICK));
    }

    private AccessibilityNodeInfo nativeNode(Predicate<AccessibilityNodeInfo> match) {
        long deadline = SystemClock.elapsedRealtime() + 15000;
        do {
            AccessibilityNodeInfo root = runner.getUiAutomation().getRootInActiveWindow();
            if (root != null) {
                AccessibilityNodeInfo value = find(root, match);
                if (value != null) return value;
            }
            SystemClock.sleep(150);
        } while (SystemClock.elapsedRealtime() < deadline);
        throw new AssertionError("Native connection control unavailable");
    }

    private static AccessibilityNodeInfo find(AccessibilityNodeInfo node, Predicate<AccessibilityNodeInfo> match) {
        if (match.test(node)) return node;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            AccessibilityNodeInfo found = find(child, match);
            if (found != null) return found;
        }
        return null;
    }

    private WebView currentWeb(WebView previous) {
        AtomicReference<WebView> found = new AtomicReference<>();
        long deadline = SystemClock.elapsedRealtime() + 20000;
        do {
            runner.runOnMainSync(() -> found.set(findWeb(activity.getWindow().getDecorView())));
            if (found.get() != null && found.get() != previous) return found.get();
            SystemClock.sleep(150);
        } while (SystemClock.elapsedRealtime() < deadline);
        throw new AssertionError("Expected a new WebView after selecting mode");
    }

    private static WebView findWeb(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++) {
            WebView child = findWeb(((ViewGroup) view).getChildAt(i));
            if (child != null) return child;
        }
        return null;
    }

    private void waitFor(String description, String expression) throws Exception {
        long end = SystemClock.elapsedRealtime() + 30000;
        do {
            if ("true".equals(js(expression))) return;
            SystemClock.sleep(250);
        } while (SystemClock.elapsedRealtime() < end);
        fail(description + ": " + js("JSON.stringify([location.href,document.body?.dataset.page,"
                + "[...document.styleSheets].map(s=>s.href),document.querySelector('.app-nav')?.innerText])"));
    }

    private String js(String code) throws InterruptedException {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> answer = new AtomicReference<>();
        runner.runOnMainSync(() -> web.evaluateJavascript(code, text -> { answer.set(text); done.countDown(); }));
        assertTrue(done.await(8, TimeUnit.SECONDS));
        return answer.get();
    }
}
