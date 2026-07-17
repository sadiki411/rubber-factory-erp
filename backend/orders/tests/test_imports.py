import io
import tempfile
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APITestCase

from orders.imports import commit_business_batch, preview_business_workbook
from orders.models import (
    BusinessImportBatch,
    MaterialReceipt,
    ProductInspectionCriterion,
    ProductSpecification,
)
from quality.models import QualityOrder


CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SYNTHETIC_SPEC = "TEST-SPEC-A"
SYNTHETIC_MATERIAL = "SYN-RUBBER-A"
SYNTHETIC_FACTORY_ORDER = "TEST-DEMAND-001"
SYNTHETIC_PROJECT = "TEST-PROJECT-001"
CUSTOM_DISPLAY_TEXT = "9/4"
CUSTOM_RAW_VALUE = 98765


def workbook_bytes(workbook):
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def upload(name, content):
    return SimpleUploadedFile(name, content, content_type=CONTENT_TYPE)


def product_workbook(*, literal_strip=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工作表1"
    sheet.append(
        [
            "规格",
            "材质",
            "料长",
            "切料重",
            "条数",
            "一次加硫条件",
            "二烤条件",
            "总孔数",
            "有效孔数",
            "模具在库",
            "备注",
        ]
    )
    sheet.append(
        [
            SYNTHETIC_SPEC,
            SYNTHETIC_MATERIAL,
            "TEST-LENGTH-A",
            "TEST-CUT-WEIGHT-A",
            CUSTOM_RAW_VALUE,
            "TEST-CURE-A",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    if literal_strip:
        sheet["E2"].number_format = CUSTOM_DISPLAY_TEXT
    return workbook_bytes(workbook)


def internal_order_workbook(order_no="ORD-1", *, duplicate=False, corrupt_styles=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "2026年订单"
    sheet.append(
        [
            "订单编号",
            "规格",
            "胶料配方",
            "交期",
            "订单量",
            "成型工时",
            "下单时间",
            "模具尺寸",
            "是否生产",
            "出货日期",
        ]
    )
    row = [
        order_no,
        SYNTHETIC_SPEC,
        SYNTHETIC_MATERIAL,
        date(2026, 8, 20),
        240,
        7.5,
        date(2026, 8, 1),
        "TEST-MOLD-SIZE-A",
        "否",
        "",
    ]
    sheet.append(row)
    if duplicate:
        sheet.append(row)
    sheet.append([None, None, None, None, None, None, None, None, "否", None])
    content = workbook_bytes(workbook)
    if not corrupt_styles:
        return content
    source = ZipFile(io.BytesIO(content))
    output = io.BytesIO()
    with source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/styles.xml":
                text = data.decode("utf-8")
                text = text.replace("</fills>", "<fill/></fills>", 1)
                data = text.encode("utf-8")
            target.writestr(item, data)
    return output.getvalue()


def factory_workbook():
    workbook = Workbook()
    first = workbook.active
    first.title = "sheet1"
    first.append(["生产工作联络单"])
    first.append(
        [
            "协力商：",
            "",
            "SYNTHETIC-SUPPLIER",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "发单时间：",
            date(2026, 8, 3),
        ]
    )
    first.append(
        [
            "独立需求号",
            "项次",
            "材质",
            "规格",
            "订单量",
            "完成日",
            "参考工时",
            "胶料用量（KG）",
            "切料重",
            "料长",
            "一次加硫条件",
            "二次加硫条件",
            "模具号",
            "模具尺寸",
        ]
    )
    first.append(
        [
            SYNTHETIC_FACTORY_ORDER,
            1,
            SYNTHETIC_MATERIAL,
            "TEST-SPEC-B",
            2400,
            date(2026, 8, 10),
            2.5,
            24,
            9.75,
            275,
            "TEST-CURE-PRIMARY",
            "TEST-CURE-SECONDARY",
            "TEST-MOLD-01",
            "TEST-MOLD-SIZE-B",
        ]
    )
    second = workbook.create_sheet("Sheet2")
    second.append(["生产工作联络单二"])
    second.append(["独立需求号", "项次", "项目号", "客户", "类别", "版本", "检验项目", "下限", "上限", "单位"])
    second.append(
        [
            SYNTHETIC_FACTORY_ORDER,
            1,
            SYNTHETIC_PROJECT,
            "SYNTHETIC-CUSTOMER",
            "TEST-CATEGORY",
            "V1",
            "TEST-DIMENSION",
            9.8,
            10.2,
            "mm",
        ]
    )
    return workbook_bytes(workbook)


def material_workbook(batch_no="TEST-BATCH-001"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "sheet1"
    sheet.append(["混料发料清单与制造进料检验记录"])
    sheet.append(["共：", "1支"])
    sheet.append(["序号", "项次", "独立需求号", "成品品名", "成品规格", "材质", "批号", "出片尺寸", "重量", "制造时间"])
    sheet.append(
        [
            1,
            1,
            SYNTHETIC_FACTORY_ORDER,
            SYNTHETIC_PROJECT,
            "TEST-SPEC-B",
            SYNTHETIC_MATERIAL,
            batch_no,
            "TEST-SHEET-SIZE",
            24.5,
            "2026/08/04",
        ]
    )
    return workbook_bytes(workbook)


class BusinessImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="importer", password="test")
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=Path(self.media_dir.name))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def preview(self, name, content):
        return preview_business_workbook(upload(name, content), self.user)

    def commit(self, result):
        return commit_business_batch(
            BusinessImportBatch.objects.get(pk=result["token"]), self.user
        )

    def test_literal_custom_number_format_uses_display_text_and_keeps_raw_value(self):
        result = self.preview("products.xlsx", product_workbook(literal_strip=True))
        self.assertEqual(result["error_count"], 0, result["issues"])
        self.commit(result)
        product = ProductSpecification.objects.get()
        self.assertEqual(product.strip_count, CUSTOM_DISPLAY_TEXT)
        self.assertEqual(product.raw_data["条数"]["raw_value"], CUSTOM_RAW_VALUE)
        self.assertEqual(
            product.raw_data["条数"]["number_format"], CUSTOM_DISPLAY_TEXT
        )

    def test_bad_styles_uses_safe_ooxml_dates_and_preserves_identical_rows(self):
        result = self.preview(
            "orders.xlsx",
            internal_order_workbook(duplicate=True, corrupt_styles=True),
        )
        self.assertEqual(result["source_type"], "INTERNAL_ORDERS")
        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["error_count"], 0, result["issues"])
        self.assertEqual(
            BusinessImportBatch.objects.get(pk=result["token"]).parser,
            "safe-ooxml-1",
        )
        self.commit(result)
        orders = list(QualityOrder.objects.order_by("source_row"))
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0].order_date, date(2026, 8, 1))
        self.assertEqual(orders[0].due_date, date(2026, 8, 20))
        self.assertNotEqual(orders[0].source_key, orders[1].source_key)

    def test_internal_order_links_unique_product_and_warns_on_ambiguous_match(self):
        unique = ProductSpecification.objects.create(
            specification=SYNTHETIC_SPEC, material=SYNTHETIC_MATERIAL
        )
        result = self.preview("order-unique.xlsx", internal_order_workbook("LINK-1"))
        self.commit(result)
        self.assertEqual(QualityOrder.objects.get(order_no="LINK-1").product_specification, unique)

        ProductSpecification.objects.create(
            specification=SYNTHETIC_SPEC, material=SYNTHETIC_MATERIAL
        )
        ambiguous = self.preview("order-ambiguous.xlsx", internal_order_workbook("LINK-2"))
        self.assertTrue(any("多条相同规格" in item["message"] for item in ambiguous["issues"]))
        self.commit(ambiguous)
        self.assertIsNone(QualityOrder.objects.get(order_no="LINK-2").product_specification)

    def test_factory_work_contact_imports_order_product_and_criterion(self):
        result = self.preview("factory.xlsx", factory_workbook())
        self.assertEqual(
            result["counts"],
            {
                "product_specifications": 1,
                "orders": 1,
                "material_receipts": 0,
                "inspection_criteria": 1,
            },
        )
        self.assertEqual(result["error_count"], 0, result["issues"])
        self.commit(result)
        order = QualityOrder.objects.get(order_no=SYNTHETIC_FACTORY_ORDER)
        criterion = ProductInspectionCriterion.objects.get()
        self.assertEqual(order.product_specification_id, criterion.product_specification_id)
        self.assertEqual(criterion.order_id, order.pk)
        self.assertEqual(criterion.project_no, SYNTHETIC_PROJECT)

    def test_material_receipt_links_unique_order_and_reimport_skips(self):
        order = QualityOrder.objects.create(
            order_no=SYNTHETIC_FACTORY_ORDER,
            item_no="1",
            specification="TEST-SPEC-B",
            order_quantity=2400,
            created_by=self.user,
        )
        content = material_workbook()
        first = self.preview("material.xlsx", content)
        self.commit(first)
        receipt = MaterialReceipt.objects.get()
        self.assertEqual(receipt.order_id, order.pk)

        repeated = self.preview("material.xlsx", content)
        self.assertEqual(repeated["warning_count"], 1)
        result = self.commit(repeated)
        self.assertEqual(result["skipped"]["material_receipts"], 1)
        self.assertEqual(MaterialReceipt.objects.count(), 1)

    def test_material_receipt_preview_warns_when_order_is_missing_or_ambiguous(self):
        missing = self.preview("material-missing.xlsx", material_workbook())
        self.assertTrue(any("未找到对应订单" in item["message"] for item in missing["issues"]))
        self.commit(missing)
        self.assertIsNone(MaterialReceipt.objects.get().order_id)

        MaterialReceipt.objects.all().delete()
        for _ in range(2):
            QualityOrder.objects.create(
                order_no=SYNTHETIC_FACTORY_ORDER,
                item_no="1",
                specification="TEST-SPEC-B",
                material=SYNTHETIC_MATERIAL,
                order_quantity=2400,
                created_by=self.user,
            )
        ambiguous_content = material_workbook("TEST-BATCH-002")
        ambiguous = self.preview("material-ambiguous.xlsx", ambiguous_content)
        self.assertTrue(any("多条可能对应" in item["message"] for item in ambiguous["issues"]))
        self.commit(ambiguous)
        receipt = MaterialReceipt.objects.order_by("-id").first()
        self.assertIsNone(receipt.order_id)

    def test_commit_is_transactional_when_payload_is_changed_after_preview(self):
        product = self.preview("products.xlsx", product_workbook())
        batch = BusinessImportBatch.objects.get(pk=product["token"])
        bad_order = {
            "row_key": "extra",
            "record_type": "ORDER",
            "sheet": "extra",
            "row": 99,
            "source_key": f"{batch.sha256}:extra:99:ORDER",
            "raw_data": {},
            "order_no": "BAD",
            "specification": "",
            "material": "",
            "order_quantity": 0,
            "status": "OPEN",
        }
        payload = batch.payload
        payload["rows"].append(bad_order)
        batch.payload = payload
        batch.save(update_fields=["payload"])
        with self.assertRaises(ValueError):
            commit_business_batch(batch, self.user)
        self.assertEqual(ProductSpecification.objects.count(), 0)
        self.assertEqual(QualityOrder.objects.count(), 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BusinessImportBatch.Status.PREVIEWED)


class BusinessImportApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="import-api", password="test")
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=Path(self.media_dir.name))
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def test_template_preview_commit_error_report_and_original_file_backup(self):
        self.assertIn(
            self.client.get("/api/orders/product-specifications/").status_code,
            (401, 403),
        )
        self.client.force_authenticate(self.user)
        template = self.client.get(
            "/api/orders/imports/template/?type=product_specifications"
        )
        self.assertEqual(template.status_code, 200, template.content)

        preview = self.client.post(
            "/api/orders/imports/preview/",
            {"file": upload("products.xlsx", product_workbook(literal_strip=True))},
            format="multipart",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        payload = preview.json()
        self.assertEqual(payload["counts"]["product_specifications"], 1)
        batch = BusinessImportBatch.objects.get(pk=payload["token"])
        self.assertTrue(batch.original_file.name.startswith("business-imports/"))
        self.assertTrue(batch.original_file.storage.exists(batch.original_file.name))

        report = self.client.get(f"/api/orders/imports/{batch.pk}/errors/")
        self.assertEqual(report.status_code, 200, report.content)
        committed = self.client.post(
            "/api/orders/imports/commit/",
            {"token": payload["token"]},
            format="json",
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(committed.json()["imported"]["product_specifications"], 1)
