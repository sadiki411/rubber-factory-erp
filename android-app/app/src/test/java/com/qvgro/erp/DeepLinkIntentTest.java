package com.qvgro.erp;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class DeepLinkIntentTest {
    @Test
    public void moldRackSlotLinksStayOnTheTrustedErpOrigin() {
        assertTrue(UrlPolicy.isTrusted("https://erp.qfylagent.org/mold-rack/slots/42"));
        assertFalse(UrlPolicy.isTrusted("http://erp.qfylagent.org/mold-rack/slots/42"));
        assertFalse(UrlPolicy.isTrusted("https://evil.example/mold-rack/slots/42"));
    }
}
