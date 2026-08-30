package com.qvgro.erp;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class WebPermissionPolicyTest {
    @Test
    public void grantsOnlyCameraToTheExactErpOrigin() {
        assertTrue(WebPermissionPolicy.canGrantCamera(
            "https://erp.qvgro.com/quality",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "https://evil.erp.qvgro.com/",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "http://erp.qvgro.com/",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE}
        ));
    }

    @Test
    public void rejectsMicrophoneAndMixedMediaRequests() {
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "https://erp.qvgro.com/",
            new String[]{"android.webkit.resource.AUDIO_CAPTURE"}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "https://erp.qvgro.com/",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE, "android.webkit.resource.AUDIO_CAPTURE"}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera("https://erp.qvgro.com/", null));
    }
}
