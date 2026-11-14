package ru.cyber102.app;

import android.Manifest;
import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import android.content.ActivityNotFoundException;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Trace;
import android.util.Log;
import android.provider.Settings;
import android.view.View;
import android.webkit.*;
import android.widget.*;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.List;
import android.graphics.drawable.GradientDrawable;
import android.content.res.ColorStateList;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.HashMap;
import java.util.Map;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.net.SocketTimeoutException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.net.ssl.SSLException;
import javax.net.ssl.HttpsURLConnection;

/** Small Android host for the team's shared inspection screen. */
public final class MainActivity extends ComponentActivity {
    private static final String DEMO = "https://appassets.androidplatform.net/index.html";
    private static final String OFFLINE = "https://appassets.androidplatform.net/offline/index.html";
    private static final int CAMERA_REQUEST = 20;
    private static final int BACKGROUND = 0xfff4f6f5;
    private static final int INK = 0xff192a28;
    private static final int MUTED = 0xff4d615d;
    private static final int ACCENT = 0xff176451;
    private static final int OUTLINE = 0xffd6e1de;
    private static final int MAX_RECENT = 4;
    private static final ExecutorService PREFERENCES_IO = Executors.newSingleThreadExecutor(
            task -> new Thread(task, "Cyber102-prefs"));
    private LinearLayout root;
    private FrameLayout content;
    private volatile WebView web;
    private TextView status;
    private ProgressBar progress;
    private PermissionRequest pendingCamera;
    private volatile String activeUrl = DEMO;
    private List<String> recent = new ArrayList<>();
    private boolean foreground;
    private boolean failed;
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private int loadGeneration;
    private String login;
    private String password;
    private AlertDialog settingsDialog;
    private OnBackPressedCallback webBackCallback;
    private ValueCallback<Uri[]> pendingFileChooser;
    private boolean filePickerActive;
    private SmolEngineManager offline;
    private boolean modelPickerActive;
    private String modelPickerRole;
    private final ActivityResultLauncher<Intent> modelPicker = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                modelPickerActive = false;
                String role = modelPickerRole;
                modelPickerRole = null;
                if (!isOffline() || role == null || result.getResultCode() != RESULT_OK || result.getData() == null) return;
                Uri uri = result.getData().getData();
                if (uri != null && "content".equals(uri.getScheme())) offline.importFile(uri, role);
            });
    private final ActivityResultLauncher<Intent> filePicker = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                filePickerActive = false;
                ValueCallback<Uri[]> callback = pendingFileChooser;
                pendingFileChooser = null;
                if (callback == null) return;
                Uri selected = result.getResultCode() == RESULT_OK && result.getData() != null ?
                        result.getData().getData() : null;
                // ACTION_OPEN_DOCUMENT grants access only to the explicitly selected file.
                callback.onReceiveValue(selected != null && "content".equals(selected.getScheme()) ?
                        new Uri[]{selected} : null);
            });

    private void cancelFileChooser() {
        ValueCallback<Uri[]> callback = pendingFileChooser;
        pendingFileChooser = null;
        if (callback != null) callback.onReceiveValue(null);
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        offline = new SmolEngineManager(this, this::onOfflineEvent);
        // AndroidX forwards both legacy Back and Android 13+ predictive Back.
        // Intercept only while WebView has history; otherwise the system owns
        // back-to-home navigation and its predictive animation.
        webBackCallback = new OnBackPressedCallback(false) {
            @Override public void handleOnBackPressed() {
                releaseCamera();
                cancelCamera();
                if (web != null && !failed && web.canGoBack()) {
                    web.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        };
        getOnBackPressedDispatcher().addCallback(this, webBackCallback);
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BACKGROUND);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        LinearLayout bar = new LinearLayout(this);
        bar.setPadding(dp(16), dp(8), dp(12), dp(8));
        bar.setBackgroundColor(Color.WHITE);
        bar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        ImageView icon = new ImageView(this);
        icon.setImageResource(getResources().getIdentifier("inspection_logo", "drawable", getPackageName()));
        icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams iconSize = new LinearLayout.LayoutParams(dp(32), dp(32));
        iconSize.setMarginEnd(dp(10));
        bar.addView(icon, iconSize);
        TextView brand = new TextView(this);
        brand.setText("Cyber102");
        brand.setTextSize(16);
        brand.setTextColor(INK);
        brand.setTypeface(null, android.graphics.Typeface.BOLD);
        bar.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
        Button settings = button("Режим", this::showSettings);
        bar.addView(settings);
        root.addView(bar);
        status = new TextView(this);
        status.setTextSize(13);
        status.setTextColor(MUTED);
        status.setPadding(dp(16), dp(4), dp(16), dp(8));
        status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        root.addView(status);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        root.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));
        content = new FrameLayout(this);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        settings.setEnabled(false);
        status.setText("Открываем настройки…");
        Context app = getApplicationContext();
        String preferencesName = getLocalClassName();
        PREFERENCES_IO.execute(() -> {
            String savedAddress = DEMO;
            List<String> savedRecent = new ArrayList<>();
            Trace.beginSection("Cyber102.preferences.read");
            try {
                SharedPreferences preferences = app.getSharedPreferences(preferencesName, MODE_PRIVATE);
                savedAddress = preferences.getString("address", DEMO);
                savedRecent = parseRecentServers(preferences.getString("recent_servers", "[]"));
            } catch (IllegalStateException | ClassCastException error) {
                Log.w("Cyber102", "Unable to read saved settings", error);
            } finally {
                Trace.endSection();
            }
            String address = savedAddress;
            List<String> servers = savedRecent;
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed()) return;
                recent = servers;
                settings.setEnabled(true);
                if (DEMO.equals(address)) showWelcome();
                else load(address);
            });
        });
    }

    private void showWelcome() {
        status.setText("Сервер не подключён");
        progress.setVisibility(View.GONE);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(panel(Color.WHITE, OUTLINE, 16));
        card.setPadding(dp(24), dp(24), dp(24), dp(20));
        TextView title = new TextView(this);
        title.setText("Выберите режим анализа");
        title.setTextColor(INK);
        title.setTextSize(25);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        card.addView(title);
        TextView description = new TextView(this);
        description.setText("Работайте с сервером предприятия или анализируйте отдельные кадры прямо на телефоне без сети.");
        description.setTextColor(MUTED);
        description.setTextSize(16);
        description.setPadding(0, dp(10), 0, dp(16));
        card.addView(description);
        Button connect = button("Выбрать сервер", this::showSettings);
        connect.setBackgroundTintList(null);
        connect.setBackground(panel(ACCENT, ACCENT, 10));
        connect.setTextColor(Color.WHITE);
        card.addView(connect, new LinearLayout.LayoutParams(-1, dp(48)));
        card.addView(button("Офлайн на телефоне", () -> saveAndLoad(OFFLINE)), new LinearLayout.LayoutParams(-1, dp(48)));

        FrameLayout center = new FrameLayout(this);
        center.setPadding(dp(20), dp(24), dp(20), dp(24));
        int available = getResources().getDisplayMetrics().widthPixels - dp(40);
        center.addView(card, new FrameLayout.LayoutParams(Math.min(dp(540), available), -2,
                android.view.Gravity.CENTER_HORIZONTAL));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(center);
        content.removeAllViews();
        content.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
    }

    private GradientDrawable panel(int fill, int stroke, int radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setStroke(dp(1), stroke);
        shape.setCornerRadius(dp(radius));
        return shape;
    }

    private void createWebView() {
        try {
            web = new WebView(this);
        } catch (RuntimeException error) {
            showError("Android System WebView недоступен. Включите или обновите его в настройках телефона.");
            return;
        }
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true); // Explicit document-picker URI grants; no storage permission.
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        WebView.setWebContentsDebuggingEnabled(false);
        settings.setGeolocationEnabled(false);
        final WebView owner = web;
        if (isOffline()) {
            // This bridge exists only in a separate offline WebView. That view
            // admits only packaged files and blocks network/frames/navigation.
            settings.setBlockNetworkLoads(true);
            web.addJavascriptInterface(new OfflineBridge(owner), "CyberSmolNative");
        }
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!sameOrigin(uri, Uri.parse(activeUrl))) return missingAsset();
                String path = uri.getPath();
                if (isOffline()) {
                    if (!"GET".equals(request.getMethod()) || path == null) return missingAsset();
                    String asset = path.startsWith("/offline/") ? path.substring("/offline/".length()) :
                            path.startsWith("/__mobile_inspection/") ? path.substring("/__mobile_inspection/".length()) : null;
                    if (!inspectionAssetAllowed(asset) || (request.isForMainFrame() && !"index.html".equals(asset))) return missingAsset();
                    return offlineAsset("inspection/" + asset);
                }
                if (isDemo()) {
                    if (path == null || path.contains("..")) return missingAsset();
                    return bundledAsset(path.substring(1));
                }
                // Let the selected server provide the same pages and styles for every tab.
                return null;
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (isOffline()) {
                    if (sameOrigin(request.getUrl(), Uri.parse(OFFLINE)) && "/offline/index.html".equals(request.getUrl().getPath())) return false;
                    if (request.isForMainFrame()) Toast.makeText(MainActivity.this,
                            "Этот раздел доступен через сервер. Переключите режим кнопкой сверху.", Toast.LENGTH_LONG).show();
                    return true;
                }
                if (sameOrigin(request.getUrl(), Uri.parse(activeUrl))) return false;
                if (request.isForMainFrame()) {
                    new AlertDialog.Builder(MainActivity.this).setTitle("Другой адрес")
                            .setMessage("Чтобы открыть другой сервер, укажите его через кнопку «Адрес».")
                            .setPositiveButton("Понятно", null).show();
                }
                return true;
            }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                cancelCamera();
                cancelFileChooser();
                if (!sameOrigin(Uri.parse(url), Uri.parse(activeUrl))) {
                    view.stopLoading();
                    showError("Сервер перенаправил на другой адрес. Укажите конечный HTTPS-адрес в настройках.");
                }
            }
            @Override public void doUpdateVisitedHistory(WebView view, String url, boolean isReload) {
                updateBackNavigation();
            }
            @Override public void onPageFinished(WebView view, String url) {
                updateBackNavigation();
                progress.setVisibility(View.GONE);
                if (!failed && sameOrigin(Uri.parse(url), Uri.parse(activeUrl))) {
                    status.setText(isOffline() ? "На телефоне · LFM3 · без сети" : isDemo() ? "Демо · ответы заданы заранее" : "Сервер · " + Uri.parse(activeUrl).getHost());
                    status.setVisibility(View.GONE);
                    if (isOffline()) onOfflineEvent(offline.snapshot());
                }
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (!request.isForMainFrame()) return;
                showError(error.getErrorCode() == ERROR_TIMEOUT ? "Сервер не ответил вовремя." :
                        "Не удалось открыть экран. Проверьте подключение к сети предприятия и адрес сервера.");
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame()) showError("Сервер вернул ошибку " + response.getStatusCode() + ". Проверьте адрес страницы.");
            }
            @Override public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                Uri configured = Uri.parse(activeUrl);
                Uri page = Uri.parse(view.getUrl() == null ? activeUrl : view.getUrl());
                if (isDemo() || isOffline() || !sameOrigin(configured, page) || !host.equalsIgnoreCase(configured.getHost())) {
                    handler.cancel();
                    return;
                }
                if (login != null) {
                    handler.proceed(login, password);
                } else {
                    handler.cancel();
                    showError("Сервер запросил вход. Укажите имя пользователя и пароль в настройках сервера.");
                }
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                showError("Не удалось проверить сертификат HTTPS. Серверу нужен действующий доверенный сертификат.");
            }
            @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                cancelCamera();
                if (isOffline()) offline.unload();
                content.removeView(view);
                view.destroy();
                web = null;
                showError("Система закрыла веб-экран. Нажмите «Повторить», чтобы открыть его снова.");
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                    FileChooserParams params) {
                cancelFileChooser();
                if (filePickerActive || !foreground || isDemo() || params.getMode() != FileChooserParams.MODE_OPEN ||
                        !sameOrigin(Uri.parse(view.getUrl() == null ? "" : view.getUrl()), Uri.parse(activeUrl))) {
                    callback.onReceiveValue(null);
                    return true;
                }
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"image/*", "video/*"});
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, false);
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                pendingFileChooser = callback;
                filePickerActive = true;
                try {
                    filePicker.launch(intent);
                } catch (ActivityNotFoundException | IllegalStateException error) {
                    filePickerActive = false;
                    cancelFileChooser();
                    Toast.makeText(MainActivity.this, "Не удалось открыть выбор файла", Toast.LENGTH_LONG).show();
                }
                return true;
            }
            @Override public void onProgressChanged(WebView view, int value) { progress.setProgress(value); }
            @Override public void onPermissionRequest(PermissionRequest request) {
                if (isDemo() || !foreground || !sameOrigin(request.getOrigin(), Uri.parse(activeUrl)) ||
                        !sameOrigin(Uri.parse(web.getUrl() == null ? "" : web.getUrl()), Uri.parse(activeUrl)) ||
                        !Arrays.asList(request.getResources()).contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                    request.deny();
                    return;
                }
                cancelCamera();
                pendingCamera = request;
                if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) grantCamera();
                else requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_REQUEST);
            }
            @Override public void onPermissionRequestCanceled(PermissionRequest request) {
                if (pendingCamera == request) pendingCamera = null;
            }
        });
        content.addView(web, new FrameLayout.LayoutParams(-1, -1));
    }

    private WebResourceResponse bundledAsset(String name) {
        String mime = name.endsWith(".js") ? "application/javascript" :
                name.endsWith(".css") ? "text/css" :
                name.endsWith(".json") ? "application/json" :
                name.endsWith(".svg") ? "image/svg+xml" :
                name.endsWith(".jpg") ? "image/jpeg" :
                name.endsWith(".png") ? "image/png" : "text/html";
        try {
            return new WebResourceResponse(mime, "UTF-8", getAssets().open(name));
        } catch (IOException error) {
            return missingAsset();
        }
    }
    private static boolean inspectionAssetAllowed(String name) {
        return name != null && name.matches("[A-Za-z0-9][A-Za-z0-9._-]*\\.(html|js|css|jpg|jpeg|png|json|svg|webp)") &&
                (!name.endsWith(".html") || name.equals("index.html"));
    }
    private WebResourceResponse offlineAsset(String name) {
        WebResourceResponse response = bundledAsset(name);
        Map<String, String> headers = new HashMap<>();
        headers.put("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'");
        headers.put("X-Content-Type-Options", "nosniff");
        headers.put("Cache-Control", "no-store");
        response.setResponseHeaders(headers);
        return response;
    }
    private static WebResourceResponse missingAsset() {
        return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", null,
                new ByteArrayInputStream(new byte[0]));
    }
    private static boolean sameOrigin(Uri a, Uri b) {
        return "https".equalsIgnoreCase(a.getScheme()) && "https".equalsIgnoreCase(b.getScheme()) &&
                a.getHost() != null && a.getHost().equalsIgnoreCase(b.getHost()) && port(a) == port(b);
    }
    private static int port(Uri uri) { return uri.getPort() == -1 ? 443 : uri.getPort(); }
    private boolean isDemo() { return DEMO.equals(activeUrl); }
    private boolean isOffline() { return OFFLINE.equals(activeUrl); }
    private void grantCamera() {
        PermissionRequest request = pendingCamera;
        pendingCamera = null;
        if (request == null) return;
        if (isDemo() || !sameOrigin(request.getOrigin(), Uri.parse(activeUrl))) request.deny();
        else request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
    }
    private void cancelCamera() {
        if (pendingCamera != null) { pendingCamera.deny(); pendingCamera = null; }
    }
    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code != CAMERA_REQUEST) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) grantCamera();
        else {
            cancelCamera();
            new AlertDialog.Builder(this).setTitle("Доступ к камере")
                    .setMessage("Разрешите камеру в настройках приложения, затем нажмите «Включить камеру» ещё раз. Примеры доступны без камеры.")
                    .setPositiveButton("Настройки", (d, w) -> startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            Uri.parse("package:" + getPackageName())))).setNegativeButton("Закрыть", null).show();
        }
    }
    private void load(String url) {
        if (url == null) url = DEMO;
        int generation = ++loadGeneration;
        releaseCamera();
        cancelCamera();
        cancelFileChooser();
        if (isOffline() && !OFFLINE.equals(url)) offline.unload();
        if (web != null) {
            web.stopLoading();
            if (!sameOrigin(Uri.parse(url), Uri.parse(activeUrl)) || isOffline() != OFFLINE.equals(url)) {
                content.removeView(web);
                web.destroy();
                web = null;
            }
        }
        activeUrl = url;
        failed = false;
        updateBackNavigation();
        content.removeAllViews();
        if (web == null) createWebView();
        else content.addView(web, new FrameLayout.LayoutParams(-1, -1));
        if (web == null) return;
        web.setVisibility(View.VISIBLE);
        status.setText(isOffline() ? "Открываем офлайн-анализ…" : isDemo() ? "Демо · ответы заданы заранее" : "Подключение к серверу…");
        status.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        if (isDemo() || isOffline()) {
            web.loadUrl(activeUrl);
            return;
        }
        web.setVisibility(View.INVISIBLE);
        String target = activeUrl;
        String user = login;
        String secret = password;
        network.execute(() -> {
            String error = checkServer(target, user, secret);
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed() || generation != loadGeneration || web == null) return;
                if (error != null) {
                    showError(error);
                } else {
                    rememberServer(target);
                    web.setVisibility(View.VISIBLE);
                    web.loadUrl(target);
                }
            });
        });
    }
    private String checkServer(String target, String user, String secret) {
        HttpsURLConnection connection = null;
        try {
            URL health = new URL(new URL(target), "/api/health");
            connection = (HttpsURLConnection) health.openConnection();
            connection.setConnectTimeout(6000);
            connection.setReadTimeout(7000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            if (user != null) {
                String token = android.util.Base64.encodeToString(
                        (user + ":" + secret).getBytes(java.nio.charset.StandardCharsets.UTF_8), android.util.Base64.NO_WRAP);
                connection.setRequestProperty("Authorization", "Basic " + token);
            }
            int code = connection.getResponseCode();
            if (code == HttpURLConnection.HTTP_OK) {
                try (java.io.InputStream input = connection.getInputStream()) {
                    java.io.ByteArrayOutputStream body = new java.io.ByteArrayOutputStream();
                    byte[] bytes = new byte[512];
                    int count;
                    while ((count = input.read(bytes)) != -1) {
                        if (body.size() + count > 8192) return "Сервер вернул слишком большой ответ проверки.";
                        body.write(bytes, 0, count);
                    }
                    org.json.JSONObject healthResult = new org.json.JSONObject(body.toString("UTF-8"));
                    if ("ok".equals(healthResult.optString("status"))) return null;
                } catch (JSONException error) {
                    return "По этому адресу получен ответ, который не соответствует API Cyber102.";
                }
                return "Cyber102 ответил, но сервер ещё не готов.";
            }
            if (code == HttpURLConnection.HTTP_UNAUTHORIZED) return "Сервер требует входа. Проверьте имя пользователя и пароль в настройках.";
            return "Проверка /api/health вернула HTTP " + code + ". Проверьте адрес сервера.";
        } catch (SocketTimeoutException error) {
            return "Сервер не ответил вовремя. Проверьте сетевое подключение и повторите попытку.";
        } catch (SSLException error) {
            return "Ошибка TLS. Проверьте имя в сертификате, срок действия и доверие к корпоративному центру сертификации.";
        } catch (IOException error) {
            return "Сервер недоступен. Проверьте адрес, порт и доступ к сети сервера.";
        } finally {
            if (connection != null) connection.disconnect();
        }
    }
    private void showError(String message) {
        failed = true;
        updateBackNavigation();
        progress.setVisibility(View.GONE);
        if (web != null) web.setVisibility(View.GONE);
        TextView error = new TextView(this);
        error.setText(message);
        error.setTextSize(17);
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.VERTICAL);
        actions.setPadding(dp(24), dp(24), dp(24), dp(24));
        actions.addView(error);
        actions.addView(button("Повторить", () -> load(activeUrl)));
        actions.addView(button("Офлайн на телефоне", () -> saveAndLoad(OFFLINE)));
        actions.addView(button("Открыть встроенное демо", () -> saveAndLoad(DEMO)));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(actions);
        content.removeAllViews();
        content.addView(scroll);
        status.setText("Экран недоступен");
        status.setVisibility(View.VISIBLE);
    }
    private void showSettings() {
        releaseCamera();
        cancelCamera();
        if (isOffline()) offline.cancel();
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(24), dp(8), dp(24), 0);
        TextView hint = new TextView(this);
        hint.setText("Подойдёт локальный или публичный IP, а также домен. Порт можно указать после двоеточия.");
        hint.setTextColor(MUTED);
        hint.setPadding(0, 0, 0, dp(12));
        form.addView(hint);
        form.addView(button("Офлайн на телефоне", () -> {
            if (settingsDialog != null) settingsDialog.dismiss();
            saveAndLoad(OFFLINE);
        }));
        EditText address = input("IP-адрес или домен", android.text.InputType.TYPE_TEXT_VARIATION_URI);
        address.setText(isDemo() || isOffline() ? "" : origin(Uri.parse(activeUrl)));
        form.addView(address);
        EditText user = input("Имя пользователя", android.text.InputType.TYPE_TEXT_VARIATION_NORMAL);
        user.setText(login);
        form.addView(user);
        EditText secret = input("Пароль", android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        form.addView(secret);
        List<String> recent = new ArrayList<>(this.recent);
        if (!recent.isEmpty()) form.addView(button("Недавние серверы", () ->
                new AlertDialog.Builder(this).setTitle("Недавние серверы")
                        .setItems(recent.toArray(new String[0]), (d, which) -> {
                            address.setText(recent.get(which));
                            user.setText("");
                            secret.setText("");
                        }).show()));
        if (!isDemo() && !isOffline()) form.addView(button("Открыть ссылку из QR", () -> {
            if (settingsDialog != null) settingsDialog.dismiss();
            openPassportLink();
        }));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(form);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Сервер предприятия")
                .setView(scroll).setPositiveButton("Подключить", null)
                .setNeutralButton("Демо", (d, w) -> saveAndLoad(DEMO))
                .setNegativeButton("Отмена", null).create();
        settingsDialog = dialog;
        dialog.setOnDismissListener(d -> settingsDialog = null);
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String value = serverUrl(address.getText().toString());
            if (value == null) {
                address.setError("Укажите корректный HTTPS-адрес без логина и пароля в ссылке");
                return;
            }
            String selectedUser = user.getText().toString().trim();
            if (selectedUser.contains(":")) {
                user.setError("Имя пользователя не может содержать двоеточие");
                return;
            }
            String selectedPassword = secret.getText().toString();
            if (!sameOrigin(Uri.parse(value), Uri.parse(activeUrl)) || !selectedUser.equals(login) ||
                    !selectedPassword.isEmpty() || selectedUser.isEmpty()) {
                login = selectedUser.isEmpty() ? null : selectedUser;
                password = selectedUser.isEmpty() ? null : selectedPassword;
            }
            dialog.dismiss();
            saveAndLoad(value);
        }));
        dialog.show();
    }
    private EditText input(String hint, int variation) {
        EditText field = new EditText(this);
        field.setSingleLine(true);
        field.setHint(hint);
        field.setInputType(android.text.InputType.TYPE_CLASS_TEXT | variation);
        return field;
    }
    private void openPassportLink() {
        EditText link = input("Вставьте HTTPS-ссылку из QR", android.text.InputType.TYPE_TEXT_VARIATION_URI);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Паспорт по QR")
                .setMessage("Отсканируйте QR штатной камерой и скопируйте ссылку сюда.")
                .setView(link).setNegativeButton("Отмена", null).setPositiveButton("Открыть", null).create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String value = validatedUrl(link.getText().toString().trim());
            if (value == null || !sameOrigin(Uri.parse(value), Uri.parse(activeUrl))) {
                link.setError("Ссылка должна вести на настроенный сервер");
                return;
            }
            Uri uri = Uri.parse(value);
            String serial = uri.getQueryParameter("serial");
            if (!"/checkpoint.html".equals(uri.getPath()) || serial == null || serial.trim().isEmpty() || serial.length() > 80) {
                link.setError("Ссылка должна содержать серийный номер паспорта");
                return;
            }
            dialog.dismiss();
            load(value);
        }));
        dialog.show();
    }
    private static String serverUrl(String value) {
        value = value.trim();
        if (value.isEmpty()) return null;
        if (!value.contains("://")) value = "https://" + value;
        return validatedUrl(value);
    }
    private static String origin(Uri url) {
        return "https://" + url.getEncodedAuthority();
    }
    private static List<String> parseRecentServers(String saved) {
        List<String> items = new ArrayList<>();
        try {
            JSONArray addresses = new JSONArray(saved);
            for (int i = 0; i < addresses.length() && items.size() < MAX_RECENT; i++) {
                String value = validatedUrl(addresses.optString(i));
                if (value != null) items.add(origin(Uri.parse(value)));
            }
        } catch (JSONException ignored) {
            // Reset a damaged list the next time a connection succeeds.
        }
        return items;
    }
    private void rememberServer(String target) {
        String address = origin(Uri.parse(target));
        recent.remove(address);
        recent.add(0, address);
        if (recent.size() > MAX_RECENT) recent.remove(MAX_RECENT);
        writePreference("recent_servers", new JSONArray(recent).toString());
    }
    private void writePreference(String key, String value) {
        Context app = getApplicationContext();
        String name = getLocalClassName();
        // commit runs on our executor. A pending apply would be drained by
        // Android on the main thread during a lifecycle transition.
        PREFERENCES_IO.execute(() -> {
            Trace.beginSection("Cyber102.preferences.write");
            try {
                boolean saved = app.getSharedPreferences(name, MODE_PRIVATE).edit().putString(key, value).commit();
                if (!saved) Log.w("Cyber102", "Unable to save preference: " + key);
            } catch (IllegalStateException error) {
                Log.w("Cyber102", "Unable to save preference: " + key, error);
            } finally {
                Trace.endSection();
            }
        });
    }
    private static String validatedUrl(String value) {
        try {
            URI url = new URI(value);
            if (!"https".equalsIgnoreCase(url.getScheme()) || url.getHost() == null || url.getUserInfo() != null ||
                    url.getPort() == 0 || url.getPort() > 65535 ||
                    "appassets.androidplatform.net".equalsIgnoreCase(url.getHost())) return null;
            String path = url.getPath();
            if (path == null || path.isEmpty() || "/".equals(path)) {
                return Uri.parse(value).buildUpon().path("/checkpoint.html")
                        .encodedQuery(url.getRawQuery() == null ? "serial=P-102" : url.getRawQuery()).build().toString();
            }
            return url.toASCIIString();
        } catch (java.net.URISyntaxException error) {
            return null;
        }
    }
    private void saveAndLoad(String url) {
        if (DEMO.equals(url) || OFFLINE.equals(url)) {
            login = null;
            password = null;
        }
        writePreference("address", url);
        load(url);
    }
    private Button button(String label, Runnable action) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(ACCENT);
        button.setBackgroundTintList(ColorStateList.valueOf(0xffe6efec));
        button.setOnClickListener(v -> action.run());
        return button;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void releaseCamera() {
        if (web != null) web.evaluateJavascript("if (typeof releaseCamera === 'function') releaseCamera();", null);
    }
    private void updateBackNavigation() {
        if (webBackCallback != null) {
            webBackCallback.setEnabled(web != null && !failed && web.canGoBack());
        }
    }
    @Override protected void onResume() {
        super.onResume(); foreground = true;
        if (web != null) web.onResume();
        updateBackNavigation();
    }
    @Override protected void onPause() {
        foreground = false;
        super.onPause();
    }
    @Override protected void onStop() {
        releaseCamera(); cancelCamera();
        // A gallery, a screen lock or a short app switch is common between frames.
        // Keep the session resident. Actual memory pressure is handled below.
        if (web != null) web.onPause();
        super.onStop();
    }
    @Override public void onTrimMemory(int level) {
        super.onTrimMemory(level);
        if (offline != null && (level == TRIM_MEMORY_RUNNING_CRITICAL || level >= TRIM_MEMORY_COMPLETE)) offline.unload();
    }
    @Override protected void onDestroy() {
        network.shutdownNow();
        if (offline != null) offline.close();
        cancelCamera();
        cancelFileChooser();
        if (web != null) { content.removeView(web); web.destroy(); web = null; }
        super.onDestroy();
    }

    private void onOfflineEvent(JSONObject event) {
        if (!isOffline() || web == null || isFinishing() || isDestroyed()) return;
        web.evaluateJavascript("window.CyberOffline && window.CyberOffline.onNativeEvent(" + event.toString() + ");", null);
        if ("imported".equals(event.optString("type"))) {
            if ("model".equals(event.optString("role"))) chooseModelFile("projector");
            else offline.load();
        }
    }

    private void chooseModelFile(String role) {
        if (!isOffline() || !foreground || modelPickerActive || filePickerActive) return;
        new AlertDialog.Builder(this).setTitle(role.equals("model") ? "Файл языковой модели" : "Файл визуального проектора")
                .setMessage("Выберите " + offline.importFileName(role) + ". Приложение сверит размер и SHA-256 с комплектом этой сборки.")
                .setNegativeButton("Отмена", null).setPositiveButton("Выбрать файл", (dialog, which) -> {
                    Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE); intent.setType("*/*");
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    modelPickerRole = role; modelPickerActive = true;
                    try { modelPicker.launch(intent); }
                    catch (ActivityNotFoundException | IllegalStateException error) {
                        modelPickerRole = null; modelPickerActive = false;
                        Toast.makeText(this, "Не удалось открыть выбор файла", Toast.LENGTH_LONG).show();
                    }
                }).show();
    }

    public final class OfflineBridge {
        private final WebView owner;
        OfflineBridge(WebView owner) { this.owner = owner; }
        private boolean allowed() { return isOffline() && web == owner && !failed && !isFinishing() && !isDestroyed(); }
        @JavascriptInterface public String equipmentCatalog() {
            if (!allowed()) return null;
            try (InputStreamReader input = new InputStreamReader(getAssets().open("equipment-catalog.json"), StandardCharsets.UTF_8)) {
                StringBuilder catalog = new StringBuilder();
                char[] block = new char[2048];
                int count;
                while ((count = input.read(block)) != -1) catalog.append(block, 0, count);
                return catalog.toString();
            } catch (IOException error) {
                return null;
            }
        }
        @JavascriptInterface public String state() {
            return allowed() ? offline.snapshot().toString() : "{}";
        }
        @JavascriptInterface public void load() {
            runOnUiThread(() -> { if (allowed() && foreground) offline.load(); });
        }
        @JavascriptInterface public void analyze(String requestId, String base64Jpeg) {
            runOnUiThread(() -> { if (allowed() && foreground) offline.analyze(requestId, base64Jpeg); });
        }
        @JavascriptInterface public void analyzeWithProfile(String requestId, String base64Jpeg, String profile) {
            runOnUiThread(() -> { if (allowed() && foreground) offline.analyze(requestId, base64Jpeg, profile); });
        }
        @JavascriptInterface public void cancel() {
            runOnUiThread(() -> { if (allowed()) offline.cancel(); });
        }
        @JavascriptInterface public void importModel() {
            runOnUiThread(() -> { if (allowed() && foreground) chooseModelFile("model"); });
        }
    }
}
