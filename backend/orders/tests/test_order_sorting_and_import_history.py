import hashlib
import io
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from openpyxl import Workbook
from rest_framework.test import APITestCase

from orders.models import BusinessImportBatch, MaterialReceipt
from quality.models import QualityOrder


def malformed_xml_workbook():
    workbook = Workbook()
    workbook.active.append(["订单编号", "规格", "订单量"])
    source_bytes = io.BytesIO()
    workbook.save(source_bytes)
    output = io.BytesIO()
    with ZipFile(io.BytesIO(source_bytes.getvalue())) as source, ZipFile(
        output, "w", ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/workbook.xml":
                data = b"<workbook>"
            target.writestr(item, data)
    return output.getvalue()


class OrderSortingApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="order-sorting", password="test"
        )
        self.client.force_authenticate(self.user)
        values = (
            ("SORT-A", date(2026, 7, 3), date(2026, 8, 30)),
            ("SORT-B", date(2026, 8, 1), date(2026, 8, 10)),
            ("SORT-C", date(2026, 7, 25), date(2026, 8, 20)),
            ("SORT-NO-DATE", None, None),
        )
        for order_no, order_date, due_date in values:
            QualityOrder.objects.create(
                order_no=order_no,
                specification=f"SPEC-{order_no}",
                order_quantity=100,
                order_date=order_date,
                due_date=due_date,
                created_by=self.user,
            )

    def _numbers(self, ordering):
        response = self.client.get(
            "/api/orders/orders/", {"ordering": ordering, "page_size": 100}
        )
        self.assertEqual(response.status_code, 200, response.content)
        return [row["order_no"] for row in response.json()["results"]]

    def test_orders_can_sort_both_directions_by_order_and_due_date(self):
        self.assertEqual(
            self._numbers("order_date"),
            ["SORT-A", "SORT-C", "SORT-B", "SORT-NO-DATE"],
        )
        self.assertEqual(
            self._numbers("-order_date"),
            ["SORT-B", "SORT-C", "SORT-A", "SORT-NO-DATE"],
        )
        self.assertEqual(
            self._numbers("due_date"),
            ["SORT-B", "SORT-C", "SORT-A", "SORT-NO-DATE"],
        )
        self.assertEqual(
            self._numbers("-due_date"),
            ["SORT-A", "SORT-C", "SORT-B", "SORT-NO-DATE"],
        )

    def test_equal_dates_use_newest_id_as_stable_secondary_order(self):
        first = QualityOrder.objects.create(
            order_no="SORT-TIE-FIRST",
            specification="SORT-TIE-SPEC",
            order_quantity=100,
            order_date=date(2026, 7, 20),
            due_date=date(2026, 8, 15),
            created_by=self.user,
        )
        second = QualityOrder.objects.create(
            order_no="SORT-TIE-SECOND",
            specification="SORT-TIE-SPEC",
            order_quantity=100,
            order_date=first.order_date,
            due_date=first.due_date,
            created_by=self.user,
        )
        for ordering in ("order_date", "-order_date", "due_date", "-due_date"):
            numbers = self._numbers(ordering)
            self.assertLess(
                numbers.index(second.order_no),
                numbers.index(first.order_no),
            )

    def test_orders_support_three_level_sort_and_process_card_filter(self):
        common = {
            "specification": "MULTI-SPEC",
            "order_quantity": 1000,
            "due_date": date(2026, 9, 1),
            "created_by": self.user,
        }
        QualityOrder.objects.create(
            order_no="MULTI-RECEIVED",
            order_date=date(2026, 7, 1),
            process_card_count=1,
            process_card_covered_quantity=1000,
            **common,
        )
        QualityOrder.objects.create(
            order_no="MULTI-NOT-RECEIVED",
            order_date=date(2026, 7, 2),
            process_card_count=0,
            process_card_covered_quantity=0,
            **common,
        )
        QualityOrder.objects.create(
            order_no="MULTI-PARTIAL",
            order_date=date(2026, 7, 3),
            process_card_count=1,
            process_card_covered_quantity=500,
            **common,
        )

        response = self.client.get(
            "/api/orders/orders/",
            {
                "q": "MULTI-",
                "ordering": "due_date,process_card_status,order_date",
                "page_size": 100,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            [row["order_no"] for row in response.json()["results"]],
            ["MULTI-NOT-RECEIVED", "MULTI-PARTIAL", "MULTI-RECEIVED"],
        )

        filtered = self.client.get(
            "/api/orders/orders/",
            {
                "q": "MULTI-",
                "process_card_status": "PARTIAL",
                "page_size": 100,
            },
        )
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual(
            [row["order_no"] for row in filtered.json()["results"]],
            ["MULTI-PARTIAL"],
        )

    def test_legacy_unknown_card_text_is_not_received_in_payload_sort_and_filter(self):
        common = {
            "specification": "LEGACY-CARD-SPEC",
            "order_quantity": 1000,
            "due_date": date(2026, 9, 10),
            "created_by": self.user,
        }
        unknown_values = {
            "LEGACY-CARD-PENDING": "待补",
            "LEGACY-CARD-UNKNOWN": "未知",
            "LEGACY-CARD-CONFIRM": "待确认",
        }
        for index, (order_no, text) in enumerate(unknown_values.items(), start=1):
            QualityOrder.objects.create(
                order_no=order_no,
                order_date=date(2026, 7, index),
                process_card_text=text,
                **common,
            )
        QualityOrder.objects.create(
            order_no="LEGACY-CARD-RECEIVED",
            order_date=date(2026, 7, 4),
            process_card_text="已收到",
            **common,
        )

        response = self.client.get(
            "/api/orders/orders/",
            {
                "q": "LEGACY-CARD-",
                "ordering": "process_card_status,order_date,due_date",
                "page_size": 100,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["results"]
        self.assertEqual(
            {row["order_no"]: row["process_card_status"] for row in rows},
            {
                **{order_no: "NOT_RECEIVED" for order_no in unknown_values},
                "LEGACY-CARD-RECEIVED": "RECEIVED",
            },
        )
        self.assertEqual(
            [row["order_no"] for row in rows],
            [*unknown_values, "LEGACY-CARD-RECEIVED"],
        )

        not_received = self.client.get(
            "/api/orders/orders/",
            {
                "q": "LEGACY-CARD-",
                "process_card_status": "NOT_RECEIVED",
                "page_size": 100,
            },
        )
        self.assertEqual(not_received.status_code, 200, not_received.content)
        self.assertEqual(
            {row["order_no"] for row in not_received.json()["results"]},
            set(unknown_values),
        )

    def test_date_and_card_multilevel_sort_preserves_each_declared_priority(self):
        common = {
            "specification": "PRIORITY-SPEC",
            "order_quantity": 1000,
            "created_by": self.user,
        }
        cases = (
            # Same due date and card state: descending order date is tertiary.
            ("PRIORITY-EARLY-NO-NEW", date(2026, 7, 3), date(2026, 9, 1), 0),
            ("PRIORITY-EARLY-NO-OLD", date(2026, 7, 1), date(2026, 9, 1), 0),
            # Same due date but received: card state remains the secondary key.
            ("PRIORITY-EARLY-YES", date(2026, 7, 4), date(2026, 9, 1), 1),
            # Later due date must stay last even though its card state sorts first.
            ("PRIORITY-LATE-NO", date(2026, 7, 5), date(2026, 9, 2), 0),
        )
        for order_no, order_date, due_date, card_count in cases:
            QualityOrder.objects.create(
                order_no=order_no,
                order_date=order_date,
                due_date=due_date,
                process_card_count=card_count,
                process_card_covered_quantity=1000 if card_count else 0,
                **common,
            )

        response = self.client.get(
            "/api/orders/orders/",
            {
                "q": "PRIORITY-",
                "ordering": "due_date,process_card_status,-order_date",
                "page_size": 100,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            [row["order_no"] for row in response.json()["results"]],
            [
                "PRIORITY-EARLY-NO-NEW",
                "PRIORITY-EARLY-NO-OLD",
                "PRIORITY-EARLY-YES",
                "PRIORITY-LATE-NO",
            ],
        )

    def test_positive_card_count_with_zero_coverage_is_partial_consistently(self):
        order = QualityOrder.objects.create(
            order_no="CARD-ZERO-COVERAGE",
            specification="CARD-ZERO-COVERAGE-SPEC",
            order_quantity=1000,
            process_card_count=1,
            process_card_covered_quantity=0,
            created_by=self.user,
        )

        detail = self.client.get(f"/api/orders/orders/{order.pk}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["process_card_status"], "PARTIAL")

        filtered = self.client.get(
            "/api/orders/orders/",
            {
                "q": order.order_no,
                "process_card_status": "PARTIAL",
                "page_size": 100,
            },
        )
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual(
            [row["order_no"] for row in filtered.json()["results"]],
            [order.order_no],
        )


class MaterialReceiptDateApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="receipt-date-sorting", password="test"
        )
        self.client.force_authenticate(self.user)
        self.first = MaterialReceipt.objects.create(
            order_no="RECEIPT-FIRST",
            weight_kg=Decimal("1.000"),
            issued_on=date(2026, 7, 25),
            manufactured_on=date(2026, 7, 22),
        )
        self.second = MaterialReceipt.objects.create(
            order_no="RECEIPT-SECOND",
            weight_kg=Decimal("2.000"),
            issued_on=date(2026, 7, 29),
            manufactured_on=date(2026, 7, 24),
        )
        self.no_issue_date = MaterialReceipt.objects.create(
            order_no="RECEIPT-NO-ISSUE-DATE",
            weight_kg=Decimal("3.000"),
            issued_on=None,
            manufactured_on=date(2026, 7, 30),
        )

    def _numbers(self, **params):
        response = self.client.get(
            "/api/orders/material-receipts/",
            {"page_size": 100, **params},
        )
        self.assertEqual(response.status_code, 200, response.content)
        return [row["order_no"] for row in response.json()["results"]]

    def test_default_filter_and_optional_ordering_use_distinct_business_dates(self):
        self.assertEqual(
            self._numbers(),
            ["RECEIPT-SECOND", "RECEIPT-FIRST", "RECEIPT-NO-ISSUE-DATE"],
        )
        self.assertEqual(
            self._numbers(ordering="issued_on"),
            ["RECEIPT-FIRST", "RECEIPT-SECOND", "RECEIPT-NO-ISSUE-DATE"],
        )
        self.assertEqual(
            self._numbers(ordering="-manufactured_on"),
            ["RECEIPT-NO-ISSUE-DATE", "RECEIPT-SECOND", "RECEIPT-FIRST"],
        )
        self.assertEqual(
            self._numbers(date_from="2026-07-26", date_to="2026-07-31"),
            ["RECEIPT-SECOND"],
        )


class BusinessImportHistoryApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="import-history", password="test"
        )
        self.other_user = get_user_model().objects.create_user(
            username="import-history-other", password="test"
        )
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=Path(self.media_dir.name))
        self.override.enable()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def test_unreadable_xlsx_is_persisted_as_failed_history(self):
        content = b"this is not a valid xlsx archive"
        response = self.client.post(
            "/api/orders/imports/preview/",
            {
                "file": SimpleUploadedFile(
                    "broken-order.xlsx",
                    content,
                    content_type=(
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)

        batch = BusinessImportBatch.objects.get()
        self.assertEqual(batch.status, BusinessImportBatch.Status.FAILED)
        self.assertEqual(batch.source_type, BusinessImportBatch.SourceType.UNKNOWN)
        self.assertEqual(batch.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(batch.errors[0]["stage"], "preview")
        self.assertEqual(
            batch.errors[0]["message"],
            "文件不是有效的.xlsx工作簿。",
        )
        self.assertTrue(batch.original_file.storage.exists(batch.original_file.name))

        history = self.client.get("/api/orders/imports/history/")
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual(history.json()["count"], 1)
        summary = history.json()["results"][0]
        self.assertEqual(summary["original_name"], "broken-order.xlsx")
        self.assertEqual(summary["status"], "FAILED")
        self.assertEqual(summary["error_count"], 1)

        detail = self.client.get(f"/api/orders/imports/history/{batch.pk}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["issues"][0]["stage"], "preview")
        self.assertEqual(detail.json()["rows"], [])

        self.client.force_authenticate(self.other_user)
        hidden_list = self.client.get("/api/orders/imports/history/")
        self.assertEqual(hidden_list.status_code, 200, hidden_list.content)
        self.assertEqual(hidden_list.json()["count"], 0)
        hidden = self.client.get(f"/api/orders/imports/history/{batch.pk}/")
        self.assertEqual(hidden.status_code, 404, hidden.content)

    def test_malformed_workbook_xml_is_persisted_as_failed_history(self):
        content = malformed_xml_workbook()
        response = self.client.post(
            "/api/orders/imports/preview/",
            {
                "file": SimpleUploadedFile(
                    "malformed-xml.xlsx",
                    content,
                    content_type=(
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)
        batch = BusinessImportBatch.objects.get(original_name="malformed-xml.xlsx")
        self.assertEqual(batch.status, BusinessImportBatch.Status.FAILED)
        self.assertEqual(
            batch.errors[0]["message"],
            "Excel文件无法读取或格式不受支持。",
        )

    def test_internal_path_is_sanitized_in_response_and_history(self):
        sensitive = r"C:\\secret\\customer-orders\\private.xlsx"
        with patch(
            "orders.views.preview_business_workbook",
            side_effect=ValueError(f"permission denied: {sensitive}"),
        ):
            response = self.client.post(
                "/api/orders/imports/preview/",
                {
                    "file": SimpleUploadedFile(
                        "private.xlsx",
                        b"not-important",
                        content_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                    )
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertNotIn("secret", response.content.decode().lower())
        batch = BusinessImportBatch.objects.get(original_name="private.xlsx")
        self.assertNotIn("secret", batch.errors[0]["message"].lower())

    def test_history_filters_validate_choices_and_commit_failure_is_recorded(self):
        invalid_status = self.client.get(
            "/api/orders/imports/history/", {"status": "not-a-status"}
        )
        self.assertEqual(invalid_status.status_code, 400, invalid_status.content)
        invalid_source = self.client.get(
            "/api/orders/imports/history/", {"source_type": "not-a-source"}
        )
        self.assertEqual(invalid_source.status_code, 400, invalid_source.content)

        batch = BusinessImportBatch.objects.create(
            source_type=BusinessImportBatch.SourceType.INTERNAL_ORDERS,
            parser="test",
            status=BusinessImportBatch.Status.PREVIEWED,
            original_name="commit-failure.xlsx",
            original_file=SimpleUploadedFile("commit-failure.xlsx", b"test"),
            sha256=hashlib.sha256(b"test").hexdigest(),
            payload={"rows": [], "parser": "test"},
            errors=[],
            warnings=[],
            created_by=self.user,
        )
        with patch(
            "orders.views.commit_business_batch",
            side_effect=ValueError("提交前复检失败：订单冲突。"),
        ):
            response = self.client.post(
                "/api/orders/imports/commit/",
                {"token": str(batch.pk)},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BusinessImportBatch.Status.FAILED)
        self.assertEqual(batch.errors[-1]["stage"], "commit")

    def test_history_detail_keeps_row_level_skip_reasons(self):
        warning = {
            "level": "warning",
            "message": "订单号和项次已存在，本行未重复新增。",
            "sheet": "订单",
            "row": 8,
            "field": "item_no",
        }
        batch = BusinessImportBatch.objects.create(
            source_type=BusinessImportBatch.SourceType.INTERNAL_ORDERS,
            parser="test",
            status=BusinessImportBatch.Status.COMMITTED,
            original_name="orders.xlsx",
            original_file=SimpleUploadedFile("orders.xlsx", b"test"),
            sha256=hashlib.sha256(b"test").hexdigest(),
            payload={
                "rows": [
                    {
                        "row_key": "订单:8:ORDER",
                        "record_type": "ORDER",
                        "sheet": "订单",
                        "row": 8,
                        "action": "SKIP",
                        "order_no": "DUP-001",
                        "item_no": "20",
                        "specification": "SPEC-DUP",
                        "skip_reason_code": "UNCHANGED",
                        "skip_reason": "订单号和项次与现有订单完全重复。",
                        "changes": {},
                    }
                ],
                "parser": "test",
            },
            errors=[],
            warnings=[warning],
            created_by=self.user,
        )

        detail = self.client.get(f"/api/orders/imports/history/{batch.pk}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        row = detail.json()["rows"][0]
        self.assertEqual(row["action"], "SKIP")
        self.assertEqual(
            row["skip_reason"], "订单号和项次与现有订单完全重复。"
        )
        self.assertEqual(row["skip_reason_code"], "UNCHANGED")
        self.assertEqual(len(row["reasons"]), 2)

        history = self.client.get("/api/orders/imports/history/")
        summary = history.json()["results"][0]
        self.assertEqual(summary["actions"]["SKIP"], 1)
        self.assertEqual(summary["counts"]["orders"], 1)
