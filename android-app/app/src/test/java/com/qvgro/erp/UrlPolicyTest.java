package com.qvgro.erp;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class UrlPolicyTest {
    @Test
    public void acceptsOnlyTheExactHttpsOrigin() {
        assertTrue(UrlPolicy.isTrusted("https://erp.qvgro.com/"));
        assertTrue(UrlPolicy.isTrusted("https://erp.qvgro.com/api/auth/session/"));
        assertTrue(UrlPolicy.isTrusted("https://erp.qvgro.com:443/orders?x=1"));
    }

    @Test
    public void rejectsLookalikesAndUnsafeSchemes() {
        assertFalse(UrlPolicy.isTrusted("http://erp.qvgro.com/"));
        assertFalse(UrlPolicy.isTrusted("https://evil.erp.qvgro.com/"));
        assertFalse(UrlPolicy.isTrusted("https://erp.qvgro.com.evil.example/"));
        assertFalse(UrlPolicy.isTrusted("https://user@erp.qvgro.com/"));
        assertFalse(UrlPolicy.isTrusted("https://erp.qvgro.com:8443/"));
        assertFalse(UrlPolicy.isTrusted("javascript:alert(1)"));
        assertFalse(UrlPolicy.isTrusted("not a url"));
    }
}
