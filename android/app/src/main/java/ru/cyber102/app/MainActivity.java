package ru.cyber102.app;

import android.Manifest;
import android.app.Activity;
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
import java.util.Locale;

/** Small Android host for the team's shared inspection screen. */
public final class MainActivity extends Activity {
    private static final String DEMO = "https://appassets.androidplatform.net/index.html";
    private static final int CAMERA_REQUEST = 20;
    private LinearLayout root;
    private FrameLayout content;
    private WebView web;
    private TextView status;
    private ProgressBar progress;
    private PermissionRequest pendingCamera;
    private String activeUrl;
    private boolean foreground;
    private boolean failed;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(242, 243, 245));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        LinearLayout bar = new LinearLayout(this);
        bar.setPadding(dp(12), dp(4), dp(8), dp(4));
        bar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        TextView brand = new TextView(this);
        brand.setText("Cyber102");
        brand.setTextSize(20);
        brand.setTextColor(Color.rgb(17, 24, 32));
        brand.setTypeface(null, android.graphics.Typeface.BOLD);
        bar.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
        Button settings = button("Адрес", this::showSettings);
        bar.addView(settings);
        bar.addView(button("Обновить", () -> load(activeUrl)));
        root.addView(bar);
        status = new TextView(this);
        status.setTextSize(13);
        status.setPadding(dp(16), dp(4), dp(16), dp(8));
        root.addView(status);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        root.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));
        content = new FrameLayout(this);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        activeUrl = getPreferences(MODE_PRIVATE).getString("address", DEMO);
        createWebView();
        if (web != null) load(activeUrl);
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
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!"appassets.androidplatform.net".equals(uri.getHost())) return null;
                if (!"https".equals(uri.getScheme())) return missingAsset();
                String path = uri.getPath();
                if (path == null || path.contains("..")) return missingAsset();
                try {
                    String name = path.substring(1);
                    String mime = name.endsWith(".js") ? "application/javascript" :
                            name.endsWith(".css") ? "text/css" : name.endsWith(".json") ?
                            "application/json" : name.endsWith(".jpg") ? "image/jpeg" : "text/html";
                    return new WebResourceResponse(mime, "UTF-8", getAssets().open(name));
                } catch (IOException error) { return missingAsset(); }
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
                if (!sameOrigin(Uri.parse(url), Uri.parse(activeUrl))) {
                    view.stopLoading();
                    showError("Сервер перенаправил на другой адрес. Укажите конечный HTTPS-адрес в настройках.");
                }
            }
            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                if (!failed) status.setText(isDemo() ? "Демо · ответы заданы заранее" : "Сервер · " + Uri.parse(activeUrl).getHost());
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showError("Не удалось открыть экран. Проверьте адрес, сеть и доступность сервера.");
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame()) showError("Сервер вернул ошибку " + response.getStatusCode() + ". Проверьте адрес страницы.");
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
                showError("Система закрыла веб-экран. Нажмите «Обновить», чтобы открыть его снова.");
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) { progress.setProgress(value); }
            @Override public void onPermissionRequest(PermissionRequest request) {
                if (!foreground || !sameOrigin(request.getOrigin(), Uri.parse(activeUrl)) ||
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
        if (!sameOrigin(request.getOrigin(), Uri.parse(activeUrl))) request.deny();
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
        cancelCamera();
        activeUrl = url;
        failed = false;
        content.removeAllViews();
        if (web == null) createWebView();
        else content.addView(web, new FrameLayout.LayoutParams(-1, -1));
        if (web == null) return;
        web.setVisibility(View.VISIBLE);
        status.setText(isDemo() ? "Демо · ответы заданы заранее" : "Подключение к серверу…");
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(activeUrl);
    }
    private void showError(String message) {
        failed = true;
        progress.setVisibility(View.GONE);
        if (web != null) web.setVisibility(View.GONE);
        TextView error = new TextView(this);
        error.setText(message + "\n\n«Обновить» повторит загрузку. Через «Адрес» можно включить встроенное демо.");
        error.setTextSize(18);
        error.setPadding(dp(24), dp(32), dp(24), dp(24));
        content.removeAllViews();
        content.addView(error);
        status.setText("Экран недоступен");
    }
    private void showSettings() {
        releaseCamera();
        cancelCamera();
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(24), dp(8), dp(24), 0);
        TextView hint = new TextView(this);
        hint.setText("HTTPS-адрес общего веб-экрана. API /analyze настраивает команда на этой странице.");
        form.addView(hint);
        EditText address = new EditText(this);
        address.setSingleLine(true);
        address.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        address.setHint("https://example.org/");
        address.setText(isDemo() ? "" : activeUrl);
        form.addView(address);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Подключение")
                .setView(form).setPositiveButton("Открыть", null)
                .setNeutralButton("Встроенное демо", (d, w) -> saveAndLoad(DEMO))
                .setNegativeButton("Отмена", null).create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String value = address.getText().toString().trim();
            Uri uri = Uri.parse(value);
            if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getHost().isEmpty() ||
                    uri.getUserInfo() != null || value.matches(".*\\s.*") || uri.getPort() > 65535 || uri.getPort() == 0 ||
                    "appassets.androidplatform.net".equalsIgnoreCase(uri.getHost())) {
                address.setError("Введите HTTPS-адрес страницы без логина и пароля");
                return;
            }
            dialog.dismiss();
            saveAndLoad(value);
        }));
        dialog.show();
    }
    private void saveAndLoad(String url) {
        getPreferences(MODE_PRIVATE).edit().putString("address", url).apply();
        load(url);
    }
    private Button button(String label, Runnable action) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(Color.rgb(0, 112, 155));
        button.setOnClickListener(v -> action.run());
        return button;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void releaseCamera() {
        if (web != null) web.evaluateJavascript("if (typeof releaseCamera === 'function') releaseCamera();", null);
    }
    @Override protected void onResume() {
        super.onResume(); foreground = true;
        if (web != null) web.onResume();
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
        cancelCamera();
        if (web != null) { content.removeView(web); web.destroy(); web = null; }
        super.onDestroy();
    }
}
