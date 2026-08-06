package com.qvgro.erp;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class FileNameUtilsTest {
    @Test
    public void decodesUtf8ContentDisposition() {
        String value = FileNameUtils.choose(
            "https://erp.qvgro.com/api/imports/template/",
            "attachment; filename*=UTF-8''%E6%A8%A1%E5%85%B7%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        );
        assertEquals("模具导入模板.xlsx", value);
    }

    @Test
    public void removesPathAndControlCharacters() {
        String value = FileNameUtils.choose(
            "https://erp.qvgro.com/download",
            "attachment; filename=\"../../bad:\\name\r\n.xlsx\"",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        );
        assertFalse(value.contains("/"));
        assertFalse(value.contains("\\"));
        assertFalse(value.contains(":"));
        assertFalse(value.contains("\r"));
        assertFalse(value.contains("\n"));
    }

    @Test
    public void fallsBackToTheUrlPath() {
        assertEquals(
            "report.xlsx",
            FileNameUtils.choose(
                "https://erp.qvgro.com/media/report.xlsx?download=1",
                null,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        );
    }

    @Test
    public void appendsATimestampBeforeTheExtension() {
        assertTrue(
            FileNameUtils.withTimestamp("report.xlsx")
                .matches("report-\\d{8}-\\d{6}-\\d{3}\\.xlsx")
        );
    }
}
