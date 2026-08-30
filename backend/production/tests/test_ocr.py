from django.test import SimpleTestCase

from production.ocr import _critical_task_blockers, _extract_log_rows, _extract_task


class ProductionPhotoOcrParsingTests(SimpleTestCase):
    def test_task_header_keeps_critical_values_and_confidence(self):
        lines = [
            {"text": "订单号 DX-20260830-01", "confidence": 97, "top": 10, "bottom": 30},
            {"text": "订单数量 10000", "confidence": 94, "top": 40, "bottom": 60},
            {"text": "模具孔数 8", "confidence": 92, "top": 70, "bottom": 90},
            {"text": "订单模数 1350", "confidence": 91, "top": 100, "bottom": 120},
        ]
        draft, confidence = _extract_task(lines)
        self.assertEqual(draft["order_no"], "DX-20260830-01")
        self.assertEqual(draft["order_quantity"], 10000)
        self.assertEqual(draft["cavities"], 8)
        self.assertEqual(draft["planned_mold_count"], 1350)
        self.assertEqual(confidence["cavities"], 92)

    def test_missing_or_low_confidence_planned_mold_count_blocks_ocr_draft(self):
        base_draft = {"order_no": "DX-20260830-01", "cavities": 8}
        base_confidence = {"order_no": 97, "cavities": 92}

        missing = _critical_task_blockers(base_draft, base_confidence)
        self.assertEqual([item["field"] for item in missing], ["planned_mold_count"])
        self.assertIn("未识别", missing[0]["message"])

        low_confidence = _critical_task_blockers(
            {**base_draft, "planned_mold_count": 1350},
            {**base_confidence, "planned_mold_count": 61},
        )
        self.assertEqual(
            [item["field"] for item in low_confidence], ["planned_mold_count"]
        )
        self.assertIn("61.0%", low_confidence[0]["message"])

        confirmed = _critical_task_blockers(
            {**base_draft, "planned_mold_count": 1350},
            {**base_confidence, "planned_mold_count": 91},
        )
        self.assertEqual(confirmed, [])

    def test_each_handoff_row_is_kept_as_an_independent_counter_reading(self):
        lines = [
            {"text": "生产日期 作业员 生产模数 欠模数", "confidence": 98},
            {"text": "2026-08-29 张三 100 900", "confidence": 96},
            {"text": "2026-08-30 李四 230 770", "confidence": 95},
        ]
        rows, blocking = _extract_log_rows(lines)
        self.assertEqual([row["cumulative_mold_count"] for row in rows], [100, 230])
        self.assertEqual([row["operator"] for row in rows], ["张三", "李四"])
        self.assertEqual(blocking, [])

    def test_low_confidence_counter_is_blocking_but_optional_person_is_not(self):
        lines = [
            {"text": "生产日期 作业员 生产模数", "confidence": 96},
            {"text": "315", "confidence": 62},
        ]
        rows, blocking = _extract_log_rows(lines)
        self.assertEqual(rows[0]["cumulative_mold_count"], 315)
        self.assertEqual(rows[0]["operator"], "")
        self.assertEqual(blocking[0]["field"], "cumulative_mold_count")
        self.assertIn("62", blocking[0]["message"])
