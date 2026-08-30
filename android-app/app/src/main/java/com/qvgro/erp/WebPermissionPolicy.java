package com.qvgro.erp;

/** Restricts WebView media permission grants to this ERP's HTTPS camera scanner. */
public final class WebPermissionPolicy {
    // Same stable value as android.webkit.PermissionRequest.RESOURCE_VIDEO_CAPTURE.
    static final String VIDEO_CAPTURE = "android.webkit.resource.VIDEO_CAPTURE";

    private WebPermissionPolicy() {
    }

    public static boolean canGrantCamera(String origin, String[] resources) {
        return UrlPolicy.isTrusted(origin)
            && resources != null
            && resources.length == 1
            && VIDEO_CAPTURE.equals(resources[0]);
    }
}
