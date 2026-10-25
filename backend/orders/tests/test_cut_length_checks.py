import csv
import io
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from openpyxl import load_workbook
from rest_framework.test import APITestCase

from orders.cut_length_checks import build_cut_length_check, cut_length_report, parse_length_mm
from orders.imports import preview_business_workbook
from orders.models import BusinessImportBatch, ProductSpecification
from quality.models import QualityOrder

from .test_imports import SYNTHETIC_MATERIAL, factory_workbook, product_workbook, upload, workbook_bytes


class LengthParsingTests(SimpleTestCase):
    def test_positive_single_mm_values_only(self):
        for value in ("281", "281.00mm", "２８１毫米", "人工实测 281mm", "实际切料长：281"):
            with self.subTest(value=value):
                self.assertEqual(parse_length_mm(value), Decimal("281"))
        for value in (None, "", 0, "-5", "281/275", "281±5", "281x2", "28cm", "NaN", "1e3", "281或300"):
            with self.subTest(value=value):
                self.assertIsNone(parse_length_mm(value))

    def test_csv_formula_injection_is_escaped(self):
        check = {"rows": [{"status": "MISSING_ACTUAL", "order_no": "=1+1", "item_no": "+1",
                           "specification": " @evil", "material": "-danger", "difference_mm": "-10"}]}
        rows = list(csv.reader(io.StringIO(cut_length_report(check).decode("utf-8-sig"))))
        self.assertEqual(rows[1][:4], ["'=1+1", "'+1", "' @evil", "'-danger"])


class CutLengthImportTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="length-importer", password="test")
        self.client.force_authenticate(self.user)
        media = tempfile.TemporaryDirectory()
        settings = override_settings(MEDIA_ROOT=Path(media.name))
        settings.enable()
        self.addCleanup(media.cleanup)
        self.addCleanup(settings.disable)

    def product(self, actual="280", **extra):
        return ProductSpecification.objects.create(
            specification="TEST-SPEC-B", material=SYNTHETIC_MATERIAL,
            actual_material_length=actual, **extra,
        )

    def preview(self, length=275, **kwargs):
        result = preview_business_workbook(upload("factory.xlsx", factory_workbook(
            material_length=length, include_criteria=False, **kwargs,
        )), self.user)
        self.assertEqual(result["error_count"], 0, result["issues"])
        return result

    def commit(self, preview, **extra):
        return self.client.post("/api/orders/imports/commit/", {"token": preview["token"], **extra}, format="json")

    def acknowledged(self, preview):
        return self.commit(preview, cut_length_acknowledged=True, cut_length_digest=preview["cut_length_check"]["digest"])

    def test_inclusive_five_mm_boundary_needs_no_acknowledgement(self):
        self.product("280", mold_no="TEST-MOLD-01")
        for index, length in enumerate((275, 280, 285)):
            with self.subTest(length=length):
                preview = self.preview(length, order_no=f"BOUNDARY-{index}")
                self.assertEqual(preview["cut_length_check"]["alert_count"], 0)
                self.assertEqual(preview["cut_length_check"]["rows"][0]["status"], "IN_TOLERANCE")
                response = self.commit(preview)
                self.assertEqual(response.status_code, 200, response.data)

    def test_shorter_and_longer_fractional_difference_alert(self):
        self.product("280")
        for length, expected in (("274.99", "SHORTER"), ("285.01", "LONGER")):
            with self.subTest(length=length):
                preview = self.preview(length)
                row = preview["cut_length_check"]["rows"][0]
                self.assertEqual(row["status"], expected)
                self.assertEqual(Decimal(row["difference_mm"]), Decimal(length) - 280)

    def test_old_customer_value_never_becomes_baseline(self):
        self.product("", material_length="275")
        check = self.preview(275)["cut_length_check"]
        self.assertEqual(check["rows"][0]["status"], "MISSING_ACTUAL")
        self.assertIsNone(check["rows"][0]["actual_mm"])
        self.assertEqual(check["alert_count"], 1)

    def test_unknown_new_product_can_be_imported_after_explicit_acknowledgement(self):
        preview = self.preview()
        response = self.commit(preview)
        self.assertEqual(response.status_code, 409, response.data)
        batch = BusinessImportBatch.objects.get(pk=preview["token"])
        self.assertEqual(batch.status, "PREVIEWED")
        self.assertEqual(batch.errors, [])
        self.assertEqual(QualityOrder.objects.count(), 0)
        self.assertEqual(ProductSpecification.objects.count(), 0)
        response = self.acknowledged(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(QualityOrder.objects.count(), 1)
        self.assertEqual(QualityOrder.objects.get().order_quantity, 2400)
        batch.refresh_from_db()
        self.assertEqual(batch.payload["cut_length_confirmation"]["user_id"], self.user.pk)
        self.assertEqual(batch.payload["cut_length_check"]["rows"][0]["status"], "MISSING_ACTUAL")

    def test_acknowledgement_requires_literal_true_and_current_digest(self):
        preview = self.preview()
        for extra in ({"cut_length_acknowledged": "true"}, {"cut_length_acknowledged": True, "cut_length_digest": "wrong"}):
            response = self.commit(preview, **extra)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(QualityOrder.objects.count(), 0)

    def test_change_to_actual_value_invalidates_previous_confirmation(self):
        product = self.product("300", mold_no="TEST-MOLD-01")
        preview = self.preview(275)
        product.actual_material_length = "310"
        product.save()
        response = self.acknowledged(preview)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(QualityOrder.objects.count(), 0)
        new_check = response.data["cut_length_check"]
        self.assertEqual(new_check["rows"][0]["actual_length"], "310")
        self.assertNotEqual(new_check["digest"], preview["cut_length_check"]["digest"])
        preview["cut_length_check"] = new_check
        response = self.acknowledged(preview)
        self.assertEqual(response.status_code, 200, response.data)
        product.refresh_from_db()
        self.assertEqual(product.actual_material_length, "310")
        self.assertEqual(product.material_length, "275")

    def test_complex_customer_or_actual_values_require_manual_check(self):
        product = self.product("281/275")
        self.assertEqual(self.preview()["cut_length_check"]["rows"][0]["status"], "UNREADABLE_ACTUAL")
        product.actual_material_length = "280"
        product.save()
        self.assertEqual(self.preview("275/300")["cut_length_check"]["rows"][0]["status"], "UNREADABLE_CUSTOMER")

    def test_unique_spec_material_is_used_even_when_customer_mold_changes(self):
        product = self.product("300", mold_no="OTHER-MOLD")
        row = self.preview()["cut_length_check"]["rows"][0]
        self.assertEqual(row["product_id"], product.pk)
        self.assertEqual(row["status"], "SHORTER")

    def test_ambiguous_manual_baselines_are_not_guessed(self):
        self.product("300", mold_no="OTHER-A")
        self.product("250", mold_no="OTHER-B")
        row = self.preview()["cut_length_check"]["rows"][0]
        self.assertEqual(row["status"], "AMBIGUOUS_PRODUCT")
        self.assertIsNone(row["product_id"])
        self.assertEqual(len(row["candidates"]), 2)

    def test_explicit_import_link_can_disambiguate_baseline(self):
        linked = self.product("300", mold_no="TEST-MOLD-01")
        self.product("250", mold_no="OTHER-B")
        row = self.preview()["cut_length_check"]["rows"][0]
        self.assertEqual(row["product_id"], linked.pk)
        self.assertEqual(row["status"], "SHORTER")

    def test_each_order_item_is_in_report_even_if_product_is_deduplicated(self):
        workbook = load_workbook(io.BytesIO(factory_workbook(include_criteria=False)))
        sheet = workbook.active
        values = [cell.value for cell in sheet[4]]
        values[1] = "02"
        sheet.append(values)
        result = preview_business_workbook(upload("factory.xlsx", workbook_bytes(workbook)), self.user)
        self.assertEqual(result["counts"]["product_specifications"], 1)
        self.assertEqual(result["cut_length_check"]["alert_count"], 2)
        self.assertEqual({r["item_no"] for r in result["cut_length_check"]["rows"]}, {"1", "02"})
        report = self.client.get(f'/api/orders/imports/{result["token"]}/cut-length/')
        self.assertEqual(report.status_code, 200)
        self.assertEqual(len(list(csv.reader(io.StringIO(report.content.decode("utf-8-sig"))))), 3)

    def test_refresh_after_manual_entry_clears_reminder_without_reupload(self):
        preview = self.preview()
        self.product("275")
        response = self.client.post(f'/api/orders/imports/{preview["token"]}/cut-length/', {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["cut_length_check"]["alert_count"], 0)
        self.assertEqual(self.commit(preview).status_code, 200)

    def test_history_is_snapshot_not_recalculated_and_cannot_refresh(self):
        product = self.product("300", mold_no="TEST-MOLD-01", actual_cut_weight="10.2g")
        preview = self.preview()
        self.assertEqual(self.acknowledged(preview).status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.actual_cut_weight, "10.2g")
        product.actual_material_length = "275"
        product.save()
        response = self.client.get(f'/api/orders/imports/history/{preview["token"]}/')
        self.assertEqual(response.data["cut_length_check"]["rows"][0]["actual_length"], "300")
        self.assertEqual(response.data["cut_length_confirmation"]["username"], "length-importer")
        response = self.client.post(f'/api/orders/imports/{preview["token"]}/cut-length/', {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_check_and_download_are_owner_only(self):
        preview = self.preview()
        other = get_user_model().objects.create_user(username="other-importer")
        self.client.force_authenticate(other)
        url = f'/api/orders/imports/{preview["token"]}/cut-length/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 404)
        self.assertEqual(self.acknowledged(preview).status_code, 404)

    def test_unrelated_import_has_no_cutting_check(self):
        result = preview_business_workbook(upload("products.xlsx", product_workbook()), self.user)
        self.assertIsNone(result["cut_length_check"])
        self.assertEqual(self.commit(result).status_code, 200)

    def test_bulk_check_has_no_per_order_query(self):
        preview = self.preview()
        records = BusinessImportBatch.objects.get(pk=preview["token"]).payload["rows"]
        records += [dict(next(row for row in records if row["record_type"] == "ORDER"), row_key=f"extra-{i}") for i in range(500)]
        with self.assertNumQueries(1):
            check = build_cut_length_check(records, "FACTORY_WORK_CONTACT")
        self.assertEqual(len(check["rows"]), 501)
