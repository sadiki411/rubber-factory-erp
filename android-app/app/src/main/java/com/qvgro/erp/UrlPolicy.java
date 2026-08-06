package com.qvgro.erp;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Keeps the WebView and authenticated downloads on the ERP HTTPS origin. */
public final class UrlPolicy {
    public static final String APP_URL = "https://erp.qvgro.com/";
    public static final String TRUSTED_HOST = "erp.qvgro.com";

    private UrlPolicy() {
    }

    public static boolean isTrusted(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        try {
            URI uri = new URI(value);
            String scheme = uri.getScheme();
            String host = uri.getHost();
            int port = uri.getPort();
            return "https".equalsIgnoreCase(scheme)
                && host != null
                && TRUSTED_HOST.equals(host.toLowerCase(Locale.ROOT))
                && uri.getRawUserInfo() == null
                && (port == -1 || port == 443);
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return false;
        }
    }

    public static boolean isExternalWebUrl(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        try {
            URI uri = new URI(value);
            String scheme = uri.getScheme();
            return uri.getHost() != null
                && uri.getRawUserInfo() == null
                && ("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme));
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return false;
        }
    }
}
