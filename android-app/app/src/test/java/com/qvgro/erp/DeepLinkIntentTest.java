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

    @Test
    public void moldRackSlotDeepLinks_enterManageModeWithoutChangingThePrintedPath() {
        String printed = "https://erp.qfylagent.org/mold-rack/slots/42";
        assertTrue(UrlPolicy.isMoldRackSlot(printed));
        assertTrue(UrlPolicy.moldRackManageUrl(printed).endsWith("/mold-rack/slots/42?mode=manage"));
        assertFalse(UrlPolicy.isMoldRackSlot("https://erp.qfylagent.org/molds/42"));
    }
}
