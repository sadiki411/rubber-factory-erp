package com.qvgro.erp;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Parcelable;
import android.provider.MediaStore;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.MimeTypeMap;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResult;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.content.FileProvider;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public final class MainActivity extends ComponentActivity {
    private static final long EXIT_CONFIRMATION_MILLIS = 2_000L;
    private final List<String> cameraGrantPackages = new ArrayList<>();
    private DownloadHandler downloadHandler;

    private final ActivityResultLauncher<Intent> fileChooserLauncher =
        registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(),
            this::handleFileChooserResult
        );

    private final ActivityResultLauncher<String> legacyStoragePermissionLauncher =
        registerForActivityResult(
            new ActivityResultContracts.RequestPermission(),
            granted -> {
                if (downloadHandler != null) {
                    downloadHandler.onLegacyStoragePermissionResult(Boolean.TRUE.equals(granted));
                }
            }
        );

    private final ActivityResultLauncher<String> cameraPermissionLauncher =
        registerForActivityResult(
            new ActivityResultContracts.RequestPermission(),
            this::handleCameraPermissionResult
        );

    private View root;
    private WebView webView;
    private ProgressBar progressBar;
    private View errorPanel;
    private TextView errorMessage;
    private ValueCallback<Uri[]> pendingFileCallback;
    private Uri capturedImageUri;
    private File capturedImageFile;
    private PermissionRequest pendingCameraPermissionRequest;
    private boolean rendererGone;
    private long lastBackPressedAt;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        root = findViewById(R.id.root);
        webView = findViewById(R.id.web_view);
        progressBar = findViewById(R.id.page_progress);
        errorPanel = findViewById(R.id.error_panel);
        errorMessage = findViewById(R.id.error_message);

        configureSystemBars();
        configureWebView();
        cleanOldCapturedImages();

        downloadHandler = new DownloadHandler(
            this,
            () -> legacyStoragePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        );
        webView.setDownloadListener(downloadHandler::start);

        findViewById(R.id.retry_button).setOnClickListener(view -> retryConnection());
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                handleBackNavigation();
            }
        });

        boolean restored = savedInstanceState != null && webView.restoreState(savedInstanceState) != null;
        if (!restored) {
            loadStartPage();
        }
    }

    private void configureSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        WindowInsetsControllerCompat controller = new WindowInsetsControllerCompat(getWindow(), root);
        controller.setAppearanceLightStatusBars(false);
        controller.setAppearanceLightNavigationBars(false);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            Insets bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout()
            );
            Insets keyboard = insets.getInsets(WindowInsetsCompat.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, keyboard.bottom));
            return insets;
        });
    }

    @SuppressWarnings("SetJavaScriptEnabled")
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setUserAgentString(
            settings.getUserAgentString() + " DongXiangERP/" + BuildConfig.VERSION_NAME
        );

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            WebView.startSafeBrowsing(this, ignored -> {
                // Unsafe pages are also rejected by ErpWebViewClient.onSafeBrowsingHit.
            });
        }

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, false);

        webView.setWebViewClient(new ErpWebViewClient(this, new ErpWebViewClient.Listener() {
            @Override
            public void onPageStarted() {
                progressBar.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageReady() {
                rendererGone = false;
                errorPanel.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
                progressBar.setVisibility(View.GONE);
                CookieManager.getInstance().flush();
            }

            @Override
            public void onConnectionError(String message) {
                showConnectionError(message);
            }

            @Override
            public void onRendererGone() {
                rendererGone = true;
                showConnectionError(getString(R.string.webview_crashed_message));
                WebView deadView = webView;
                webView = null;
                if (deadView != null) {
                    ((android.view.ViewGroup) deadView.getParent()).removeView(deadView);
                    deadView.destroy();
                }
            }
        }));

        webView.setWebChromeClient(new ErpWebChromeClient(new ErpWebChromeClient.Listener() {
            @Override
            public void onProgressChanged(int progress) {
                progressBar.setProgress(progress);
                progressBar.setVisibility(progress >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override
            public boolean openFileChooser(
                ValueCallback<Uri[]> callback,
                WebChromeClient.FileChooserParams params
            ) {
                return launchFileChooser(callback, params);
            }

            @Override
            public void onWebPermissionRequest(PermissionRequest request) {
                handleWebPermissionRequest(request);
            }

            @Override
            public void onWebPermissionRequestCanceled(PermissionRequest request) {
                if (pendingCameraPermissionRequest == request) {
                    pendingCameraPermissionRequest = null;
                }
            }
        }));
    }

    private void handleWebPermissionRequest(PermissionRequest request) {
        if (request == null
            || !WebPermissionPolicy.canGrantCamera(
                request.getOrigin() == null ? null : request.getOrigin().toString(),
                request.getResources()
            )) {
            if (request != null) {
                request.deny();
            }
            return;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED) {
            request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            return;
        }
        if (pendingCameraPermissionRequest != null && pendingCameraPermissionRequest != request) {
            pendingCameraPermissionRequest.deny();
        }
        pendingCameraPermissionRequest = request;
        cameraPermissionLauncher.launch(Manifest.permission.CAMERA);
    }

    private void handleCameraPermissionResult(Boolean granted) {
        PermissionRequest request = pendingCameraPermissionRequest;
        pendingCameraPermissionRequest = null;
        if (request == null) {
            return;
        }
        if (Boolean.TRUE.equals(granted)) {
            request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        } else {
            request.deny();
            Toast.makeText(
                this,
                "未授予相机权限，仍可在扫码页手动输入流程卡单号。",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void loadStartPage() {
        if (webView == null) {
            return;
        }
        rendererGone = false;
        webView.loadUrl(UrlPolicy.APP_URL);
    }

    private void retryConnection() {
        if (rendererGone || webView == null) {
            recreate();
            return;
        }
        if (!hasNetworkConnection()) {
            showConnectionError(getString(R.string.network_error_message));
            return;
        }
        String currentUrl = webView.getUrl();
        if (UrlPolicy.isTrusted(currentUrl)) {
            webView.reload();
        } else {
            loadStartPage();
        }
    }

    private boolean hasNetworkConnection() {
        ConnectivityManager manager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null) {
            return false;
        }
        Network network = manager.getActiveNetwork();
        NetworkCapabilities capabilities = manager.getNetworkCapabilities(network);
        return capabilities != null
            && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void showConnectionError(String message) {
        progressBar.setVisibility(View.GONE);
        if (webView != null) {
            webView.setVisibility(View.GONE);
        }
        errorMessage.setText(message);
        errorPanel.setVisibility(View.VISIBLE);
    }

    private boolean launchFileChooser(
        ValueCallback<Uri[]> callback,
        WebChromeClient.FileChooserParams params
    ) {
        if (pendingFileCallback != null) {
            pendingFileCallback.onReceiveValue(null);
        }
        clearPendingCapture(true);
        pendingFileCallback = callback;

        String[] acceptedTypes = normalizeAcceptTypes(params.getAcceptTypes());
        Intent openDocument = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        openDocument.addCategory(Intent.CATEGORY_OPENABLE);
        openDocument.setType(primaryMimeType(acceptedTypes));
        openDocument.putExtra(Intent.EXTRA_MIME_TYPES, acceptedTypes);
        openDocument.putExtra(
            Intent.EXTRA_ALLOW_MULTIPLE,
            params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE
        );

        Intent chooser = Intent.createChooser(openDocument, getString(R.string.select_file));
        Intent cameraIntent = acceptsImages(acceptedTypes) ? createCameraIntent() : null;
        if (cameraIntent != null) {
            chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Parcelable[]{cameraIntent});
        }

        try {
            fileChooserLauncher.launch(chooser);
            return true;
        } catch (Exception error) {
            pendingFileCallback = null;
            clearPendingCapture(true);
            callback.onReceiveValue(null);
            Toast.makeText(this, R.string.no_file_app, Toast.LENGTH_LONG).show();
            return false;
        }
    }

    private Intent createCameraIntent() {
        Intent intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        if (intent.resolveActivity(getPackageManager()) == null) {
            return null;
        }
        try {
            File directory = new File(getCacheDir(), "camera");
            if (!directory.exists() && !directory.mkdirs()) {
                return null;
            }
            File imageFile = File.createTempFile("mold-photo-", ".jpg", directory);
            capturedImageFile = imageFile;
            capturedImageUri = FileProvider.getUriForFile(
                this,
                BuildConfig.APPLICATION_ID + ".fileprovider",
                imageFile
            );
            intent.putExtra(MediaStore.EXTRA_OUTPUT, capturedImageUri);
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            for (ResolveInfo info : getPackageManager().queryIntentActivities(intent, 0)) {
                String packageName = info.activityInfo.packageName;
                grantUriPermission(
                    packageName,
                    capturedImageUri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                );
                cameraGrantPackages.add(packageName);
            }
            return intent;
        } catch (IOException | IllegalArgumentException error) {
            clearPendingCapture(true);
            return null;
        }
    }

    private void handleFileChooserResult(ActivityResult result) {
        ValueCallback<Uri[]> callback = pendingFileCallback;
        pendingFileCallback = null;
        if (callback == null) {
            clearPendingCapture(true);
            return;
        }

        Uri cameraUri = capturedImageUri;
        Uri[] selected = null;
        Intent data = result.getData();
        if (result.getResultCode() == Activity.RESULT_OK) {
            if (data != null && data.getClipData() != null) {
                ClipData clipData = data.getClipData();
                selected = new Uri[clipData.getItemCount()];
                for (int index = 0; index < clipData.getItemCount(); index++) {
                    selected[index] = clipData.getItemAt(index).getUri();
                }
            } else if (data != null && data.getData() != null) {
                selected = new Uri[]{data.getData()};
            } else if (capturedImageUri != null) {
                selected = new Uri[]{capturedImageUri};
            }
        }
        boolean usedCamera = selected != null
            && selected.length == 1
            && cameraUri != null
            && cameraUri.equals(selected[0]);
        clearPendingCapture(!usedCamera);
        callback.onReceiveValue(selected);
    }

    private String[] normalizeAcceptTypes(String[] values) {
        Set<String> result = new LinkedHashSet<>();
        boolean acceptsSpreadsheet = false;
        if (values != null) {
            for (String value : values) {
                if (value == null) {
                    continue;
                }
                for (String part : value.split(",")) {
                    String type = part.trim().toLowerCase(Locale.ROOT);
                    if (type.isBlank()) {
                        continue;
                    }
                    if (type.startsWith(".")) {
                        String extension = type.substring(1);
                        acceptsSpreadsheet = acceptsSpreadsheet
                            || "xlsx".equals(extension)
                            || "xls".equals(extension);
                        type = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
                        if (type == null) {
                            type = switch (extension) {
                                case "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
                                case "xls" -> "application/vnd.ms-excel";
                                case "csv" -> "text/csv";
                                default -> "application/octet-stream";
                            };
                        }
                    }
                    if ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet".equals(type)
                        || "application/vnd.ms-excel".equals(type)) {
                        acceptsSpreadsheet = true;
                    }
                    result.add(type);
                }
            }
        }
        if (result.isEmpty()) {
            result.add("*/*");
        } else if (acceptsSpreadsheet) {
            // ColorOS and files received through WeChat may expose .xlsx as a generic binary MIME.
            result.add("application/octet-stream");
        }
        return result.toArray(new String[0]);
    }

    private void clearPendingCapture(boolean deleteFile) {
        Uri uri = capturedImageUri;
        File file = capturedImageFile;
        capturedImageUri = null;
        capturedImageFile = null;
        if (uri != null) {
            for (String packageName : cameraGrantPackages) {
                revokeUriPermission(
                    packageName,
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                );
            }
        }
        cameraGrantPackages.clear();
        if (deleteFile && file != null && file.isFile()) {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
        }
    }

    private String primaryMimeType(String[] acceptedTypes) {
        if (acceptedTypes.length == 1) {
            return acceptedTypes[0];
        }
        String first = acceptedTypes[0];
        int separator = first.indexOf('/');
        if (separator <= 0) {
            return "*/*";
        }
        String group = first.substring(0, separator);
        for (String type : acceptedTypes) {
            if (!type.startsWith(group + "/")) {
                return "*/*";
            }
        }
        return group + "/*";
    }

    private boolean acceptsImages(String[] acceptedTypes) {
        for (String type : acceptedTypes) {
            if ("*/*".equals(type) || type.startsWith("image/")) {
                return true;
            }
        }
        return false;
    }

    private void cleanOldCapturedImages() {
        File directory = new File(getCacheDir(), "camera");
        File[] files = directory.listFiles();
        if (files == null) {
            return;
        }
        long cutoff = System.currentTimeMillis() - 7L * 24L * 60L * 60L * 1_000L;
        for (File file : files) {
            if (file.isFile() && file.lastModified() < cutoff) {
                //noinspection ResultOfMethodCallIgnored
                file.delete();
            }
        }
    }

    private void handleBackNavigation() {
        if (errorPanel.getVisibility() == View.VISIBLE) {
            requestExit();
            return;
        }
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        requestExit();
    }

    private void requestExit() {
        long now = System.currentTimeMillis();
        if (now - lastBackPressedAt <= EXIT_CONFIRMATION_MILLIS) {
            finish();
            return;
        }
        lastBackPressedAt = now;
        Toast.makeText(this, R.string.exit_hint, Toast.LENGTH_SHORT).show();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.onResume();
        }
    }

    @Override
    protected void onPause() {
        if (webView != null) {
            webView.onPause();
        }
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        if (webView != null && !rendererGone) {
            webView.saveState(outState);
        }
        super.onSaveInstanceState(outState);
    }

    @Override
    protected void onDestroy() {
        if (pendingCameraPermissionRequest != null) {
            pendingCameraPermissionRequest.deny();
            pendingCameraPermissionRequest = null;
        }
        if (pendingFileCallback != null) {
            pendingFileCallback.onReceiveValue(null);
            pendingFileCallback = null;
        }
        clearPendingCapture(true);
        if (webView != null) {
            webView.stopLoading();
            webView.setWebChromeClient(null);
            webView.setWebViewClient(null);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
