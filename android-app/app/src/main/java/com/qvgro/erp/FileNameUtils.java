package com.qvgro.erp;

import java.net.URI;
import java.net.URLDecoder;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Extracts a safe, readable file name from authenticated download headers. */
public final class FileNameUtils {
    private static final Pattern UTF8_FILE_NAME = Pattern.compile(
        "(?i)filename\\*\\s*=\\s*UTF-8''([^;]+)"
    );
    private static final Pattern PLAIN_FILE_NAME = Pattern.compile(
        "(?i)filename\\s*=\\s*(?:\"([^\"]+)\"|([^;]+))"
    );
    private static final Pattern FORBIDDEN = Pattern.compile("[\\\\/:*?\"<>|\\r\\n\\t]");
    private static final DateTimeFormatter FALLBACK_TIME = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss");
    private static final DateTimeFormatter UNIQUE_TIME = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSS");
    private static final int MAX_LENGTH = 120;

    private FileNameUtils() {
    }

    public static String choose(String url, String contentDisposition, String mimeType) {
        String candidate = fromContentDisposition(contentDisposition);
        if (candidate == null || candidate.isBlank()) {
            candidate = fromUrl(url);
        }
        candidate = sanitize(candidate);
        if (candidate.isBlank() || ".".equals(candidate) || "..".equals(candidate)) {
            candidate = "dongxiang-erp-" + LocalDateTime.now().format(FALLBACK_TIME) + extensionFor(mimeType);
        }
        return limitLength(candidate);
    }

    static String withTimestamp(String value) {
        String safeValue = sanitize(value);
        int dot = safeValue.lastIndexOf('.');
        String suffix = "-" + LocalDateTime.now().format(UNIQUE_TIME);
        String extension = dot > 0 && safeValue.length() - dot <= 12
            ? safeValue.substring(dot)
            : "";
        String body = extension.isEmpty() ? safeValue : safeValue.substring(0, dot);
        int maximumBodyLength = Math.max(1, MAX_LENGTH - suffix.length() - extension.length());
        if (body.length() > maximumBodyLength) {
            body = body.substring(0, maximumBodyLength);
        }
        return body + suffix + extension;
    }

    static String fromContentDisposition(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        Matcher utf8 = UTF8_FILE_NAME.matcher(value);
        if (utf8.find()) {
            try {
                return decodeUtf8(utf8.group(1).trim());
            } catch (IllegalArgumentException ignored) {
                // Fall through to the plain filename parameter.
            }
        }
        Matcher plain = PLAIN_FILE_NAME.matcher(value);
        if (plain.find()) {
            String quoted = plain.group(1);
            return (quoted != null ? quoted : plain.group(2)).trim();
        }
        return null;
    }

    static String sanitize(String value) {
        if (value == null) {
            return "";
        }
        String result = FORBIDDEN.matcher(value).replaceAll("_").trim();
        while (result.startsWith(".")) {
            result = result.substring(1).trim();
        }
        while (result.endsWith(".")) {
            result = result.substring(0, result.length() - 1).trim();
        }
        return result;
    }

    private static String fromUrl(String value) {
        if (value == null || value.isBlank()) {
            return "";
        }
        try {
            String path = new URI(value).getPath();
            if (path == null || path.isBlank() || path.endsWith("/")) {
                return "";
            }
            int separator = path.lastIndexOf('/');
            return decodeUtf8(path.substring(separator + 1));
        } catch (Exception ignored) {
            return "";
        }
    }

    private static String extensionFor(String mimeType) {
        if (mimeType == null) {
            return "";
        }
        return switch (mimeType.toLowerCase(Locale.ROOT)) {
            case "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" -> ".xlsx";
            case "application/vnd.ms-excel" -> ".xls";
            case "text/csv" -> ".csv";
            case "application/pdf" -> ".pdf";
            case "image/jpeg" -> ".jpg";
            case "image/png" -> ".png";
            case "image/webp" -> ".webp";
            default -> "";
        };
    }

    private static String decodeUtf8(String value) {
        try {
            return URLDecoder.decode(value, "UTF-8");
        } catch (Exception ignored) {
            return value;
        }
    }

    private static String limitLength(String value) {
        if (value.length() <= MAX_LENGTH) {
            return value;
        }
        int dot = value.lastIndexOf('.');
        String extension = dot > 0 && value.length() - dot <= 12 ? value.substring(dot) : "";
        int bodyLength = Math.max(1, MAX_LENGTH - extension.length());
        return value.substring(0, bodyLength) + extension;
    }
}
