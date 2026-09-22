package com.qvgro.erp;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class WebPermissionPolicyTest {
    @Test
    public void grantsOnlyCameraToTheExactErpOrigin() {
        assertTrue(WebPermissionPolicy.canGrantCamera(
            "https://erp.qfylagent.org/quality",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "https://evil.erp.qfylagent.org/",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "http://erp.qfylagent.org/",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE}
        ));
    }

    @Test
    public void rejectsMicrophoneAndMixedMediaRequests() {
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "https://erp.qfylagent.org/",
            new String[]{"android.webkit.resource.AUDIO_CAPTURE"}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera(
            "https://erp.qfylagent.org/",
            new String[]{WebPermissionPolicy.VIDEO_CAPTURE, "android.webkit.resource.AUDIO_CAPTURE"}
        ));
        assertFalse(WebPermissionPolicy.canGrantCamera("https://erp.qfylagent.org/", null));
    }
}
