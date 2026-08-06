package com.qvgro.erp;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Bitmap;
import android.net.Uri;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SafeBrowsingResponse;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.annotation.RequiresApi;

import java.io.ByteArrayInputStream;
import java.util.Collections;
import java.util.Locale;

public final class ErpWebViewClient extends WebViewClient {
    public interface Listener {
        void onPageStarted();

        void onPageReady();

        void onConnectionError(String message);

        void onRendererGone();
    }

    private final Activity activity;
    private final Listener listener;
    private boolean mainFrameFailed;

    public ErpWebViewClient(Activity activity, Listener listener) {
        this.activity = activity;
        this.listener = listener;
    }

    @Override
    public void onPageStarted(WebView view, String url, Bitmap favicon) {
        mainFrameFailed = false;
        listener.onPageStarted();
    }

    @Override
    public void onPageFinished(WebView view, String url) {
        if (!mainFrameFailed && UrlPolicy.isTrusted(url)) {
            listener.onPageReady();
        }
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        Uri uri = request.getUrl();
        if (UrlPolicy.isTrusted(uri == null ? null : uri.toString())) {
            return false;
        }
        if (!request.isForMainFrame()) {
            return true;
        }
        return handleNavigation(uri, request.hasGesture());
    }

    @SuppressWarnings("deprecation")
    @Override
    public boolean shouldOverrideUrlLoading(WebView view, String url) {
        return handleNavigation(Uri.parse(url), false);
    }

    private boolean handleNavigation(Uri uri, boolean hasUserGesture) {
        String value = uri == null ? "" : uri.toString();
        if (UrlPolicy.isTrusted(value)) {
            return false;
        }

        String scheme = uri == null || uri.getScheme() == null
            ? ""
            : uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri == null ? null : uri.getHost();

        // Never downgrade the authenticated ERP origin to HTTP or a custom scheme.
        if (UrlPolicy.TRUSTED_HOST.equalsIgnoreCase(host)) {
            showBlockedLink();
            return true;
        }

        if (hasUserGesture
            && (UrlPolicy.isExternalWebUrl(value) || "tel".equals(scheme) || "mailto".equals(scheme))) {
            openExternal(uri);
            return true;
        }

        showBlockedLink();
        return true;
    }

    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
        Uri uri = request.getUrl();
        String scheme = uri == null || uri.getScheme() == null
            ? ""
            : uri.getScheme().toLowerCase(Locale.ROOT);
        if (("http".equals(scheme) || "https".equals(scheme))
            && !UrlPolicy.isTrusted(uri.toString())) {
            return new WebResourceResponse(
                "text/plain",
                "UTF-8",
                403,
                "Blocked",
                Collections.emptyMap(),
                new ByteArrayInputStream(new byte[0])
            );
        }
        return null;
    }

    private void openExternal(Uri uri) {
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, uri);
            intent.addCategory(Intent.CATEGORY_BROWSABLE);
            activity.startActivity(intent);
        } catch (ActivityNotFoundException error) {
            Toast.makeText(activity, R.string.no_external_app, Toast.LENGTH_LONG).show();
        }
    }

    private void showBlockedLink() {
        Toast.makeText(activity, R.string.external_link_blocked, Toast.LENGTH_LONG).show();
    }

    @Override
    public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
        if (request.isForMainFrame()) {
            mainFrameFailed = true;
            listener.onConnectionError(activity.getString(R.string.network_error_message));
        }
    }

    @Override
    public void onReceivedHttpError(
        WebView view,
        WebResourceRequest request,
        WebResourceResponse errorResponse
    ) {
        if (request.isForMainFrame() && errorResponse.getStatusCode() >= 500) {
            mainFrameFailed = true;
            listener.onConnectionError(activity.getString(R.string.server_error_message));
        }
    }

    @Override
    public void onReceivedSslError(WebView view, SslErrorHandler handler, android.net.http.SslError error) {
        handler.cancel();
        mainFrameFailed = true;
        listener.onConnectionError(activity.getString(R.string.ssl_error_message));
    }

    @Override
    @RequiresApi(27)
    public void onSafeBrowsingHit(
        WebView view,
        WebResourceRequest request,
        int threatType,
        SafeBrowsingResponse callback
    ) {
        callback.backToSafety(true);
        mainFrameFailed = true;
        listener.onConnectionError(activity.getString(R.string.unsafe_page_message));
    }

    @Override
    public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
        mainFrameFailed = true;
        listener.onRendererGone();
        return true;
    }
}
