package com.qvgro.erp;

import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;

public final class ErpWebChromeClient extends WebChromeClient {
    public interface Listener {
        void onProgressChanged(int progress);

        boolean openFileChooser(ValueCallback<android.net.Uri[]> callback, FileChooserParams params);
    }

    private final Listener listener;

    public ErpWebChromeClient(Listener listener) {
        this.listener = listener;
    }

    @Override
    public void onProgressChanged(WebView view, int newProgress) {
        listener.onProgressChanged(newProgress);
    }

    @Override
    public boolean onShowFileChooser(
        WebView webView,
        ValueCallback<android.net.Uri[]> filePathCallback,
        FileChooserParams fileChooserParams
    ) {
        return listener.openFileChooser(filePathCallback, fileChooserParams);
    }

    @Override
    public void onPermissionRequest(PermissionRequest request) {
        // The ERP does not need camera, microphone, location or protected-media access in WebView.
        request.deny();
    }

    @Override
    public void onGeolocationPermissionsShowPrompt(
        String origin,
        GeolocationPermissions.Callback callback
    ) {
        callback.invoke(origin, false, false);
    }

    @Override
    public boolean onCreateWindow(
        WebView view,
        boolean isDialog,
        boolean isUserGesture,
        android.os.Message resultMsg
    ) {
        // New windows can bypass the origin policy, so the app deliberately declines them.
        return false;
    }
}
