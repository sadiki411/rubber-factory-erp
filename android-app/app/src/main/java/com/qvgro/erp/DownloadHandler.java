package com.qvgro.erp;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.webkit.CookieManager;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

public final class DownloadHandler {
    public interface PermissionRequester {
        void requestLegacyStoragePermission();
    }

    private static final class PendingDownload {
        final String url;
        final String userAgent;
        final String contentDisposition;
        final String mimeType;

        PendingDownload(String url, String userAgent, String contentDisposition, String mimeType) {
            this.url = url;
            this.userAgent = userAgent;
            this.contentDisposition = contentDisposition;
            this.mimeType = mimeType;
        }
    }

    private final Activity activity;
    private final PermissionRequester permissionRequester;
    private PendingDownload pendingDownload;

    public DownloadHandler(Activity activity, PermissionRequester permissionRequester) {
        this.activity = activity;
        this.permissionRequester = permissionRequester;
    }

    public void start(
        String url,
        String userAgent,
        String contentDisposition,
        String mimeType,
        long contentLength
    ) {
        if (!UrlPolicy.isTrusted(url)) {
            Toast.makeText(activity, R.string.external_link_blocked, Toast.LENGTH_LONG).show();
            return;
        }

        PendingDownload download = new PendingDownload(url, userAgent, contentDisposition, mimeType);
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
            && ContextCompat.checkSelfPermission(activity, Manifest.permission.WRITE_EXTERNAL_STORAGE)
            != PackageManager.PERMISSION_GRANTED) {
            pendingDownload = download;
            permissionRequester.requestLegacyStoragePermission();
            return;
        }
        enqueue(download);
    }

    public void onLegacyStoragePermissionResult(boolean granted) {
        PendingDownload download = pendingDownload;
        pendingDownload = null;
        if (!granted) {
            Toast.makeText(activity, R.string.download_permission_denied, Toast.LENGTH_LONG).show();
            return;
        }
        if (download != null) {
            enqueue(download);
        }
    }

    private void enqueue(PendingDownload download) {
        try {
            String fileName = FileNameUtils.withTimestamp(
                FileNameUtils.choose(
                    download.url,
                    download.contentDisposition,
                    download.mimeType
                )
            );
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(download.url));
            request.setTitle(fileName);
            request.setDescription(activity.getString(R.string.app_name));
            request.setAllowedOverMetered(true);
            request.setAllowedOverRoaming(true);
            request.setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            );
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName);

            if (download.mimeType != null && !download.mimeType.isBlank()) {
                request.setMimeType(download.mimeType);
            }
            if (download.userAgent != null && !download.userAgent.isBlank()) {
                request.addRequestHeader("User-Agent", download.userAgent);
            }
            String cookies = CookieManager.getInstance().getCookie(download.url);
            if (cookies != null && !cookies.isBlank()) {
                request.addRequestHeader("Cookie", cookies);
            }
            request.addRequestHeader("Referer", UrlPolicy.APP_URL);

            DownloadManager manager = (DownloadManager) activity.getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager == null) {
                throw new IllegalStateException("DownloadManager unavailable");
            }
            manager.enqueue(request);
            Toast.makeText(activity, R.string.download_started, Toast.LENGTH_LONG).show();
        } catch (Exception error) {
            Toast.makeText(activity, R.string.download_failed, Toast.LENGTH_LONG).show();
        }
    }
}
