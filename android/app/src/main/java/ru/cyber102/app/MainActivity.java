package ru.cyber102.app;

import android.Manifest;
import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import android.content.ActivityNotFoundException;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.webkit.*;
import android.widget.*;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.List;
import android.graphics.drawable.GradientDrawable;
import android.content.res.ColorStateList;
import org.json.JSONArray;
import org.json.JSONException;
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
    private static final int CAMERA_REQUEST = 20;
    private static final int BACKGROUND = 0xfff4f6f5;
    private static final int INK = 0xff192a28;
    private static final int MUTED = 0xff4d615d;
    private static final int ACCENT = 0xff176451;
    private static final int OUTLINE = 0xffd6e1de;
    private static final int MAX_RECENT = 4;
    private LinearLayout root;
    private FrameLayout content;
    private WebView web;
    private TextView status;
    private ProgressBar progress;
    private PermissionRequest pendingCamera;
    private volatile String activeUrl;
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
        brand.setText("Брови и ресницы");
        brand.setTextSize(16);
        brand.setTextColor(INK);
        brand.setTypeface(null, android.graphics.Typeface.BOLD);
        bar.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
        Button settings = button("Сервер", this::showSettings);
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
        activeUrl = getPreferences(MODE_PRIVATE).getString("address", DEMO);
        if (isDemo()) showWelcome();
        else load(activeUrl);
    }

    private void showWelcome() {
        status.setText("Сервер не подключён");
        progress.setVisibility(View.GONE);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(panel(Color.WHITE, OUTLINE, 16));
        card.setPadding(dp(24), dp(24), dp(24), dp(20));
        TextView title = new TextView(this);
        title.setText("Подключение к серверу");
        title.setTextColor(INK);
        title.setTextSize(25);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        card.addView(title);
        TextView description = new TextView(this);
        description.setText("Укажите IP-адрес или имя сервера, чтобы открыть рабочие данные и осмотр оборудования.");
        description.setTextColor(MUTED);
        description.setTextSize(16);
        description.setPadding(0, dp(10), 0, dp(16));
        card.addView(description);
        Button connect = button("Выбрать сервер", this::showSettings);
        connect.setBackgroundTintList(null);
        connect.setBackground(panel(ACCENT, ACCENT, 10));
        connect.setTextColor(Color.WHITE);
        card.addView(connect, new LinearLayout.LayoutParams(-1, dp(48)));

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
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!sameOrigin(uri, Uri.parse(activeUrl))) return missingAsset();
                String path = uri.getPath();
                if (isDemo()) {
                    if (path == null || path.contains("..")) return missingAsset();
                    return bundledAsset(path.substring(1));
                }
                if (!"GET".equals(request.getMethod()) || path == null) return null;
                if (request.isForMainFrame() && "/index.html".equals(path)) {
                    return bundledAsset("inspection/index.html");
                }
                switch (path) {
                    case "/__mobile_inspection/app.js":
                        return bundledAsset("inspection/app.js");
                    case "/__mobile_inspection/media.js":
                        return bundledAsset("inspection/media.js");
                    case "/__mobile_inspection/inspection.js":
                        return bundledAsset("inspection/inspection.js");
                    case "/__mobile_inspection/styles.css":
                        return bundledAsset("inspection/styles.css");
                    default:
                        return null;
                }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
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
                if (!failed && sameOrigin(Uri.parse(url), Uri.parse(activeUrl))) status.setText(isDemo() ? "Демо · ответы заданы заранее" : "Сервер · " + Uri.parse(activeUrl).getHost());
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
                if (isDemo() || !sameOrigin(configured, page) || !host.equalsIgnoreCase(configured.getHost())) {
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
                name.endsWith(".jpg") ? "image/jpeg" :
                name.endsWith(".png") ? "image/png" : "text/html";
        try {
            return new WebResourceResponse(mime, "UTF-8", getAssets().open(name));
        } catch (IOException error) {
            return missingAsset();
        }
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
    private boolean isDemo() { return activeUrl.equals(DEMO); }
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
        if (web != null) {
            web.stopLoading();
            if (!sameOrigin(Uri.parse(url), Uri.parse(activeUrl))) {
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
        status.setText(isDemo() ? "Демо · ответы заданы заранее" : "Подключение к серверу…");
        progress.setVisibility(View.VISIBLE);
        if (isDemo()) {
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
        actions.addView(button("Открыть встроенное демо", () -> saveAndLoad(DEMO)));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(actions);
        content.removeAllViews();
        content.addView(scroll);
        status.setText("Экран недоступен");
    }
    private void showSettings() {
        releaseCamera();
        cancelCamera();
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(24), dp(8), dp(24), 0);
        TextView hint = new TextView(this);
        hint.setText("Подойдёт локальный или публичный IP, а также домен. Порт можно указать после двоеточия.");
        hint.setTextColor(MUTED);
        hint.setPadding(0, 0, 0, dp(12));
        form.addView(hint);
        EditText address = input("IP-адрес или домен", android.text.InputType.TYPE_TEXT_VARIATION_URI);
        address.setText(isDemo() ? "" : origin(Uri.parse(activeUrl)));
        form.addView(address);
        EditText user = input("Имя пользователя", android.text.InputType.TYPE_TEXT_VARIATION_NORMAL);
        user.setText(login);
        form.addView(user);
        EditText secret = input("Пароль", android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        form.addView(secret);
        List<String> recent = recentServers();
        if (!recent.isEmpty()) form.addView(button("Недавние серверы", () ->
                new AlertDialog.Builder(this).setTitle("Недавние серверы")
                        .setItems(recent.toArray(new String[0]), (d, which) -> {
                            address.setText(recent.get(which));
                            user.setText("");
                            secret.setText("");
                        }).show()));
        if (!isDemo()) form.addView(button("Открыть ссылку из QR", () -> {
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
    private List<String> recentServers() {
        List<String> items = new ArrayList<>();
        String saved = getPreferences(MODE_PRIVATE).getString("recent_servers", "[]");
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
        List<String> items = recentServers();
        String address = origin(Uri.parse(target));
        items.remove(address);
        items.add(0, address);
        JSONArray saved = new JSONArray(items.subList(0, Math.min(items.size(), MAX_RECENT)));
        getPreferences(MODE_PRIVATE).edit().putString("recent_servers", saved.toString()).apply();
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
        if (DEMO.equals(url)) {
            login = null;
            password = null;
        }
        getPreferences(MODE_PRIVATE).edit().putString("address", url).apply();
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
        if (web != null) web.onPause();
        super.onStop();
    }
    @Override protected void onDestroy() {
        network.shutdownNow();
        cancelCamera();
        cancelFileChooser();
        if (web != null) { content.removeView(web); web.destroy(); web = null; }
        super.onDestroy();
    }
}
