package com.qvgro.erp;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class UrlPolicyTest {
    @Test
    public void acceptsOnlyTheExactHttpsOrigin() {
        assertTrue(UrlPolicy.isTrusted("https://erp.qfylagent.org/"));
        assertTrue(UrlPolicy.isTrusted("https://erp.qfylagent.org/api/auth/session/"));
        assertTrue(UrlPolicy.isTrusted("https://erp.qfylagent.org:443/orders?x=1"));
    }

    @Test
    public void rejectsLookalikesAndUnsafeSchemes() {
        assertFalse(UrlPolicy.isTrusted("http://erp.qfylagent.org/"));
        assertFalse(UrlPolicy.isTrusted("https://evil.erp.qfylagent.org/"));
        assertFalse(UrlPolicy.isTrusted("https://erp.qfylagent.org.evil.example/"));
        assertFalse(UrlPolicy.isTrusted("https://user@erp.qfylagent.org/"));
        assertFalse(UrlPolicy.isTrusted("https://erp.qfylagent.org:8443/"));
        assertFalse(UrlPolicy.isTrusted("javascript:alert(1)"));
        assertFalse(UrlPolicy.isTrusted("not a url"));
    }
}
